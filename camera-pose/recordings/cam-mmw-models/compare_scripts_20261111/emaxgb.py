import sys, json
exec(open("sgxgb.py", encoding="utf-8").read().split("R = {")[0])
XS = prep("P3 지수 평활(EMA α=0.1)")
o, bestp, bestpr = run(XS, "XGBoost", want_proba=True)
R = dict(acc=o["acc"], f1=o["f1"], recall=o["recall"], cm=o["cm"], per=o["per"])
R["by_person_loro"] = {p: float(accuracy_score(y_all[mask & (person == p)], bestp[mask & (person == p)])) for p in ("lee", "vic", "kim")}
lp, lo = {}, {}
for p in ("lee", "vic", "kim"):
    te = mask & (person == p); tr = mask & ~te; keep = XS.columns[XS[tr].notna().any()]
    pr, _ = fit("XGBoost", XS.loc[tr, keep], y_all[tr]); yp = pr(XS.loc[te, keep]); yt = y_all[te].to_numpy()
    lo[p] = float((yp == yt).mean()); lp[p] = {c: float(((yt == c) & (yp == c)).sum() / max(1, (yt == c).sum())) for c in CLASSES}
R["lopo"], R["lopo_recall"] = lo, lp
R["pooled"] = {}
for c in CLASSES:
    sel = mask & (y_all == c); pr = bestpr[sel]
    R["pooled"][c] = dict(n=int(sel.sum()), raw={k: float(data.loc[sel, k].median()) for k in ["mmw_z", "mmw_head_z", "mmw_npts"]},
                          pre={k: float(XS.loc[sel, k].median()) for k in ["mmw_z", "mmw_head_z", "mmw_npts"]},
                          prob={k: float(v) for k, v in pr.mean().sort_values(ascending=False).items()}, pred={k: int(v) for k, v in bestp[sel].value_counts().items()})
json.dump(R, open("emaxgb.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
