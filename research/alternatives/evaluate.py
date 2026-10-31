"""python -m research.alternatives.evaluate [--rag] [--allow-api]

New CPU experiments and label artifacts only. No TabPFN import, GPU work,
deployment checkpoint writes, live HTTP requests, or production DB mutations.
--allow-api permits ONE batch of public literature-query embeddings on cache miss.
"""
from __future__ import annotations

import os
for _key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

import argparse
from collections import Counter
import hashlib
import json
import sys
import time

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, average_precision_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .data import ARTIFACTS, ROOT, digest, fusion_dataset, posture_windows, purged_split, readonly_db, write_json, write_jsonl
from .models import ContextualProfile, RandomInterceptLMM, train_autoencoder

sys.path.insert(0, str(ROOT / "server"))
from maintenance_ai import AdaptiveWindow, MaintenanceMonitor


def metrics(y, score, threshold):
    y, score = np.asarray(y), np.asarray(score)
    pred = score > threshold
    return {"n": len(y), "positive": int(y.sum()), "threshold": float(threshold),
            "accuracy": float(accuracy_score(y, pred)), "precision": float(precision_score(y, pred, zero_division=0)),
            "recall": float(recall_score(y, pred, zero_division=0)), "f1": float(f1_score(y, pred, zero_division=0)),
            "roc_auc": float(roc_auc_score(y, score)) if len(set(y)) == 2 else None,
            "average_precision": float(average_precision_score(y, score)) if y.sum() else None,
            "confusion_tn_fp_fn_tp": confusion_matrix(y, pred, labels=[0, 1]).ravel().tolist()}


def anomaly_comparison():
    w, rows = posture_windows()
    splits = {s: np.array([i for i, r in enumerate(rows) if r["split"] == s], dtype=int)
              for s in ("train", "calibration", "test")}
    tr, cal, te = (splits[k] for k in ("train", "calibration", "test"))
    contexts = [r["context"] for r in rows]
    cmp = ContextualProfile().fit(w[tr], [contexts[i] for i in tr])
    cmp.calibrate(w[cal], [contexts[i] for i in cal])
    valid = [i for i in te if cmp.predict(w[i], contexts[i])["threshold"] is not None]
    y = [rows[i]["proxy_target"] for i in valid]
    scores = [cmp.distance(w[i], contexts[i]) / max(cmp.thresholds[contexts[i]], 1e-12) for i in valid]
    ae_threshold, ae_scores = train_autoencoder(w[tr], w[cal], w[valid])
    predictions = [{"id": rows[i]["id"], "proxy_target": rows[i]["proxy_target"],
                    "cmp": cmp.predict(w[i], contexts[i]), "ae_mse": float(ae_scores[j]),
                    "ae_threshold": ae_threshold} for j, i in enumerate(valid)]
    write_jsonl(ARTIFACTS / "anomaly_predictions.jsonl", predictions)
    return {"task": "capture-labelled non-lying posture proxy, not clinical anomaly/fall",
            "features": "12 distance summary features, 20 paired frames; NOT deployment 12 spatial features",
            "split": {k: len(v) for k, v in splits.items()}, "contexts": dict(Counter(contexts)),
            "normalization": "training windows only", "threshold": "99th percentile of six normal calibration windows",
            "cmp": metrics(y, scores, 1.0), "fresh_cpu_lstm_ae": metrics(y, ae_scores, ae_threshold),
            "deploy": False, "limitations": ["single known recording day, subject IDs unavailable",
                "only six calibration windows; unstable 99th percentile", "no clinical outcome labels",
                "empty recordings fail continuity gate: empty context abstains, never treated as an anomaly",
                "sitting/edge_sit can be normal daily activity; proxy positives are not real hazards"]}


def fusion_comparison():
    tof, radar, rows, all_labels = fusion_dataset()
    splits = purged_split(rows)
    write_jsonl(ARTIFACTS / "fusion_labels.jsonl", rows)
    result = {"task": "3-second future sustained ToF RF empty transition (weak target)",
              "strict_eligible": sum(r["strict_eligible"] for r in all_labels),
              "weak_label_counts": dict(Counter(str(r["target"]) for r in all_labels)),
              "alignment_counts": dict(Counter(r["alignment_status"] for r in all_labels)),
              "split": {k: {"n": len(v), "positive": sum(rows[i]["target"] for i in v)} for k, v in splits.items()},
              "deploy": False, "architecture": "balanced logistic regression on concatenated low-level summaries",
              "limitations": ["no strict high-confidence occupied labels; exploratory only", "same short single-subject recording",
                "ToF classifier supplies target; correlated labelling error and teacher bias remain",
                "clock sync unverified; KST conversion plus past-only 500ms matching is provisional",
                "late max comparator uses learned modality probabilities, NOT deployed alert-level max",
                "calibration block has no positives; fixed 0.5 threshold, no tuning or probability calibration"]}
    tr, te = splits["train"], splits["test"]
    y = np.array([r["target"] for r in rows])
    if len(tr) == 0 or len(te) == 0 or len(set(y[tr])) < 2 or len(set(y[te])) < 2:
        return {**result, "status": "insufficient_classes"}
    probabilities = {}
    for name, x in (("tof_only", tof), ("radar_only", radar), ("early_features", np.hstack((tof, radar)))):
        model = make_pipeline(StandardScaler(), LogisticRegression(C=1, class_weight="balanced", max_iter=1000, random_state=42))
        model.fit(x[tr], y[tr])
        probabilities[name] = model.predict_proba(x[te])[:, 1]
    probabilities["late_max"] = np.maximum(probabilities["tof_only"], probabilities["radar_only"])
    result["metrics"] = {name: metrics(y[te], score, .5) for name, score in probabilities.items()}
    write_jsonl(ARTIFACTS / "fusion_predictions.jsonl",
                [{"id": rows[i]["id"], "weak_target": int(y[i]),
                  **{name: float(score[j]) for name, score in probabilities.items()}} for j, i in enumerate(te)])
    return {**result, "status": "exploratory_completed"}


def drift_comparison():
    # Explicit engineering controls. These generated labels describe a numerical
    # distribution shift; they are never claimed as clinical event labels.
    rng = np.random.default_rng(42)
    stable = np.clip(rng.normal(.2, .015, 800), 0, 1)
    shifted = np.concatenate((stable[:400], np.clip(rng.normal(.8, .015, 400), 0, 1)))
    results, labels = {}, []
    for name, values in (("stationary", stable), ("injected_shift", shifted)):
        monitor, alarms = AdaptiveWindow(), []
        for i, value in enumerate(values):
            result = monitor.update(float(value))
            if result["drift"]:
                alarms.append(i)
            labels.append({"scenario": name, "sample": i, "value": float(value),
                           "label_type": "synthetic_engineering_control",
                           "distribution_regime": int(name == "injected_shift" and i >= 400),
                           "clinical_outcome": None})
        first = next((i for i in alarms if i >= 400), None)
        results[name] = {"alarm_indices": alarms, "delay_samples": first-400 if first is not None else None,
                         "false_alarms_before_shift": sum(i < 400 for i in alarms)}
    write_jsonl(ARTIFACTS / "drift_control_labels.jsonl", labels)
    # Replay the historical mmWave speed stream, requiring stored quality and
    # one stable target; no clinical truth exists for these changes.
    conn = readonly_db(ROOT / "server/majubom_4x4bak.db")
    monitor = MaintenanceMonitor({"speed": (0., 3.)}, max_gap_sec=180)
    statuses, events = Counter(), []
    try:
        for rid, ts, target, speed, reliable in conn.execute(
                "SELECT id,timestamp,target_id,speed,reliable FROM mmw_readings WHERE target_id='room_01' ORDER BY timestamp,id"):
            result = monitor.observe(target, "mmwave", "speed", speed, ts, reliable=bool(reliable))
            statuses[result.get("reason", result["status"])] += 1
            if result.get("drift"):
                events.append({"source_raw_id": rid, "timestamp": ts, **result})
    finally:
        conn.close()
    write_jsonl(ARTIFACTS / "historical_drift_candidates.jsonl", events)
    return {"engineering_controls": results, "historical_replay": {"statuses": dict(statuses), "candidates": len(events)},
            "clinical_sensitivity": None, "automatic_baseline_update": False,
            "limitations": ["bounded ADWIN1-style implementation, not ADWIN2", "unlabelled speed changes may reflect activity, not deterioration"]}


def lmm_comparison():
    rng = np.random.default_rng(42)
    subjects = np.repeat(np.arange(8), 30)
    offsets = rng.normal(0, 1.5, 8)
    values = 5 + offsets[subjects] + rng.normal(0, .3, len(subjects))
    lmm = RandomInterceptLMM().fit(values, subjects)
    return {"real_data_status": "blocked_missing_independent_subject_ids_and_longitudinal_outcomes",
            "requires_outcome_labels_to_fit": False, "requires_population_reference_data": True,
            "synthetic_engineering_fit": {"mean": lmm.mean, "within_variance": lmm.within,
                "between_variance": lmm.between, "example": lmm.score(8., values[:10])},
            "deploy": False, "note": "Recording files and simulated beds were not invented as independent real people."}


def rag_comparison(allow_api=False):
    import paper_kb
    from retrieval import fuse_rankings, sparse_scores
    qrels_path = ROOT / "research/alternatives/rag_qrels.json"
    qrels = json.loads(qrels_path.read_text(encoding="utf-8"))
    cases = qrels["queries"]
    connection = paper_kb._connect()
    if not paper_kb._table_exists(connection, paper_kb.PAPERS_TABLE):
        return {"status": "missing_index"}
    table = connection.open_table(paper_kb.PAPERS_TABLE)
    rows = table.to_arrow().to_pylist()  # Read only: never build/migrate/re-embed corpus.
    vectors = np.asarray([r["vector"] for r in rows], dtype=float)
    cache = ARTIFACTS / "query_embeddings.npz"
    cache_key = hashlib.sha256((qrels_path.read_text(encoding="utf-8")+paper_kb.EMBEDDING_MODEL).encode()).hexdigest()
    embedded = None
    if cache.exists():
        with np.load(cache, allow_pickle=False) as stored:
            if str(stored["cache_key"]) == cache_key:
                embedded = stored["vectors"]
    if embedded is None:
        if not allow_api:
            return {"status": "query_embeddings_missing", "qrels": len(cases)}
        response = paper_kb.embed_texts([c["query"] for c in cases])
        if not response["success"]:
            return {"status": "embedding_failed", "error_type": response.get("error_type")}
        embedded = np.asarray(response["embeddings"])
        np.savez_compressed(cache, vectors=embedded, cache_key=cache_key)
    ids = [r["chunk_id"] for r in rows]
    by_id = {r["chunk_id"]: r for r in rows}
    texts = [r["original_text"] for r in rows]
    results = []
    for case, qvec in zip(cases, embedded):
        similarities = vectors @ qvec / np.maximum(np.linalg.norm(vectors, axis=1) * np.linalg.norm(qvec), 1e-12)
        dense = [ids[i] for i in np.argsort(-similarities, kind="stable")]
        lexical = dict(zip(ids, sparse_scores(case["query"], texts)))
        hybrid, _, _, _ = fuse_rankings(dense, lexical)
        sparse = sorted((cid for cid in ids if lexical[cid] > 0), key=lambda cid: (-lexical[cid], cid))
        scores = {}
        expected = set(case["relevant_papers"])
        for method, ranking in (("dense", dense), ("bm25", sparse), ("hybrid", hybrid)):
            # Paper-level top five UNIQUE papers: duplicates/aliases must not
            # consume slots or count a paper twice.
            papers = []
            for cid in ranking:
                pid = by_id[cid]["paper_id"]
                pid = qrels["aliases"].get(pid, pid)
                if pid not in papers:
                    papers.append(pid)
                if len(papers) == 5:
                    break
            hits = [int(p in expected) for p in papers]
            dcg = sum(h/np.log2(i+2) for i,h in enumerate(hits))
            ideal = sum(1/np.log2(i+2) for i in range(min(5,len(expected))))
            scores[method] = {"papers": papers, "recall_at_5": len(set(papers)&expected)/len(expected),
                              "mrr_at_5": next((1/(i+1) for i,h in enumerate(hits) if h), 0),
                              "ndcg_at_5": float(dcg/ideal)}
        results.append({"id": case["id"], "query": case["query"], "relevant_papers": case["relevant_papers"], **scores})
    write_json(ARTIFACTS / "rag_per_query.json", results)
    return {"status": "completed", "queries": len(cases), "index_chunks": len(rows),
            "corpus_fingerprint": hashlib.sha256(json.dumps([(r['chunk_id'], r['original_text']) for r in rows],ensure_ascii=False).encode()).hexdigest(),
            "qrels_sha256": digest(qrels_path), "embedding_model": paper_kb.EMBEDDING_MODEL,
            "label_scope": "assistant-authored, evidence-linked relevance seed; not independent exhaustive judgements",
            "metrics": {m: {k: float(np.mean([r[m][k] for r in results])) for k in ("recall_at_5", "mrr_at_5", "ndcg_at_5")}
                        for m in ("dense", "bm25", "hybrid")},
            "limitations": ["small curated development set, no clinical performance claim", "unjudged papers counted nonrelevant provisionally",
                            "unique-paper top5 differs from live chunk top5; inspect rag_per_query.json before tuning"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rag", action="store_true")
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    with threadpool_limits(limits=1):
        result = {"date": "2026-09-18", "anomaly": anomaly_comparison(), "fusion": fusion_comparison(),
                  "drift": drift_comparison(), "lmm": lmm_comparison()}
        if args.rag:
            result["rag"] = rag_comparison(args.allow_api)
    result["elapsed_seconds"] = time.monotonic()-started
    write_json(ARTIFACTS / "evaluation.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
