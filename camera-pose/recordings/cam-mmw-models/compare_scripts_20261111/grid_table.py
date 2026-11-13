import json, sys, io, numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
L = lambda f: json.load(open(f, encoding="utf-8"))
a, b, c, d, e = L("mmw_compare_9rec.json"), L("clf2.json"), L("prep2.json"), L("sgxgb.json"), L("grid_rest.json")
pr = lambda o: o.get("per_rec") or o.get("per")
cell = {
 ("EMA", "HistGB"): a["prep"]["P3 지수 평활(EMA α=0.1)"], ("이동 평균", "HistGB"): a["prep"]["P1 이동 평균(1초)"], ("칼만 필터", "HistGB"): a["prep"]["P4 칼만 필터"],
 ("Savitzky-Golay", "HistGB"): c["P9 Savitzky-Golay (1초, 2차, 과거 값만)"], ("EMA", "MLP"): a["clf"]["MLP 신경망(64-32)"],
 ("EMA", "CatBoost"): b["CatBoost"], ("Savitzky-Golay", "CatBoost"): d["clf"]["CatBoost"], ("Savitzky-Golay", "MLP"): d["clf"]["MLP 신경망(64-32)"],
 **{(p, "XGBoost"): d["prep"][p] for p in ("Savitzky-Golay", "EMA", "이동 평균", "칼만 필터")},
 **{(k.split("|")[0], "MLP" if "MLP" in k else "CatBoost"): v for k, v in e.items()}}
recs = sorted(pr(cell[("EMA", "HistGB")]))
keys = sorted(cell)
M = np.array([[pr(cell[k])[r] for r in recs] for k in keys])           # combos x recordings
rank = (-M).argsort(0).argsort(0) + 1                                   # rank per recording (1 = best)
out = []
for i, k in enumerate(keys):
    out.append(dict(pre=k[0], clf=k[1], acc=cell[k]["acc"], f1=cell[k]["f1"], mean_rank=float(rank[i].mean()), top4=int((rank[i] <= 4).sum()),
                    worst=float(M[i].min()), recall=cell[k]["recall"]))
out.sort(key=lambda o: -o["acc"])
for o in out: print(f"{o['pre']:15s} {o['clf']:9s} acc {100*o['acc']:.1f} f1 {100*o['f1']:.1f} 평균순위 {o['mean_rank']:.1f} top4 {o['top4']}/9 최저녹화 {100*o['worst']:.1f} 옆누움 {100*o['recall']['옆누움']:.0f}")
json.dump(out, open("grid16.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
