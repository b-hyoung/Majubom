# Exit-turn recording: per cue step, sit-up height, moving points at the rail edge, and turn direction from TLV 1020 points.
import json, math, struct, sys, os, datetime
import numpy as np
ses, bedf, out = sys.argv[1:4]
S = json.load(open(os.path.join(ses, "session.json"), encoding="utf-8"))
H, FLIP = S["height_m"], -1.0
M = json.load(open(os.path.join(ses, "marks.json"), encoding="utf-8-sig"))
BED = json.load(open(bedf, encoding="utf-8-sig"))["corners"]
bx = [c[0] for c in BED]; by = [c[1] for c in BED]
EDGE = max(bx)
X0, X1, Y0, Y1 = min(bx) - 0.2, EDGE + 0.6, min(by) - 0.2, max(by) + 0.2
day = datetime.datetime.fromtimestamp(S["started_at"]).date()
ts = lambda s: datetime.datetime.combine(day, datetime.datetime.strptime(s, "%H:%M:%S.%f").time()).timestamp()
marks = [dict(m, t=ts(m["time"])) for m in M["marks"]]
frames = [json.loads(l) for l in open(os.path.join(ses, "frames.jsonl"), encoding="utf-8")]
raw = open(os.path.join(ses, "raw.bin"), "rb").read()
def pts(f):
    b = raw[f["offset"]:f["offset"] + f["length"]]
    if b[:8] != bytes([2, 1, 4, 3, 6, 5, 8, 7]): return []
    n = struct.unpack_from("<I", b, 32)[0]; off = 40; P = []
    for _ in range(n):
        if off + 8 > len(b): break
        t, ln = struct.unpack_from("<2I", b, off); p = b[off + 8:off + 8 + ln]; off += 8 + ln
        if t != 1020 or ln < 20: continue
        eu, au, du, ru, su = struct.unpack_from("<5f", p, 0)
        for i in range((ln - 20) // 8):
            el_i, az_i, dop, rng, snr = struct.unpack_from("<bbhHH", p, 20 + i * 8)
            r, el, az = rng * ru, el_i * eu, az_i * au
            if r < 0.4: continue
            px, py, pz = r * math.cos(el) * math.sin(az), r * math.cos(el) * math.cos(az), r * math.sin(el)
            x, y, z = FLIP * px, FLIP * pz, H - py
            if X0 <= x <= X1 and Y0 <= y <= Y1 and 0.3 <= z <= 1.8: P.append((x, y, z, dop * du, snr * su))
    return P
F = [(f["t"], pts(f)) for f in frames]
def win(a, b):
    P = [p for t, ps in F if a <= t < b for p in ps]
    return np.array(P) if P else np.zeros((0, 5))
def zp90(a, b):
    v = []
    for k in np.arange(a, b, 1.0):
        P = win(k, k + 1.0)
        if len(P) >= 5: v.append(np.percentile(P[:, 2], 90))
    return float(np.median(v)) if v else None
rows = []
for i, m in enumerate(marks[:-1]):
    a, nxt = m["t"], marks[i + 1]["t"]
    W = win(a, min(a + 8, nxt)); mv = W[W[:, 3] != 0] if len(W) else W
    bed_mv = mv[mv[:, 0] <= EDGE + 0.05] if len(mv) else mv
    edge_mv = mv[mv[:, 0] > EDGE - 0.15] if len(mv) else mv
    before, after = zp90(a - 3, a), zp90(a + 3, min(a + 8, nxt))
    cx0 = win(a - 2, a); cx1 = win(min(a + 6, nxt - 2), min(a + 8, nxt))
    rows.append(dict(i=i, trial=m["trial"], kind=m["kind"], step=m["label"], dur=round(nxt - a, 1),
        z_before=round(before, 2) if before else None, z_after=round(after, 2) if after else None,
        moving=int(len(mv)), edge_moving=int(len(edge_mv)),
        corr_x_dop=round(float(np.corrcoef(bed_mv[:, 0], bed_mv[:, 3])[0, 1]), 3) if len(bed_mv) > 5 else None,
        corr_y_dop=round(float(np.corrcoef(bed_mv[:, 1], bed_mv[:, 3])[0, 1]), 3) if len(bed_mv) > 5 else None,
        dop_mean=round(float(bed_mv[:, 3].mean()), 3) if len(bed_mv) else None,
        cx_shift=round(float(np.median(cx1[:, 0]) - np.median(cx0[:, 0])), 3) if len(cx0) > 5 and len(cx1) > 5 else None))
json.dump(dict(session=os.path.basename(ses), participant=M.get("participant"), edge_x=EDGE, steps=rows), open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for r in rows: print(r)
