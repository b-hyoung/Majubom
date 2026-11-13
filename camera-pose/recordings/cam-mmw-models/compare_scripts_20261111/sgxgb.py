"""Re-run posture tables with Savitzky-Golay + XGBoost as the chosen pair."""
import sys, json, time
sys.argv = ["x", "unused4.json"]
exec(open("prep2.py", encoding="utf-8").read().split("ref = Hist")[0])          # data, prep(), make(), loro(), models()
exec(open("clf2.py", encoding="utf-8").read().split("X = prep(")[0].split("exec(src)")[1])  # lgb/xgb/cb imports
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.base import clone
who = json.load(open(r"C:\Majubom\camera-pose\recordings\cam-mmw\participants.json", encoding="utf-8"))
person = groups.map(lambda r: who.get(r, "?"))
idx = {c: i for i, c in enumerate(CLASSES)}
def boost(name):
    if name == "XGBoost": return xgb.XGBClassifier(n_estimators=300, learning_rate=0.05, max_depth=6, reg_lambda=1.0, tree_method="hist", random_state=0, n_jobs=-1)
    if name == "LightGBM": return lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31, reg_lambda=1.0, class_weight="balanced", random_state=0, verbose=-1)
    return cb.CatBoostClassifier(iterations=300, learning_rate=0.05, depth=6, l2_leaf_reg=1.0, auto_class_weights="Balanced", random_seed=0, verbose=0, thread_count=-1)
def fit(name, Xtr, ytr):
    if name in ("XGBoost", "LightGBM", "CatBoost"):
        yi = ytr.map(idx).to_numpy(); m = boost(name)
        m.fit(Xtr, yi, sample_weight=compute_sample_weight("balanced", yi)) if name == "XGBoost" else m.fit(Xtr, yi)
        return lambda Xte: np.array(CLASSES)[np.asarray(m.predict(Xte)).ravel().astype(int)], lambda Xte: pd.DataFrame(m.predict_proba(Xte), index=Xte.index, columns=CLASSES)
    if name == "규칙 판정(높이 문턱)":
        return (lambda Xte: rule_predict(Xtr, ytr, Xte)), None
    m = clone(models()[name])
    if name.startswith("SVM") and len(Xtr) > 8000: s = Xtr.sample(8000, random_state=0); m.fit(s, ytr[s.index])
    else: m.fit(Xtr, ytr)
    return m.predict, None
def run(X, name, want_proba=False, split=None):
    preds = pd.Series(index=X.index, dtype=object); proba = pd.DataFrame(index=X.index, columns=CLASSES, dtype=float); per = {}
    folds = sorted(groups[mask].unique()) if split is None else split
    for r in folds:
        te = mask & ((groups == r) if split is None else (person == r)); tr = mask & ~te
        keep = X.columns[X[tr].notna().any()]
        p, pp = fit(name, X.loc[tr, keep], y_all[tr]); preds[te] = p(X.loc[te, keep])
        if want_proba and pp: proba.loc[te] = pp(X.loc[te, keep]).to_numpy()
        per[r] = accuracy_score(y_all[te], preds[te])
    yt, yp = y_all[mask], preds[mask]
    out = dict(acc=accuracy_score(yt, yp), f1=f1_score(yt, yp, average="macro", labels=CLASSES), per=per,
               recall={c: float(((yt == c) & (yp == c)).sum() / (yt == c).sum()) for c in CLASSES}, cm=confusion_matrix(yt, yp, labels=CLASSES).tolist())
    return out, preds, proba
R = {"prep": {}, "clf": {}}
PREPS = {"Savitzky-Golay": make("P9"), "EMA": prep("P3 지수 평활(EMA α=0.1)"), "이동 평균": prep("P1 이동 평균(1초)"), "칼만 필터": prep("P4 칼만 필터")}
for k, X in PREPS.items():
    t = time.time(); R["prep"][k] = run(X, "XGBoost")[0]; print("prep", k, round(R["prep"][k]["acc"], 4), round(R["prep"][k]["f1"], 4), f"{time.time()-t:.0f}s", flush=True)
XS = PREPS["Savitzky-Golay"]
for name in ["XGBoost", "LightGBM", "HistGradientBoosting", "MLP 신경망(64-32)", "랜덤 포레스트", "CatBoost", "SVM(RBF)", "k-최근접 이웃(k=15)", "로지스틱 회귀", "결정 트리", "규칙 판정(높이 문턱)"]:
    t = time.time(); o, preds, proba = run(XS, name, want_proba=(name == "XGBoost")); R["clf"][name] = o
    print("clf", name, round(o["acc"], 4), round(o["f1"], 4), f"{time.time()-t:.0f}s", flush=True)
    if name == "XGBoost": bestp, bestpr = preds, proba
    json.dump(R, open("sgxgb.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
R["by_person_loro"] = {p: float(accuracy_score(y_all[mask & (person == p)], bestp[mask & (person == p)])) for p in ("lee", "vic", "kim")}
lo, _, _ = run(XS, "XGBoost", split=["lee", "vic", "kim"]); R["lopo"] = lo["per"]
lp = {}
for p in ("lee", "vic", "kim"):
    te = mask & (person == p); tr = mask & ~te; keep = XS.columns[XS[tr].notna().any()]
    pr, _ = fit("XGBoost", XS.loc[tr, keep], y_all[tr]); yp = pr(XS.loc[te, keep]); yt = y_all[te].to_numpy()
    lp[p] = {c: float(((yt == c) & (yp == c)).sum() / max(1, (yt == c).sum())) for c in CLASSES}
R["lopo_recall"] = lp
pool = {}
for c in CLASSES:
    sel = mask & (y_all == c); pr = bestpr[sel]; pd_ = bestp[sel]
    pool[c] = dict(n=int(sel.sum()), raw={k: float(data.loc[sel, k].median()) for k in ["mmw_z", "mmw_head_z", "mmw_npts"]},
                   pre={k: float(XS.loc[sel, k].median()) for k in ["mmw_z", "mmw_head_z", "mmw_npts"]},
                   prob={k: float(v) for k, v in pr.mean().sort_values(ascending=False).items()}, pred={k: int(v) for k, v in pd_.value_counts().items()})
R["pooled"] = pool
json.dump(R, open("sgxgb.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("done", flush=True)
