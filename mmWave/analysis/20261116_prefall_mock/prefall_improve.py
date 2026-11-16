"""Model improvement for the pre-fall prediction (after the first run of prefall_eval.py).

What changes compared with prefall_eval.py:
  * features: (A) the first run's set, (B) change relative to the person's own walking only (ratios), and
    (C) B plus trends: the same values 1.5 s and 3 s earlier, so a slowing-down or growing unsteadiness is visible
  * training data: mock only, or mock + REAL lee/kim walking as extra non-fall examples. Real walking is scored
    leave-one-recording-out, so a recording is never used to train the model that scores it
  * threshold: chosen inside the training folds on out-of-fold predictions (the first run picked it on the
    training predictions themselves, which XGBoost overfits), for at most 2 false alarms per hour of mock non-fall time
  * alarm rule: 2 windows in a row above the threshold, or the mean of the last 3 windows above it
Usage: python prefall_improve.py   → prefall_improve_result.json (features cached in feature_cache.pkl)
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
sys.path.insert(0, HERE)
import normal_gait_stats as N  # noqa: E402
import prefall_eval as P  # noqa: E402

FALL3 = ("unsteady_fall", "freeze_collapse", "dizzy_collapse")
KEYS = ("speed", "speed_cv", "sway", "freeze_ratio", "stride_length", "stride_cv", "straightness", "disp")


def series(f, base, win, stop):
    """Windows ending every STEP s from 10+win to stop: list of (end, feature dict)."""
    out = []
    for end in np.arange(10.0 + win, stop, P.STEP):
        g = P.window_feats(f, end, win)
        if g:
            out.append((float(end), {k: float(g[k]) for k in KEYS}))
    return out


def cache(win):
    path = os.path.join(HERE, f"feature_cache_{win}s.pkl")
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    data = pickle.load(open(os.path.join(HERE, "mock_episodes.pkl"), "rb"))
    mock = []
    for i, e in enumerate(data["episodes"]):
        f = e["frames"]
        base = P.baseline(f, win)
        if base is None:
            continue
        stop = e["drop_start"] if e["fall"] else f[-1, 0]
        mock.append({"ep": i, "person": e["person"], "kind": e["kind"], "drop": e["drop_start"], "base": base,
                     "rows": series(f, base, win, stop)})
    real = []
    for r_i, (person, ses, ranges, rel) in enumerate(N.RECORDINGS):
        for lab, v in N.walker_tracks(ses, ranges, rel):
            f = np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]])
            base = P.baseline(f, win)
            if base is None:
                continue
            real.append({"rec": f"{ses}:{lab}", "person": person, "base": base, "rows": series(f, base, win, f[-1, 0])})
    out = {"mock": mock, "real": real}
    pickle.dump(out, open(path, "wb"))
    return out


def ratio(a, b, floor):
    return a / max(b, floor)


def vec(rows, i, base, fset):
    end, g = rows[i]
    if fset == "A":
        return [g[k] for k in P.BASE] + [g["straightness"], g["disp"]] + [g[k] - base[k] for k in P.BASE]
    rel = [ratio(g["speed"], base["speed"], 0.1), ratio(g["speed_cv"], base["speed_cv"], 0.05),
           ratio(g["sway"], base["sway"], 0.01), g["freeze_ratio"] - base["freeze_ratio"],
           ratio(g["stride_cv"], base["stride_cv"], 0.03), g["straightness"], g["speed"]]
    if fset == "B":
        return rel
    trend = []
    for lag in (1.5, 3.0):
        j = i - int(round(lag / P.STEP))
        if j >= 0 and abs(rows[j][0] - (end - lag)) < 1e-6:
            pg = rows[j][1]
            trend += [g["speed"] - pg["speed"], g["speed_cv"] - pg["speed_cv"], g["sway"] - pg["sway"],
                      g["freeze_ratio"] - pg["freeze_ratio"]]
        else:
            trend += [0.0, 0.0, 0.0, 0.0]
    return rel + trend


def matrices(c, fset):
    X, y, grp, ep, lead, kind = [], [], [], [], [], []
    for e in c["mock"]:
        for i, (end, _) in enumerate(e["rows"]):
            X.append(vec(e["rows"], i, e["base"], fset))
            to_drop = (e["drop"] - end) if e["drop"] is not None else np.inf
            y.append(int(e["kind"] in FALL3 and to_drop <= P.HORIZON))
            grp.append(e["person"]); ep.append(e["ep"]); lead.append(to_drop); kind.append(e["kind"])
    R, rrec = [], []
    for k, e in enumerate(c["real"]):
        for i in range(len(e["rows"])):
            R.append(vec(e["rows"], i, e["base"], fset)); rrec.append(k)
    return (np.array(X), np.array(y), np.array(grp), np.array(ep), np.array(lead), np.array(kind),
            np.array(R), np.array(rrec))


def make(name):
    if name == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced"))
    return XGBClassifier(n_estimators=250, learning_rate=0.05, max_depth=4, subsample=0.9, colsample_bytree=0.9,
                         eval_metric="logloss", n_jobs=8)


def alarm(prob, ep, thr, rule):
    hit = np.zeros(len(prob), bool)
    for i in range(1, len(prob)):
        if ep[i] != ep[i - 1]:
            continue
        if rule == "2in_a_row":
            hit[i] = prob[i] >= thr and prob[i - 1] >= thr
        elif i >= 2 and ep[i - 2] == ep[i]:
            hit[i] = prob[i - 2:i + 1].mean() >= thr
    return hit


def events_per_hour(hit, ep, neg_mask):
    fa, last = 0, {}
    for i in np.where(hit & neg_mask)[0]:
        if ep[i] not in last or i - last[ep[i]] > 10.0 / P.STEP:
            fa += 1
        last[ep[i]] = i
    hours = neg_mask.sum() * P.STEP / 3600
    return fa / hours if hours else 0.0


def pick_thr(prob, ep, lead, rule):
    neg = lead > P.HORIZON
    for thr in np.linspace(0.05, 0.99, 95):
        if events_per_hour(alarm(prob, ep, thr, rule), ep, neg) <= P.FAR_LIMIT_PER_H:
            return float(thr)
    return 0.99


def run(c, fset, model, use_real, rule):
    X, y, g, ep, lead, kind, R, rrec = matrices(c, fset)
    prob = np.zeros(len(y)); hit = np.zeros(len(y), bool)
    real_prob = np.zeros(len(R)); real_cnt = np.zeros(len(R))
    recs = np.unique(rrec)
    for fold, (tr, te) in enumerate(GroupKFold(5).split(X, y, g)):
        # threshold from out-of-fold predictions inside the training part
        inner = np.zeros(len(tr))
        for itr, ite in GroupKFold(3).split(X[tr], y[tr], g[tr]):
            Xi, yi = X[tr][itr], y[tr][itr]
            if use_real:
                Xi, yi = np.vstack([Xi, R]), np.concatenate([yi, np.zeros(len(R), int)])
            inner[ite] = make(model).fit(Xi, yi).predict_proba(X[tr][ite])[:, 1]
        thr = pick_thr(inner, ep[tr], lead[tr], rule)
        # real recordings held out one at a time: this fold's model is trained without the recording it scores
        hold = recs[fold::5] if use_real else np.array([], int)
        keep = ~np.isin(rrec, hold)
        Xt, yt = X[tr], y[tr]
        if use_real:
            Xt, yt = np.vstack([Xt, R[keep]]), np.concatenate([yt, np.zeros(keep.sum(), int)])
        m = make(model).fit(Xt, yt)
        prob[te] = m.predict_proba(X[te])[:, 1]
        hit[te] = alarm(prob[te], ep[te], thr, rule)
        score = ~keep if use_real else np.ones(len(R), bool)
        p = m.predict_proba(R[score])[:, 1]
        real_prob[score] += np.where(alarm(p, rrec[score], thr, rule), 1, 0)
        real_cnt[score] += 1
    neg = lead > P.HORIZON
    by = {}
    for k in FALL3 + ("trip_fall",):
        eids = np.unique(ep[kind == k])
        caught = [e for e in eids if (hit & (ep == e) & (lead <= P.HORIZON)).any()]
        leads = [lead[(ep == e) & hit & (lead <= P.HORIZON)].max() for e in caught]
        by[k] = {"recall": len(caught) / len(eids), "lead": float(np.mean(leads)) if leads else None}
    real_hit = real_prob / np.maximum(real_cnt, 1) >= 0.5
    real_hours = len(R) * P.STEP / 3600
    return {"auc": float(roc_auc_score(y, prob)), "recall3": float(np.mean([by[k]["recall"] for k in FALL3])),
            "by_kind": by, "mock_fa_per_h": events_per_hour(hit, ep, neg),
            "real_fa_per_h": events_per_hour(real_hit, rrec, np.ones(len(R), bool)), "real_hours": real_hours}


def main():
    result = {}
    for win in (3, 6):
        c = cache(win)
        for fset in ("A", "B", "C"):
            for model in ("logistic", "xgboost"):
                for use_real in (False, True):
                    for rule in ("2in_a_row", "mean3"):
                        if fset != "C" and rule == "mean3":
                            continue
                        key = f"{win}s|{fset}|{model}|{'mock+real' if use_real else 'mock'}|{rule}"
                        r = run(c, fset, model, use_real, rule)
                        result[key] = r
                        print(f"{key:42s} AUC {r['auc']:.3f} recall {r['recall3']:.1%} "
                              f"(unst {r['by_kind']['unsteady_fall']['recall']:.0%} frz {r['by_kind']['freeze_collapse']['recall']:.0%} "
                              f"dzy {r['by_kind']['dizzy_collapse']['recall']:.0%} trip {r['by_kind']['trip_fall']['recall']:.0%}) "
                              f"mockFA/h {r['mock_fa_per_h']:.2f} realFA/h {r['real_fa_per_h']:.1f}", flush=True)
                        json.dump(result, open(os.path.join(HERE, "prefall_improve_result.json"), "w", encoding="utf-8"),
                                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
