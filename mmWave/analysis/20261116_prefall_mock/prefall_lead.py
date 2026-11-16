"""Can the pre-fall warning come earlier? Compare training targets and window settings (2026-11-16).

Settings (XGBoost, same features as the deployed model, same gates: upright + walking 1.5-4 s before):
  base   3 s window, every 0.5 s, label = drop within 3 s, alarm = mean of 3 windows   (deployed model)
  A5     same, label = drop within 5 s
  A8     same, label = drop within 8 s
  B      same, label = the whole warning behaviour (from its start until the drop)
  C      2 s window, every 0.25 s, label = drop within 3 s, alarm = mean of 2 windows
Scoring is the same for all: a fall episode is caught if an alarm comes after the warning behaviour starts and before
the drop; lead = drop - first such alarm. An alarm before the warning starts is a false alarm. False alarms per hour are
counted over all non-fall time (control episodes and fall episodes before the warning). Each setting's threshold is the
lowest giving at most 2 false alarms per hour on the validation mock set.
Tested on (1) a new mock test set (40 new virtual people) and (2) real trials of a person left out of training
(mock made only from the other person's trials). Trip falls have no warning and are not counted as catches.
Usage: python prefall_lead.py   → prefall_lead_result.json
"""
import json
import os
import pickle
import subprocess
import sys

import numpy as np
from sklearn.metrics import roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prefall_cross_person as X  # noqa: E402
import prefall_eval as P  # noqa: E402
import prefall_final as F  # noqa: E402
import prefall_improve as I  # noqa: E402
import real_check as R  # noqa: E402
import real_scenario_profiles as RS  # noqa: E402
import normal_gait_stats as N  # noqa: E402

SETTINGS = {"base": (3, 0.5, 3, ("h", 3.0)), "A5": (3, 0.5, 3, ("h", 5.0)), "A8": (3, 0.5, 3, ("h", 8.0)),
            "B": (3, 0.5, 3, ("warn", None)), "C": (2, 0.25, 2, ("h", 3.0))}
FAR = 2.0


def window_feats(f, end, win):
    g = P.window_feats(f, end, win)
    return None if not g else {k: float(g[k]) for k in I.KEYS}


def baseline(f, win):
    rows = [window_feats(f, e, win) for e in np.arange(win, 10.0 + 1e-9, 1.0)]
    rows = [r for r in rows if r]
    return {k: float(np.median([r[k] for r in rows])) for k in P.BASE} if rows else None


def vec(g, base, p15, p30):
    v = [g["speed"] / max(base["speed"], 0.1), g["speed_cv"] / max(base["speed_cv"], 0.05), g["sway"] / max(base["sway"], 0.01),
         g["freeze_ratio"] - base["freeze_ratio"], g["stride_cv"] / max(base["stride_cv"], 0.03), g["straightness"], g["speed"]]
    for pg in (p15, p30):
        v += [g["speed"] - pg["speed"], g["speed_cv"] - pg["speed_cv"], g["sway"] - pg["sway"],
              g["freeze_ratio"] - pg["freeze_ratio"]] if pg else [0.0] * 4
    return v


def windows(f, win, step, stop):
    base = baseline(f, win)
    if base is None:
        return None
    walk_z = float(np.median(f[(f[:, 0] >= 1) & (f[:, 0] <= 10), 3]))
    t = f[:, 0]
    rows, ends, Xs, gate = {}, [], [], []
    for end in np.arange(10.0 + win, stop, step):
        g = window_feats(f, end, win)
        if not g:
            continue
        key = round(end, 3)
        rows[key] = g
        Xs.append(vec(g, base, rows.get(round(end - 1.5, 3)), rows.get(round(end - 3.0, 3))))
        ends.append(end)
        up = np.median(f[(t > end - 0.5) & (t <= end), 3]) >= F.UPRIGHT * walk_z
        past = f[(t > end - 4.0) & (t <= end - 1.5), 4]
        gate.append(bool(up and len(past) and np.median(past) >= 0.25))
    if len(ends) < 3:
        return None
    return np.array(Xs), np.array(ends), np.array(gate)


def mock_eps(pkl, win, step):
    path = os.path.join(HERE, f"lead_cache_{win}_{step}_{pkl}")
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    data = pickle.load(open(os.path.join(HERE, pkl), "rb"))
    out = []
    for i, e in enumerate(data["episodes"]):
        if e["kind"] == F.SKIP:
            continue
        w = windows(e["frames"], win, step, e["drop_start"] if e["fall"] else e["frames"][-1, 0])
        if w:
            out.append({"id": i, "person": e["person"], "kind": e["kind"], "drop": e["drop_start"], "warn": e.get("warn_start"),
                        "X": w[0], "ends": w[1], "gate": w[2]})
    pickle.dump(out, open(path, "wb"))
    return out


def real_eps(sessions, win, step):
    out = []
    for s in sessions:
        cues = RS.load_cues(os.path.join(R.os.path.expanduser(r"~\Downloads\mmw_viewer\sessions"), s))
        for tr in R.load_trials(os.path.join(os.path.expanduser(r"~\Downloads\mmw_viewer\sessions"), s)):
            if tr["kind"] == F.SKIP:
                continue
            f = tr["frames"]
            drop = R.drop_start(f, tr["cue_mat"]) if tr["kind"] in F.FALLS else None
            if tr["kind"] in F.FALLS and drop is None:
                continue
            warn = None
            if drop is not None:
                c = cues[tr["trial"]]
                warn = next(t for lab, t in c if lab == RS.WARN_CUE[tr["kind"]]) - c[0][1]
            w = windows(f, win, step, drop if drop else f[-1, 0])
            if w:
                out.append({"session": s, "trial": tr["trial"], "kind": tr["kind"], "drop": drop, "warn": warn,
                            "X": w[0], "ends": w[1], "gate": w[2]})
    return out


def walking_X(win, step, exclude=None):
    Xs = []
    for person, ses, ranges, rel in N.RECORDINGS:
        if person == exclude:
            continue
        for lab, v in N.walker_tracks(ses, ranges, rel):
            f = np.column_stack([v[:, 0] - v[0, 0], v[:, 1], v[:, 2], v[:, 3], v[:, 4], v[:, 5]])
            w = windows(f, win, step, f[-1, 0])
            if w:
                Xs.append(w[0])
    return np.vstack(Xs)


def labels(e, target):
    if e["kind"] not in F.FALLS:
        return np.zeros(len(e["ends"]), int)
    if target[0] == "h":
        return (e["drop"] - e["ends"] <= target[1]).astype(int)
    return (e["ends"] >= e["warn"]).astype(int)


def alarm(e, p, thr, k):
    m = np.array([p[max(0, i - k + 1):i + 1].mean() if i >= k - 1 else 0.0 for i in range(len(p))])
    return (m >= thr) & e["gate"]


def nonfall_mask(e):
    return np.ones(len(e["ends"]), bool) if e["drop"] is None else e["ends"] < e["warn"]


def far(eps, probs, thr, k, step):
    fa, t = 0, 0.0
    for e, p in zip(eps, probs):
        h, m = alarm(e, p, thr, k), nonfall_mask(e)
        t += m.sum() * step
        last = -1e9
        for end in e["ends"][h & m]:
            if end - last > 10.0:
                fa += 1
            last = end
    return fa / (t / 3600)


def pick(eps, probs, k, step):
    for thr in np.linspace(0.05, 0.99, 95):
        if far(eps, probs, thr, k, step) <= FAR:
            return float(thr)
    return 0.99


def score(eps, probs, thr, k, step):
    res = {"threshold": round(thr, 3), "false_alarms_per_h": round(far(eps, probs, thr, k, step), 2)}
    for kind in F.FALLS + ("trip_fall",):
        leads, early, n = [], 0, 0
        for e, p in zip(eps, probs):
            if e["kind"] != kind:
                continue
            n += 1
            h = alarm(e, p, thr, k)
            ok = h & (e["ends"] >= e["warn"]) & (e["ends"] < e["drop"])
            early += bool((h & (e["ends"] < e["warn"])).any())
            if ok.any():
                leads.append(float(e["drop"] - e["ends"][ok][0]))
        if n:
            res[kind] = {"n": n, "caught_pct": round(100 * len(leads) / n, 1),
                         "mean_lead_sec": round(float(np.mean(leads)), 2) if leads else None,
                         "median_lead_sec": round(float(np.median(leads)), 2) if leads else None,
                         "alarm_before_warning_pct": round(100 * early / n, 1)}
    ctrl = [(e, p) for e, p in zip(eps, probs) if e["kind"] not in F.FALLS + ("trip_fall",)]
    res["control_alarm_pct"] = round(100 * float(np.mean([alarm(e, p, thr, k).any() for e, p in ctrl])), 1) if ctrl else None
    falls = [res[k_] for k_ in F.FALLS if k_ in res]
    res["caught_pct"] = round(float(np.mean([f["caught_pct"] for f in falls])), 1)
    leads = [f["mean_lead_sec"] for f in falls if f["mean_lead_sec"] is not None]
    res["mean_lead_sec"] = round(float(np.mean(leads)), 2) if leads else None
    return res


def train(mock, real, W, target):
    Xm = np.vstack([e["X"] for e in mock]); ym = np.concatenate([labels(e, target) for e in mock])
    Xr = np.vstack([e["X"] for e in real] + [W]); yr = np.concatenate([labels(e, target) for e in real] + [np.zeros(len(W), int)])
    wr = F.REAL_SHARE / (1 - F.REAL_SHARE) * len(ym) / len(yr)
    return I.make("xgboost").fit(np.vstack([Xm, Xr]), np.concatenate([ym, yr]),
                                 sample_weight=np.concatenate([np.ones(len(ym)), np.full(len(yr), wr)]))


def gen(pkl, exclude, seed):
    if not os.path.exists(os.path.join(HERE, pkl)):
        cmd = [sys.executable, os.path.join(HERE, "mock_generator.py"), "--people", "40", "--per-kind", "4", "--seed", str(seed),
               "--profile", os.path.join(HERE, "real_scenario_profiles.json"), "--out", pkl, "--mix", "--skip-kind", F.SKIP]
        for s in exclude:
            cmd += ["--exclude-session", s]
        subprocess.run(cmd, check=True)


def main():
    ss = X.scenario_sessions()
    allr = [s for v in ss.values() for s in v]
    result = {"settings": {k: {"window_s": v[0], "step_s": v[1], "mean_of": v[2], "label": v[3]} for k, v in SETTINGS.items()},
              "mock_test": {}, "real_left_out": {}}
    for i, person in enumerate(sorted(ss)):
        gen(f"mock_lead_not_{person}.pkl", ss[person], 9400 + i)
    for name, (win, step, k, target) in SETTINGS.items():
        tr, va, te = (mock_eps(p, win, step) for p in ("mock_lead_train.pkl", "mock_lead_val.pkl", "mock_lead_test.pkl"))
        model = train(tr, real_eps(allr, win, step), walking_X(win, step), target)
        thr = pick(va, [model.predict_proba(e["X"])[:, 1] for e in va], k, step)
        probs = [model.predict_proba(e["X"])[:, 1] for e in te]
        r = score(te, probs, thr, k, step)
        y = np.concatenate([labels(e, target) for e in te]); r["auc"] = round(float(roc_auc_score(y, np.concatenate(probs))), 3)
        result["mock_test"][name] = r
        print(f"[mock test] {name:4s}", json.dumps({x: r[x] for x in ("caught_pct", "mean_lead_sec", "false_alarms_per_h", "control_alarm_pct", "threshold")}),
              {f: (r[f]["caught_pct"], r[f]["mean_lead_sec"], r[f]["alarm_before_warning_pct"]) for f in F.FALLS}, flush=True)
        real_rows = {}
        for person in sorted(ss):
            m_not = mock_eps(f"mock_lead_not_{person}.pkl", win, step)
            other = [s for p, v in ss.items() if p != person for s in v]
            model_p = train(m_not, real_eps(other, win, step), walking_X(win, step, person), target)
            vthr = pick(va, [model_p.predict_proba(e["X"])[:, 1] for e in va], k, step)
            own = real_eps(ss[person], win, step)
            rr = score(own, [model_p.predict_proba(e["X"])[:, 1] for e in own], vthr, k, step)
            real_rows[person] = rr
            print(f"   [real, {person} left out] {name:4s}", json.dumps({x: rr[x] for x in ("caught_pct", "mean_lead_sec", "control_alarm_pct")}),
                  {f: (rr[f]["caught_pct"], rr[f]["mean_lead_sec"], rr[f]["alarm_before_warning_pct"]) for f in F.FALLS}, flush=True)
        result["real_left_out"][name] = real_rows
        json.dump(result, open(os.path.join(HERE, "prefall_lead_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
