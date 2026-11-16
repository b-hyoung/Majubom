"""Step 6 of the pre-fall mock-data work: gait features → "will this person fall within 3 s?" with 3 s vs 6 s windows.

1. Features: every 0.5 s, the last W seconds (W = 3 or 6) of each episode go through the reader's own gait code
   (send_mmw._gait_from_samples + straightness_by_part). Only windows that END BEFORE the drop starts are used, so
   this is prediction, not detection. Each feature is also expressed relative to the person's own walking in the
   first 10 s of the episode (the server judges against a personal baseline in the same way).
2. Label: positive if the drop starts within the next 3 s.
3. Realism check: mock normal walking vs the real lee/kim walking windows (normal_gait_stats.json).
4. Models: logistic regression and XGBoost, 5-fold grouped by virtual person (a person is never in train and test).
   Alarm = probability above the threshold in 2 windows in a row; the threshold is picked on the training folds for
   at most 2 false alarms per hour of non-fall time.
5. Real-data check: the same models on the real lee/kim walking recordings (no falls) → false alarms per hour.
Usage: python prefall_eval.py   → prefall_eval_result.json
"""
import json
import os
import pickle
import sys

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import send_mmw as M  # noqa: E402

HORIZON = 3.0
STEP = 0.5
BASE = ("speed", "speed_cv", "sway", "freeze_ratio", "stride_length", "stride_cv")
FAR_LIMIT_PER_H = 2.0


def window_feats(frames, end, win):
    t = frames[:, 0]
    idx = np.where((t > end - win) & (t <= end))[0]
    if len(idx) < 10:
        return None
    p = frames[idx]
    g = M._gait_from_samples([(q[0], q[1], q[2], q[3], q[4], 0.0) for q in p])
    if g is None:
        return None
    xy = p[:, 1:3]
    g["straightness"] = M.straightness_by_part(p[:, 0], xy)
    g["disp"] = float(np.hypot(*(xy[-1] - xy[0])))
    return g


def baseline(frames, win):
    rows = [window_feats(frames, e, win) for e in np.arange(win, 10.0 + 1e-9, 1.0)]
    rows = [r for r in rows if r]
    return {k: float(np.median([r[k] for r in rows])) for k in BASE} if rows else None


def vector(g, base):
    v = [g[k] for k in BASE] + [g["straightness"], g["disp"]]
    v += [g[k] - base[k] for k in BASE]        # change from the person's own walking
    return v


NAMES = list(BASE) + ["straightness", "disp"] + [f"d_{k}" for k in BASE]


def build(eps, win):
    X, y, grp, ep_id, tend, lead = [], [], [], [], [], []
    for i, e in enumerate(eps):
        f = e["frames"]
        base = baseline(f, win)
        if base is None:
            continue
        stop = e["drop_start"] if e["fall"] else f[-1, 0]
        for end in np.arange(10.0 + win, stop, STEP):
            g = window_feats(f, end, win)
            if g is None:
                continue
            X.append(vector(g, base))
            to_drop = (e["drop_start"] - end) if e["fall"] else np.inf
            y.append(int(e["fall"] and to_drop <= HORIZON and e["kind"] != "trip_fall"))
            grp.append(e["person"]); ep_id.append(i); tend.append(end); lead.append(to_drop)
    return (np.array(X), np.array(y), np.array(grp), np.array(ep_id), np.array(tend), np.array(lead))


def models():
    return {"logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced")),
            "xgboost": lambda: XGBClassifier(n_estimators=300, learning_rate=0.05, max_depth=4, subsample=0.9,
                                             colsample_bytree=0.9, eval_metric="logloss", n_jobs=4)}


def alarms(prob, ep, thr):
    """2 consecutive windows above thr (same episode) → alarm at the second window."""
    hit = np.zeros(len(prob), bool)
    for i in range(1, len(prob)):
        hit[i] = ep[i] == ep[i - 1] and prob[i] >= thr and prob[i - 1] >= thr
    return hit


def far_per_hour(hit, ep, lead, is_fall_ep):
    """False alarms per hour of non-fall time: alarms more than HORIZON before a drop, or in non-fall episodes.
    Counted once per 10 s so one long stretch above threshold is one alarm."""
    fa, last = 0, {}
    neg = lead > HORIZON
    for i in np.where(hit & neg)[0]:
        e = ep[i]
        if e not in last or i - last[e] > 10.0 / STEP:
            fa += 1
        last[e] = i
    hours = neg.sum() * STEP / 3600
    return fa / hours if hours else 0.0


def pick_threshold(prob, ep, lead):
    for thr in np.linspace(0.05, 0.99, 95):
        if far_per_hour(alarms(prob, ep, thr), ep, lead, None) <= FAR_LIMIT_PER_H:
            return float(thr)
    return 0.99


def episode_metrics(hit, ep, lead, kinds):
    out = {}
    for kind in sorted(set(kinds.values())):
        eids = [e for e, k in kinds.items() if k == kind]
        caught, leads = 0, []
        for e in eids:
            m = (ep == e) & hit & (lead <= HORIZON)
            if m.any():
                caught += 1
                leads.append(float(lead[m].max()))
        out[kind] = {"episodes": len(eids), "caught": caught, "recall": caught / len(eids) if eids else None,
                     "mean_lead_sec": float(np.mean(leads)) if leads else None}
    return out


def real_walking(win):
    """Real lee/kim walking recordings as windows (no falls), personal baseline = first 10 s of each labelled range."""
    import normal_gait_stats as N
    X = []
    hours = 0.0
    for person, ses, ranges, rel in N.RECORDINGS:
        for lab, v in N.walker_tracks(ses, ranges, rel):
            f = np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]])
            base = baseline(f, win)
            if base is None:
                continue
            ends = np.arange(10.0 + win, f[-1, 0], STEP)
            rows = [window_feats(f, end, win) for end in ends]
            X += [vector(g, base) for g in rows if g]
            hours += len(ends) * STEP / 3600
    return np.array(X), hours


def realism(eps, win):
    """Median gait features of mock normal walking vs real normal walking (walking windows only)."""
    real = json.load(open(os.path.join(HERE, "normal_gait_stats.json"), encoding="utf-8"))["window"][f"{win}s"]
    mock = []
    for e in eps:
        if e["kind"] != "walk_normal":
            continue
        f = e["frames"]
        for end in np.arange(win, f[-1, 0], 1.0):
            g = window_feats(f, end, win)
            if g and g["speed"] >= M.WALK_SPEED_MIN and g["disp"] >= M.WALK_DISP_MIN and g["straightness"] >= M.STRAIGHT_MIN:
                mock.append(g)
    out = {}
    for k in BASE:
        r = [real[n]["features_walking"][k]["p50"] for n in real if n.endswith(":normal") and real[n]["features_walking"][k]]
        out[k] = {"real_p50_lee_kim": [round(x, 3) for x in r], "mock_p50": round(float(np.median([g[k] for g in mock])), 3),
                  "mock_p10_p90": [round(float(np.percentile([g[k] for g in mock], q)), 3) for q in (10, 90)]}
    return out


def main():
    data = pickle.load(open(os.path.join(HERE, "mock_episodes.pkl"), "rb"))
    eps = data["episodes"]
    kinds = {i: e["kind"] for i, e in enumerate(eps)}
    result = {"horizon_sec": HORIZON, "step_sec": STEP, "far_limit_per_h": FAR_LIMIT_PER_H, "features": NAMES,
              "episodes": len(eps), "people": len({e["person"] for e in eps}), "windows": {}}
    for win in (3, 6):
        X, y, g, ep, tend, lead = build(eps, win)
        Xr, real_hours = real_walking(win)
        res = {"windows": int(len(y)), "positives": int(y.sum()), "realism": realism(eps, win)}
        for name, mk in models().items():
            prob = np.zeros(len(y))
            hit = np.zeros(len(y), bool)
            thrs = []
            for tr, te in GroupKFold(n_splits=5).split(X, y, g):
                m = mk().fit(X[tr], y[tr])
                p_tr = m.predict_proba(X[tr])[:, 1]
                thr = pick_threshold(p_tr, ep[tr], lead[tr])
                thrs.append(thr)
                prob[te] = m.predict_proba(X[te])[:, 1]
                hit[te] = alarms(prob[te], ep[te], thr)
            full = mk().fit(X, y)
            thr_full = pick_threshold(full.predict_proba(X)[:, 1], ep, lead)
            pr = full.predict_proba(Xr)[:, 1] if len(Xr) else np.zeros(0)
            real_alarm = sum(1 for i in range(1, len(pr)) if pr[i] >= thr_full and pr[i - 1] >= thr_full)
            res[name] = {
                "window_auc": float(roc_auc_score(y, prob)),
                "thresholds": [round(t, 2) for t in thrs],
                "false_alarms_per_h_mock": far_per_hour(hit, ep, lead, None),
                "by_kind": episode_metrics(hit, ep, lead, kinds),
                "real_walking_hours": round(real_hours, 3), "real_walking_alarm_windows": int(real_alarm),
                "real_walking_windows": int(len(pr))}
            if name == "xgboost":
                imp = full.feature_importances_
                res[name]["top_features"] = [(NAMES[i], round(float(imp[i]), 3)) for i in np.argsort(imp)[::-1][:6]]
        result["windows"][f"{win}s"] = res
        fall_kinds = ("unsteady_fall", "freeze_collapse", "dizzy_collapse")
        for name in ("logistic", "xgboost"):
            r = res[name]
            rec = np.mean([r["by_kind"][k]["recall"] for k in fall_kinds])
            print(f"[{win}s {name:8s}] AUC {r['window_auc']:.3f} | recall(3 scenarios) {rec:.1%} | trip {r['by_kind']['trip_fall']['recall']:.1%} "
                  f"| mock FA/h {r['false_alarms_per_h_mock']:.2f} | real walking alarms {r['real_walking_alarm_windows']}/{r['real_walking_windows']} windows "
                  f"({r['real_walking_hours']} h) | leads " + ", ".join(f"{k[:6]} {r['by_kind'][k]['mean_lead_sec']}" for k in fall_kinds))
        print(f"[{win}s] realism:", json.dumps(res["realism"], ensure_ascii=False))
    json.dump(result, open(os.path.join(HERE, "prefall_eval_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
