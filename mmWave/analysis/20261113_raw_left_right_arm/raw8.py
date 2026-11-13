# For the site's 누움 example frame (track 23), collect only the raw firmware fields behind each of the 8 values,
# then recompute the 8 values from them with the viewer's own formulas as a check.
# Usage: python raw8.py <session_dir> <bed_corners.json> <out.json>
import json, math, struct, sys, os
ses, bedf, out = sys.argv[1:4]
S = json.load(open(os.path.join(ses, "session.json"), encoding="utf-8"))
H, TILT, FLIP = S["height_m"], S["tilt_deg"], -1.0 if S["upside_down"] else 1.0
BED = json.load(open(bedf, encoding="utf-8-sig"))["corners"]
HEAD_MIN_POINTS, HEAD_TOP_FRACTION, HEAD_WINDOW_SEC, HEAD_CEILING_MARGIN_M = 8, 0.9, 1.0, 0.4
TID = 23
frames = [json.loads(l) for l in open(os.path.join(ses, "frames.jsonl"), encoding="utf-8")]
raw = open(os.path.join(ses, "raw.bin"), "rb").read()

def tlvs(f):
    b = raw[f["offset"]:f["offset"] + f["length"]]; n = struct.unpack_from("<I", b, 32)[0]; off, d = 40, {}
    for _ in range(n):
        t, ln = struct.unpack_from("<2I", b, off); d[t] = b[off + 8:off + 8 + ln]; off += 8 + ln
    return d

def world(px, py, pz, vx=0.0, vy=0.0, vz=0.0):  # send_mmw.track_to_world
    a = math.radians(TILT); c, s = math.cos(a), math.sin(a)
    y, z, vy2, vz2 = py, FLIP * pz, vy, FLIP * vz
    return dict(x=FLIP * px, y=y * c + z * s, z=H - y * s + z * c, vx=FLIP * vx, vy=vy2 * c + vz2 * s, vz=-vy2 * s + vz2 * c)

def points(p):  # viewer parse_points: raw ints -> world z (rounded 2, as the viewer does)
    if len(p) < 20: return None, []
    u = struct.unpack_from("<5f", p, 0); pts = []
    for i in range((len(p) - 20) // 8):
        el_i, az_i, dop, rng, snr = struct.unpack_from("<bbhHH", p, 20 + i * 8)
        r, el, az = rng * u[3], el_i * u[0], az_i * u[1]
        w = world(r * math.cos(el) * math.sin(az), r * math.cos(el) * math.cos(az), r * math.sin(el))
        pts.append(dict(raw=dict(elevation=el_i, azimuth=az_i, doppler=dop, range=rng, snr=snr), z=round(w["z"], 2)))
    return dict(zip(["elevationUnit", "azimuthUnit", "dopplerUnit", "rangeUnit", "snrUnit"], u)), pts

def target(p, tid):
    for i in range(len(p) // 112):
        v = struct.unpack_from("<I27f", p, i * 112)
        if v[0] == tid: return dict(zip(["tid", "posX", "posY", "posZ", "velX", "velY", "velZ"], v[:7]))

def bed_distance(x, y):  # tools/match_cam_mmw.py
    inside, best = False, float("inf")
    for (x1, y1), (x2, y2) in zip(BED, BED[1:] + BED[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1): inside = not inside
        dx, dy = x2 - x1, y2 - y1; u = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, ((x1 + u * dx - x) ** 2 + (y1 + u * dy - y) ** 2) ** 0.5)
    return 0.0 if inside else best

k = next(i for i, f in enumerate(frames) for t in f.get("tracks") or []
         if t["tid"] == TID and t.get("head_z") == 1.13 and t.get("npts") == 6 and abs(t["world"][2] - 0.662) < 6e-4)
f = frames[k]; T = tlvs(f); tg = target(T[1010], TID); idx = list(T[1011])
w = world(tg["posX"], tg["posY"], tg["posZ"], tg["velX"], tg["velY"], tg["velZ"])

# head_z: every frame in the last 1 s; each uses the PREVIOUS frame's 1020 points and its own 1011 owners
limit = H - HEAD_CEILING_MARGIN_M; hz_frames, samples = [], []
j = k
while j > 0 and f["t"] - frames[j]["t"] <= HEAD_WINDOW_SEC:
    cur, prev = tlvs(frames[j]), tlvs(frames[j - 1])
    units, pts = points(prev.get(1020, b"")); own = list(cur.get(1011, b""))
    mine = [p for o, p in zip(own, pts) if o == TID] if len(own) == len(pts) else []
    zs = sorted(p["z"] for p in mine if p["z"] <= limit)
    smp = zs[int(HEAD_TOP_FRACTION * (len(zs) - 1))] if len(zs) >= HEAD_MIN_POINTS else None
    if smp is not None: samples.append(smp)
    hz_frames.append(dict(frame=frames[j]["frame"], received_at_unix=frames[j]["t"], points_from_frame=frames[j - 1]["frame"],
                          units=units, track23_points=[p["raw"] for p in mine], count=len(mine), sample_z=smp))
    j -= 1
hz_frames.reverse()
head_z = round(sorted(samples)[len(samples) // 2], 2) if samples else None

calc = dict(x=round(w["x"], 3), y=round(w["y"], 3), z=round(w["z"], 3), speed=round(math.hypot(w["vx"], w["vy"]), 3),
            vz=round(w["vz"], 3), npts=idx.count(TID), bed_dist=round(bed_distance(round(w["x"], 3), round(w["y"], 3)), 3), head_z=head_z)
site = dict(z=0.662, head_z=1.13, speed=0.066, vz=-0.004, npts=6, bed_dist=0.0, x=-0.755, y=-0.116)
tlv1010 = {"source": "TLV 1010 target list, slot tid 23 (float32 as sent)", **{k2: tg[k2] for k2 in ("posX", "posY", "posZ")}}
doc = {
    "about": "Raw radar fields behind the 8 posture inputs for one frame (vic, cue 누움, track 23). Values are exactly as the radar sent them; 'check' recomputes each input from these fields.",
    "frame": {"recording": os.path.basename(ses), "frame_number": f["frame"], "received_at_unix": f["t"], "raw_file": "raw.bin",
              "byte_offset": f["offset"], "byte_length": f["length"]},
    "settings_used_by_formulas": {"radar_height_m": H, "tilt_deg": TILT, "mounted_upside_down": S["upside_down"],
                                  "bed_corners_xy_m": BED, "head_rules": {"min_points": HEAD_MIN_POINTS, "top_fraction": HEAD_TOP_FRACTION,
                                  "window_sec": HEAD_WINDOW_SEC, "exclude_points_above_m": limit}},
    "inputs": {
        "z": {"raw": tlv1010, "formula": "height − posY·sin(tilt) + (−posZ)·cos(tilt)"},
        "x": {"raw": tlv1010, "formula": "−posX (upside-down mount)"},
        "y": {"raw": tlv1010, "formula": "posY·cos(tilt) + (−posZ)·sin(tilt)"},
        "speed": {"raw": {"source": "TLV 1010 target list, slot tid 23", "velX": tg["velX"], "velY": tg["velY"], "velZ": tg["velZ"]},
                  "formula": "√(vx² + vy²) after rotating velocities like positions"},
        "vz": {"raw": {"source": "TLV 1010 target list, slot tid 23", "velY": tg["velY"], "velZ": tg["velZ"]},
               "formula": "−velY·sin(tilt) + (−velZ)·cos(tilt)"},
        "npts": {"raw": {"source": "TLV 1011 target index of this frame (owner of each previous-frame point)", "index": idx},
                 "formula": "count of entries equal to 23"},
        "bed_dist": {"raw": {**tlv1010, "plus": "bed_corners_xy_m (setting, not radar)"},
                     "formula": "0 if (x, y) is inside the bed polygon, else distance to nearest bed edge"},
        "head_z": {"raw": {"source": "TLV 1020 points of the previous frame + TLV 1011 owners of the current frame, for every frame in the last 1 s",
                           "frames": hz_frames},
                   "formula": "per frame: points owned by 23 → height; drop above limit; if ≥ 8 points take the 90% value; head_z = median over 1 s"},
    },
    "check": {"recomputed": calc, "site_values": site, "all_match": all(calc[k2] == site[k2] for k2 in site)},
}
json.dump(doc, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(doc["check"], ensure_ascii=False)); print("head frames", len(hz_frames), "samples", samples)
