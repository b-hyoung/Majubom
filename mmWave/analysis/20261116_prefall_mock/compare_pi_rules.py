"""Compare what the Pi runs today (commit 77ccfc8) with the new pre-fall model on lee/kim acted trials (2026-11-16).

On the Pi there is no pre-fall model. Two things react to falls:
  1. gait risk (server/mmw_logic.py): every 6 s window of WALKING (speed >= 0.2 m/s, moved >= 0.3 m, 6 s straightness
     >= 0.5) is scored against the person's usual walking; caution / warning / critical. Warning or critical raise an
     alarm. Here the usual walking comes from that person's normal-walking recording of the same day, and windows end
     every 1 s (the Pi sends one every 6 s, so this is the most favourable case for it). The Pi's own gait code is used
     (send_mmw.py at 77ccfc8: whole-window straightness, start-to-end sway).
  2. fall detection (mmWave/fall_detect.py): body centre from >= 80% to <= 25% of usual height and held 3 s. Estimated
     here from the trial track: first time the height stays at or below 25% of walking height (+0.15 m, same as the
     drop finder) for 3 s.
The new model's numbers come from prefall_lead.py (setting A8 = deployed v2, each person left out of training).
Usage: python compare_pi_rules.py   → compare_pi_rules_result.json
"""
import json
import os
import subprocess
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MMW = os.path.abspath(os.path.join(HERE, "..", ".."))
REPO = os.path.abspath(os.path.join(MMW, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, MMW)
sys.path.insert(0, os.path.join(REPO, "server"))
import mmw_logic as L  # noqa: E402
import normal_gait_stats as N  # noqa: E402
import prefall_cross_person as X  # noqa: E402
import real_check as R  # noqa: E402
import real_scenario_profiles as RS  # noqa: E402

SES = os.path.expanduser(r"~\Downloads\mmw_viewer\sessions")
WALK = {"lee": "TEST_20261116_151728", "kim": "TEST_20261116_155829"}
FALLS = ("unsteady_fall", "freeze_collapse", "dizzy_collapse")


def pi_reader():
    src = subprocess.run(["git", "-C", REPO, "show", "77ccfc8:mmWave/send_mmw.py"], capture_output=True, check=True).stdout.decode("utf-8")
    mod = types.ModuleType("send_mmw_pi")
    mod.__file__ = os.path.join(MMW, "send_mmw.py")
    exec(compile(src, "send_mmw_pi", "exec"), mod.__dict__)
    return mod


def pi_window(Mpi, f, end, win=6.0):
    p = f[(f[:, 0] > end - win) & (f[:, 0] <= end)]
    if len(p) < 10:
        return None
    raw = Mpi._gait_from_samples([(q[0], q[1], q[2], q[3], q[4], 0.0) for q in p])
    xy = p[:, 1:3]
    disp = float(np.hypot(*(xy[-1] - xy[0])))
    path = float(np.sum(np.hypot(*np.diff(xy, axis=0).T)))
    straight = disp / path if path > 1e-6 else 0.0
    walking = raw["speed"] >= Mpi.WALK_SPEED_MIN and disp >= Mpi.WALK_DISP_MIN and straight >= Mpi.STRAIGHT_MIN
    return raw, walking


def baseline_stats(Mpi, person):
    rows, loose = [], []
    for p, ses, ranges, rel in N.RECORDINGS:
        if ses != WALK[person]:
            continue
        for lab, v in N.walker_tracks(ses, ranges, rel):
            if lab != "normal":
                continue
            f = np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]])
            for end in np.arange(6.0, f[-1, 0], 1.0):
                w = pi_window(Mpi, f, end)
                if w and w[1]:
                    rows.append(w[0])
                elif w and w[0]["speed"] >= Mpi.WALK_SPEED_MIN:
                    loose.append(w[0])
    pi_gate_windows = len(rows)
    if len(rows) < 5:
        # The walking recordings follow a circle, so the Pi's 6 s straightness rule rejects every window and the Pi
        # could never build this baseline. Use all walking windows instead so the scoring can still be compared.
        rows = loose
    st = {}
    for m in L.JUDGED_METRICS:
        vals = [r[m] for r in rows]
        st[f"{m}_mu"] = float(np.mean(vals))
        st[f"{m}_sigma"] = max(float(np.std(vals)), L.SIGMA_FLOOR[m])
    return st, {"pi_rule_windows": pi_gate_windows, "used_windows": len(rows)}


def fall_detect_time(f, walk_z, drop):
    low = f[:, 3] <= 0.25 * walk_z + 0.15
    t = f[:, 0]
    start = None
    for i in range(len(t)):
        if t[i] < (drop or 10):
            continue
        if low[i]:
            start = t[i] if start is None else start
            if t[i] - start >= 3.0:
                return float(t[i])
        else:
            start = None
    return None


def main():
    Mpi = pi_reader()
    ss = X.scenario_sessions()
    out = {"trials": [], "baseline_windows": {}}
    for person, sessions in sorted(ss.items()):
        stats, n = baseline_stats(Mpi, person)
        out["baseline_windows"][person] = n
        for s in sessions:
            cues = RS.load_cues(os.path.join(SES, s))
            for tr in R.load_trials(os.path.join(SES, s)):
                f = tr["frames"]
                walk_z = float(np.median(f[(f[:, 0] >= 1) & (f[:, 0] <= 10), 3]))
                drop = R.drop_start(f, tr["cue_mat"]) if tr["kind"] in FALLS else None
                if tr["kind"] in FALLS and drop is None:
                    continue
                warn = None
                if drop is not None:
                    c = cues[tr["trial"]]
                    warn = next(t for lab, t in c if lab == RS.WARN_CUE[tr["kind"]]) - c[0][1]
                stop = drop if drop else f[-1, 0]
                judged, levels = 0, []
                for end in np.arange(6.0, stop, 1.0):
                    w = pi_window(Mpi, f, end)
                    if not w or not w[1]:
                        continue
                    judged += 1
                    lvl = L.classify_gait(L.gait_score(L.compute_zscore(w[0], stats), None)["combined"])
                    levels.append((float(end), lvl))
                row = {"person": person, "session": s, "trial": tr["trial"], "kind": tr["kind"], "judged_windows": judged}
                alarm = [e for e, l in levels if l in ("warning", "critical")]
                caution = [e for e, l in levels if l != "normal"]
                if drop is not None:
                    inwin = [e for e in alarm if warn <= e < drop]
                    row.update(caught=bool(inwin), lead_sec=round(drop - inwin[0], 2) if inwin else None,
                               caught_with_caution=any(warn <= e < drop for e in caution),
                               alarm_before_warning=any(e < warn for e in alarm),
                               judged_in_warning=sum(warn <= e < drop for e, _ in levels))
                    fd = fall_detect_time(f, walk_z, drop)
                    row["fall_detect_after_drop_sec"] = round(fd - drop, 2) if fd else None
                else:
                    row.update(false_alarm=bool(alarm), false_caution=bool(caution))
                    row["fall_detect_fired"] = fall_detect_time(f, walk_z, 10.0) is not None
                out["trials"].append(row)
    T = out["trials"]
    summ = {}
    for kind in FALLS:
        rs = [r for r in T if r["kind"] == kind]
        leads = [r["lead_sec"] for r in rs if r["lead_sec"] is not None]
        fds = [r["fall_detect_after_drop_sec"] for r in rs if r["fall_detect_after_drop_sec"] is not None]
        summ[kind] = {"n": len(rs), "gait_risk_caught": sum(r["caught"] for r in rs), "gait_risk_mean_lead": round(float(np.mean(leads)), 2) if leads else None,
                      "caught_if_caution_counts": sum(r["caught_with_caution"] for r in rs),
                      "judged_windows_in_warning_median": float(np.median([r["judged_in_warning"] for r in rs])),
                      "fall_detect_fired": len(fds), "fall_detect_mean_after_drop": round(float(np.mean(fds)), 2) if fds else None}
    ctrl = [r for r in T if "false_alarm" in r]
    summ["controls"] = {"n": len(ctrl), "gait_risk_alarm": sum(r["false_alarm"] for r in ctrl), "gait_caution": sum(r["false_caution"] for r in ctrl),
                        "fall_detect_fired": sum(r["fall_detect_fired"] for r in ctrl)}
    lead = json.load(open(os.path.join(HERE, "prefall_lead_result.json"), encoding="utf-8"))
    summ["new_model_v2_left_out"] = lead["real_left_out"]["A8"]
    out["summary"] = summ
    json.dump(out, open(os.path.join(HERE, "compare_pi_rules_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in summ.items() if k != "new_model_v2_left_out"}, ensure_ascii=False, indent=1))
    print("baseline windows:", out["baseline_windows"])


if __name__ == "__main__":
    main()
