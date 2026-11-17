"""Catch rate vs false alarms over many thresholds: v3 vs v3+path (2026-11-17, not deployed).

The earlier comparison fixed each model's threshold at <= 2 false alarms/h on validation mock, which let v3 keep more
false alarms than v3+path. Here both models are scored at thresholds 0.20-0.80 so they can be compared at the same
false-alarm level. Same models and data as prefall_v3_path.py (features cached there).
  real  = lee and kim, each left out of training (pooled: 22 fall trials, 24 control trials)
  mock  = new mock test, walk-first + right-after-getting-up together
Usage: python prefall_threshold_sweep.py   → prefall_threshold_sweep_result.json
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prefall_cross_person as X  # noqa: E402
import prefall_final as F  # noqa: E402
import prefall_lead as L  # noqa: E402
import prefall_popbase as B  # noqa: E402
import prefall_v3_path as V  # noqa: E402

THRESHOLDS = [round(x, 3) for x in np.arange(0.20, 0.801, 0.025)]


def counts(eps, probs, thr):
    """falls caught / falls, control trials with alarm / controls, lead list, false alarms per hour."""
    caught = falls = ctrl_alarm = ctrl = 0
    leads = []
    for e, p in zip(eps, probs):
        h = L.alarm(e, p, thr, 3)
        if e["kind"] in F.FALLS:
            falls += 1
            ok = h & (e["ends"] >= e["warn"]) & (e["ends"] < e["drop"])
            if ok.any():
                caught += 1
                leads.append(float(e["drop"] - e["ends"][ok][0]))
        elif e["kind"] != "trip_fall":
            ctrl += 1
            ctrl_alarm += bool(h.any())
    return caught, falls, ctrl_alarm, ctrl, leads, L.far(eps, probs, thr, 3, 0.5)


def main():
    ss = X.scenario_sessions()
    allr = [s for v in ss.values() for s in v]
    ref = B.reference()
    tr = V.mock("mock_lead_train.pkl", ref, "all") + V.mock("mock_exit_train.pkl", ref, "all")
    te = V.mock("mock_lead_test.pkl", ref, "all") + V.mock("mock_exit_test.pkl", ref, "all")
    R_all, W_all = V.real(allr, ref), V.walk_X(ref)
    per = {}
    for p in sorted(ss):
        refp = B.reference(exclude=p)
        per[p] = (V.mock(f"mock_lead_not_{p}.pkl", refp, f"not{p}") + V.mock(f"mock_exit_not_{p}.pkl", refp, f"not{p}"),
                  V.real([s for q, v in ss.items() if q != p for s in v], refp), V.walk_X(refp, p), V.real(ss[p], refp))
    result = {"thresholds": THRESHOLDS, "models": {}}
    for var, sel in V.VARIANTS.items():
        model = L.train(V.sub(tr, sel), V.sub(R_all, sel), W_all[:, sel], ("h", 8.0))
        ts = V.sub(te, sel)
        mock_p = [model.predict_proba(e["X"])[:, 1] for e in ts]
        real_eps, real_p = [], []
        for p, (m_not, r_other, w_not, own) in per.items():
            mp = L.train(V.sub(m_not, sel), V.sub(r_other, sel), w_not[:, sel], ("h", 8.0))
            o = V.sub(own, sel)
            real_eps += o
            real_p += [mp.predict_proba(e["X"])[:, 1] for e in o]
        rows = []
        for thr in THRESHOLDS:
            c, f, ca, cn, leads, _ = counts(real_eps, real_p, thr)
            mc, mf, mca, mcn, mleads, mfar = counts(ts, mock_p, thr)
            rows.append({"threshold": thr, "real_caught": c, "real_falls": f, "real_control_alarm": ca, "real_controls": cn,
                         "real_mean_lead": round(float(np.mean(leads)), 2) if leads else None,
                         "mock_caught_pct": round(100 * mc / mf, 1), "mock_false_alarms_per_h": round(mfar, 2),
                         "mock_control_alarm_pct": round(100 * mca / mcn, 1),
                         "mock_mean_lead": round(float(np.mean(mleads)), 2) if mleads else None})
        result["models"][var] = rows
        print(f"\n== {var}")
        for r in rows:
            print(f"thr {r['threshold']:.3f} | real {r['real_caught']:2d}/{r['real_falls']} caught, controls {r['real_control_alarm']:2d}/{r['real_controls']}, lead {r['real_mean_lead']} "
                  f"| mock {r['mock_caught_pct']:5.1f}% caught, FA/h {r['mock_false_alarms_per_h']:5.2f}, lead {r['mock_mean_lead']}", flush=True)
    json.dump(result, open(os.path.join(HERE, "prefall_threshold_sweep_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
