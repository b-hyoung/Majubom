"""Does adding the path-shape features (Pi 5099's 9) to the current v3 model help? (2026-11-17, not deployed)

v3 (deployed): 15 features against a fixed reference walking profile, scoring from 3 s, upright gate only,
label = drop within 8 s, trained on walk-first + right-after-getting-up mock, all real trials and real walking.
v3+path: the same plus pi_walk_features.py (speed change, lateral spread, turning, path efficiency, height drop vs a few
seconds earlier, height spread, downward speed, lateral gain; computed at the live-snapshot rate). Missing values
(first seconds of a track) stay NaN, which XGBoost handles.
Same training data, threshold rule (<= 2 false alarms/h on validation mock) and scoring as prefall_popbase.py.
Tested on new mock (after walking / right after getting up) and on each real person left out.
Usage: python prefall_v3_path.py   → prefall_v3_path_result.json
"""
import json
import os
import pickle
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import compare_pi_models as C  # noqa: E402
import pi_walk_features as PF  # noqa: E402
import prefall_cross_person as X  # noqa: E402
import prefall_final as F  # noqa: E402
import prefall_lead as L  # noqa: E402
import prefall_popbase as B  # noqa: E402

HORIZON = 8.0
VARIANTS = {"v3": slice(0, 15), "v3+path": slice(0, 24)}


def windows(f, stop, ref):
    w = B.windows(f, stop, ref)
    if not w:
        return None
    pis = PF.series(f, stop)
    times = np.array([t for t, _ in pis]) if pis else np.zeros(0)
    extra = []
    for e in w["ends"]:
        i = np.searchsorted(times, e, side="right") - 1
        extra.append(list(pis[i][1]) if i >= 0 and e - times[i] <= 0.6 else [np.nan] * 9)
    return {"X": np.hstack([w["X"], np.array(extra, dtype=float)]), "ends": w["ends"], "gate": w["gates"]["none"]}


def mock(pkl, ref, tag):
    path = os.path.join(HERE, f"path_cache_{tag}_{pkl}")
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    data = pickle.load(open(os.path.join(HERE, pkl), "rb"))
    out = []
    for i, e in enumerate(data["episodes"]):
        if e["kind"] == F.SKIP:
            continue
        w = windows(e["frames"], e["drop_start"] if e["fall"] else e["frames"][-1, 0], ref)
        if w:
            out.append({"id": i, "kind": e["kind"], "drop": e["drop_start"], "warn": e.get("warn_start"), **w})
    pickle.dump(out, open(path, "wb"))
    return out


def real(sessions, ref):
    out = []
    for t in C.trials(sessions):
        w = windows(t["frames"], t["drop"] if t["drop"] is not None else t["frames"][-1, 0], ref)
        if w:
            out.append({**{k: t[k] for k in ("session", "trial", "kind", "drop", "warn")}, **w})
    return out


def walk_X(ref, exclude=None):
    return np.vstack([w["X"] for f in B.walking_frames(exclude) if (w := windows(f, f[-1, 0], ref))])


def sub(eps, sel):
    return [{**e, "X": e["X"][:, sel]} for e in eps]


def main():
    ss = X.scenario_sessions()
    allr = [s for v in ss.values() for s in v]
    ref = B.reference()
    tr = mock("mock_lead_train.pkl", ref, "all") + mock("mock_exit_train.pkl", ref, "all")
    va = mock("mock_lead_val.pkl", ref, "all") + mock("mock_exit_val.pkl", ref, "all")
    tests = {"mock_after_walking": mock("mock_lead_test.pkl", ref, "all"),
             "mock_right_after_getting_up": mock("mock_exit_test.pkl", ref, "all")}
    R_all, W_all = real(allr, ref), walk_X(ref)
    per = {}
    for p in sorted(ss):
        refp = B.reference(exclude=p)
        per[p] = (refp, mock(f"mock_lead_not_{p}.pkl", refp, f"not{p}") + mock(f"mock_exit_not_{p}.pkl", refp, f"not{p}"),
                  real([s for q, v in ss.items() if q != p for s in v], refp), walk_X(refp, p), real(ss[p], refp),
                  mock("mock_lead_val.pkl", refp, f"not{p}") + mock("mock_exit_val.pkl", refp, f"not{p}"))
    result = {name: {} for name in list(tests) + ["real_left_out"]}
    for var, sel in VARIANTS.items():
        model = L.train(sub(tr, sel), sub(R_all, sel), W_all[:, sel], ("h", HORIZON))
        vs = sub(va, sel)
        thr = L.pick(vs, [model.predict_proba(e["X"])[:, 1] for e in vs], 3, 0.5)
        for name, te in tests.items():
            ts = sub(te, sel)
            r = L.score(ts, [model.predict_proba(e["X"])[:, 1] for e in ts], thr, 3, 0.5)
            result[name][var] = r
            print(f"[{name}] {var:8s}", json.dumps({k: r[k] for k in ("caught_pct", "mean_lead_sec", "false_alarms_per_h", "control_alarm_pct", "threshold")}),
                  {k: (r[k]["caught_pct"], r[k]["mean_lead_sec"]) for k in F.FALLS}, flush=True)
        result["real_left_out"][var] = {}
        for p, (refp, m_not, r_other, w_not, own, va_p) in per.items():
            mp = L.train(sub(m_not, sel), sub(r_other, sel), w_not[:, sel], ("h", HORIZON))
            vp = sub(va_p, sel)
            th = L.pick(vp, [mp.predict_proba(e["X"])[:, 1] for e in vp], 3, 0.5)
            o = sub(own, sel)
            rr = L.score(o, [mp.predict_proba(e["X"])[:, 1] for e in o], th, 3, 0.5)
            result["real_left_out"][var][p] = rr
            print(f"   [real, {p} left out] {var:8s}", json.dumps({k: rr[k] for k in ("caught_pct", "mean_lead_sec", "control_alarm_pct")}),
                  {k: (rr[k]["caught_pct"], rr[k]["mean_lead_sec"], rr[k]["alarm_before_warning_pct"]) for k in F.FALLS}, flush=True)
        json.dump(result, open(os.path.join(HERE, "prefall_v3_path_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
