import collections
src = open("sway_new.py", encoding="utf-8").read().split("W = track(")[0]
exec(src)
W = track("TEST_20261112_175646", mranges("TEST_20261112_175646"), False)
base_ok = lambda w: w["speed"] >= 0.2 and w["disp"] >= 0.3
for lab in ["평소", "천천히", "종종걸음"]:
    ws = [x for x in W if x["lab"] == lab]
    print(f"{lab}: 구간 {len(ws)}, 지금 검사 통과 {sum(base_ok(x) and x['straight'] >= 0.5 for x in ws)}, 3초 검사 통과 {sum(base_ok(x) and x['st3'] >= 0.5 for x in ws)}, 평균 속도 {np.mean([x['speed'] for x in ws]):.2f}")
CFG = {"① 지금": (lambda w: w["straight"] >= 0.5, "sway_old"), "③ 3초 검사 + 새 흔들림": (lambda w: w["st3"] >= 0.5, "sway_new")}
for name, (gate, sk) in CFG.items():
    ok = [w for w in W if base_ok(w) and gate(w)]; norm = [w for w in ok if w["lab"] == "평소"]
    if len(norm) < 4: print(name, "평소 통과 구간", len(norm), "개라 기준선 부족"); continue
    def stats(b):
        X = {"speed": [w["speed"] for w in b], "speed_cv": [w["speed_cv"] for w in b], "sway": [w[sk] for w in b], "freeze_ratio": [w["freeze_ratio"] for w in b]}
        return {f"{m}_mu": float(np.mean(v)) for m, v in X.items()} | {f"{m}_sigma": max(float(np.std(v)), L.SIGMA_FLOOR[m]) for m, v in X.items()}
    lv = collections.defaultdict(collections.Counter); c = t = 0
    for w in ok:
        b = [n for n in norm if not (n["lab"] == w["lab"] and abs(n["i"] - w["i"]) <= 1)] if w["lab"] == "평소" else norm
        if len(b) < 3: continue
        raw = {"speed": w["speed"], "speed_cv": w["speed_cv"], "sway": w[sk], "freeze_ratio": w["freeze_ratio"]}
        l = L.classify_gait(L.gait_score(L.compute_zscore(raw, stats(b)), None)["combined"]); lv[w["lab"]][l] += 1; t += 1; c += (l == "normal") == (w["lab"] == "평소")
    print(name, f"정확도 {c}/{t} = {100*c/t:.1f}%", {k: dict(v) for k, v in lv.items()})
