"""Improving the pre-fall model for a person it has not seen (focus: kim), 2026-11-16.

Two changes, tested separately and together, always with the tested person's data left out of training
(mock profile from the other person only, real walking negatives without that person):
  * mix: mock_generator.py --mix → each fall episode blends two real trials and half of them are weakened (factor
    0.5-1 toward "no change"), to cover milder actors than the recorded ones
  * height (feature set H): body-centre height in the window relative to the person's walking height, its spread,
    its slope over the window, and points per frame relative to walking. Sitting down or crouching lowers the body
    in the window; walking just before a fall does not (the drop itself is excluded from fall windows).
Also records, for control trials with an alarm, whether the alarm came before the body started to go down.
Usage: python prefall_kim.py   → prefall_kim_result.json
"""
import json
import os
import pickle
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import normal_gait_stats as N  # noqa: E402
import prefall_cross_person as X  # noqa: E402
import prefall_eval as P  # noqa: E402
import prefall_improve as I  # noqa: E402
import prefall_real_eval as E  # noqa: E402
import real_check as R  # noqa: E402

WIN = 3


def base_height(f):
    m = (f[:, 0] >= 1) & (f[:, 0] <= 10)
    return {"z": float(np.median(f[m, 3])), "zstd": float(np.std(f[m, 3])), "npts": float(np.mean(f[m, 5]))}


def series_h(f, stop):
    base = P.baseline(f, WIN)
    if base is None:
        return None, None
    bh = base_height(f)
    rows = []
    t = f[:, 0]
    for end in np.arange(10.0 + WIN, stop, P.STEP):
        g = P.window_feats(f, end, WIN)
        if not g:
            continue
        w = f[(t > end - WIN) & (t <= end)]
        z, tw = w[:, 3], w[:, 0]
        g = {k: float(g[k]) for k in I.KEYS}
        g["z_ratio"] = float(np.median(z)) / max(bh["z"], 0.3)
        g["z_std_ratio"] = float(np.std(z)) / max(bh["zstd"], 0.03)
        g["z_slope"] = float(z[tw > end - 1].mean() - z[tw <= end - WIN + 1].mean())
        g["npts_ratio"] = float(w[:, 5].mean()) / max(bh["npts"], 1.0)
        rows.append((float(end), g))
    return base, rows


def vec(rows, i, base, fset):
    v = I.vec(rows, i, base, "C")
    if fset == "H":
        g = rows[i][1]
        v += [g["z_ratio"], g["z_std_ratio"], g["z_slope"], g["npts_ratio"]]
    return v


def mock_xy(pkl, fset):
    data = pickle.load(open(os.path.join(HERE, pkl), "rb"))
    Xs, y, g, ep, lead = [], [], [], [], []
    for i, e in enumerate(data["episodes"]):
        f = e["frames"]
        base, rows = series_h(f, e["drop_start"] if e["fall"] else f[-1, 0])
        if base is None:
            continue
        for j, (end, _) in enumerate(rows):
            Xs.append(vec(rows, j, base, fset))
            to_drop = (e["drop_start"] - end) if e["fall"] else np.inf
            y.append(int(e["kind"] in E.FALL3 and to_drop <= P.HORIZON))
            g.append(e["person"]); ep.append(i); lead.append(to_drop)
    return np.array(Xs), np.array(y), np.array(g), np.array(ep), np.array(lead)


def walking(exclude, fset):
    out = []
    for person, ses, ranges, rel in N.RECORDINGS:
        if person == exclude:
            continue
        for lab, v in N.walker_tracks(ses, ranges, rel):
            f = np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]])
            base, rows = series_h(f, f[-1, 0])
            if base:
                out += [vec(rows, j, base, fset) for j in range(len(rows))]
    return np.array(out)


def score(model, thr, ses, fset):
    out = []
    for tr in R.load_trials(os.path.join(E.SES, ses)):
        f = tr["frames"]
        drop = R.drop_start(f, tr["cue_mat"]) if tr["kind"] in E.FALL3 else None
        if tr["kind"] in E.FALL3 and drop is None:
            continue
        base, rows = series_h(f, drop if drop else f[-1, 0])
        if base is None or len(rows) < 3:
            continue
        p = model.predict_proba(np.array([vec(rows, j, base, fset) for j in range(len(rows))]))[:, 1]
        hit = I.alarm(p, np.zeros(len(p), int), thr, E.RULE)
        ends = np.array([r[0] for r in rows])
        if drop:
            lead = drop - ends
            early = hit & (lead <= P.HORIZON)
            out.append({"session": ses, "trial": tr["trial"], "kind": tr["kind"], "caught": bool(early.any()),
                        "lead_sec": round(float(lead[early].max()), 2) if early.any() else None,
                        "alarm_before_warning_window": bool((hit & (lead > P.HORIZON)).any())})
        else:
            walk_z = base_height(f)["z"]
            low = np.where((f[:, 0] > 10) & (f[:, 3] < 0.8 * walk_z))[0]
            down_at = float(f[low[0], 0]) if len(low) else None
            first = float(ends[hit][0]) if hit.any() else None
            out.append({"session": ses, "trial": tr["trial"], "kind": tr["kind"], "false_alarm": bool(hit.any()),
                        "alarm_before_going_down": (first is not None and (down_at is None or first < down_at))})
    return out


def main():
    sessions = X.scenario_sessions()
    result = {}
    for person in ("kim", "lee"):
        others = sessions[person]
        for mix in (False, True):
            pkl = f"mock_profile_{'mix_' if mix else ''}not_{person}.pkl"
            if not os.path.exists(os.path.join(HERE, pkl)):
                import subprocess
                cmd = [sys.executable, os.path.join(HERE, "mock_generator.py"), "--people", "40", "--per-kind", "4",
                       "--seed", str(500 + mix), "--profile", os.path.join(HERE, "real_scenario_profiles.json"), "--out", pkl]
                for s in others:
                    cmd += ["--exclude-session", s]
                if mix:
                    cmd.append("--mix")
                subprocess.run(cmd, check=True)
            for fset in ("C", "H"):
                key = f"test {person} | {'mix' if mix else 'plain'} | {fset}"
                Xm, y, g, ep, lead = mock_xy(pkl, fset)
                model, thr = E.train(Xm, y, g, ep, lead, walking(person, fset))
                trials = sum((score(model, thr, s, fset) for s in sessions[person]), [])
                s = E.summarize(trials)
                ctrl = [t for t in trials if "false_alarm" in t]
                s["control_alarms_before_going_down"] = f"{sum(t['alarm_before_going_down'] for t in ctrl)}/{len(ctrl)}"
                s["threshold"] = round(thr, 2)
                result[key] = {"summary": s, "trials": trials}
                print(key, json.dumps({k: v for k, v in s.items() if k != "skipped"}, ensure_ascii=False), flush=True)
                json.dump(result, open(os.path.join(HERE, "prefall_kim_result.json"), "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
