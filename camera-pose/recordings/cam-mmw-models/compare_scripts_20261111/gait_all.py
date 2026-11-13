"""All real gait recordings (lee, kim): current vs proposed gate/sway; four combining rules; same-session baseline."""
import collections, json
src = open("sway_new.py", encoding="utf-8").read().split("W = track(")[0]
exec(src)
G = [("lee", "11-05", track("S3_20261105_121146", [("평소", 76.5, 138.5), ("천천히", 163.3, 225.3)], True) + track("S3_20261105_124521", [("종종걸음", 9.1, 93.2)], True)),
     ("lee", "11-12 ①", track("TEST_20261112_164724", mranges("TEST_20261112_164724"), False) + track("TEST_20261112_165534", mranges("TEST_20261112_165534"), False)),
     ("lee", "11-12 ②", track("TEST_20261112_173849", mranges("TEST_20261112_173849"), False)),
     ("kim", "11-12", track("TEST_20261112_175646", mranges("TEST_20261112_175646"), False))]
base_ok = lambda w: w["speed"] >= 0.2 and w["disp"] >= 0.3
CFG = {"지금": (lambda w: w["straight"] >= 0.5, "sway_old"), "수정안": (lambda w: w["st3"] >= 0.5, "sway_new")}
RULES = {"양방향 |z| 합 ≥ 3 (예전)": lambda sc, z: z["total_abs"] >= 3, "A 위험 방향 합 ≥ 2": lambda sc, z: sc["n_a"] >= 2,
         "S 속도만 ≥ 2": lambda sc, z: sc["n_s"] >= 2, "A+S 큰 쪽 ≥ 2 (지금 서버)": lambda sc, z: sc["combined"] >= 2}
LABS = ["평소", "천천히", "종종걸음"]
out = {"windows": {}, "speed": {}, "cfg": {}}
for who, tag, W in G:
    key = f"{who} {tag}"
    out["windows"][key] = {l: sum(1 for w in W if w["lab"] == l) for l in LABS}
    out["speed"][key] = {l: round(float(np.mean([w["speed"] for w in W if w["lab"] == l])), 2) for l in LABS if any(w["lab"] == l for w in W)}
for cname, (gate, sk) in CFG.items():
    res = {"pass": {}, "rules": {r: collections.defaultdict(lambda: [0, 0]) for r in RULES}, "levels": collections.defaultdict(collections.Counter), "nobase": []}
    for who, tag, W in G:
        key = f"{who} {tag}"
        ok = [w for w in W if base_ok(w) and gate(w)]
        res["pass"][key] = {l: sum(1 for w in ok if w["lab"] == l) for l in LABS}
        norm = [w for w in ok if w["lab"] == "평소"]
        if len(norm) < 4: res["nobase"].append(key); continue
        def stats(b):
            X = {"speed": [w["speed"] for w in b], "speed_cv": [w["speed_cv"] for w in b], "sway": [w[sk] for w in b], "freeze_ratio": [w["freeze_ratio"] for w in b]}
            return {f"{m}_mu": float(np.mean(v)) for m, v in X.items()} | {f"{m}_sigma": max(float(np.std(v)), L.SIGMA_FLOOR[m]) for m, v in X.items()}
        for w in ok:
            b = [n for n in norm if not (n["ses"] == w["ses"] and n["lab"] == w["lab"] and abs(n["i"] - w["i"]) <= 1)] if w["lab"] == "평소" else norm
            raw = {"speed": w["speed"], "speed_cv": w["speed_cv"], "sway": w[sk], "freeze_ratio": w["freeze_ratio"]}
            z = L.compute_zscore(raw, stats(b)); sc = L.gait_score(z, None)
            for r, f in RULES.items():
                cell = res["rules"][r][f"{who}|{w['lab']}"]; cell[1] += 1; cell[0] += (f(sc, z) == (w["lab"] != "평소"))
            res["levels"][f"{who}|{w['lab']}"][L.classify_gait(sc["combined"])] += 1
    summ = {}
    for r, d in res["rules"].items():
        tot = [sum(v[0] for v in d.values()), sum(v[1] for v in d.values())]
        per = {}
        for who in ("lee", "kim"):
            c = [sum(v[0] for k, v in d.items() if k.startswith(who)), sum(v[1] for k, v in d.items() if k.startswith(who))]
            per[who] = c
        summ[r] = dict(total=tot, per=per, cells={k: v for k, v in d.items()})
    out["cfg"][cname] = dict(passed=res["pass"], nobase=res["nobase"], rules=summ, levels={k: dict(v) for k, v in res["levels"].items()})
json.dump(out, open("gait_all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out["windows"], ensure_ascii=False)); print(json.dumps(out["speed"], ensure_ascii=False))
for c, v in out["cfg"].items():
    print("##", c, "통과", json.dumps(v["passed"], ensure_ascii=False), "기준선 부족", v["nobase"])
    for r, s in v["rules"].items():
        t = s["total"]; print(f"  {r}: 전체 {t[0]}/{t[1]} = {100*t[0]/max(t[1],1):.1f}% | lee {s['per']['lee']} | kim {s['per']['kim']} | {s['cells']}")
    print("  단계", json.dumps(v["levels"], ensure_ascii=False))
