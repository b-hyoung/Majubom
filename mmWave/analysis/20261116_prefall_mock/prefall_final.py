"""Final pre-fall model (2026-11-16): 2 scenarios (unsteady walking, freezing), dizzy collapse left out.

Training data
  * real: lee and kim acted trials, 6 rounds (tools/Record-FallScenario.ps1), dizzy trials removed, plus their real
    normal walking (no-fall examples). Real windows are up-weighted so they count for about 15% of the training weight.
  * mock: generated from those real trials (mock_generator.py --profile --mix --skip-kind dizzy_collapse), with the
    radar's low-speed behaviour (points vanish, height drifts up) copied from the same recordings.
Model: 3 s windows every 0.5 s, gait features relative to the person's first 10 s of walking + 1.5 s / 3 s trends,
XGBoost, alarm when the mean of the last 3 windows passes the threshold, and only while the person is upright
(body height >= 80% of walking height); the threshold allows at most 2 false alarms per hour on mock non-fall time.

1. Leave-one-person-out check: for person P, mock data from the other person's trials only, the other person's real
   trials and walking; scored on all of P's trials.
2. Final model on everything → mmWave/models/prefall_xgb.json + prefall_xgb_meta.json (read by mmWave/prefall_predictor.py).
Usage: python prefall_final.py   → prefall_final_result.json
"""
import json
import os
import pickle
import subprocess
import sys

import numpy as np
from sklearn.model_selection import GroupKFold

HERE = os.path.dirname(os.path.abspath(__file__))
MMW = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import normal_gait_stats as N  # noqa: E402
import prefall_cross_person as X  # noqa: E402
import prefall_eval as P  # noqa: E402
import prefall_improve as I  # noqa: E402
import prefall_real_eval as E  # noqa: E402
import real_check as R  # noqa: E402

FALLS = ("unsteady_fall", "freeze_collapse")
SKIP = "dizzy_collapse"
WIN, FSET, RULE = 3, "C", "mean3"
UPRIGHT = 0.8
REAL_SHARE = 0.15


def mock_rows(pkl):
    data = pickle.load(open(os.path.join(HERE, pkl), "rb"))
    Xs, y, g, ep, lead = [], [], [], [], []
    for i, e in enumerate(data["episodes"]):
        if e["kind"] == SKIP:
            continue
        f = e["frames"]
        base = P.baseline(f, WIN)
        if base is None:
            continue
        rows = I.series(f, base, WIN, e["drop_start"] if e["fall"] else f[-1, 0])
        for j, (end, _) in enumerate(rows):
            Xs.append(I.vec(rows, j, base, FSET))
            to_drop = (e["drop_start"] - end) if e["fall"] else np.inf
            y.append(int(e["kind"] in FALLS and to_drop <= P.HORIZON))
            g.append(e["person"]); ep.append(i); lead.append(to_drop)
    return np.array(Xs), np.array(y), np.array(g), np.array(ep), np.array(lead)


def trial_windows(tr):
    """Windows of one real trial: (X, labels, ends, drop, upright flags, walk height)."""
    f = tr["frames"]
    drop = R.drop_start(f, tr["cue_mat"]) if tr["kind"] in FALLS else None
    if tr["kind"] in FALLS and drop is None:
        return None
    base = P.baseline(f, WIN)
    if base is None:
        return None
    rows = I.series(f, base, WIN, drop if drop else f[-1, 0])
    if len(rows) < 3:
        return None
    walk_z = float(np.median(f[(f[:, 0] >= 1) & (f[:, 0] <= 10), 3]))
    ends = np.array([r[0] for r in rows])
    upright = np.array([np.median(f[(f[:, 0] > e - 0.5) & (f[:, 0] <= e), 3]) >= UPRIGHT * walk_z for e in ends])
    Xs = np.array([I.vec(rows, j, base, FSET) for j in range(len(rows))])
    y = np.array([int(drop is not None and drop - e <= P.HORIZON) for e in ends])
    return Xs, y, ends, drop, upright


def real_trials(sessions):
    out = []
    for s in sessions:
        for tr in R.load_trials(os.path.join(E.SES, s)):
            if tr["kind"] == SKIP:
                continue
            w = trial_windows(tr)
            if w:
                out.append({"session": s, "trial": tr["trial"], "kind": tr["kind"], "w": w})
    return out


def walking(exclude=None):
    return X.walking_X(exclude) if exclude else E.real_walking_X()


def fit(Xm, ym, gm, epm, leadm, real, W):
    """Threshold from mock out-of-fold predictions; final fit on mock + real trials + real walking (weighted)."""
    Xr = np.vstack([t["w"][0] for t in real]) if real else np.zeros((0, Xm.shape[1]))
    yr = np.concatenate([t["w"][1] for t in real]) if real else np.zeros(0, int)
    Xr, yr = np.vstack([Xr, W]), np.concatenate([yr, np.zeros(len(W), int)])
    wr = REAL_SHARE / (1 - REAL_SHARE) * len(ym) / max(len(yr), 1)
    inner = np.zeros(len(ym))
    for tr, te in GroupKFold(3).split(Xm, ym, gm):
        m = I.make("xgboost").fit(np.vstack([Xm[tr], Xr]), np.concatenate([ym[tr], yr]),
                                  sample_weight=np.concatenate([np.ones(len(tr)), np.full(len(yr), wr)]))
        inner[te] = m.predict_proba(Xm[te])[:, 1]
    thr = I.pick_thr(inner, epm, leadm, RULE)
    model = I.make("xgboost").fit(np.vstack([Xm, Xr]), np.concatenate([ym, yr]),
                                  sample_weight=np.concatenate([np.ones(len(ym)), np.full(len(yr), wr)]))
    return model, thr, wr


def score(model, thr, trials):
    out = []
    for t in trials:
        Xs, y, ends, drop, upright = t["w"]
        p = model.predict_proba(Xs)[:, 1]
        hit = I.alarm(p, np.zeros(len(p), int), thr, RULE) & upright
        row = {"session": t["session"], "trial": t["trial"], "kind": t["kind"]}
        if drop is not None:
            lead = drop - ends
            early = hit & (lead <= P.HORIZON)
            row.update(caught=bool(early.any()), lead_sec=round(float(lead[early].max()), 2) if early.any() else None,
                       early_alarm=bool((hit & (lead > P.HORIZON)).any()))
        else:
            row.update(false_alarm=bool(hit.any()))
        out.append(row)
    return out


def summary(rows):
    falls = [r for r in rows if "caught" in r]
    ctrl = [r for r in rows if "false_alarm" in r]
    leads = [r["lead_sec"] for r in falls if r["lead_sec"] is not None]
    return {"falls_caught": f"{sum(r['caught'] for r in falls)}/{len(falls)}",
            "by_kind": {k: f"{sum(r['caught'] for r in falls if r['kind'] == k)}/{sum(r['kind'] == k for r in falls)}" for k in FALLS},
            "mean_lead_sec": round(float(np.mean(leads)), 2) if leads else None,
            "control_trials_with_alarm": f"{sum(r['false_alarm'] for r in ctrl)}/{len(ctrl)}",
            "fall_trials_with_early_alarm": f"{sum(r['early_alarm'] for r in falls)}/{len(falls)}"}


def generate(pkl, exclude, seed, people=40, per_kind=4):
    if os.path.exists(os.path.join(HERE, pkl)):
        return
    cmd = [sys.executable, os.path.join(HERE, "mock_generator.py"), "--people", str(people), "--per-kind", str(per_kind),
           "--seed", str(seed), "--profile", os.path.join(HERE, "real_scenario_profiles.json"), "--out", pkl,
           "--mix", "--skip-kind", SKIP]
    for s in exclude:
        cmd += ["--exclude-session", s]
    subprocess.run(cmd, check=True)


def main():
    sessions = X.scenario_sessions()
    result = {"scenarios": FALLS, "sessions": sessions, "lopo": {}}
    for k, (person, own) in enumerate(sorted(sessions.items())):
        other = [s for p, ss in sessions.items() if p != person for s in ss]
        pkl = f"mock_final_not_{person}.pkl"
        generate(pkl, own, 700 + k)
        model, thr, wr = fit(*mock_rows(pkl), real_trials(other), walking(person))
        rows = score(model, thr, real_trials(own))
        result["lopo"][person] = {"threshold": round(thr, 3), "real_weight": round(wr, 2), "summary": summary(rows), "trials": rows}
        print(f"left out {person}:", json.dumps(result["lopo"][person]["summary"], ensure_ascii=False), flush=True)
    every = [s for ss in sessions.values() for s in ss]
    generate("mock_final_all.pkl", [], 777, people=60, per_kind=5)
    model, thr, wr = fit(*mock_rows("mock_final_all.pkl"), real_trials(every), walking())
    os.makedirs(os.path.join(MMW, "models"), exist_ok=True)
    model.save_model(os.path.join(MMW, "models", "prefall_xgb.json"))
    meta = {"created": "2026-11-16", "scenarios": FALLS, "window_sec": WIN, "step_sec": P.STEP, "horizon_sec": P.HORIZON,
            "baseline_sec": 10.0, "trend_lags_sec": [1.5, 3.0], "rule": "mean of last 3 windows >= threshold",
            "threshold": thr, "upright_ratio": UPRIGHT, "features": ["speed_ratio", "speed_cv_ratio", "sway_ratio",
            "freeze_diff", "stride_cv_ratio", "straightness", "speed", "d_speed_1.5s", "d_speed_cv_1.5s", "d_sway_1.5s",
            "d_freeze_1.5s", "d_speed_3s", "d_speed_cv_3s", "d_sway_3s", "d_freeze_3s"],
            "training": {"real_sessions": every, "mock": "mock_final_all.pkl (60 virtual people × 5 episodes × kind)",
                         "real_weight_per_window": wr},
            "leave_one_person_out": {p: v["summary"] for p, v in result["lopo"].items()}}
    json.dump(meta, open(os.path.join(MMW, "models", "prefall_xgb_meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    result["final"] = {"threshold": thr, "model": "mmWave/models/prefall_xgb.json"}
    print("final model saved, threshold", round(thr, 3), flush=True)
    json.dump(result, open(os.path.join(HERE, "prefall_final_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
