"""Deployed pre-fall model v3 (2026-11-16): fixed reference walking profile, no 10 s wait, warns right after getting up.

From prefall_popbase.py (gate "none"): features are relative to one reference walking profile measured from the real
walking recordings + mock normal walking, scoring starts 3 s after the track appears, and the only gate is "upright"
(body centre >= 80% of the reference walking height). Training: mock with normal walking first (mock_lead_train) +
mock starting right beside the bed (mock_exit_train) + all lee/kim acted trials (dizzy out) + real walking.
Label = drop within 8 s. Threshold for <= 2 false alarms/h on both validation mock sets.
Results (prefall_popbase_result.json): new mock after walking 56.0% caught, 3.1 s ahead; new mock right after getting up
54.5%, 2.7 s (v2 could not warn at all here); people left out: lee 90% (4.0 s avg), kim 41.6%; control-trial alarms 16.7%.
Writes mmWave/models/prefall_xgb.json + prefall_xgb_meta.json (v2 kept as prefall_xgb_v2_8s*.json).
Usage: python prefall_final_v3.py
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MMW = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import prefall_cross_person as X  # noqa: E402
import prefall_lead as L  # noqa: E402
import prefall_popbase as B  # noqa: E402

HORIZON = 8.0


def main():
    models = os.path.join(MMW, "models")
    if not os.path.exists(os.path.join(models, "prefall_xgb_v2_8s.json")):
        shutil.copy(os.path.join(models, "prefall_xgb.json"), os.path.join(models, "prefall_xgb_v2_8s.json"))
        shutil.copy(os.path.join(models, "prefall_xgb_meta.json"), os.path.join(models, "prefall_xgb_v2_8s_meta.json"))
    ss = X.scenario_sessions()
    every = [s for v in ss.values() for s in v]
    ref = B.reference()
    tr = B.mock("mock_lead_train.pkl", ref, "all") + B.mock("mock_exit_train.pkl", ref, "all")
    va = B.gated(B.mock("mock_lead_val.pkl", ref, "all") + B.mock("mock_exit_val.pkl", ref, "all"), "none")
    model = L.train(tr, B.real(every, ref), B.walk_X(ref), ("h", HORIZON))
    thr = L.pick(va, [model.predict_proba(e["X"])[:, 1] for e in va], 3, 0.5)
    res = json.load(open(os.path.join(HERE, "prefall_popbase_result.json"), encoding="utf-8"))
    model.save_model(os.path.join(models, "prefall_xgb.json"))
    meta = {"created": "2026-11-16", "version": 3, "scenarios": ["unsteady_fall", "freeze_collapse"],
            "baseline": "reference", "reference": {k: ref[k] for k in B.KEYS}, "reference_walk_z": ref["walk_z"],
            "start_sec": 3.0, "walking_gate": False,
            "window_sec": 3, "step_sec": 0.5, "horizon_sec": HORIZON, "trend_lags_sec": [1.5, 3.0],
            "rule": "mean of last 3 windows >= threshold and upright (body centre >= 80% of reference walking height)",
            "threshold": thr, "upright_ratio": 0.8,
            "features": ["speed_ratio", "speed_cv_ratio", "sway_ratio", "freeze_diff", "stride_cv_ratio", "straightness", "speed",
                         "d_speed_1.5s", "d_speed_cv_1.5s", "d_sway_1.5s", "d_freeze_1.5s", "d_speed_3s", "d_speed_cv_3s",
                         "d_sway_3s", "d_freeze_3s"],
            "training": {"mock": ["mock_lead_train.pkl", "mock_exit_train.pkl"], "real_sessions": every, "real_share": 0.15,
                         "reference_windows": ref["windows"]},
            "results": {"new_mock_after_walking": {k: res["mock_after_walking"]["none"][k] for k in ("caught_pct", "mean_lead_sec", "false_alarms_per_h")},
                        "new_mock_right_after_getting_up": {k: res["mock_right_after_standing"]["none"][k] for k in ("caught_pct", "mean_lead_sec", "false_alarms_per_h")},
                        "real_left_out": {p: {k: v["none"][k] for k in ("caught_pct", "mean_lead_sec", "control_alarm_pct")}
                                          for p, v in res["real_left_out"].items()}},
            "previous_version": "v2: personal 10 s baseline + walking-before gate (prefall_xgb_v2_8s.json)"}
    json.dump(meta, open(os.path.join(models, "prefall_xgb_meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved v3, threshold", round(thr, 3), json.dumps(meta["results"], ensure_ascii=False))


if __name__ == "__main__":
    main()
