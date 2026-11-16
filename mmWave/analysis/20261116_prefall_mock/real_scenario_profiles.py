"""Measure each pre-fall scenario from real acted recordings (tools/Record-FallScenario.ps1) so the mock generator can
draw scenario changes from what our radar actually saw, instead of literature ratios alone.

For each fall trial (unsteady_fall / freeze_collapse / dizzy_collapse):
  * baseline = the walker's first 10 s of normal walking in that trial
  * warning phase = from the scenario cue (비틀 / 종종 / 서기) to the drop start
  * drop start = last time the body centre was >= 80% of walking height before it fell below 25% (+0.15 m)
Measured: warning duration, speed ratio, speed-oscillation ratio, lateral sway ratio (std around the 1 s smoothed
path), share of frames slower than 0.15 m/s, drop seconds, landing height, shift during the drop.
Control trials give the same numbers for sit_chair / crouch / stop_stand look-alikes.
Usage: python real_scenario_profiles.py <session_dir> [...]   → real_scenario_profiles.json
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import real_check as R  # noqa: E402  (load_trials, drop_start)

WARN_CUE = {"unsteady_fall": "비틀", "freeze_collapse": "종종", "dizzy_collapse": "서기"}


def smooth(a, k):
    pad = np.pad(a, ((k // 2, k // 2),) + ((0, 0),) * (a.ndim - 1), mode="edge")
    if a.ndim == 1:
        return np.convolve(pad, np.ones(k) / k, mode="valid")
    return np.column_stack([np.convolve(pad[:, i], np.ones(k) / k, mode="valid") for i in range(a.shape[1])])


def phase_stats(f):
    t, xy, sp = f[:, 0], f[:, 1:3], f[:, 4]
    if len(t) < 8:
        return None
    fs = 1 / np.median(np.diff(t))
    k = max(3, int(round(fs)) | 1)
    k = min(k, len(t) - (1 - len(t) % 2))
    sm = smooth(xy, k)
    tg = np.gradient(sm, axis=0)
    tg /= np.maximum(np.linalg.norm(tg, axis=1, keepdims=True), 1e-9)
    d = xy - sm
    lat = d[:, 0] * tg[:, 1] - d[:, 1] * tg[:, 0]
    osc = sp - smooth(sp, k)
    return {"speed": float(sp.mean()), "osc": float(osc.std()), "sway": float(lat.std()),
            "slow_frac": float((sp < 0.15).mean()), "z": float(np.median(f[:, 3]))}


def load_cues(ses):
    import datetime as dt
    S = json.load(open(os.path.join(ses, "session.json"), encoding="utf-8"))
    M = json.load(open(os.path.join(ses, "marks.json"), encoding="utf-8-sig"))
    day = dt.datetime.fromtimestamp(S["started_at"]).date()
    ts = lambda h: dt.datetime.combine(day, dt.datetime.strptime(h, "%H:%M:%S.%f").time()).timestamp()
    out = {}
    for m in M["marks"]:
        if m["trial"] is not None:
            out.setdefault(m["trial"], []).append((m["label"], ts(m["time"])))
    return out


def main():
    rows = []
    for ses in sys.argv[1:]:
        cues = load_cues(ses)
        for tr in R.load_trials(ses):
            f = tr["frames"]
            start = cues[tr["trial"]][0][1]
            rel = {lab: t - start for lab, t in cues[tr["trial"]]}
            base = phase_stats(f[(f[:, 0] >= 1) & (f[:, 0] <= 10)])
            if base is None:
                continue
            row = {"session": os.path.basename(ses), "trial": tr["trial"], "kind": tr["kind"]}
            if tr["kind"] in WARN_CUE:
                drop = R.drop_start(f, tr["cue_mat"])
                warn_at = rel.get(WARN_CUE[tr["kind"]])
                if drop is None or warn_at is None or drop <= warn_at + 0.5:
                    row["note"] = "drop or cue not found"; rows.append(row); continue
                w = phase_stats(f[(f[:, 0] >= warn_at) & (f[:, 0] < drop)])
                after = f[(f[:, 0] >= drop) & (f[:, 0] <= drop + 6)]
                z = after[:, 3]
                land_i = np.argmax(z <= base["z"] * 0.25 + 0.15) if (z <= base["z"] * 0.25 + 0.15).any() else len(z) - 1
                row.update({
                    "warning_sec": round(drop - warn_at, 2),
                    "speed_ratio": w["speed"] / max(base["speed"], 0.1), "osc_ratio": w["osc"] / max(base["osc"], 0.02),
                    "sway_ratio": w["sway"] / max(base["sway"], 0.01), "slow_frac": w["slow_frac"],
                    "drop_sec": float(after[land_i, 0] - drop), "floor_z": float(np.median(z[land_i:])) if land_i < len(z) else None,
                    "shift_m": float(np.hypot(*(after[land_i, 1:3] - after[0, 1:3]))), "walk_z": base["z"], "walk_speed": base["speed"]})
            else:
                w = phase_stats(f[f[:, 0] > 10])
                row.update({"min_z": float(f[f[:, 0] > 10, 3].min()), "walk_z": base["z"],
                            "speed_ratio": w["speed"] / max(base["speed"], 0.1), "sway_ratio": w["sway"] / max(base["sway"], 0.01)})
            rows.append(row)
    summary = {}
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind and "note" not in r]
        keys = [k for k in rs[0] if isinstance(rs[0][k], float)] if rs else []
        summary[kind] = {"n": len(rs), **{k: [round(float(np.min([r[k] for r in rs if r[k] is not None])), 3),
                                              round(float(np.median([r[k] for r in rs if r[k] is not None])), 3),
                                              round(float(np.max([r[k] for r in rs if r[k] is not None])), 3)] for k in keys}}
    json.dump({"sessions": [os.path.basename(s) for s in sys.argv[1:]], "trials": rows, "summary_min_median_max": summary},
              open(os.path.join(HERE, "real_scenario_profiles.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for kind, s in summary.items():
        print(kind, json.dumps(s, ensure_ascii=False))
    for r in rows:
        if "note" in r:
            print("skip", r)


if __name__ == "__main__":
    main()
