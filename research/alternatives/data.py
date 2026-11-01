"""Traceable labels from recordings. Human capture labels != RF weak labels != falls.

All SQLite access is read-only. Source recordings/checkpoints are never rewritten.
Times without an offset are interpreted as Asia/Seoul, matching the capture host.
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(__file__).parent / "artifacts"
KST = timezone(timedelta(hours=9))
OCCUPIED = {"supine", "side_left", "side_right", "sitting", "edge_sit"}


def seconds(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=KST)).timestamp()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def readonly_db(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def audit_project(out=ARTIFACTS):
    """Read all first-party source/docs/config; record hashes, never secret contents."""
    extensions = {".py", ".js", ".cjs", ".html", ".css", ".md", ".ino", ".cpp", ".ini", ".cfg"}
    inventory = []
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or any(part in {".git", ".claude", "__pycache__", "node_modules", ".pio", ".venv"} for part in rel.parts):
            continue
        if "research" == rel.parts[0] or p.suffix not in extensions:
            continue
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        inventory.append({"path": rel.as_posix(), "sha256": digest(p), "lines": len(text.splitlines()),
                          "label_mentions": text.lower().count("label") + text.count("라벨")})
    databases = {}
    for name in ("majubom.db", "majubom_4x4bak.db"):
        path = ROOT / "server" / name
        if not path.exists():
            continue
        conn = readonly_db(path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {table: conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                      for table in sorted(tables)}
            if "activity_log" in tables:
                counts["activity_log_by_source"] = [list(r) for r in conn.execute(
                    "SELECT CASE WHEN bed_id LIKE 'bed_sim_%' THEN 'simulation' ELSE 'hardware' END,"
                    "event_type,count(*) FROM activity_log GROUP BY 1,2")]
            databases[name] = counts
        finally:
            conn.close()
    protected = [ROOT / "TOF/ml/compare_rf_tabpfn.py", *sorted((ROOT / "TOF/ml").glob("*.joblib")),
                 ROOT / "TOF/ml/lstm_ae.pt", *sorted((ROOT / "TOF/dataset").glob("*.jsonl")),
                 ROOT / "mmWave/exit_seq/seq_dataset.jsonl.gz"]
    result = {"date": "2026-09-18", "timezone": "Asia/Seoul", "files": inventory,
              "databases": databases, "protected_sha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in protected},
              "clinical_fall_labels": 0,
              "excluded_as_truth": ["simulation events", "LLM analysis", "risk alerts", "unlabelled backup rows"],
              "reviewed_flow": ["firmware -> capture -> raw data", "ToF ROI/RF/presence/edge rules",
                                "mmWave UART/tracking/gait/sequences", "SQLite/baselines/quality/FSM",
                                "risk fusion/LLM/RAG", "dashboard/simulation/tests", "all project documents"]}
    write_json(Path(out) / "project_audit.json", result)
    return result


def pair_frames(rows, tolerance=0.25):
    """One-to-one pairing inside ONE recording; no cross-session index zip."""
    sensors = {}
    for sensor in ("tof1", "tof2"):
        sensors[sensor] = sorted([(seconds(r["t"]), i + 1, r) for i, r in enumerate(rows)
                                  if r.get("sensor") == sensor], key=lambda r: (r[0], r[1]))
    a, b = sensors["tof1"], sensors["tof2"]
    i = j = 0
    pairs = []
    while i < len(a) and j < len(b):
        ta, la, ra = a[i]
        tb, lb, rb = b[j]
        if abs(ta - tb) <= tolerance:
            if ra.get("label") == rb.get("label") and ra.get("resolution") == rb.get("resolution"):
                pairs.append((max(ta, tb), la, lb, ra, rb, abs(ta - tb)))
            i += 1
            j += 1
        elif ta < tb:
            i += 1
        else:
            j += 1
    return pairs


def distance_features(distances):
    a = np.asarray([float(v) if v is not None else -1.0 for v in distances])
    valid = a[np.isfinite(a) & (a > 0)]
    if not len(valid):
        return [0.0] * 6
    # Resolution-independent summaries, no padding a 4x4 frame into a fake 8x8 frame.
    return [len(valid) / len(a), float(np.median(valid)) / 1000, float(np.std(valid)) / 1000,
            float(np.quantile(valid, .1)) / 1000, float(np.quantile(valid, .9)) / 1000,
            float(np.mean((a > 0) & (a < 1500)))]


def posture_windows(out=ARTIFACTS, size=20):
    sessions, labels, windows = [], [], []
    for path in sorted((ROOT / "TOF/dataset").glob("*.jsonl")):
        if "filtered" in path.name:
            continue
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        pairs = pair_frames(rows)
        sha = digest(path)
        sessions.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha,
                         "raw_frames": len(rows), "paired_frames": len(pairs),
                         "labels": dict(Counter(r["label"] for r in rows))})
        # Non-overlapping windows. Split on gaps before windowing.
        blocks, block = [], []
        for pair in pairs:
            if block and (pair[0] - block[-1][0] > 1.0 or pair[3]["label"] != block[-1][3]["label"]):
                blocks.append(block)
                block = []
            block.append(pair)
        blocks.append(block)
        for block in blocks:
            for start in range(0, len(block) - size + 1, size):
                w = block[start:start + size]
                label = w[0][3]["label"]
                if label not in OCCUPIED | {"empty"}:
                    continue
                record = {"id": f"{path.stem}:{w[0][1]}", "source": path.relative_to(ROOT).as_posix(),
                          "source_sha256": sha, "session": path.stem, "subject_id": None,
                          "start": w[0][0], "end": w[-1][0],
                          "source_lines": [[p[1], p[2]] for p in w],
                          "pair_max_skew_sec": max(p[5] for p in w), "posture": label,
                          "context": "empty" if label == "empty" else "occupied",
                          "label_type": "capture_instruction", "clinical_outcome": None,
                          "proxy_target": int(label in {"sitting", "edge_sit"}),
                          "proxy_definition": "non-lying posture, NOT a fall or confirmed anomaly",
                          "review_status": "not_independently_reviewed", "resolution": w[0][3]["resolution"]}
                labels.append(record)
                windows.append([distance_features(p[3]["distances_mm"]) + distance_features(p[4]["distances_mm"]) for p in w])
    by_class = defaultdict(list)
    for row in labels:
        if row["session"] not in by_class[row["posture"]]:
            by_class[row["posture"]].append(row["session"])
    for cls, groups in by_class.items():
        if cls in {"sitting", "edge_sit"}:
            for r in labels:
                if r["posture"] == cls:
                    r["split"] = "test"
            continue
        for group in groups:
            rr = [r for r in labels if r["session"] == group]
            for i, r in enumerate(rr):
                if len(groups) > 1 and group == groups[-1]:
                    r["split"] = "test"
                elif len(groups) == 1:
                    r["split"] = "train" if i < len(rr) * .5 else ("calibration" if i < len(rr) * .75 else "test")
                else:
                    r["split"] = "train" if i < len(rr) * .75 else "calibration"
    write_jsonl(Path(out) / "posture_labels.jsonl", labels)
    write_json(Path(out) / "posture_sources.json", sessions)
    return np.asarray(windows, dtype=np.float32), labels


def weak_exit_labels(rows, horizon=3.0, stable_empty=2.0, min_conf=.8, max_gap=3.0):
    """Abstain on uncertain/censored future; every target includes its evidence end.

    Positive: occupied now, followed within horizon by >= stable_empty seconds of
    high-confidence empty. Negative: occupied throughout the complete horizon.
    Neither label is an independently observed bed exit, let alone a fall.
    """
    ts = np.asarray([r["ts"] / 1000 for r in rows])
    if len(ts) > 1 and np.any(np.diff(ts) < 0):
        raise ValueError("Sequence timestamps must be sorted")
    labels = []
    for i, r in enumerate(rows):
        rec = {"id": f"mmw:{i+1}", "source_line": i + 1, "timestamp": float(ts[i]),
               "feature_start": float(ts[i] - 3.0), "feature_end": float(ts[i]),
               "target": None, "evidence_end": None, "reason": "not_occupied",
               "label_type": "weak_tof_rf_future_transition", "clinical_outcome": None,
               "review_status": "pending", "session": "mmw_20260704_single_session", "subject_id": None}
        if r.get("tof_posture") not in OCCUPIED:
            labels.append(rec)
            continue
        end = bisect.bisect_left(ts, ts[i] + horizon)
        if end >= len(rows):
            rec["reason"] = "right_censored"
        elif any(float(q.get("tof_conf") or 0) < min_conf for q in rows[i:end+1]):
            rec["reason"] = "low_confidence"
        elif np.max(np.diff(ts[i:end+1]), initial=0) > max_gap:
            rec["reason"] = "time_gap"
        else:
            future = range(i + 1, end + 1)
            first_empty = next((j for j in future if ts[j] <= ts[i] + horizon and rows[j].get("tof_posture") == "empty"), None)
            if first_empty is not None:
                stop = bisect.bisect_left(ts, ts[first_empty] + stable_empty)
                if stop < len(rows) and all(q.get("tof_posture") == "empty" and float(q.get("tof_conf") or 0) >= min_conf
                                            for q in rows[first_empty:stop+1]) and np.max(np.diff(ts[first_empty:stop+1]), initial=0) <= max_gap:
                    rec.update(target=1, evidence_end=float(ts[stop]), reason="stable_empty_transition")
                else:
                    rec["reason"] = "unconfirmed_empty_transition"
            elif all(rows[j].get("tof_posture") in OCCUPIED for j in future):
                rec.update(target=0, evidence_end=float(ts[end]), reason="observed_occupied_horizon")
            else:
                rec["reason"] = "unknown_future"
        labels.append(rec)
    return labels


def radar_features(row):
    out = []
    for frame in row["seq"]:
        a = np.asarray(frame, dtype=float)
        a = a[np.any(a != 0, axis=1)]
        if len(a):
            out.append([*np.median(a, axis=0), float(len(a)), float(np.std(a[:, 2]))])
        else:
            out.append([0.] * 6)
    a = np.asarray(out)
    return np.concatenate([a.mean(0), a.std(0), a[-1] - a[0]]).tolist()


def fusion_dataset(out=ARTIFACTS):
    source = ROOT / "mmWave/exit_seq/seq_dataset.jsonl.gz"
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    strict = weak_exit_labels(rows)
    # Preserve ALL strict rejections. A second, explicitly unfiltered weak-label
    # experiment tests feasibility; it must never be promoted to clinical truth.
    labels = weak_exit_labels(rows, min_conf=0.0)
    for label, checked in zip(labels, strict):
        label["strict_eligible"] = checked["target"] is not None
        label["strict_reason"] = checked["reason"]
        i = label["source_line"] - 1
        label["current_tof_confidence"] = rows[i].get("tof_conf")
        label["training_scope"] = "exploratory_weak_labels_only"
    # Query only the relevant five minutes from the historical DB, in read-only mode.
    first = datetime.fromtimestamp(rows[0]["ts"] / 1000 - 5, KST).replace(tzinfo=None).isoformat()
    last = datetime.fromtimestamp(rows[-1]["ts"] / 1000 + 5, KST).replace(tzinfo=None).isoformat()
    path = ROOT / "server/majubom_4x4bak.db"
    streams = {"tof1": [], "tof2": []}
    if path.exists():
        conn = readonly_db(path)
        try:
            for raw in conn.execute("SELECT id,timestamp,sensor,resolution," + ",".join(f"d{i}" for i in range(16)) +
                                    " FROM tof_readings WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp,id", (first, last)):
                if raw[3] == "4x4" and raw[2] in streams:
                    streams[raw[2]].append((seconds(raw[1]), raw[0], raw[4:]))
        finally:
            conn.close()
    times = {k: [v[0] for v in values] for k, values in streams.items()}
    aligned, radar, tof = [], [], []
    for row, label in zip(rows, labels):
        label["source"] = source.relative_to(ROOT).as_posix()
        label["source_sha256"] = digest(source) if not labels[0].get("source_sha256") else labels[0]["source_sha256"]
        label["alignment_status"] = "missing_past_tof"
        selected = []
        for sensor in ("tof1", "tof2"):
            j = bisect.bisect_right(times[sensor], label["timestamp"]) - 1
            if j >= 0 and label["timestamp"] - times[sensor][j] <= .5:
                selected.append(streams[sensor][j])
        if len(selected) == 2:
            label["alignment_status"] = "past_only_within_500ms_clock_unverified"
            label["tof_raw_ids"] = [v[1] for v in selected]
            label["tof_age_sec"] = [label["timestamp"] - v[0] for v in selected]
            if label["target"] is not None:
                aligned.append(label)
                radar.append(radar_features(row))
                tof.append(distance_features(selected[0][2]) + distance_features(selected[1][2]))
    write_jsonl(Path(out) / "exit_labels.jsonl", labels)
    write_jsonl(Path(out) / "review_queue.jsonl", [dict(r, reviewed_target=None, reviewer=None, evidence_uri=None)
                                                  for r in labels if r["reason"] in {"stable_empty_transition", "unconfirmed_empty_transition"}])
    return np.asarray(tof), np.asarray(radar), aligned, labels


def purged_split(labels, train_fraction=.6, calibration_fraction=.2):
    """Split by real clock time; entire input AND future target must lie in one block."""
    if not labels:
        return {k: [] for k in ("train", "calibration", "test", "purged")}
    lo = min(r["feature_start"] for r in labels)
    hi = max(r["evidence_end"] for r in labels)
    a, b = lo + (hi-lo) * train_fraction, lo + (hi-lo) * (train_fraction+calibration_fraction)
    result = {k: [] for k in ("train", "calibration", "test", "purged")}
    for i, r in enumerate(labels):
        s, e = r["feature_start"], r["evidence_end"]
        split = "train" if e < a else "calibration" if s >= a and e < b else "test" if s >= b else "purged"
        result[split].append(i)
        r["split"] = split
    return result
