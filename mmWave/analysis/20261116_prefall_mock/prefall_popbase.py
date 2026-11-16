"""Pre-fall model without the 10 s personal baseline, so it can warn right after someone gets out of bed (2026-11-16).

Changes from the deployed v2:
  * reference walking = one fixed profile measured from the real walking recordings (lee, kim) and the mock normal
    walking, instead of the person's own first 10 s. Scoring starts 3 s after the track appears.
  * upright check = body centre >= 80% of the reference walking height (fixed), instead of the person's own.
  * "is walking" gate, three options compared:
      before  walking (median >= 0.25 m/s) 1.5-4 s before the window end   (deployed v2; blocks right-after-standing)
      now     moving now (median >= 0.20 m/s over the last 1.5 s)
      none    no walking gate
  * training adds mock episodes that start standing beside the bed with 0-3 s of walking before the scenario
    (mock_exit_*.pkl): some stagger or freeze at once, the rest just walk off, sit, crouch or stop.
Label = drop within 8 s; threshold for <= 2 false alarms/h on validation mock (both kinds); XGBoost; mean of 3 windows.
Usage: python prefall_popbase.py   → prefall_popbase_result.json
"""
import json
import os
import pickle
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_pi_models as C  # noqa: E402
import normal_gait_stats as N  # noqa: E402
import prefall_cross_person as X  # noqa: E402
import prefall_final as F  # noqa: E402
import prefall_lead as L  # noqa: E402

KEYS = ("speed", "speed_cv", "sway", "freeze_ratio", "stride_length", "stride_cv")
GATES = ("before", "now", "none")
HORIZON = 8.0


def walking_frames(exclude=None):
    out = []
    for person, ses, ranges, rel in N.RECORDINGS:
        if person == exclude:
            continue
        for lab, v in N.walker_tracks(ses, ranges, rel):
            out.append(np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]]))
    return out


def reference(exclude=None):
    """Median walking profile over real walking recordings + mock normal walking (3 s windows that are walking)."""
    rows, zs = [], []
    data = pickle.load(open(os.path.join(HERE, "mock_lead_train.pkl"), "rb"))
    frames = walking_frames(exclude) + [e["frames"] for e in data["episodes"] if e["kind"] == "walk_normal"][:200]
    for f in frames:
        for end in np.arange(3.0, f[-1, 0], 1.0):
            g = L.window_feats(f, end, 3)
            if g and g["speed"] >= 0.2 and g["disp"] >= 0.3 and g["straightness"] >= 0.5:
                rows.append(g)
                zs.append(float(np.median(f[(f[:, 0] > end - 3) & (f[:, 0] <= end), 3])))
    return {**{k: float(np.median([r[k] for r in rows])) for k in KEYS}, "walk_z": float(np.median(zs)), "windows": len(rows)}


def windows(f, stop, ref):
    t = f[:, 0]
    rows, ends, Xs, up, before, now = {}, [], [], [], [], []
    for end in np.arange(3.0, stop, 0.5):
        g = L.window_feats(f, end, 3)
        if not g:
            continue
        key = round(end, 3)
        rows[key] = g
        Xs.append(L.vec(g, ref, rows.get(round(end - 1.5, 3)), rows.get(round(end - 3.0, 3))))
        ends.append(end)
        up.append(bool(np.median(f[(t > end - 0.5) & (t <= end), 3]) >= F.UPRIGHT * ref["walk_z"]))
        past = f[(t > end - 4.0) & (t <= end - 1.5), 4]
        before.append(bool(len(past) and np.median(past) >= 0.25))
        recent = f[(t > end - 1.5) & (t <= end), 4]
        now.append(bool(len(recent) and np.median(recent) >= 0.20))
    if len(ends) < 3:
        return None
    up = np.array(up)
    return {"X": np.array(Xs), "ends": np.array(ends),
            "gates": {"before": up & np.array(before), "now": up & np.array(now), "none": up}}


def mock(pkl, ref, tag):
    path = os.path.join(HERE, f"pop_cache_{tag}_{pkl}")
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
    return np.vstack([w["X"] for f in walking_frames(exclude) if (w := windows(f, f[-1, 0], ref))])


def gated(eps, gate):
    return [{**e, "gate": e["gates"][gate]} for e in eps]


def gen(pkl, exclude, seed, exit_start):
    if os.path.exists(os.path.join(HERE, pkl)):
        return
    cmd = [sys.executable, os.path.join(HERE, "mock_generator.py"), "--people", "40", "--per-kind", "4", "--seed", str(seed),
           "--profile", os.path.join(HERE, "real_scenario_profiles.json"), "--out", pkl, "--mix", "--skip-kind", F.SKIP]
    if exit_start:
        cmd += ["--lead-walk", "0", "3", "--start-stand"]
    for s in exclude:
        cmd += ["--exclude-session", s]
    subprocess.run(cmd, check=True)


def main():
    ss = X.scenario_sessions()
    allr = [s for v in ss.values() for s in v]
    ref = reference()
    print("reference walking:", json.dumps({k: round(v, 3) for k, v in ref.items()}), flush=True)
    tr = mock("mock_lead_train.pkl", ref, "all") + mock("mock_exit_train.pkl", ref, "all")
    va = mock("mock_lead_val.pkl", ref, "all") + mock("mock_exit_val.pkl", ref, "all")
    te_long, te_exit = mock("mock_lead_test.pkl", ref, "all"), mock("mock_exit_test.pkl", ref, "all")
    R_all, W_all = real(allr, ref), walk_X(ref)
    model = L.train(tr, R_all, W_all, ("h", HORIZON))
    result = {"reference": ref, "mock_after_walking": {}, "mock_right_after_standing": {}, "real_left_out": {}}
    for gate in GATES:
        vg = gated(va, gate)
        thr = L.pick(vg, [model.predict_proba(e["X"])[:, 1] for e in vg], 3, 0.5)
        for name, te in (("mock_after_walking", te_long), ("mock_right_after_standing", te_exit)):
            tg = gated(te, gate)
            r = L.score(tg, [model.predict_proba(e["X"])[:, 1] for e in tg], thr, 3, 0.5)
            result[name][gate] = r
            print(f"[{name}] gate={gate:6s}", json.dumps({k: r[k] for k in ("caught_pct", "mean_lead_sec", "false_alarms_per_h", "control_alarm_pct", "threshold")}),
                  {k: (r[k]["caught_pct"], r[k]["mean_lead_sec"]) for k in F.FALLS}, flush=True)
    for i, p in enumerate(sorted(ss)):
        refp = reference(exclude=p)
        gen(f"mock_lead_not_{p}.pkl", ss[p], 9400 + i, False)
        gen(f"mock_exit_not_{p}.pkl", ss[p], 9600 + i, True)
        other = [s for q, v in ss.items() if q != p for s in v]
        mp = L.train(mock(f"mock_lead_not_{p}.pkl", refp, f"not{p}") + mock(f"mock_exit_not_{p}.pkl", refp, f"not{p}"),
                     real(other, refp), walk_X(refp, p), ("h", HORIZON))
        va_p = mock("mock_lead_val.pkl", refp, f"not{p}") + mock("mock_exit_val.pkl", refp, f"not{p}")
        own = real(ss[p], refp)
        result["real_left_out"][p] = {}
        for gate in GATES:
            vg = gated(va_p, gate)
            th = L.pick(vg, [mp.predict_proba(e["X"])[:, 1] for e in vg], 3, 0.5)
            og = gated(own, gate)
            rr = L.score(og, [mp.predict_proba(e["X"])[:, 1] for e in og], th, 3, 0.5)
            result["real_left_out"][p][gate] = rr
            print(f"   [real, {p} left out] gate={gate:6s}", json.dumps({k: rr[k] for k in ("caught_pct", "mean_lead_sec", "control_alarm_pct")}),
                  {k: (rr[k]["caught_pct"], rr[k]["mean_lead_sec"], rr[k]["alarm_before_warning_pct"]) for k in F.FALLS}, flush=True)
        json.dump(result, open(os.path.join(HERE, "prefall_popbase_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(result, open(os.path.join(HERE, "prefall_popbase_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
