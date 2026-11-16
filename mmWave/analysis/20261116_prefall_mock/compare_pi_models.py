"""Head-to-head on lee/kim acted trials: the Pi's room-walking fall forecast (port 5099, another session's experiment in
~/.local/share/bed-fall-mmwave-experiment, not in git) vs the new pre-fall model v2 (2026-11-16).

Pi model: logistic regression on 9 features of the last 3 s of the track (speed, speed change, lateral spread,
direction change, path efficiency, height drop vs. the last few seconds, height spread, downward speed, lateral gain),
trained on lee's rounds (v2: rounds 1-2, v3: + round 3 and two observed falls) with positives 5-10 s before the
detector's first low height; alert = score >= threshold for 0.38 s, re-alert needs 8 s and a fresh rise.
Its code is loaded from a local copy (--pi-code, copied from the Pi; not part of this repo).
New model: the same leave-one-person-out setup as prefall_lead.py, setting A8 (= deployed v2).
Same scoring for both: a fall trial is caught if the first alert after the warning cue comes before the drop start;
lead = drop - that alert; an alert before the warning cue is an early (false) alert; control trials with any alert.
Usage: python compare_pi_models.py --pi-code <folder with walk_refine.py, walk-general-v2/, walk-general-v3/>
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prefall_cross_person as X  # noqa: E402
import prefall_lead as L  # noqa: E402
import real_check as R  # noqa: E402
import real_scenario_profiles as RS  # noqa: E402

SES = os.path.expanduser(r"~\Downloads\mmw_viewer\sessions")
FALLS = ("unsteady_fall", "freeze_collapse")


def trials(sessions):
    out = []
    for s in sessions:
        cues = RS.load_cues(os.path.join(SES, s))
        for tr in R.load_trials(os.path.join(SES, s)):
            if tr["kind"] == "dizzy_collapse":
                continue
            f = tr["frames"]
            drop = R.drop_start(f, tr["cue_mat"]) if tr["kind"] in FALLS else None
            if tr["kind"] in FALLS and drop is None:
                continue
            warn = None
            if drop is not None:
                c = cues[tr["trial"]]
                warn = next(t for lab, t in c if lab == RS.WARN_CUE[tr["kind"]]) - c[0][1]
            out.append({"session": s, "trial": tr["trial"], "kind": tr["kind"], "frames": f, "drop": drop, "warn": warn})
    return out


def outcome(t, alerts):
    if t["drop"] is None:
        return {"false_alarm": bool(alerts)}
    ok = [a for a in alerts if t["warn"] <= a < t["drop"]]
    return {"caught": bool(ok), "lead_sec": round(t["drop"] - ok[0], 2) if ok else None,
            "early_alarm": any(a < t["warn"] for a in alerts)}


LIVE_PERIOD = 0.26   # the Pi model reads the server's live snapshot: ~3.9 new track samples per second (its clips)


def pi_alerts(W, model, t):
    stop = t["drop"] if t["drop"] is not None else t["frames"][-1, 0]
    rows, next_t = [], -1.0
    for r in t["frames"]:
        if r[0] >= stop:
            break
        if r[0] >= next_t:          # resample the 18 Hz track to the live snapshot rate the model was built on
            rows.append((r[0], {"id": 0, "stamp": float(r[0]), "xyz": [round(float(r[1]), 3), round(float(r[2]), 3), round(float(r[3]), 3)]}))
            next_t = r[0] + LIVE_PERIOD
    records = list(W.extract(rows, W.RelativeWalkingWindow(model["feature_set"])))
    policy = W.RiskRise(**model.get("policy_config", {}))
    alerts = []
    for at, tid, v in records:
        s = None if v is None else W.score(v, model)
        if policy.step(at, tid, s, model["threshold"]):
            alerts.append(at)
    return alerts


def new_model_alerts(model, thr, t):
    e = L.windows(t["frames"], 3, 0.5, t["drop"] if t["drop"] is not None else t["frames"][-1, 0])
    if not e:
        return []
    Xs, ends, gate = e
    p = model.predict_proba(Xs)[:, 1]
    hit = L.alarm({"gate": gate}, p, thr, 3)
    return list(ends[hit])


def summary(rows):
    falls = [r for r in rows if "caught" in r]
    ctrl = [r for r in rows if "false_alarm" in r]
    leads = [r["lead_sec"] for r in falls if r["lead_sec"] is not None]
    return {"falls_caught": f"{sum(r['caught'] for r in falls)}/{len(falls)}",
            "by_kind": {k: f"{sum(r['caught'] for r in falls if r['kind'] == k)}/{sum(r['kind'] == k for r in falls)}" for k in FALLS},
            "mean_lead_sec": round(float(np.mean(leads)), 2) if leads else None,
            "early_alarm": f"{sum(r['early_alarm'] for r in falls)}/{len(falls)}",
            "control_alarm": f"{sum(r['false_alarm'] for r in ctrl)}/{len(ctrl)}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi-code", required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.pi_code)
    import walk_refine as W
    pi = {v: json.load(open(os.path.join(a.pi_code, f"walk-general-{v}", "model.json"), encoding="utf-8")) for v in ("v2", "v3")}
    ss = X.scenario_sessions()
    lee_r3 = [ss["lee"][2]]                      # lee round 3: not used to train the Pi's v2
    sets = {"kim (all 3 rounds)": ss["kim"], "lee round 3": lee_r3}
    result = {"sets": sets, "results": {}}
    new = {}
    for person in ("kim", "lee"):
        m_not = L.mock_eps(f"mock_lead_not_{person}.pkl", 3, 0.5)
        other = [s for p, v in ss.items() if p != person for s in v]
        model = L.train(m_not, L.real_eps(other, 3, 0.5), L.walking_X(3, 0.5, person), ("h", 8.0))
        va = L.mock_eps("mock_lead_val.pkl", 3, 0.5)
        new[person] = (model, L.pick(va, [model.predict_proba(e["X"])[:, 1] for e in va], 3, 0.5))
    for name, sessions in sets.items():
        ts = trials(sessions)
        person = "kim" if name.startswith("kim") else "lee"
        res = {}
        for v in ("v2", "v3"):
            if v == "v3" and person == "lee":
                continue                          # v3 was trained on lee round 3
            res[f"Pi walking model {v}"] = summary([{**outcome(t, pi_alerts(W, pi[v], t)), "kind": t["kind"]} for t in ts])
        model, thr = new[person]
        res["new model v2 (person left out)"] = summary([{**outcome(t, new_model_alerts(model, thr, t)), "kind": t["kind"]} for t in ts])
        result["results"][name] = res
        for k, v in res.items():
            print(f"{name:20s} {k:32s} {json.dumps(v, ensure_ascii=False)}", flush=True)
    json.dump(result, open(os.path.join(HERE, "compare_pi_models_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
