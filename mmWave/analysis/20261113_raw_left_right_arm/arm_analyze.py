# Arm on bed rail (radar-side edge) vs arm inside the bed, while lying on the right side. mmWave TLV 1020 points only.
# Usage: python arm_analyze.py <session_dir> <bed_corners.json> <out.json>
import json, math, struct, sys, os, datetime
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

ses, bedf, out = sys.argv[1:4]
S = json.load(open(os.path.join(ses, "session.json"), encoding="utf-8"))
H, FLIP = S["height_m"], -1.0 if S.get("upside_down", True) else 1.0
M = json.load(open(os.path.join(ses, "marks.json"), encoding="utf-8-sig"))
BED = json.load(open(bedf, encoding="utf-8-sig"))["corners"]
bx = [c[0] for c in BED]; by = [c[1] for c in BED]
EDGE = max(bx)                                   # radar-side long edge (radar sits at x = 0)
BX0, BX1, BY0, BY1 = min(bx) - 0.3, EDGE + 0.6, min(by) - 0.3, max(by) + 0.3
day = datetime.datetime.fromtimestamp(S["started_at"]).date()
marks = [(m["label"], datetime.datetime.combine(day, datetime.datetime.strptime(m["time"], "%H:%M:%S.%f").time()).timestamp()) for m in M["marks"]]
frames = [json.loads(l) for l in open(os.path.join(ses, "frames.jsonl"), encoding="utf-8")]
raw = open(os.path.join(ses, "raw.bin"), "rb").read()

def pts1020(f):
    b = raw[f["offset"]:f["offset"] + f["length"]]
    if b[:8] != bytes([2, 1, 4, 3, 6, 5, 8, 7]): return np.zeros((0, 5))
    n = struct.unpack_from("<I", b, 32)[0]; off = 40
    for _ in range(n):
        if off + 8 > len(b): break
        t, ln = struct.unpack_from("<2I", b, off); p = b[off + 8:off + 8 + ln]; off += 8 + ln
        if t != 1020 or ln < 20: continue
        eu, au, du, ru, su = struct.unpack_from("<5f", p, 0); P = []
        for i in range((ln - 20) // 8):
            el_i, az_i, dop, rng, snr = struct.unpack_from("<bbhHH", p, 20 + i * 8)
            r, el, az = rng * ru, el_i * eu, az_i * au
            if r < 0.4: continue
            px, py, pz = r * math.cos(el) * math.sin(az), r * math.cos(el) * math.cos(az), r * math.sin(el)
            x, y, z = FLIP * px, FLIP * pz, H - py
            if BX0 <= x <= BX1 and BY0 <= y <= BY1 and 0.2 <= z <= 1.6: P.append((x, y, z, dop * du, snr * su))
        return np.array(P) if P else np.zeros((0, 5))
    return np.zeros((0, 5))

def lab_at(t):
    l, s, i = None, None, None
    for k, (lab, tt) in enumerate(marks):
        if tt <= t: l, s, i = lab, tt, k
    return l, (t - s) if s else None, i

rows = []
for f in frames:
    l, dt, i = lab_at(f["t"])
    if l in ("팔 안전바", "팔 내림"): rows.append(dict(t=f["t"], lab=l, dt=dt, cue=i, P=pts1020(f)))

def feats(P):
    x, y, z, d, s = P.T if len(P) else (np.zeros(0),) * 5
    rail = (x > EDGE - 0.15)                      # points over/near the radar-side edge and beyond
    out_ = (x > EDGE + 0.05)                      # clearly outside the mattress
    f = [len(P), rail.sum(), out_.sum(), s[rail].sum() if rail.any() else 0, np.log1p(s[out_]).sum() if out_.any() else 0,
         x.max() if len(x) else EDGE - 0.5, np.percentile(x, 90) if len(x) else EDGE - 0.5, x.mean() if len(x) else EDGE - 0.5,
         z[rail].max() if rail.any() else 0, z[rail].mean() if rail.any() else 0]
    return f
FN = ["n_bed", "n_edge_zone", "n_outside_edge", "snr_edge_zone", "logsnr_outside", "x_max", "x_p90", "x_mean", "z_max_edge", "z_mean_edge"]

# 1) static state: 1-s windows from 4 s after each cue
X, Y, G = [], [], []
for c in sorted({r["cue"] for r in rows}):
    rs = [r for r in rows if r["cue"] == c and r["dt"] >= 4.0]
    if not rs: continue
    t0 = rs[0]["t"]
    for k in range(int((rs[-1]["t"] - t0) // 1.0)):
        w = [r["P"] for r in rs if t0 + k <= r["t"] < t0 + k + 1 and len(r["P"])]
        if not w: continue
        X.append(feats(np.vstack(w))); Y.append(1 if rs[0]["lab"] == "팔 안전바" else 0); G.append(c)
X, Y, G = np.array(X, float), np.array(Y), np.array(G)
cycle = (G - G.min()) // 2                         # up+down pair = one cycle
pred = np.zeros_like(Y)
for c in np.unique(cycle):
    tr, te = cycle != c, cycle == c
    m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(X[tr], Y[tr]); pred[te] = m.predict(X[te])
seg = {int(g): dict(label=("안전바" if Y[G == g][0] else "내림"), acc=round(float((pred[G == g] == Y[G == g]).mean()), 3)) for g in np.unique(G)}
res = {"session": os.path.basename(ses), "participant": M.get("participant"), "edge_x": EDGE,
       "static": {"windows": {"on_rail": int(Y.sum()), "down": int((1 - Y).sum())}, "leave_one_cycle_out_acc": round(float((pred == Y).mean()), 3),
                  "segments_correct": f"{sum(v['acc'] >= 0.5 for v in seg.values())}/{len(seg)}", "per_segment": seg},
       "feature_means": {FN[j]: {("UP" if Y[G == g][0] else "DN") + str(int(g)): round(float(X[G == g, j].mean()), 3) for g in np.unique(G)} for j in range(len(FN))}}
for j, f in enumerate(FN):
    up = [X[G == g, j].mean() for g in np.unique(G) if Y[G == g][0]]; dn = [X[G == g, j].mean() for g in np.unique(G) if not Y[G == g][0]]
    res["feature_means"][f]["separates"] = bool(max(up) < min(dn) or max(dn) < min(up))

# 2) the moment of moving the arm: 0-8 s after each cue, moving points near the edge
mov = []
for c in sorted({r["cue"] for r in rows}):
    w = [r["P"] for r in rows if r["cue"] == c and r["dt"] < 8.0 and len(r["P"])]
    P = np.vstack(w) if w else np.zeros((0, 5)); m_ = P[(np.abs(P[:, 3]) > 0)] if len(P) else P
    e = m_[m_[:, 0] > EDGE - 0.3] if len(m_) else m_
    lab = [r["lab"] for r in rows if r["cue"] == c][0]
    mov.append(dict(cue=c, label=lab, moving_points=int(len(m_)), moving_near_edge=int(len(e)),
                    doppler_mean_near_edge=round(float(e[:, 3].mean()), 4) if len(e) else None,
                    corr_x_doppler_near_edge=round(float(np.corrcoef(e[:, 0], e[:, 3])[0, 1]), 3) if len(e) > 5 else None,
                    corr_x_doppler_all=round(float(np.corrcoef(m_[:, 0], m_[:, 3])[0, 1]), 3) if len(m_) > 5 else None))
res["moment"] = mov
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(res["static"], ensure_ascii=False))
for f, v in res["feature_means"].items(): print(f, v)
for m_ in mov: print(m_)
