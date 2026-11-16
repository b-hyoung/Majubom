"""Step 2 of the pre-fall mock-data work: measure how our radar sees normal walking.

Reads the lee/kim gait recordings (viewer sessions, 11-05 and 11-12) and writes normal_gait_stats.json:
  * tracker behaviour per frame: frame interval, speed oscillation (cadence), position jitter around the
    1 s smoothed path, body-centre height, points per frame, dropout runs, straight-run length between turns
  * gait features per window (3 s and 6 s, step 1 s) through the reader's own code (send_mmw).
The mock generator (step 5) draws its parameters from this file.
Usage: python normal_gait_stats.py
"""
import collections
import datetime as dt
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import send_mmw as M  # noqa: E402

SES = os.path.expanduser(r"~\Downloads\mmw_viewer\sessions")
LAB = {"평소 걷기": "normal", "천천히 걷기": "slow", "종종걸음": "shuffle", "평소": "normal", "천천히": "slow"}


def marks_ranges(ses):
    mk = json.load(open(os.path.join(SES, ses, "marks.json"), encoding="utf-8-sig"))
    day = ses.split("_")[1]
    ts = lambda h: dt.datetime.strptime(day + " " + h, "%Y%m%d %H:%M:%S.%f").timestamp()
    seg = sorted((ts(v), k) for k, v in mk["marks"].items())
    return [(LAB[k], a + 3, b) for (a, k), (b, _) in zip(seg, seg[1:]) if k in LAB], False


RECORDINGS = [  # (person, session, ranges, ranges are relative to session start)
    ("lee", "S3_20261105_121146", [("normal", 76.5, 138.5), ("slow", 163.3, 225.3)], True),
    ("lee", "S3_20261105_124521", [("shuffle", 9.1, 93.2)], True),
    ("lee", "TEST_20261112_164724", *marks_ranges("TEST_20261112_164724")),
    ("lee", "TEST_20261112_165534", *marks_ranges("TEST_20261112_165534")),
    ("lee", "TEST_20261112_173849", *marks_ranges("TEST_20261112_173849")),
    ("kim", "TEST_20261112_175646", *marks_ranges("TEST_20261112_175646")),
    ("lee", "TEST_20261116_151728", *marks_ranges("TEST_20261116_151728")),   # 2 min each, recorded for the pre-fall work
    ("kim", "TEST_20261116_155829", *marks_ranges("TEST_20261116_155829")),
]


def walker_tracks(ses, ranges, rel):
    """For each labelled range, the frames of the track that travelled furthest (the walker)."""
    start = json.load(open(os.path.join(SES, ses, "session.json"), encoding="utf-8"))["started_at"]
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for line in open(os.path.join(SES, ses, "frames.jsonl"), encoding="utf-8"):
        f = json.loads(line)
        t = f["t"] - (start if rel else 0)
        for lab, a, b in ranges:
            if a <= t < b:
                for tr in f.get("tracks") or []:
                    x, y, z = tr["world"]
                    per[lab][tr["tid"]].append((f["t"], x, y, z, tr["speed"], tr["npts"], tr.get("head_z")))
    out = []
    for lab, tracks in per.items():
        def path(v):
            xy = np.array([[q[1], q[2]] for q in v])
            return np.linalg.norm(np.diff(xy, axis=0), axis=1).sum() if len(v) > 1 else 0.0
        out.append((lab, np.array(sorted(tracks[max(tracks, key=lambda k: path(sorted(tracks[k])))]), dtype=float)))
    return out


def runs(mask):
    lengths, n = [], 0
    for m in mask:
        if m:
            n += 1
        elif n:
            lengths.append(n); n = 0
    if n:
        lengths.append(n)
    return lengths


def smooth(a, k):
    pad = np.pad(a, ((k // 2, k // 2),) + ((0, 0),) * (a.ndim - 1), mode="edge")
    if a.ndim == 1:
        return np.convolve(pad, np.ones(k) / k, mode="valid")
    return np.column_stack([np.convolve(pad[:, i], np.ones(k) / k, mode="valid") for i in range(a.shape[1])])


def frame_stats(v):
    t, x, y, z, sp, npts = (v[:, i] for i in range(6))
    dtv = np.diff(t)
    fs = 1.0 / np.median(dtv)
    k = int(round(fs)) | 1
    xy = np.column_stack([x, y])
    sm = smooth(xy, k)
    tg = np.gradient(sm, axis=0)
    tg /= np.maximum(np.linalg.norm(tg, axis=1, keepdims=True), 1e-9)
    d = xy - sm
    lateral = d[:, 0] * tg[:, 1] - d[:, 1] * tg[:, 0]
    along = d[:, 0] * tg[:, 0] + d[:, 1] * tg[:, 1]
    # cadence: dominant 0.5-3 Hz frequency of the tracker speed
    sig = sp - sp.mean()
    fr = np.fft.rfftfreq(len(sig), 1 / fs)
    mag = np.abs(np.fft.rfft(sig))
    band = (fr >= 0.5) & (fr <= 3.0)
    cadence = float(fr[band][np.argmax(mag[band])])
    osc = sp - smooth(sp, k)
    # straight runs: time between heading changes > 60 degrees (1 s smoothed path)
    head = np.unwrap(np.arctan2(tg[:, 1], tg[:, 0]))
    turns, last = [], 0
    for i in range(1, len(head)):
        if abs(head[i] - head[last]) > np.radians(60):
            turns.append(t[i] - t[last]); last = i
    zero = npts == 0
    return {
        "frames": int(len(t)), "fs_hz": float(fs), "gap_over_0_2s": int((dtv > 0.2).sum()),
        "speed_mean": float(sp.mean()), "speed_std": float(sp.std()), "speed_osc_std": float(osc.std()),
        "cadence_hz": cadence,
        "jitter_lateral_std": float(lateral.std()), "jitter_along_std": float(along.std()),
        "z_mean": float(z.mean()), "z_std": float(z.std()), "z_osc_std": float((z - smooth(z, k)).std()),
        "npts_mean": float(npts.mean()), "npts_zero_frac": float(zero.mean()),
        "dropout_runs_frames": runs(zero),
        "straight_run_sec": [float(s) for s in turns],
    }


def window_features(v, win, step=1.0):
    t = v[:, 0]
    out = []
    s = t[0]
    while s + win <= t[-1]:
        idx = np.where((t >= s) & (t < s + win))[0]
        if len(idx) >= 10:
            p = v[idx]
            samples = [(q[0], q[1], q[2], q[3], q[4], 0.0) for q in p]   # same as replay: tracker speed as vx
            g = M._gait_from_samples(samples)
            xy = p[:, 1:3]
            g["straightness"] = M.straightness_by_part(p[:, 0], xy)
            g["disp"] = float(np.hypot(*(xy[-1] - xy[0])))
            g["walking"] = bool(g["speed"] >= M.WALK_SPEED_MIN and g["disp"] >= M.WALK_DISP_MIN and g["straightness"] >= M.STRAIGHT_MIN)
            out.append(g)
        s += step
    return out


def summarize(values):
    a = np.array(values, dtype=float)
    if not len(a):
        return None
    return {"n": int(len(a)), "mean": float(a.mean()), "std": float(a.std()), "p10": float(np.percentile(a, 10)),
            "p50": float(np.median(a)), "p90": float(np.percentile(a, 90))}


def main():
    frames = collections.defaultdict(list)
    windows = {3: collections.defaultdict(list), 6: collections.defaultdict(list)}
    for person, ses, ranges, rel in RECORDINGS:
        for lab, v in walker_tracks(ses, ranges, rel):
            if len(v) < 50:
                continue
            frames[(person, lab)].append(frame_stats(v))
            for win in windows:
                windows[win][(person, lab)] += window_features(v, win)
    result = {"recordings": [r[:2] for r in RECORDINGS], "frame": {}, "window": {}}
    scalar = ["fs_hz", "speed_mean", "speed_std", "speed_osc_std", "cadence_hz", "jitter_lateral_std", "jitter_along_std",
              "z_mean", "z_std", "z_osc_std", "npts_mean", "npts_zero_frac"]
    for key, items in frames.items():
        name = f"{key[0]}:{key[1]}"
        result["frame"][name] = {s: summarize([it[s] for it in items]) for s in scalar}
        result["frame"][name]["dropout_runs_frames"] = summarize(sum((it["dropout_runs_frames"] for it in items), []))
        result["frame"][name]["straight_run_sec"] = summarize(sum((it["straight_run_sec"] for it in items), []))
    feats = ["speed", "speed_cv", "sway", "freeze_ratio", "stride_length", "stride_cv", "straightness"]
    for win, groups in windows.items():
        result["window"][f"{win}s"] = {}
        for key, ws in groups.items():
            walk = [w for w in ws if w["walking"]]
            result["window"][f"{win}s"][f"{key[0]}:{key[1]}"] = {
                "windows": len(ws), "walking_pass": len(walk),
                "features_walking": {f: summarize([w[f] for w in walk]) for f in feats}}
    with open(os.path.join(HERE, "normal_gait_stats.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    for name, st in result["frame"].items():
        print(f"{name:14s} fs {st['fs_hz']['mean']:.1f}Hz speed {st['speed_mean']['mean']:.2f}±{st['speed_std']['mean']:.2f} "
              f"cad {st['cadence_hz']['mean']:.2f}Hz jitterL {st['jitter_lateral_std']['mean']:.3f} z {st['z_mean']['mean']:.2f}±{st['z_std']['mean']:.2f} "
              f"npts {st['npts_mean']['mean']:.1f} zero {st['npts_zero_frac']['mean']:.2%} straight-run p50 {st['straight_run_sec']['p50'] if st['straight_run_sec'] else None}")
    for win in ("3s", "6s"):
        for name, w in result["window"][win].items():
            f = w["features_walking"]
            show = {k: round(f[k]["p50"], 3) for k in ("speed", "speed_cv", "sway", "freeze_ratio", "stride_cv") if f[k]}
            print(f"{win} {name:14s} pass {w['walking_pass']}/{w['windows']} median {show}")


if __name__ == "__main__":
    main()
