"""Score the saved pre-fall model on NEW mock data and compare prediction algorithms (2026-11-16).

Data
  * train: the final model's training data (mock_final_all.pkl + lee/kim real acted trials + real walking; dizzy out)
  * validation: mock_val_new.pkl (20 new virtual people, seed 9101) — only used to choose each algorithm's threshold
  * test: mock_test_new.pkl (40 new virtual people, seed 9202) — never used for training or thresholds
  New mock data comes from the same generator and the same lee/kim trials, so this measures how well each algorithm
  learns that data, not how it will do on new real people (see the leave-one-person-out results for that).
Alarm, as in the live predictor: mean of the last 3 windows >= threshold, body upright (>= 80% of walking height) and
walking 1.5-4 s before. Threshold per algorithm: lowest with at most 2 false alarms per hour on validation non-fall time.
Usage: python prefall_algo_compare.py   → prefall_algo_compare_result.json
"""
import json
import os
import pickle
import sys
import time

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import prefall_cross_person as X  # noqa: E402
import prefall_eval as P  # noqa: E402
import prefall_final as F  # noqa: E402
import prefall_improve as I  # noqa: E402
from prefall_predictor import load_model  # noqa: E402

FAR = 2.0
KINDS = ("unsteady_fall", "freeze_collapse", "trip_fall", "walk_normal", "walk_slow", "walk_shuffle", "stop_stand",
         "sit_chair", "crouch")


def episode_windows(pkl):
    data = pickle.load(open(os.path.join(HERE, pkl), "rb"))
    out = []
    for i, e in enumerate(data["episodes"]):
        if e["kind"] == F.SKIP:
            continue
        f = e["frames"]
        base = P.baseline(f, F.WIN)
        if base is None:
            continue
        rows = I.series(f, base, F.WIN, e["drop_start"] if e["fall"] else f[-1, 0])
        if len(rows) < 3:
            continue
        walk_z = float(np.median(f[(f[:, 0] >= 1) & (f[:, 0] <= 10), 3]))
        ends = np.array([r[0] for r in rows])
        t = f[:, 0]
        upright = np.array([np.median(f[(t > en - 0.5) & (t <= en), 3]) >= F.UPRIGHT * walk_z for en in ends])
        moving = np.array([(lambda s: len(s) > 0 and np.median(s) >= 0.25)(f[(t > en - 4.0) & (t <= en - 1.5), 4]) for en in ends])
        lead = (e["drop_start"] - ends) if e["fall"] else np.full(len(ends), np.inf)
        out.append({"ep": i, "kind": e["kind"], "X": np.array([I.vec(rows, j, base, F.FSET) for j in range(len(rows))]),
                    "y": ((lead <= P.HORIZON) & np.isin(e["kind"], F.FALLS)).astype(int), "lead": lead,
                    "gate": upright & moving})
    return out


def cached(pkl):
    path = os.path.join(HERE, f"algo_cache_{pkl}")
    if not os.path.exists(path):
        pickle.dump(episode_windows(pkl), open(path, "wb"))
    return pickle.load(open(path, "rb"))


def alarms(eps, probs, thr):
    """Per episode: alarm flags (mean of 3 >= thr, gated)."""
    out = []
    for e, p in zip(eps, probs):
        m3 = np.array([p[max(0, i - 2):i + 1].mean() if i >= 2 else 0.0 for i in range(len(p))])
        out.append((m3 >= thr) & e["gate"])
    return out


def fa_per_hour(eps, hits):
    fa, neg = 0, 0
    for e, h in zip(eps, hits):
        mask = e["lead"] > P.HORIZON
        neg += mask.sum()
        idx = np.where(h & mask)[0]
        last = -999
        for i in idx:
            if i - last > 10.0 / P.STEP:
                fa += 1
            last = i
    return fa / (neg * P.STEP / 3600)


def pick_thr(eps, probs):
    for thr in np.linspace(0.05, 0.99, 95):
        if fa_per_hour(eps, alarms(eps, probs, thr)) <= FAR:
            return float(thr)
    return 0.99


def metrics(eps, probs, thr):
    hits = alarms(eps, probs, thr)
    y = np.concatenate([e["y"] for e in eps]); p = np.concatenate(probs)
    res = {"auc": float(roc_auc_score(y, p)), "threshold": round(thr, 3), "false_alarms_per_h": round(fa_per_hour(eps, hits), 2),
           "by_kind": {}}
    for k in KINDS:
        ks = [(e, h) for e, h in zip(eps, hits) if e["kind"] == k]
        if not ks:
            continue
        if k in F.FALLS or k == "trip_fall":
            caught = [e["lead"][h & (e["lead"] <= P.HORIZON)].max() for e, h in ks if (h & (e["lead"] <= P.HORIZON)).any()]
            early = sum((h & (e["lead"] > P.HORIZON)).any() for e, h in ks)
            res["by_kind"][k] = {"n": len(ks), "caught_pct": round(100 * len(caught) / len(ks), 1),
                                 "mean_lead_sec": round(float(np.mean(caught)), 2) if caught else None,
                                 "early_alarm_pct": round(100 * early / len(ks), 1)}
        else:
            res["by_kind"][k] = {"n": len(ks), "alarm_pct": round(100 * sum(h.any() for _, h in ks) / len(ks), 1)}
    falls = [res["by_kind"][k] for k in F.FALLS]
    res["prediction_rate_pct"] = round(float(np.mean([f["caught_pct"] for f in falls])), 1)
    res["control_alarm_pct"] = round(float(np.mean([res["by_kind"][k]["alarm_pct"] for k in KINDS if "alarm_pct" in res["by_kind"][k]])), 1)
    return res


def algorithms():
    # Windows application control on this PC blocks scikit-learn's tree modules (sklearn.tree / sklearn.ensemble), so
    # random forest runs as XGBoost's random-forest mode and extra trees as LightGBM's random-forest mode with random
    # splits; scikit-learn's HistGradientBoosting is left out (same method as LightGBM).
    import lightgbm
    from xgboost import XGBRFClassifier
    return {
        "Logistic regression": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced")),
        "Random forest": lambda: XGBRFClassifier(n_estimators=300, max_depth=10, subsample=0.8, colsample_bynode=0.6,
                                                 min_child_weight=5, n_jobs=8, random_state=0),
        "Extra trees": lambda: lightgbm.LGBMClassifier(boosting_type="rf", extra_trees=True, n_estimators=300, num_leaves=127,
                                                       bagging_fraction=0.8, bagging_freq=1, feature_fraction=0.6,
                                                       min_child_samples=5, verbose=-1, random_state=0),
        "XGBoost": lambda: I.make("xgboost"),
        "LightGBM": lambda: __import__("lightgbm").LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31,
                                                                  subsample=0.9, colsample_bytree=0.9, verbose=-1, random_state=0),
        "CatBoost": lambda: __import__("catboost").CatBoostClassifier(iterations=400, learning_rate=0.05, depth=6, verbose=0,
                                                                      random_seed=0, thread_count=8),
        "MLP (neural net)": lambda: make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(64, 32), early_stopping=True,
                                                                                  max_iter=300, random_state=0)),
    }


def fit_weighted(model, Xs, ys, ws):
    try:
        if hasattr(model, "steps"):
            return model.fit(Xs, ys, **{f"{model.steps[-1][0]}__sample_weight": ws})
        return model.fit(Xs, ys, sample_weight=ws)
    except TypeError:   # MLP: no sample weights → repeat real rows instead
        rep = np.maximum(1, np.round(ws).astype(int))
        return model.fit(np.repeat(Xs, rep, axis=0), np.repeat(ys, rep))


def main():
    train = cached("mock_final_all.pkl")
    val, test = cached("mock_val_new.pkl"), cached("mock_test_new.pkl")
    ss = X.scenario_sessions()
    real = F.real_trials([s for v in ss.values() for s in v])
    W = F.walking()
    Xm = np.vstack([e["X"] for e in train]); ym = np.concatenate([e["y"] for e in train])
    Xr = np.vstack([t["w"][0] for t in real] + [W]); yr = np.concatenate([t["w"][1] for t in real] + [np.zeros(len(W), int)])
    wr = F.REAL_SHARE / (1 - F.REAL_SHARE) * len(ym) / len(yr)
    Xt, yt, wt = np.vstack([Xm, Xr]), np.concatenate([ym, yr]), np.concatenate([np.ones(len(ym)), np.full(len(yr), wr)])
    print(f"train windows: mock {len(ym)} + real {len(yr)} (weight {wr:.2f}); validation episodes {len(val)}, test episodes {len(test)}", flush=True)
    result = {"train_windows": {"mock": int(len(ym)), "real": int(len(yr)), "real_weight": wr},
              "validation_episodes": len(val), "test_episodes": len(test), "results": {}}
    saved = load_model(os.path.join(HERE, "..", "..", "models", "prefall_xgb.json"))
    meta = json.load(open(os.path.join(HERE, "..", "..", "models", "prefall_xgb_meta.json"), encoding="utf-8"))
    probs = [saved.predict_proba(e["X"])[:, 1] for e in test]
    r = metrics(test, probs, meta["threshold"]); r["note"] = "saved model, its own threshold"
    result["results"]["Saved model (XGBoost, deployed)"] = r
    print("Saved model:", json.dumps({k: r[k] for k in ("prediction_rate_pct", "control_alarm_pct", "false_alarms_per_h", "auc")}), flush=True)
    for name, mk in algorithms().items():
        t0 = time.time()
        m = fit_weighted(mk(), Xt, yt, wt)
        thr = pick_thr(val, [m.predict_proba(e["X"])[:, 1] for e in val])
        r = metrics(test, [m.predict_proba(e["X"])[:, 1] for e in test], thr)
        r["train_sec"] = round(time.time() - t0, 1)
        result["results"][name] = r
        print(f"{name}:", json.dumps({k: r[k] for k in ("prediction_rate_pct", "control_alarm_pct", "false_alarms_per_h", "auc", "threshold")}),
              {k: v.get("caught_pct", v.get("alarm_pct")) for k, v in r["by_kind"].items()}, flush=True)
        json.dump(result, open(os.path.join(HERE, "prefall_algo_compare_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
