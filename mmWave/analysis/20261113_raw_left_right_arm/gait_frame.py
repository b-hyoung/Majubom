# One real 6-second gait window, judged the same way as the site's result table (수정안: 3s straightness gate, local sway,
# same-session 평소 windows as baseline). Run from compare_scripts_20261111. Prints JSON to stdout.
import json
exec(open("gait_all.py", encoding="utf-8").read().split("LABS = [")[0])
SES, WHO = "lee 11-12 ②", "lee"
W = [w for who, tag, ws in G if f"{who} {tag}" == SES for w in ws]
gate, sk = CFG["수정안"]
ok = [w for w in W if base_ok(w) and gate(w)]
norm = [w for w in ok if w["lab"] == "평소"]
MET = ["speed", "speed_cv", "sway", "freeze_ratio"]
val = lambda w: {"speed": w["speed"], "speed_cv": w["speed_cv"], "sway": w[sk], "freeze_ratio": w["freeze_ratio"]}
X = {m: [val(n)[m] for n in norm] for m in MET}
st = {f"{m}_mu": float(np.mean(v)) for m, v in X.items()} | {f"{m}_sigma": max(float(np.std(v)), L.SIGMA_FLOOR[m]) for m, v in X.items()}
out = {"session": SES, "n_base": len(norm), "examples": {}}
for lab in ("평소", "천천히"):
    ws = [w for w in ok if w["lab"] == lab]
    w = ws[len(ws) // 2]
    b = [n for n in norm if not (n["ses"] == w["ses"] and abs(n["i"] - w["i"]) <= 1)] if lab == "평소" else norm
    Xb = {m: [val(n)[m] for n in b] for m in MET}
    stb = {f"{m}_mu": float(np.mean(v)) for m, v in Xb.items()} | {f"{m}_sigma": max(float(np.std(v)), L.SIGMA_FLOOR[m]) for m, v in Xb.items()}
    raw = val(w); z = L.compute_zscore(raw, stb); sc = L.gait_score(z, None)
    out["examples"][lab] = dict(ses=w["ses"], i=w["i"], n_windows=len(ws), n_base=len(b), disp=round(float(w["disp"]), 2),
        path=round(float(w["disp"] / w["straight"]), 2) if w["straight"] else None, st3=round(float(w["st3"]), 2),
        metrics={m: round(float(raw[m]), 3) for m in MET},
        mu={m: round(stb[f"{m}_mu"], 3) for m in MET}, sigma={m: round(stb[f"{m}_sigma"], 3) for m in MET},
        sigma_floor={m: L.SIGMA_FLOOR[m] for m in MET},
        z={m: round(float(z[m]), 2) for m in MET}, total_abs=round(float(z["total_abs"]), 2),
        a=round(sc["a"], 2), s=round(sc["s"], 2), n_a=round(sc["n_a"], 2), n_s=round(sc["n_s"], 2),
        combined=round(sc["combined"], 2), sum_as=round(sc["n_a"] + sc["n_s"], 2), driver=sc["driver"],
        level=L.classify_gait(sc["combined"]), a_default=L.A_DEFAULT, s_default=L.S_DEFAULT)
print(json.dumps(out, ensure_ascii=False, indent=1))
