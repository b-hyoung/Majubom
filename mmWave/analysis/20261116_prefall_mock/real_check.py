"""Step 7 (after recording): score real pre-fall scenario recordings with models trained only on mock data.

Input: viewer sessions recorded with tools/Record-FallScenario.ps1 (marks.json holds trial number and kind per cue).
For each trial: follow the walker, take the first 10 s of walking as the personal baseline, find the drop start in
fall trials (last time the body centre was >= 80% of walking height before it fell below 25%), then slide 3 s / 6 s
windows that end before the drop. Models are trained on all mock episodes with the same settings as prefall_eval.py.
Output per trial: alarm yes/no, lead time before the drop, and false alarms in control trials.
Usage: python real_check.py <session_dir> [<session_dir> ...]   → real_check_result.json
"""
import datetime as dt
import json
import os
import pickle
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prefall_eval as P  # noqa: E402

FALL_KINDS = ("unsteady_fall", "freeze_collapse", "dizzy_collapse")


def load_trials(ses):
    S = json.load(open(os.path.join(ses, "session.json"), encoding="utf-8"))
    M = json.load(open(os.path.join(ses, "marks.json"), encoding="utf-8-sig"))
    day = dt.datetime.fromtimestamp(S["started_at"]).date()
    ts = lambda h: dt.datetime.combine(day, dt.datetime.strptime(h, "%H:%M:%S.%f").time()).timestamp()
    marks = [(m["trial"], m["kind"], m["label"], ts(m["time"])) for m in M["marks"]]
    frames = [json.loads(l) for l in open(os.path.join(ses, "frames.jsonl"), encoding="utf-8")]
    trials = []
    for n in sorted({m[0] for m in marks if m[0] is not None}):
        cues = [m for m in marks if m[0] == n]
        a = cues[0][3]
        b = next((m[3] for m in marks if m[3] > cues[-1][3]), cues[-1][3] + 10)
        per = {}
        for f in frames:
            if a <= f["t"] < b:
                for tr in f.get("tracks") or []:
                    x, y, z = tr["world"]
                    per.setdefault(tr["tid"], []).append((f["t"], x, y, z, tr["speed"], tr["npts"]))
        if not per:
            continue
        def path(v):
            xy = np.array([[q[1], q[2]] for q in v])
            return np.linalg.norm(np.diff(xy, axis=0), axis=1).sum() if len(v) > 1 else 0.0
        v = np.array(sorted(max(per.values(), key=path)), dtype=float)
        v[:, 0] -= a
        trials.append({"trial": int(n), "kind": cues[0][1], "frames": v,
                       "cue_mat": next((m[3] - a for m in cues if m[2] == "매트앉기"), None)})
    return trials


def drop_start(f, cue):
    t, z = f[:, 0], f[:, 3]
    walk_z = float(np.median(z[(t >= 2) & (t <= 10)]))
    low = np.where((t >= (cue or 0)) & (z <= 0.25 * walk_z + 0.15))[0]
    if not len(low):
        return None
    i = low[0]
    while i > 0 and z[i] < 0.8 * walk_z:
        i -= 1
    return float(t[i])


def main():
    sessions = sys.argv[1:]
    data = pickle.load(open(os.path.join(HERE, "mock_episodes.pkl"), "rb"))
    out = {"sessions": [os.path.basename(s) for s in sessions], "windows": {}}
    trials = sum((load_trials(s) for s in sessions), [])
    for win in (3, 6):
        X, y, g, ep, tend, lead = P.build(data["episodes"], win)
        res = {}
        for name, mk in P.models().items():
            m = mk().fit(X, y)
            thr = P.pick_threshold(m.predict_proba(X)[:, 1], ep, lead)
            rows = []
            for tr in trials:
                f = tr["frames"]
                base = P.baseline(f, win)
                if base is None:
                    continue
                stop = drop_start(f, tr["cue_mat"]) if tr["kind"] in FALL_KINDS else f[-1, 0]
                if stop is None:
                    rows.append({"trial": tr["trial"], "kind": tr["kind"], "note": "drop not found"}); continue
                ends = np.arange(10.0 + win, stop, P.STEP)
                feats = [(e, P.window_feats(f, e, win)) for e in ends]
                feats = [(e, gf) for e, gf in feats if gf]
                if not feats:
                    rows.append({"trial": tr["trial"], "kind": tr["kind"], "note": "no windows"}); continue
                prob = m.predict_proba(np.array([P.vector(gf, base) for _, gf in feats]))[:, 1]
                hit = [i for i in range(1, len(prob)) if prob[i] >= thr and prob[i - 1] >= thr]
                if tr["kind"] in FALL_KINDS:
                    early = [stop - feats[i][0] for i in hit if stop - feats[i][0] <= P.HORIZON]
                    false = [i for i in hit if stop - feats[i][0] > P.HORIZON]
                    rows.append({"trial": tr["trial"], "kind": tr["kind"], "caught": bool(early),
                                 "lead_sec": round(max(early), 2) if early else None, "early_alarm_windows": len(false)})
                else:
                    rows.append({"trial": tr["trial"], "kind": tr["kind"], "false_alarm_windows": len(hit), "windows": len(prob)})
            res[name] = {"threshold": round(thr, 2), "trials": rows}
            falls = [r for r in rows if r["kind"] in FALL_KINDS and "caught" in r]
            ctrl = [r for r in rows if r["kind"] not in FALL_KINDS and "windows" in r]
            print(f"[{win}s {name}] falls caught {sum(r['caught'] for r in falls)}/{len(falls)} | "
                  f"control trials with an alarm {sum(r['false_alarm_windows'] > 0 for r in ctrl)}/{len(ctrl)}")
        out["windows"][f"{win}s"] = res
    json.dump(out, open(os.path.join(HERE, "real_check_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
