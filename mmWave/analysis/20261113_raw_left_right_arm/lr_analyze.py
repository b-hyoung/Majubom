# Left vs right side-lying from mmWave, and turning direction at posture changes.
# Usage: python lr_analyze.py <session_dir> <bed_corners.json> <out.json>
import json, math, struct, sys, os, datetime
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

ses, bedf, out = sys.argv[1:4]
S = json.load(open(os.path.join(ses, "session.json"), encoding="utf-8"))
H, FLIP = S["height_m"], -1.0 if S.get("upside_down", True) else 1.0
M = json.load(open(os.path.join(ses, "marks.json"), encoding="utf-8-sig"))
BED = json.load(open(bedf, encoding="utf-8-sig"))["corners"]
bx = [c[0] for c in BED]; by = [c[1] for c in BED]
BX0, BX1, BY0, BY1 = min(bx) - 0.3, max(bx) + 0.3, min(by) - 0.3, max(by) + 0.3   # bed box + 30 cm
BED_CX = (min(bx) + max(bx)) / 2
frames = [json.loads(l) for l in open(os.path.join(ses, "frames.jsonl"), encoding="utf-8")]
raw = open(os.path.join(ses, "raw.bin"), "rb").read()
day = datetime.datetime.fromtimestamp(S["started_at"]).date()
marks = [(m["label"], datetime.datetime.combine(day, datetime.datetime.strptime(m["time"], "%H:%M:%S.%f").time()).timestamp()) for m in M["marks"]]

def tlvs(f):
    b = raw[f["offset"]:f["offset"] + f["length"]]
    if b[:8] != bytes([2, 1, 4, 3, 6, 5, 8, 7]) or len(b) < 40: return {}
    n = struct.unpack_from("<I", b, 32)[0]; off, d = 40, {}
    for _ in range(n):
        if off + 8 > len(b): break
        t, ln = struct.unpack_from("<2I", b, off); d[t] = b[off + 8:off + 8 + ln]; off += 8 + ln
    return d

def bed_points(p):
    """1020 points -> world (x, y, z, doppler m/s, snr), kept only inside the bed box, 0.2-1.6 m high."""
    if len(p) < 20: return np.zeros((0, 5))
    eu, au, du, ru, su = struct.unpack_from("<5f", p, 0); pts = []
    for i in range((len(p) - 20) // 8):
        el_i, az_i, dop, rng, snr = struct.unpack_from("<bbhHH", p, 20 + i * 8)
        r, el, az = rng * ru, el_i * eu, az_i * au
        px, py, pz = r * math.cos(el) * math.sin(az), r * math.cos(el) * math.cos(az), r * math.sin(el)
        x, y, z = FLIP * px, FLIP * pz, H - py          # tilt 90 deg
        if r < 0.4: continue                             # radar body leakage at ~0.25 m
        if BX0 <= x <= BX1 and BY0 <= y <= BY1 and 0.2 <= z <= 1.6:
            pts.append((x, y, z, dop * du, snr * su))
    return np.array(pts) if pts else np.zeros((0, 5))

def label_at(t):
    lab, since = None, None
    for l, tt in marks:
        if tt <= t: lab, since = l, tt
    return lab, (t - since) if since else None

def feats(P):
    if len(P) < 3: return None
    x, y, z, dop, snr = P.T; w = snr / snr.sum()
    f = [len(P), x.mean(), y.mean(), z.mean(), x.std(), y.std(), z.std(),
         np.average(x, weights=w), np.average(z, weights=w),
         ((x - x.mean()) * (z - z.mean())).mean(),          # x-z tilt of the body blob
         ((x - x.mean()) * (y - y.mean())).mean(),
         ((x - x.mean()) ** 3).mean() / (x.std() ** 3 + 1e-9),   # left/right skew
         z.max(), np.percentile(z, 90), np.log1p(snr).mean(), (x < BED_CX).mean()]
    return f
FN = ["n", "x_mean", "y_mean", "z_mean", "x_std", "y_std", "z_std", "x_snrw", "z_snrw", "cov_xz", "cov_xy", "x_skew",
      "z_max", "z_p90", "log_snr", "frac_left_of_bed_center"]

# --- per-frame table ---
rows = []
for f in frames:
    lab, dt = label_at(f["t"])
    if lab is None or lab == "끝": continue
    T = tlvs(f)
    P = bed_points(T.get(1020, b""))
    rows.append(dict(t=f["t"], lab=lab, dt=dt, P=P, tracks=f.get("tracks") or []))

# segment id = index of the cue
seg_start = [tt for l, tt in marks]
def seg_of(t): return max(i for i, tt in enumerate(seg_start) if tt <= t)

# --- 1) left vs right: 1-second windows, skip first 5 s after each cue ---
X, Y, G = [], [], []
for r in rows: r["seg"] = seg_of(r["t"])
for sid in sorted({r["seg"] for r in rows}):
    rs = [r for r in rows if r["seg"] == sid and r["dt"] >= 5.0]
    if not rs or rs[0]["lab"] not in ("왼쪽 옆누움", "오른쪽 옆누움"): continue
    t0 = rs[0]["t"]
    for k in range(int((rs[-1]["t"] - t0) // 1.0)):
        win = [r for r in rs if t0 + k <= r["t"] < t0 + k + 1]
        P = np.vstack([r["P"] for r in win if len(r["P"])]) if any(len(r["P"]) for r in win) else np.zeros((0, 5))
        fv = feats(P)
        if fv is None: continue
        X.append(fv); Y.append(1 if rs[0]["lab"] == "왼쪽 옆누움" else 0); G.append(sid)
X, Y, G = np.array(X), np.array(Y), np.array(G)
res = {"session": os.path.basename(ses), "participant": M.get("participant"), "bed_box": [BX0, BX1, BY0, BY1],
       "windows": {"left": int((Y == 1).sum()), "right": int((Y == 0).sum())}, "segments": {}}
models = {"logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
          "random_forest": lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=3, random_state=0)}
lr = {}
for name, mk in models.items():
    pred = np.zeros_like(Y); per = {}
    for g in np.unique(G):                      # leave one side-lying segment out
        tr, te = G != g, G == g
        if len(np.unique(Y[tr])) < 2: continue
        m = mk().fit(X[tr], Y[tr]); pred[te] = m.predict(X[te])
        per[int(g)] = dict(label="왼쪽" if Y[te][0] == 1 else "오른쪽", acc=round(float((pred[te] == Y[te]).mean()), 3), n=int(te.sum()))
    seg_major = [int(round(float((pred[G == g] == Y[G == g]).mean() >= 0.5))) for g in np.unique(G)]
    lr[name] = dict(window_acc=round(float((pred == Y).mean()), 3), segments_correct=f"{sum(seg_major)}/{len(seg_major)}", per_segment=per)
res["left_right"] = lr
# simple single-feature separation (mean per segment)
res["feature_means_by_segment"] = {FN[j]: {("L" if Y[G == g][0] == 1 else "R") + str(int(g)): round(float(X[G == g, j].mean()), 3)
                                           for g in np.unique(G)} for j in range(len(FN))}
# also: supine-relative x offset per side segment (the supine right before it)
rel = {}
for sid in np.unique(G):
    prev = [r for r in rows if r["seg"] == sid - 1 and r["dt"] >= 5.0 and len(r["P"])]
    cur = [r for r in rows if r["seg"] == sid and r["dt"] >= 5.0 and len(r["P"])]
    if prev and cur:
        px = np.vstack([r["P"] for r in prev])[:, 0].mean(); cx = np.vstack([r["P"] for r in cur])[:, 0].mean()
        rel[("L" if Y[G == sid][0] == 1 else "R") + str(int(sid))] = round(float(cx - px), 3)
res["x_shift_from_previous_supine_m"] = rel

# --- 2) turning direction: 0-8 s after each cue (cue is stamped before the ~2-3 s voice line) ---
turns = []
for i, (lab, tt) in enumerate(marks[:-1]):
    if i == 0: continue
    prev_lab = marks[i - 1][0]
    win = [r for r in rows if tt <= r["t"] < tt + 8.0]
    if not win: continue
    P = [r["P"] for r in win if len(r["P"])]
    allP = np.vstack(P) if P else np.zeros((0, 5))
    # track nearest bed center: world x velocity is not stored; use track world x path
    tr_x = []
    for r in win:
        cand = [t for t in r["tracks"] if BX0 <= t["world"][0] <= BX1 and BY0 <= t["world"][1] <= BY1]
        if cand: tr_x.append(min(cand, key=lambda t: abs(t["world"][0] - BED_CX))["world"][0])
    moving = allP[np.abs(allP[:, 3]) > 0] if len(allP) else allP
    xs = [r["P"][:, 0].mean() for r in win if len(r["P"]) >= 3]
    turns.append(dict(cue=i, from_=prev_lab, to=lab,
                      kind=("supine→" + ("L" if "왼쪽" in lab else "R")) if prev_lab == "누움" else (("L" if "왼쪽" in prev_lab else "R") + "→supine"),
                      moving_points=int(len(moving)), all_points=int(len(allP)),
                      doppler_mean=round(float(moving[:, 3].mean()), 3) if len(moving) else None,
                      doppler_x_corr=round(float(np.corrcoef(moving[:, 0], moving[:, 3])[0, 1]), 3) if len(moving) > 5 else None,
                      cloud_x_shift=round(float(np.mean(xs[-5:]) - np.mean(xs[:5])), 3) if len(xs) >= 10 else None,
                      track_x_shift=round(float(tr_x[-1] - tr_x[0]), 3) if len(tr_x) >= 2 else None))
res["turns"] = turns
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: res[k] for k in ("windows", "left_right", "x_shift_from_previous_supine_m")}, ensure_ascii=False, indent=1))
for t in turns: print(t)

np.savez(out.replace(".json", "_feats.npz"), X=X, Y=Y, G=G, FN=np.array(FN))
