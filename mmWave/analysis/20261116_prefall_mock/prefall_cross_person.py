"""Cross-person test of the pre-fall model with real-profile mock data (lee and kim, 2026-11-16).

For each test person P (lee, kim):
  * the mock data is generated from the OTHER person's acted trials only (mock_generator.py --profile, P's sessions excluded)
  * the real "no fall" walking added to training excludes P's walking recordings
  * the model (3 s windows, ratio + trend features, XGBoost, mean of 3 windows; threshold for <= 2 false alarms/h on mock)
    then scores all of P's acted trials
A combined model (mock from both people) is also scored with leave-one-person-out in the same way, i.e. it equals the
per-person runs; so the "both" row instead reports both people's profiles pooled and tested round-by-round
(leave-one-round-out within each person, other person's trials always available).
Usage: python prefall_cross_person.py   → prefall_cross_person_result.json
"""
import glob
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import normal_gait_stats as N  # noqa: E402
import prefall_eval as P  # noqa: E402
import prefall_improve as I  # noqa: E402
import prefall_real_eval as E  # noqa: E402

SES = E.SES


def scenario_sessions():
    out = {}
    for path in sorted(glob.glob(os.path.join(SES, "TEST_20261116_*", "marks.json"))):
        m = json.load(open(path, encoding="utf-8-sig"))
        if m.get("kind") == "prefall_scenario":
            out.setdefault(m["participant"], []).append(m["mmwave_session"])
    return out


def walking_X(exclude_person):
    X = []
    for person, ses, ranges, rel in N.RECORDINGS:
        if person == exclude_person:
            continue
        for lab, v in N.walker_tracks(ses, ranges, rel):
            f = np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]])
            base = P.baseline(f, E.WIN)
            if base:
                rows = I.series(f, base, E.WIN, f[-1, 0])
                X += [I.vec(rows, i, base, E.FSET) for i in range(len(rows))]
    return np.array(X)


def generate(pkl, exclude, seed):
    if not os.path.exists(os.path.join(HERE, pkl)):
        cmd = [sys.executable, os.path.join(HERE, "mock_generator.py"), "--people", "40", "--per-kind", "4", "--seed", str(seed),
               "--profile", os.path.join(HERE, "real_scenario_profiles.json"), "--out", pkl]
        for s in exclude:
            cmd += ["--exclude-session", s]
        subprocess.run(cmd, check=True)


def main():
    by_person = scenario_sessions()
    print("scenario sessions:", by_person, flush=True)
    result = {"sessions": by_person, "cross": {}, "pooled_leave_round_out": {}}
    for k, (person, sessions) in enumerate(sorted(by_person.items())):
        pkl = f"mock_profile_not_{person}.pkl"
        generate(pkl, sessions, 300 + k)
        model, thr = E.train(*E.to_xy(E.mock_matrix(pkl)), walking_X(person))
        trials = []
        for s in sessions:
            for t in E.score_round(model, thr, s):
                t["session"] = s
                trials.append(t)
        result["cross"][person] = {"threshold": thr, "trials": trials, "summary": E.summarize(trials)}
        print(f"trained without {person} -> test {person}:", json.dumps(E.summarize(trials), ensure_ascii=False), flush=True)
    for person, sessions in sorted(by_person.items()):
        trials = []
        for r, s in enumerate(sessions):
            pkl = f"mock_profile_pool_hold_{s}.pkl"
            generate(pkl, [s], 400 + r)
            model, thr = E.train(*E.to_xy(E.mock_matrix(pkl)), walking_X(None))
            for t in E.score_round(model, thr, s):
                t["session"] = s
                trials.append(t)
        result["pooled_leave_round_out"][person] = {"trials": trials, "summary": E.summarize(trials)}
        print(f"pooled profiles, leave-one-round-out -> {person}:", json.dumps(E.summarize(trials), ensure_ascii=False), flush=True)
    json.dump(result, open(os.path.join(HERE, "prefall_cross_person_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
