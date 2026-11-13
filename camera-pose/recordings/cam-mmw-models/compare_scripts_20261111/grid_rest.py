import sys, json, time
exec(open("sgxgb.py", encoding="utf-8").read().split("R = {")[0])
R = {}
for pk, pn in (("이동 평균", "P1 이동 평균(1초)"), ("칼만 필터", "P4 칼만 필터")):
    X = prep(pn)
    for cn in ("CatBoost", "MLP 신경망(64-32)"):
        t = time.time(); o = run(X, cn)[0]; R[f"{pk}|{cn}"] = o
        print(pk, cn, round(o["acc"], 4), f"{time.time()-t:.0f}s", flush=True)
        json.dump(R, open("grid_rest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
