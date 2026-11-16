"""Combine the Pi walking forecast (5099) and the new pre-fall model (2026-11-16).

What each brought:
  * Pi 5099: 9 path-shape features (speed, speed change, lateral spread, turning, path efficiency, height drop,
    height spread, downward speed, lateral gain) at the live-snapshot rate, trained for 5-10 s ahead → early alerts
    (~9 s) and high catch rate, but trained with one normal-walking control → alerts in every stop / sit / crouch.
  * New model: 15 features relative to the person's own first 10 s of walking + 1.5/3 s trends, trained on mock + real
    data that include stop / sit / crouch / walking controls, with upright and "was walking" gates → few false alarms.
Variants (all XGBoost, same gates, mean of 3 windows, threshold for <= 2 false alarms/h on validation mock):
  ours_h8      15 features, label = drop within 8 s (deployed v2)
  pi9_h8       Pi's 9 features only, trained on our data
  combined_h8  15 + 9 features, 8 s
  combined_h10 15 + 9 features, 10 s
Tested on new mock data and on each real person left out of training (same scoring as prefall_lead.py).
Usage: python prefall_combined.py   → prefall_combined_result.json
"""
import json
import os
import pickle
import sys

import numpy as np
from sklearn.metrics import roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import compare_pi_models as C  # noqa: E402
import normal_gait_stats as N  # noqa: E402
import pi_walk_features as PF  # noqa: E402
import prefall_cross_person as X  # noqa: E402
import prefall_final as F  # noqa: E402
import prefall_lead as L  # noqa: E402

VARIANTS = {"ours_h8": ("ours", 8.0), "pi9_h8": ("pi", 8.0), "combined_h8": ("both", 8.0), "combined_h10": ("both", 10.0)}
SEL = {"ours": slice(0, 15), "pi": slice(15, 24), "both": slice(0, 24)}


def windows(f, stop):
    w = L.windows(f, 3, 0.5, stop)
    if not w:
        return None
    Xs, ends, gate = w
    pis = PF.series(f, stop)
    times = np.array([t for t, _ in pis]) if pis else np.zeros(0)
    extra = []
    for e in ends:
        i = np.searchsorted(times, e, side="right") - 1
        extra.append(list(pis[i][1]) if i >= 0 and e - times[i] <= 0.6 else [np.nan] * 9)
    return np.hstack([Xs, np.array(extra, dtype=float)]), ends, gate


def mock(pkl):
    path = os.path.join(HERE, f"comb_cache_{pkl}")
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    data = pickle.load(open(os.path.join(HERE, pkl), "rb"))
    out = []
    for i, e in enumerate(data["episodes"]):
        if e["kind"] == F.SKIP:
            continue
        w = windows(e["frames"], e["drop_start"] if e["fall"] else e["frames"][-1, 0])
        if w:
            out.append({"id": i, "kind": e["kind"], "drop": e["drop_start"], "warn": e.get("warn_start"), "X": w[0], "ends": w[1], "gate": w[2]})
    pickle.dump(out, open(path, "wb"))
    return out


def real(sessions):
    out = []
    for t in C.trials(sessions):
        w = windows(t["frames"], t["drop"] if t["drop"] is not None else t["frames"][-1, 0])
        if w:
            out.append({**{k: t[k] for k in ("session", "trial", "kind", "drop", "warn")}, "X": w[0], "ends": w[1], "gate": w[2]})
    return out


def walking(exclude=None):
    Xs = []
    for person, ses, ranges, rel in N.RECORDINGS:
        if person == exclude:
            continue
        for lab, v in N.walker_tracks(ses, ranges, rel):
            f = np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]])
            w = windows(f, f[-1, 0])
            if w:
                Xs.append(w[0])
    return np.vstack(Xs)


def sub(eps, sel):
    return [{**e, "X": e["X"][:, sel]} for e in eps]


def main():
    ss = X.scenario_sessions()
    allr = [s for v in ss.values() for s in v]
    tr, va, te = mock("mock_lead_train.pkl"), mock("mock_lead_val.pkl"), mock("mock_lead_test.pkl")
    R_all, W_all = real(allr), walking()
    per = {p: (mock(f"mock_lead_not_{p}.pkl"), real([s for q, v in ss.items() if q != p for s in v]), walking(p), real(ss[p])) for p in ss}
    result = {"mock_test": {}, "real_left_out": {}}
    for name, (fs, h) in VARIANTS.items():
        sel = SEL[fs]
        model = L.train(sub(tr, sel), sub(R_all, sel), W_all[:, sel], ("h", h))
        vs = sub(va, sel)
        thr = L.pick(vs, [model.predict_proba(e["X"])[:, 1] for e in vs], 3, 0.5)
        ts = sub(te, sel)
        probs = [model.predict_proba(e["X"])[:, 1] for e in ts]
        r = L.score(ts, probs, thr, 3, 0.5)
        r["auc"] = round(float(roc_auc_score(np.concatenate([L.labels(e, ("h", h)) for e in ts]), np.concatenate(probs))), 3)
        result["mock_test"][name] = r
        print(f"[mock] {name:13s}", json.dumps({k: r[k] for k in ("caught_pct", "mean_lead_sec", "false_alarms_per_h", "control_alarm_pct", "auc")}),
              {k: (r[k]["caught_pct"], r[k]["mean_lead_sec"]) for k in F.FALLS}, flush=True)
        rows = {}
        for p, (m_not, r_other, w_not, own) in per.items():
            mp = L.train(sub(m_not, sel), sub(r_other, sel), w_not[:, sel], ("h", h))
            th = L.pick(vs, [mp.predict_proba(e["X"])[:, 1] for e in vs], 3, 0.5)
            o = sub(own, sel)
            rr = L.score(o, [mp.predict_proba(e["X"])[:, 1] for e in o], th, 3, 0.5)
            rows[p] = rr
            print(f"   [real, {p} left out] {name:13s}", json.dumps({k: rr[k] for k in ("caught_pct", "mean_lead_sec", "control_alarm_pct")}),
                  {k: (rr[k]["caught_pct"], rr[k]["mean_lead_sec"], rr[k]["alarm_before_warning_pct"]) for k in F.FALLS}, flush=True)
        result["real_left_out"][name] = rows
        json.dump(result, open(os.path.join(HERE, "prefall_combined_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
