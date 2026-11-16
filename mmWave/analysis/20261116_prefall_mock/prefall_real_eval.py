"""Test the pre-fall model on lee's real acted trials (3 rounds of tools/Record-FallScenario.ps1, 2026-11-16).

Two kinds of mock training data are compared, with the best setup from prefall_improve.py
(3 s windows, personal-ratio + trend features, XGBoost, real walking as extra "no fall" examples, mean of 3 windows):
  * literature mock: mock_episodes.pkl (scenario changes from literature_effects.json; no lee trial used)
  * real-profile mock: scenario changes copied from lee's acted trials. To keep the test fair, the mock data used to
    score round k is generated WITHOUT round k's trials (leave-one-round-out).
Threshold: picked on mock out-of-fold predictions for at most 2 false alarms per hour (same as before).
Reported per round and in total: fall trials caught before the drop (and how early), control trials with an alarm,
and alarms in the warning-free part of fall trials (more than 3 s before the drop).
Usage: python prefall_real_eval.py   → prefall_real_eval_result.json
"""
import json
import os
import pickle
import subprocess
import sys

import numpy as np
from sklearn.model_selection import GroupKFold

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prefall_eval as P  # noqa: E402
import prefall_improve as I  # noqa: E402
import real_check as R  # noqa: E402

SES = os.path.expanduser(r"~\Downloads\mmw_viewer\sessions")
ROUNDS = ["TEST_20261116_152746", "TEST_20261116_153708", "TEST_20261116_154621"]
WIN, FSET, RULE = 3, "C", "mean3"
FALL3 = I.FALL3


def mock_matrix(pkl):
    data = pickle.load(open(os.path.join(HERE, pkl), "rb"))
    mock = []
    for i, e in enumerate(data["episodes"]):
        f = e["frames"]
        base = P.baseline(f, WIN)
        if base is None:
            continue
        stop = e["drop_start"] if e["fall"] else f[-1, 0]
        mock.append({"ep": i, "person": e["person"], "kind": e["kind"], "drop": e["drop_start"], "base": base,
                     "rows": I.series(f, base, WIN, stop)})
    return mock


def to_xy(mock):
    X, y, g, ep, lead = [], [], [], [], []
    for e in mock:
        for i, (end, _) in enumerate(e["rows"]):
            X.append(I.vec(e["rows"], i, e["base"], FSET))
            to_drop = (e["drop"] - end) if e["drop"] is not None else np.inf
            y.append(int(e["kind"] in FALL3 and to_drop <= P.HORIZON))
            g.append(e["person"]); ep.append(e["ep"]); lead.append(to_drop)
    return np.array(X), np.array(y), np.array(g), np.array(ep), np.array(lead)


def real_walking_X():
    c = {"real": []}
    import normal_gait_stats as N
    for person, ses, ranges, rel in N.RECORDINGS:
        for lab, v in N.walker_tracks(ses, ranges, rel):
            f = np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]])
            base = P.baseline(f, WIN)
            if base:
                rows = I.series(f, base, WIN, f[-1, 0])
                c["real"] += [I.vec(rows, i, base, FSET) for i in range(len(rows))]
    return np.array(c["real"])


def train(X, y, g, ep, lead, W):
    inner = np.zeros(len(y))
    for tr, te in GroupKFold(3).split(X, y, g):
        m = I.make("xgboost").fit(np.vstack([X[tr], W]), np.concatenate([y[tr], np.zeros(len(W), int)]))
        inner[te] = m.predict_proba(X[te])[:, 1]
    thr = I.pick_thr(inner, ep, lead, RULE)
    model = I.make("xgboost").fit(np.vstack([X, W]), np.concatenate([y, np.zeros(len(W), int)]))
    return model, thr


def score_round(model, thr, ses):
    out = []
    for tr in R.load_trials(os.path.join(SES, ses)):
        f = tr["frames"]
        base = P.baseline(f, WIN)
        if base is None:
            out.append({"trial": tr["trial"], "kind": tr["kind"], "note": "no baseline"}); continue
        drop = R.drop_start(f, tr["cue_mat"]) if tr["kind"] in FALL3 else None
        if tr["kind"] in FALL3 and drop is None:
            out.append({"trial": tr["trial"], "kind": tr["kind"], "note": "drop not found"}); continue
        rows = I.series(f, base, WIN, drop if drop else f[-1, 0])
        if len(rows) < 3:
            out.append({"trial": tr["trial"], "kind": tr["kind"], "note": "too few windows"}); continue
        X = np.array([I.vec(rows, i, base, FSET) for i in range(len(rows))])
        prob = model.predict_proba(X)[:, 1]
        hit = I.alarm(prob, np.zeros(len(prob), int), thr, RULE)
        ends = np.array([r[0] for r in rows])
        if drop:
            lead = drop - ends
            early = hit & (lead <= P.HORIZON)
            out.append({"trial": tr["trial"], "kind": tr["kind"], "caught": bool(early.any()),
                        "lead_sec": round(float(lead[early].max()), 2) if early.any() else None,
                        "alarm_before_warning_window": bool((hit & (lead > P.HORIZON)).any()),
                        "max_prob_last3s": round(float(prob[lead <= P.HORIZON].max()), 3) if (lead <= P.HORIZON).any() else None})
        else:
            out.append({"trial": tr["trial"], "kind": tr["kind"], "false_alarm": bool(hit.any()),
                        "max_prob": round(float(prob.max()), 3)})
    return out


def summarize(trials):
    falls = [t for t in trials if t["kind"] in FALL3 and "caught" in t]
    ctrl = [t for t in trials if "false_alarm" in t]
    by = {k: f"{sum(t['caught'] for t in falls if t['kind'] == k)}/{sum(1 for t in falls if t['kind'] == k)}" for k in FALL3}
    leads = [t["lead_sec"] for t in falls if t["lead_sec"] is not None]
    return {"falls_caught": f"{sum(t['caught'] for t in falls)}/{len(falls)}", "by_kind": by,
            "mean_lead_sec": round(float(np.mean(leads)), 2) if leads else None,
            "control_trials_with_alarm": f"{sum(t['false_alarm'] for t in ctrl)}/{len(ctrl)}",
            "fall_trials_alarm_too_early": f"{sum(t['alarm_before_warning_window'] for t in falls)}/{len(falls)}",
            "skipped": [t for t in trials if "note" in t]}


def main():
    W = real_walking_X()
    result = {"rounds": ROUNDS, "setup": {"window_s": WIN, "features": FSET, "rule": RULE, "model": "xgboost"},
              "real_walking_windows": int(len(W))}
    # literature mock: one model, scored on all rounds
    model, thr = train(*to_xy(mock_matrix("mock_episodes.pkl")), W)
    lit = sum((score_round(model, thr, s) for s in ROUNDS), [])
    result["literature_mock"] = {"threshold": thr, "trials": lit, "summary": summarize(lit)}
    print("literature mock:", json.dumps(result["literature_mock"]["summary"], ensure_ascii=False), flush=True)
    # real-profile mock, leave one round out
    allr = []
    for k, ses in enumerate(ROUNDS):
        pkl = f"mock_profile_hold{k + 1}.pkl"
        if not os.path.exists(os.path.join(HERE, pkl)):
            subprocess.run([sys.executable, os.path.join(HERE, "mock_generator.py"), "--people", "40", "--per-kind", "4",
                            "--seed", str(100 + k), "--profile", os.path.join(HERE, "real_scenario_profiles.json"),
                            "--exclude-session", ses, "--out", pkl], check=True)
        model, thr = train(*to_xy(mock_matrix(pkl)), W)
        tr = score_round(model, thr, ses)
        for t in tr:
            t["round"] = k + 1
        allr += tr
        print(f"real-profile mock, round {k + 1} held out (thr {thr:.2f}):", json.dumps(summarize(tr), ensure_ascii=False), flush=True)
    result["real_profile_mock"] = {"trials": allr, "summary": summarize(allr)}
    print("real-profile mock total:", json.dumps(result["real_profile_mock"]["summary"], ensure_ascii=False))
    json.dump(result, open(os.path.join(HERE, "prefall_real_eval_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
