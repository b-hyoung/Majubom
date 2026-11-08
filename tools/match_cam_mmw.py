"""Pair each camera frame (MediaPipe joints + posture verdict) with the nearest mmWave frame (2026-11-08).

Output: one CSV row per camera frame with the cued posture (truth), the camera's posture verdict,
the 33 MediaPipe joints (normalized x, y) and the person's mmWave track (world x/y/z, head_z,
speed, vz, points, distance to the bed). Also prints a per-posture summary.

Usage:
  python tools/match_cam_mmw.py <marks.json> <mmWave session folder> NAME=<camera run folder> [NAME=<folder> ...]
         --bed <mmw_viewer>/bed_corners.json [--out matched.csv]
  marks.json is written by tools/Record-CamMmw.ps1 into the mmWave session folder.

Person track, per posture segment: among tracks within 1 m of the bed, the one that moved (or
appeared) when the posture changed; if none moved >= 0.3 m, the previous segment's track is kept.
Frames where that track is missing fall back to the track closest to the bed (mmw_pick=fallback).
Why: when the person gets up, the radar often keeps the old track at the spot they left, still with
points, so "closest to the bed" picked a leftover track (2026-11-08 TEST_20261108_162145, 섬: z 0.80 m
instead of 1.56 m). Picking by point count chose a static reflection near the door (tid 10).

Times: camera received_ns (perf_counter) is turned into PC epoch seconds with the
perf->UTC offset from control_events.jsonl (same as tools/cam_mmw_join.py).
mmWave frames.jsonl already has PC epoch seconds in "t".
Truth comes from the spoken-cue times in marks.json; the first 3 s after each cue
are marked transition=1 so they can be excluded.
A cued segment the person did not actually hold can be listed in marks.json as
"exclude": ["<cue label>", ...]; its rows get truth=제외 and stay out of the summary.
"""
import argparse
import bisect
import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

JOINTS = ["nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner", "right_eye",
          "right_eye_outer", "left_ear", "right_ear", "mouth_left", "mouth_right", "left_shoulder",
          "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_pinky",
          "right_pinky", "left_index", "right_index", "left_thumb", "right_thumb", "left_hip", "right_hip",
          "left_knee", "right_knee", "left_ankle", "right_ankle", "left_heel", "right_heel",
          "left_foot_index", "right_foot_index"]
TRANSITION_S = 3.0
MAX_GAP_S = 0.15
EXCLUDED = "제외"


def load_marks(path, day):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    marks = [(k, datetime.strptime(f"{day} {v}", "%Y-%m-%d %H:%M:%S.%f").timestamp()) for k, v in data["marks"].items()]
    excluded = set(data.get("exclude") or [])
    unknown = excluded - set(data["marks"])
    if unknown:
        raise SystemExit(f"exclude has labels not in marks: {sorted(unknown)}")
    return sorted(marks, key=lambda m: m[1]), excluded


def truth_at(marks, t):
    for (name, a), (_, b) in zip(marks, marks[1:]):
        if a <= t < b:
            return name, int(t < a + TRANSITION_S)
    return "", 0


def load_mmwave(folder):
    frames = []
    for line in open(folder / "frames.jsonl", encoding="utf-8"):
        if line.startswith('{"t"'):
            frames.append(json.loads(line))
    frames.sort(key=lambda f: f["t"])
    return frames


def load_bed(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return data["corners"] if isinstance(data, dict) else data


def bed_distance(bed, x, y):
    """0 inside the bed polygon, otherwise distance to its nearest edge (m)."""
    inside = False
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(bed, bed[1:] + bed[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
        dx, dy = x2 - x1, y2 - y1
        u = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, ((x1 + u * dx - x) ** 2 + (y1 + u * dy - y) ** 2) ** 0.5)
    return 0.0 if inside else best


def main_track(tracks, bed):
    """The person track: the one closest to the bed (static reflections such as the
    door-side object carry many points, so point count alone picks the wrong track).
    Ties: more points."""
    tracks = [t for t in tracks or [] if t.get("world")]
    if not tracks:
        return None
    return min(tracks, key=lambda t: (round(bed_distance(bed, t["world"][0], t["world"][1]), 2), -(t.get("npts") or 0)))


NEAR_BED_M = 1.0
MOVED_M = 0.3


def segment_tracks(frames, a, b):
    """Median position of each track seen in at least half the frames of [a, b)."""
    per, n = defaultdict(list), 0
    for f in frames:
        if a <= f["t"] < b:
            n += 1
            for t in f.get("tracks") or []:
                if t.get("world"):
                    per[t["tid"]].append(t["world"])
    return {tid: [statistics.median(p[i] for p in ws) for i in range(3)]
            for tid, ws in per.items() if len(ws) >= 0.5 * max(n, 1)}


def segment_person_tids(frames, marks, bed):
    """[(start, end, tid or None, reason)] for each posture segment (see module docstring)."""
    out, person = [], None
    prev = segment_tracks(frames, marks[0][1] - 8, marks[0][1])
    for (_, a), (_, b) in zip(marks, marks[1:]):
        cur = segment_tracks(frames, a + TRANSITION_S, b)
        best = None
        for tid, (x, y, z) in cur.items():
            if bed_distance(bed, x, y) > NEAR_BED_M:
                continue
            p = prev.get(tid)
            moved = float("inf") if p is None else ((x - p[0]) ** 2 + (y - p[1]) ** 2) ** 0.5 + abs(z - p[2])
            if best is None or moved > best[0]:
                best = (moved, tid)
        if best and best[0] >= MOVED_M:
            person = best[1]
            out.append((a, b, person, "new" if best[0] == float("inf") else "moved"))
        elif person in cur:
            out.append((a, b, person, "kept"))
        else:
            out.append((a, b, None, "none"))
        prev = cur
    return out


def pick_track(tracks, bed, segments, t):
    for a, b, tid, _ in segments:
        if a <= t < b and tid is not None:
            for tr in tracks or []:
                if tr.get("tid") == tid and tr.get("world"):
                    return tr, "segment"
            return main_track(tracks, bed), "fallback"
    return main_track(tracks, bed), "nearest"


def people_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def load_camera(folder):
    offsets = {}
    for line in open(folder / "control_events.jsonl", encoding="utf-8"):
        e = json.loads(line)
        r = e["request"]
        offsets.setdefault(e["clock_id"], int(r["requested_utc_ns"]) - int(r["requested_time_ns"]))
    rows = []
    for line in open(folder / "live_frames.jsonl", encoding="utf-8"):
        r = json.loads(line)
        pa = r.get("posture_assessment") or {}
        clock = pa.get("clock_id") or r.get("clock_id")
        received = pa.get("received_ns") or r.get("received_ns")
        if clock not in offsets or not received:
            continue
        people = people_list(r.get("mapped_people"))
        target = pa.get("candidate_id")
        person = next((p for p in people if p.get("candidate_id") == target), people[0] if people else None)
        joints = {}
        if person:
            for j in person.get("joints") or []:
                i = j.get("source_index")
                if i is not None and j.get("x_norm") is not None:
                    joints[JOINTS[i]] = (j["x_norm"], j["y_norm"])
        sp = r.get("stable_posture") or {}
        rows.append(dict(t=(int(received) + offsets[clock]) / 1e9, frame_id=r.get("frame_id"),
                         people=len(people), stable=sp.get("label") or "", instant=sp.get("instantaneous_label") or "",
                         joints=joints))
    rows.sort(key=lambda x: x["t"])
    return rows


def med(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("marks")
    ap.add_argument("mmwave", type=Path)
    ap.add_argument("cameras", nargs="+", help="NAME=<camera run folder>")
    ap.add_argument("--out", type=Path, default=Path("matched.csv"))
    ap.add_argument("--bed", required=True, help="mmw_viewer bed_corners.json")
    args = ap.parse_args()
    bed = load_bed(args.bed)

    mm = load_mmwave(args.mmwave)
    mm_t = [f["t"] for f in mm]
    day = datetime.fromtimestamp(mm_t[0]).strftime("%Y-%m-%d")
    marks, excluded = load_marks(args.marks, day)
    segments = segment_person_tids(mm, marks, bed)
    for (name, _), (a, b, tid, why) in zip(marks, segments):
        print(f"person track {name}: tid {tid} ({why})")

    header = ["camera", "time", "truth", "transition", "cam_stable", "cam_instant", "cam_people",
              "mmw_dt_ms", "mmw_frame", "mmw_presence", "mmw_tracks", "mmw_tid", "mmw_pick", "mmw_bed_dist", "mmw_x", "mmw_y", "mmw_z",
              "mmw_head_z", "mmw_speed", "mmw_vz", "mmw_npts"]
    header += [f"{n}_{a}" for n in JOINTS for a in ("x", "y")]
    summary = defaultdict(lambda: defaultdict(list))
    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for spec in args.cameras:
            name, folder = spec.split("=", 1)
            for c in load_camera(Path(folder)):
                i = bisect.bisect_left(mm_t, c["t"])
                j = min((k for k in (i - 1, i) if 0 <= k < len(mm)), key=lambda k: abs(mm_t[k] - c["t"]))
                dt = mm_t[j] - c["t"]
                if abs(dt) > MAX_GAP_S:
                    continue
                f = mm[j]
                tr, pick = pick_track(f.get("tracks"), bed, segments, c["t"])
                truth, transition = truth_at(marks, c["t"])
                if truth in excluded:
                    truth = EXCLUDED
                wx, wy, wz = tr["world"] if tr else (None, None, None)
                row = [name, datetime.fromtimestamp(c["t"]).strftime("%H:%M:%S.%f")[:-3], truth, transition,
                       c["stable"], c["instant"], c["people"], round(dt * 1000, 1), f.get("frame"),
                       f.get("presence"), len(f.get("tracks") or []), tr.get("tid") if tr else None, pick,
                       round(bed_distance(bed, wx, wy), 3) if tr else None, wx, wy, wz,
                       tr.get("head_z") if tr else None, tr.get("speed") if tr else None,
                       tr.get("vz") if tr else None, tr.get("npts") if tr else None]
                for n in JOINTS:
                    row += list(c["joints"].get(n, (None, None)))
                w.writerow(row)
                if truth and truth != EXCLUDED and not transition:
                    s = summary[(name, truth)]
                    s["n"].append(1)
                    s["mmw_z"].append(wz)
                    s["mmw_head_z"].append(tr.get("head_z") if tr else None)
                    s["mmw_speed"].append(tr.get("speed") if tr else None)
                    s["mmw_npts"].append(tr.get("npts") if tr else None)
                    s["has_track"].append(1 if tr else 0)
                    s["tid"].append(tr.get("tid") if tr else None)
                    s["segment_pick"].append(1 if pick == "segment" else 0)
                    s["has_pose"].append(1 if c["joints"] else 0)
                    hips = [c["joints"][k][1] for k in ("left_hip", "right_hip") if k in c["joints"]]
                    sh = [c["joints"][k][1] for k in ("left_shoulder", "right_shoulder") if k in c["joints"]]
                    s["hip_y"].append(sum(hips) / len(hips) if hips else None)
                    s["shoulder_y"].append(sum(sh) / len(sh) if sh else None)
                    s["stable"].append(c["stable"] or "None")

    print(f"wrote {args.out}")
    order = [m[0] for m in marks[:-1]]
    for (name, truth) in sorted(summary, key=lambda k: (k[0], order.index(k[1]) if k[1] in order else 99)):
        s = summary[(name, truth)]
        n = len(s["n"])
        labels = defaultdict(int)
        for lab in s["stable"]:
            labels[lab] += 1
        top = ", ".join(f"{k}:{v}" for k, v in sorted(labels.items(), key=lambda kv: -kv[1])[:3])
        tids = defaultdict(int)
        for tid in s["tid"]:
            tids[tid] += 1
        top_tid = max(tids, key=tids.get)
        top += f" | mmw tid {top_tid} {100 * tids[top_tid] // n}%, segment track {100 * sum(s['segment_pick']) // n}%"
        print(f"[{name}] {truth}: frames {n}, pose {100*sum(s['has_pose'])//n}%, mmw track {100*sum(s['has_track'])//n}% | "
              f"mmw z {med(s['mmw_z'])} head_z {med(s['mmw_head_z'])} speed {med(s['mmw_speed'])} npts {med(s['mmw_npts'])} | "
              f"MP hip_y {med(s['hip_y'])} shoulder_y {med(s['shoulder_y'])} | cam {top}")


if __name__ == "__main__":
    main()
