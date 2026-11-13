"""Compare current gate+sway vs 3s gate vs 3s gate + local-direction sway on lee walking recordings."""
import json, sys, io, collections, datetime as dt, numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\Majubom\mmWave"); sys.path.insert(0, r"C:\Majubom\server")
import send_mmw as M, mmw_logic as L
SES = r"C:\Users\tmdal\Downloads\mmw_viewer\sessions"; K = 19  # ~1 s smoothing
def sway_local(xy):
    pad = np.pad(xy, ((K // 2, K // 2), (0, 0)), mode="edge")
    sm = np.column_stack([np.convolve(pad[:, i], np.ones(K) / K, mode="valid") for i in range(2)])
    tg = np.gradient(sm, axis=0); n = np.linalg.norm(tg, axis=1, keepdims=True); tg = tg / np.where(n > 1e-9, n, 1)
    d = xy - sm; lat = d[:, 0] * tg[:, 1] - d[:, 1] * tg[:, 0]
    return float(np.std(lat))
def track(ses, ranges, rel):
    st = json.load(open(f"{SES}/{ses}/session.json", encoding="utf-8"))["started_at"]
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for line in open(f"{SES}/{ses}/frames.jsonl", encoding="utf-8"):
        f = json.loads(line); t = f["t"] - (st if rel else 0)
        for lab, a, b in ranges:
            if a <= t < b:
                for tr in f.get("tracks") or []: per[lab][tr["tid"]].append((f["t"], tr["world"][0], tr["world"][1], tr["speed"]))
    out = []
    for lab, d in per.items():
        def path(v): xy = np.array([[q[1], q[2]] for q in sorted(v)]); return np.linalg.norm(np.diff(xy, axis=0), axis=1).sum() if len(v) > 1 else 0
        v = sorted(d[max(d, key=lambda k: path(d[k]))])
        for i, s in enumerate(range(0, len(v) - 109 + 1, 55)):
            p = v[s:s + 109]; xy = np.array([[q[1], q[2]] for q in p]); sp = np.array([q[3] for q in p])
            sl = np.linalg.norm(np.diff(xy, axis=0), axis=1); pl = sl.sum(); disp = np.linalg.norm(xy[-1] - xy[0])
            st3 = min(np.linalg.norm(xy[b] - xy[a]) / max(sl[a:b].sum(), 1e-9) for a, b in ((0, 54), (54, 108)))
            g = M._gait_from_samples([(q[0], q[1], q[2], 0.0, q[3], 0.0) for q in p])
            out.append(dict(ses=ses, lab=lab, i=i, speed=g["speed"], speed_cv=g["speed_cv"], freeze_ratio=g["freeze_ratio"],
                            sway_old=g["sway"], sway_new=sway_local(xy), straight=disp / pl if pl else 0, st3=st3, disp=disp))
    return out
def mranges(ses):
    mk = json.load(open(f"{SES}/{ses}/marks.json", encoding="utf-8-sig")); day = ses.split("_")[1]
    ts = lambda h: dt.datetime.strptime(day + " " + h, "%Y%m%d %H:%M:%S.%f").timestamp()
    seg = sorted((ts(v), k) for k, v in mk["marks"].items())
    lab = {"평소 걷기": "평소", "천천히 걷기": "천천히", "종종걸음": "종종걸음"}
    return [(lab[k], a + 3, b) for (a, k), (b, _) in zip(seg, seg[1:])]
W = track("TEST_20261112_164724", mranges("TEST_20261112_164724"), False) + track("TEST_20261112_165534", mranges("TEST_20261112_165534"), False) \
  + track("S3_20261105_121146", [("평소", 76.5, 138.5), ("천천히", 163.3, 225.3)], True) + track("S3_20261105_124521", [("종종걸음", 9.1, 93.2)], True)
base_ok = lambda w: w["speed"] >= 0.2 and w["disp"] >= 0.3
CFG = {"① 지금 (6초 직진성 ≥ 0.5, 흔들림 지금 방식)": (lambda w: w["straight"] >= 0.5, "sway_old"),
       "② 3초 직진성만 바꿈 (흔들림 지금 방식)": (lambda w: w["st3"] >= 0.5, "sway_old"),
       "③ 3초 직진성 + 진행 방향 기준 흔들림": (lambda w: w["st3"] >= 0.5, "sway_new")}
LABS = ["평소", "천천히", "종종걸음"]
print("전체 6초 구간:", dict(collections.Counter(w["lab"] for w in W)))
# curvature check: among 평소 windows, sway on straight (≥0.5) vs curved (<0.5) windows
for key in ("sway_old", "sway_new"):
    s = [w[key] for w in W if w["lab"] == "평소" and base_ok(w) and w["straight"] >= 0.5]; c = [w[key] for w in W if w["lab"] == "평소" and base_ok(w) and w["straight"] < 0.5]
    print(f"[{key}] 평소: 곧은 구간 중앙값 {np.median(s):.3f} (n={len(s)}) | 휜 구간 중앙값 {np.median(c):.3f} (n={len(c)})")
R = {}
for name, (gate, sk) in CFG.items():
    ok = [w for w in W if base_ok(w) and gate(w)]
    norm = [w for w in ok if w["lab"] == "평소"]
    def stats(b):
        X = {"speed": [w["speed"] for w in b], "speed_cv": [w["speed_cv"] for w in b], "sway": [w[sk] for w in b], "freeze_ratio": [w["freeze_ratio"] for w in b]}
        return {f"{m}_mu": float(np.mean(v)) for m, v in X.items()} | {f"{m}_sigma": max(float(np.std(v)), L.SIGMA_FLOOR[m]) for m, v in X.items()}
    lv = {l: collections.Counter() for l in LABS}; correct = total = 0
    for w in ok:
        b = [n for n in norm if not (n["ses"] == w["ses"] and n["lab"] == w["lab"] and abs(n["i"] - w["i"]) <= 1)] if w["lab"] == "평소" else norm
        raw = {"speed": w["speed"], "speed_cv": w["speed_cv"], "sway": w[sk], "freeze_ratio": w["freeze_ratio"]}
        level = L.classify_gait(L.gait_score(L.compute_zscore(raw, stats(b)), None)["combined"])
        lv[w["lab"]][level] += 1; total += 1; correct += (level == "normal") == (w["lab"] == "평소")
    R[name] = dict(n={l: sum(lv[l].values()) for l in LABS}, sway={l: float(np.median([w[sk] for w in ok if w["lab"] == l])) for l in LABS},
                   levels={l: dict(lv[l]) for l in LABS}, acc=correct / total, total=total)
    print("\n##", name); print("  통과 구간:", R[name]["n"], "합계", total); print("  흔들림 중앙값:", {k: round(v, 3) for k, v in R[name]["sway"].items()})
    print("  판정:", R[name]["levels"]); print(f"  정확도: {correct}/{total} = {100 * correct / total:.1f}%")
json.dump(R, open("sway_new.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
