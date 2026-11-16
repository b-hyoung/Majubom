"""Step 3 of the pre-fall mock-data work: measure what a fall (and look-alikes) looks like to our radar.

Sources (already in the repo as test data, 키 170cm, radar 2.5 m tilted 50°):
  * tests/data/fall_trials_20261012.json  slow2m / slowfar / bedside = lying down slowly on the floor, fast = fast sit-down
  * tests/data/rec_session_20261013.json  labelled: walking, crouch 1 s / 4 s, chair, slow lie on floor, fast sit-down on mat
For every descent of the followed person's body-centre height it records: standing height (ref), floor height, time from
80% to 25% of ref, fastest downward speed, horizontal shift during the drop, and height noise after landing.
Output: fall_moment_stats.json (read by the mock generator).
Usage: python fall_moment_stats.py
"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.path.join(HERE, "..", "..", "tests", "data"))
HIGH, LOW = 0.8, 0.25


def follow(frames, start_xy=None):
    """Greedy patient path: start on the tallest track with points, then keep the same tid or hand over to the nearest
    track within 1 m (the chip often splits a falling person into a new track). frames: [(t, [(tid,x,y,z,vz?,npts?)])]."""
    out, tid, last = [], None, start_xy
    for t, tracks in frames:
        if not tracks:
            continue
        cur = next((p for p in tracks if p[0] == tid), None)
        if cur is None:
            if last is None:
                cur = max(tracks, key=lambda p: p[3])
            else:
                near = [p for p in tracks if np.hypot(p[1] - last[0], p[2] - last[1]) < 1.0]
                if not near:
                    continue
                cur = min(near, key=lambda p: np.hypot(p[1] - last[0], p[2] - last[1]))
            tid = cur[0]
        last = (cur[1], cur[2])
        out.append((t, cur[1], cur[2], cur[3], cur[4] if len(cur) > 4 else None))
    return np.array([[r[0], r[1], r[2], r[3], np.nan if r[4] is None else r[4]] for r in out], dtype=float)


def per_track(frames):
    """Split frames into one series per tid, duplicate timestamps averaged, resampled to 10 Hz (gaps < 0.5 s)."""
    by = {}
    for t, tracks in frames:
        for q in tracks:
            by.setdefault(q[0], {}).setdefault(round(t, 3), []).append(
                [q[1], q[2], q[3], q[4] if len(q) > 4 and q[4] is not None else np.nan])
    out = {}
    for tid, d in by.items():
        if len(d) < 30:
            continue
        t = np.array(sorted(d))
        v = np.array([np.nanmean(np.array(d[k], dtype=float), axis=0) for k in t])
        g = np.arange(t[0], t[-1], 0.1)
        keep = np.array([np.min(np.abs(t - x)) < 0.5 for x in g])
        cols = [np.interp(g, t, v[:, i]) for i in range(3)] + [np.interp(g, t[np.isfinite(v[:, 3])], v[np.isfinite(v[:, 3]), 3]) if np.isfinite(v[:, 3]).sum() > 1 else np.full(len(g), np.nan)]
        out[tid] = np.column_stack([g] + cols)[keep]
    return out


def descents(p, name):
    t, x, y, z, vz = p.T
    zs = np.convolve(np.pad(z, (2, 2), mode="edge"), np.ones(5) / 5, mode="valid")
    found, i = [], 0
    while i < len(t):
        before = (t >= t[i] - 3) & (t <= t[i])
        ref = float(np.percentile(zs[before], 90)) if before.any() else zs[i]
        if ref < 1.0 or zs[i] > LOW * ref:
            i += 1
            continue
        # zs[i] is low: walk back to the last time it was >= 80% of ref
        j = i
        while j > 0 and zs[j] < HIGH * ref and t[i] - t[j] < 5:
            j -= 1
        if zs[j] < HIGH * ref:
            i += 1
            continue
        k = i
        while k < len(t) - 1 and zs[k + 1] <= LOW * ref * 1.6 and t[k + 1] - t[i] < 6:
            k += 1
        after = (t >= t[i]) & (t <= t[k])
        dz = np.diff(zs[j:i + 1]) / np.maximum(np.diff(t[j:i + 1]), 1e-3)
        vz_track = vz[j:i + 1]
        found.append({
            "source": name, "t": float(t[i]), "ref_m": ref, "floor_m": float(np.median(z[after])),
            "drop_sec": float(t[i] - t[j]), "dz_min_m_s": float(dz.min()) if len(dz) else None,
            "vz_track_min_m_s": float(np.nanmin(vz_track)) if np.isfinite(vz_track).any() else None,
            "shift_m": float(np.hypot(x[i] - x[j], y[i] - y[j])),
            "low_sec": float(t[k] - t[i]), "floor_noise_m": float(np.std(z[after])) if after.sum() > 2 else None})
        i = k + 1
    return found


def main():
    events, lookalikes = [], {}
    trials = json.load(open(os.path.join(DATA, "fall_trials_20261012.json"), encoding="utf-8"))["trials"]
    for name, frames in trials.items():
        if name == "posR2":
            continue
        for tid, p in per_track([(t, [tuple(q) for q in ps]) for t, ps in frames]).items():
            events += descents(p, f"10-12 {name} #{tid}")
    rec = json.load(open(os.path.join(DATA, "rec_session_20261013.json"), encoding="utf-8"))["frames"]
    labels = list(dict.fromkeys(l for _, l, _ in rec))
    for lab in labels:
        frames = [(t, [tuple(q[:5]) for q in ps]) for t, l, ps in rec if l == lab]
        tracks = per_track(frames)
        found = sum((descents(p, f"10-13 {lab} #{tid}") for tid, p in tracks.items()), [])
        p = max(tracks.values(), key=len)
        if lab.startswith(("7_", "8_")):
            events += found
        else:
            z = p[:, 3]
            lookalikes[lab] = {"events_found_by_rule": len(found), "z_p10": float(np.percentile(z, 10)),
                               "z_p90": float(np.percentile(z, 90)), "frames": int(len(z))}
    fast = [e for e in events if "fast" in e["source"] or "8_" in e["source"]]
    slow = [e for e in events if e not in fast]
    def summary(es, key):
        v = [e[key] for e in es if e[key] is not None]
        return {"n": len(v), "min": float(np.min(v)), "median": float(np.median(v)), "max": float(np.max(v))} if v else None
    keys = ["ref_m", "floor_m", "drop_sec", "dz_min_m_s", "vz_track_min_m_s", "shift_m", "floor_noise_m"]
    result = {"events": events, "lookalikes": lookalikes,
              "summary": {"fast": {k: summary(fast, k) for k in keys}, "slow": {k: summary(slow, k) for k in keys}}}
    # Values measured earlier and written in fall_detect.py (same 10-12/10-13 trials; some descents split across two chip
    # tracks, which this per-track pass cannot join, so the generator also uses these ranges).
    result["documented_fall_detect"] = {
        "standing_center_m": [1.25, 1.60], "floor_center_m": [-0.20, 0.28], "edge_sit_center_m": 0.6, "kneel_center_m": 0.43,
        "drop_80_to_25_sec": {"fast": 0.88, "slow": [1.26, 3.07]},
        "vz_min_m_s": {"slow_sit": -0.85, "squat": [-1.25, -1.2], "fast_sit_mat": -1.73, "fast_sit_10_12": -1.6},
        "shift_during_fall_m": [0.5, 1.0]}
    json.dump(result, open(os.path.join(HERE, "fall_moment_stats.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for e in events:
        print(f"{e['source']:28s} ref {e['ref_m']:.2f} floor {e['floor_m']:.2f} drop {e['drop_sec']:.2f}s "
              f"dz/dt {e['dz_min_m_s']:.2f} vz {e['vz_track_min_m_s']} shift {e['shift_m']:.2f} low {e['low_sec']:.1f}s")
    print("look-alikes:", json.dumps(lookalikes, ensure_ascii=False))


if __name__ == "__main__":
    main()
