"""Deployed pre-fall model, version 2 (2026-11-16): trained to flag a drop within the next 8 s (was 3 s).

Chosen from prefall_lead.py (setting A8): first alarms about 0.5 s earlier on new mock data and 0.9 s earlier on real
people left out of training (lee 4.6 s, kim 2.9 s before the drop), with more control-trial alarms (17% vs 8%).
Training: mock_lead_train.pkl (60 virtual people, warning start recorded) + all lee/kim acted trials (dizzy out) +
real walking, real windows weighted to ~15%. Threshold: lowest with <= 2 false alarms per hour on mock_lead_val.pkl,
counting only alarms before the warning behaviour (or in control episodes) as false, with the live gates (upright,
walking 1.5-4 s before) and the mean-of-3 rule. Writes mmWave/models/prefall_xgb.json + prefall_xgb_meta.json.
Usage: python prefall_final8.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MMW = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import prefall_cross_person as X  # noqa: E402
import prefall_lead as L  # noqa: E402

HORIZON = 8.0
WIN, STEP, K = 3, 0.5, 3


def main():
    ss = X.scenario_sessions()
    every = [s for v in ss.values() for s in v]
    tr, va = L.mock_eps("mock_lead_train.pkl", WIN, STEP), L.mock_eps("mock_lead_val.pkl", WIN, STEP)
    model = L.train(tr, L.real_eps(every, WIN, STEP), L.walking_X(WIN, STEP), ("h", HORIZON))
    thr = L.pick(va, [model.predict_proba(e["X"])[:, 1] for e in va], K, STEP)
    te = L.mock_eps("mock_lead_test.pkl", WIN, STEP)
    test = L.score(te, [model.predict_proba(e["X"])[:, 1] for e in te], thr, K, STEP)
    lead = json.load(open(os.path.join(HERE, "prefall_lead_result.json"), encoding="utf-8"))
    model.save_model(os.path.join(MMW, "models", "prefall_xgb.json"))
    meta = {"created": "2026-11-16", "version": 2, "scenarios": ["unsteady_fall", "freeze_collapse"],
            "window_sec": WIN, "step_sec": STEP, "horizon_sec": HORIZON, "baseline_sec": 10.0, "trend_lags_sec": [1.5, 3.0],
            "rule": "mean of last 3 windows >= threshold, upright, walking 1.5-4 s before", "threshold": thr,
            "upright_ratio": 0.8, "min_baseline_speed": 0.2,
            "features": ["speed_ratio", "speed_cv_ratio", "sway_ratio", "freeze_diff", "stride_cv_ratio", "straightness", "speed",
                         "d_speed_1.5s", "d_speed_cv_1.5s", "d_sway_1.5s", "d_freeze_1.5s", "d_speed_3s", "d_speed_cv_3s",
                         "d_sway_3s", "d_freeze_3s"],
            "training": {"mock": "mock_lead_train.pkl (60 virtual people x 5 episodes x 9 kinds, dizzy out)",
                         "real_sessions": every, "real_share": 0.15},
            "new_mock_test": {k: test[k] for k in ("caught_pct", "mean_lead_sec", "false_alarms_per_h", "control_alarm_pct")},
            "real_left_out (prefall_lead.py A8)": {p: {k: v[k] for k in ("caught_pct", "mean_lead_sec", "control_alarm_pct")}
                                                   for p, v in lead["real_left_out"]["A8"].items()},
            "previous_version": "v1 2026-11-16: 3 s horizon, threshold 0.51"}
    json.dump(meta, open(os.path.join(MMW, "models", "prefall_xgb_meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved v2, threshold", round(thr, 3), "| new mock test:", json.dumps(meta["new_mock_test"]),
          "| real left out:", json.dumps(meta["real_left_out (prefall_lead.py A8)"]))


if __name__ == "__main__":
    main()
