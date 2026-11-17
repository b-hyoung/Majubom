"""Does a patient's own fall history help the pre-fall model? (2026-11-17)

Simulates "this patient has fallen before and our sensor recorded it" with the acted rounds: each of lee and kim has
3 rounds (sessions). For each person p and each round r of p:
  test     = round r of p (unsteady_fall + freeze_collapse trials, and the look-alike control trials)
  history  = the other 2 rounds of p (the "recorded past falls")
Conditions (all v3 setup: fixed reference walking without p, no walking gate, label = drop within 8 s, XGBoost,
mean of 3 windows, threshold for <= 2 false alarms/h on the validation mock):
  none        no history: mock without any of p's rounds + the other person's real trials + real walking (= v3 left out)
  hist_real   + p's 2 history rounds as real training trials
  hist_mock   + p's 2 history rounds as real trials + personal mock built only from those 2 rounds
              (mock_generator --profile with every other session excluded; walk-first and right-after-getting-up
              variants, 20 people x 4 per kind each). Two generator seeds to see how much the mock draw matters.
Round r never enters the training data, the personal mock or the reference walking of its own test.
Note: the validation mock used for the threshold (mock_lead_val / mock_exit_val) was drawn from all six rounds, the
same as in prefall_popbase.py; it only sets the alarm level and is the same for every condition.
Results (2026-11-17, falls caught / control trials with an alarm, summed over the 3 test rounds):
  kim  none 5/12 (2.3 s ahead), 2/12 | hist_real 8/12 (1.7 s), 3/12 | hist_mock 7-8/12 (2.2-2.4 s), 2-3/12
  lee  none 10/11 (3.8 s), 2/12      | hist_real 9/11 (4.8 s), 2/12 | hist_mock 9/11 (4.5-4.7 s), 2/12
  "none" reproduces prefall_popbase.py (v3 left out: kim 41.6%, lee 90%, controls 16.7%).
Usage: python prefall_history.py   → prefall_history_result.json
"""
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prefall_cross_person as X  # noqa: E402
import prefall_final as F  # noqa: E402
import prefall_lead as L  # noqa: E402
import prefall_popbase as B  # noqa: E402

HORIZON = 8.0
GATE = "none"
SEEDS = (0, 1)
PROFILE = os.path.join(HERE, "real_scenario_profiles.json")


def gen_personal(pkl, keep, every, seed, exit_start):
    if os.path.exists(os.path.join(HERE, pkl)):
        return
    cmd = [sys.executable, os.path.join(HERE, "mock_generator.py"), "--people", "20", "--per-kind", "4", "--seed", str(seed),
           "--profile", PROFILE, "--out", pkl, "--mix", "--skip-kind", F.SKIP]
    if exit_start:
        cmd += ["--lead-walk", "0", "3", "--start-stand"]
    for s in every:
        if s not in keep:
            cmd += ["--exclude-session", s]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def counts(eps, probs, thr):
    """Per-trial outcome on one test round."""
    out = {"falls": 0, "caught": 0, "leads": [], "early": 0, "controls": 0, "control_alarms": 0, "by_kind": {}}
    for e, p in zip(eps, probs):
        h = L.alarm(e, p, thr, 3)
        if e["kind"] in F.FALLS:
            ok = h & (e["ends"] >= e["warn"]) & (e["ends"] < e["drop"])
            k = out["by_kind"].setdefault(e["kind"], [0, 0])
            out["falls"] += 1; k[1] += 1
            out["early"] += int((h & (e["ends"] < e["warn"])).any())
            if ok.any():
                out["caught"] += 1; k[0] += 1
                out["leads"].append(round(float(e["drop"] - e["ends"][ok][0]), 2))
        else:
            out["controls"] += 1
            out["control_alarms"] += int(h.any())
    return out


def add(a, b):
    if not a:
        a.update(json.loads(json.dumps(b)))
        return a
    for k in ("falls", "caught", "early", "controls", "control_alarms"):
        a[k] += b[k]
    a["leads"] += b["leads"]
    for kind, (c, n) in b["by_kind"].items():
        x = a["by_kind"].setdefault(kind, [0, 0]); x[0] += c; x[1] += n
    return a


def summary(c):
    return {"caught": f"{c['caught']}/{c['falls']}", "caught_pct": round(100 * c["caught"] / max(c["falls"], 1), 1),
            "mean_lead_sec": round(float(np.mean(c["leads"])), 2) if c["leads"] else None,
            "by_kind": {k: f"{v[0]}/{v[1]}" for k, v in c["by_kind"].items()},
            "control_alarms": f"{c['control_alarms']}/{c['controls']}", "alarm_before_warning": f"{c['early']}/{c['falls']}"}


def fit_and_score(mock, real, W, va, test):
    m = L.train(mock, real, W, ("h", HORIZON))
    thr = L.pick(va, [m.predict_proba(e["X"])[:, 1] for e in va], 3, 0.5)
    return thr, counts(test, [m.predict_proba(e["X"])[:, 1] for e in test], thr)


def main():
    ss = X.scenario_sessions()
    every = [s for v in ss.values() for s in v]
    result = {"sessions": ss, "conditions": ["none", "hist_real"] + [f"hist_mock_s{s}" for s in SEEDS], "rounds": {}, "per_person": {}}
    for p in sorted(ss):
        refp = B.reference(exclude=p)
        other = [s for q, v in ss.items() if q != p for s in v]
        base_mock = B.mock(f"mock_lead_not_{p}.pkl", refp, f"not{p}") + B.mock(f"mock_exit_not_{p}.pkl", refp, f"not{p}")
        R_other, W = B.real(other, refp), B.walk_X(refp, p)
        va = B.gated(B.mock("mock_lead_val.pkl", refp, f"not{p}") + B.mock("mock_exit_val.pkl", refp, f"not{p}"), GATE)
        own = {s: B.gated(B.real([s], refp), GATE) for s in ss[p]}
        # "none" does not depend on the round: one model, scored on every round
        m0 = L.train(base_mock, R_other, W, ("h", HORIZON))
        thr0 = L.pick(va, [m0.predict_proba(e["X"])[:, 1] for e in va], 3, 0.5)
        tot = {c: {} for c in result["conditions"]}
        for r, test_s in enumerate(ss[p], 1):
            hist = [s for s in ss[p] if s != test_s]
            test = own[test_s]
            row = {"test_session": test_s, "history_sessions": hist}
            c0 = counts(test, [m0.predict_proba(e["X"])[:, 1] for e in test], thr0)
            row["none"] = {"threshold": round(thr0, 3), **summary(c0)}; add(tot["none"], c0)
            R_hist = B.real(hist, refp)
            thr, c = fit_and_score(base_mock, R_other + R_hist, W, va, test)
            row["hist_real"] = {"threshold": round(thr, 3), **summary(c)}; add(tot["hist_real"], c)
            for seed in SEEDS:
                tag = f"hist_{p}_r{r}_s{seed}"
                gen_personal(f"mock_{tag}_lead.pkl", hist, every, 9800 + 10 * r + seed, False)
                gen_personal(f"mock_{tag}_exit.pkl", hist, every, 9900 + 10 * r + seed, True)
                pm = B.mock(f"mock_{tag}_lead.pkl", refp, f"not{p}") + B.mock(f"mock_{tag}_exit.pkl", refp, f"not{p}")
                thr, c = fit_and_score(base_mock + pm, R_other + R_hist, W, va, test)
                name = f"hist_mock_s{seed}"
                row[name] = {"threshold": round(thr, 3), **summary(c)}; add(tot[name], c)
            result["rounds"][f"{p}_r{r}"] = row
            print(f"[{p} round {r} = {test_s}]", flush=True)
            for cname in result["conditions"]:
                v = row[cname]
                print(f"   {cname:13s} thr {v['threshold']:.2f} caught {v['caught']:5s} lead {v['mean_lead_sec']}  "
                      f"{v['by_kind']}  control alarms {v['control_alarms']}  early {v['alarm_before_warning']}", flush=True)
            json.dump(result, open(os.path.join(HERE, "prefall_history_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        result["per_person"][p] = {c: summary(v) for c, v in tot.items()}
        print(f"== {p} total ==", flush=True)
        for cname, v in result["per_person"][p].items():
            print(f"   {cname:13s} caught {v['caught']:6s} ({v['caught_pct']}%) lead {v['mean_lead_sec']}  {v['by_kind']}  "
                  f"control alarms {v['control_alarms']}  early {v['alarm_before_warning']}", flush=True)
        json.dump(result, open(os.path.join(HERE, "prefall_history_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
