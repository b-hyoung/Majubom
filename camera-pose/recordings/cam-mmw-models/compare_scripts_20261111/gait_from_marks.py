"""Gait windows from a -Gait recording (marks.json). Walker = track with the longest path in each segment."""
import json, sys, datetime as dt, collections, numpy as np
sys.path.insert(0, r"C:\Majubom\mmWave"); sys.path.insert(0, r"C:\Majubom\server")
import send_mmw as M, mmw_logic as L
MET = list(L.JUDGED_METRICS); SES = r"C:\Users\tmdal\Downloads\mmw_viewer\sessions"
def load(ses):
    mk = json.load(open(f"{SES}/{ses}/marks.json", encoding="utf-8-sig"))
    day = ses.split("_")[1]
    ts = lambda h: dt.datetime.strptime(day + " " + h, "%Y%m%d %H:%M:%S.%f").timestamp()
    seg = sorted((ts(v), k) for k, v in mk["marks"].items())
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for line in open(f"{SES}/{ses}/frames.jsonl", encoding="utf-8"):
        f = json.loads(line); t = f["t"]
        for (a, name), (b, _) in zip(seg, seg[1:]):
            if a + 3 <= t < b:
                for tr in f.get("tracks") or []:
                    per[name][tr["tid"]].append((t, tr["world"][0], tr["world"][1], tr["speed"], tr["npts"]))
    out = {}
    for name, d in per.items():
        def path(v): xy = np.array([[p[1], p[2]] for p in v]); return float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum()) if len(v) > 1 else 0
        tid = max(d, key=lambda k: path(d[k]))
        v = sorted(d[tid]); wins = []
        for s in range(0, len(v) - 109 + 1, 55):
            p = v[s:s + 109]
            smp = [(q[0], q[1], q[2], 0.0, q[3], 0.0) for q in p]
            g = M._gait_from_samples(smp)
            xy = np.array([[q[1], q[2]] for q in p]); pl = float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum()); disp = float(np.linalg.norm(xy[-1] - xy[0]))
            ok = g["speed"] >= 0.2 and disp >= 0.3 and (disp / pl if pl > 0 else 0) >= 0.5
            wins.append(dict(g={m: g[m] for m in MET}, walking=ok, npts=float(np.median([q[4] for q in p]))))
        out[name] = dict(tid=tid, frames=len(v), wins=wins)
    return mk, out
if __name__ == "__main__":
    mk, out = load(sys.argv[1])
    norm = [w for w in out.get("평소 걷기", {}).get("wins", []) if w["walking"]]
    def stats(base):
        X = np.array([[w["g"][m] for m in MET] for w in base])
        return {f"{m}_mu": float(X[:, i].mean()) for i, m in enumerate(MET)} | {f"{m}_sigma": max(float(X[:, i].std()), L.SIGMA_FLOOR[m]) for i, m in enumerate(MET)}
    for name in mk["order"]:
        o = out[name]; ws = o["wins"]; wk = [w for w in ws if w["walking"]]
        lv = collections.Counter()
        for w in wk:
            base = [n for n in norm if n is not w] if name == "평소 걷기" else norm
            if len(base) < 3: continue
            z = L.compute_zscore(w["g"], stats(base)); lv[L.classify_gait(L.gait_score(z, None)["combined"])] += 1
        mean = lambda m: np.mean([w["g"][m] for w in wk]) if wk else float("nan")
        print(f"{name}: tid {o['tid']}, 구간 {len(ws)}개 중 보행 {len(wk)}개 | 속도 {mean('speed'):.3f} 속도변동 {mean('speed_cv'):.3f} 흔들림 {mean('sway'):.3f} 멈춤 {mean('freeze_ratio'):.3f} | 판정 {dict(lv)}")
