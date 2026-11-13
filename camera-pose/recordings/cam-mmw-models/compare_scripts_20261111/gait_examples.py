import json, sys, math, numpy as np
sys.path.insert(0, r"C:\Majubom\mmWave"); sys.path.insert(0, r"C:\Majubom\server")
import send_mmw as M, mmw_logic as L
SES = r"C:\Users\tmdal\Downloads\mmw_viewer\sessions"
MET = list(L.JUDGED_METRICS)

def series(name, tid):
    st = json.load(open(f"{SES}/{name}/session.json", encoding="utf-8"))["started_at"]
    out = []
    for line in open(f"{SES}/{name}/frames.jsonl", encoding="utf-8"):
        f = json.loads(line)
        for t in f.get("tracks") or []:
            if t["tid"] == tid: out.append((f["t"] - st, t)); break
    return out

def windows(name, tid, a, b, hop=55):
    seq = [s for s in series(name, tid) if a <= s[0] < b]
    out = []
    for s in range(0, len(seq) - 109 + 1, hop):
        p = seq[s:s + 109]
        smp = [(t, tr["world"][0], tr["world"][1], 0.0, tr["speed"], 0.0) for t, tr in p]
        g = M._gait_from_samples(smp)
        xy = np.array([[tr["world"][0], tr["world"][1]] for _, tr in p]); sp = np.array([tr["speed"] for _, tr in p])
        path = float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum()); disp = float(np.linalg.norm(xy[-1] - xy[0]))
        out.append(dict(t0=round(p[0][0], 1), t1=round(p[-1][0], 1), n=len(p), path=round(path, 2), disp=round(disp, 2),
                        sp_min=round(float(sp.min()), 2), sp_max=round(float(sp.max()), 2), g={m: g[m] for m in MET}))
    return out

NORM = windows("S3_20261105_121146", 5, 76.5, 138.5)
ABN = {"천천히": windows("S3_20261105_121146", 5, 163.3, 225.3), "종종걸음": windows("S3_20261105_124521", 20, 9.1, 93.2)}
print("counts", len(NORM), {k: len(v) for k, v in ABN.items()})

def stats(base):
    X = np.array([[w["g"][m] for m in MET] for w in base])
    return {f"{m}_mu": float(X[:, i].mean()) for i, m in enumerate(MET)} | {f"{m}_sigma": max(float(X[:, i].std()), L.SIGMA_FLOOR[m]) for i, m in enumerate(MET)}

def judge(w, base):
    st = stats(base); z = L.compute_zscore(w["g"], st); sc = L.gait_score(z, None)
    return dict(z=z, a=round(sc["a"], 2), s=round(sc["s"], 2), n_a=round(sc["n_a"], 2), n_s=round(sc["n_s"], 2),
                score=round(sc["combined"], 2), driver=sc["driver"], level=L.classify_gait(sc["combined"]),
                old=L.classify_alert(z["total_abs"]))

rows = []
for i, w in enumerate(NORM):
    rows.append(("평소", w, judge(w, [v for j, v in enumerate(NORM) if abs(j - i) > 1])))
for k, ws in ABN.items():
    for w in ws: rows.append((k, w, judge(w, NORM)))

rules = {"0 양방향 |z| 합 ≥ 3 (예전)": lambda r: r["z"]["total_abs"] >= 3, "A 위험 방향 합 ≥ 2": lambda r: r["n_a"] >= 2,
         "S 속도만 ≥ 2": lambda r: r["n_s"] >= 2, "A+S 큰 쪽 ≥ 2 (지금)": lambda r: r["score"] >= 2}
acc = {}
for name, f in rules.items():
    ok = {c: [f(r) == (c != "평소") for c, _, r in rows if c == cc] for cc in ["평소", "천천히", "종종걸음"] for c in [cc]}
    tot = sum(sum(v) for v in ok.values()); n = sum(len(v) for v in ok.values())
    acc[name] = dict(acc=tot / n, **{c: f"{sum(v)}/{len(v)}" for c, v in ok.items()})
    print(name, acc[name])
lv = {}
for c, _, r in rows: lv.setdefault(c, {}).setdefault(r["level"], 0); lv[c][r["level"]] += 1
print("levels", lv)
st = stats(NORM); print("baseline", {k: round(v, 3) for k, v in st.items()})
json.dump(dict(rows=[(c, w, r) for c, w, r in rows], acc=acc, levels=lv, baseline=st), open("gait_examples.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for c in ["평소", "천천히", "종종걸음"]:
    rs = [x for x in rows if x[0] == c]; c_, w, r = rs[len(rs) // 2]
    print(c, w, r)
