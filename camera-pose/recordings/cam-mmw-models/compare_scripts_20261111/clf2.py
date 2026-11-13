"""LightGBM / XGBoost / CatBoost vs the same setup (9 recordings, EMA, leave-one-recording-out)."""
import sys, json, time
sys.argv = ["x", "unused3.json"]
src = open("mmw_compare.py", encoding="utf-8").read().split("res = {")[0]
exec(src)
import lightgbm as lgb, xgboost as xgb, catboost as cb
from sklearn.utils.class_weight import compute_sample_weight
X = prep("P3 지수 평활(EMA α=0.1)")
idx = {c: i for i, c in enumerate(CLASSES)}
def fit_pred(name, Xtr, ytr, Xte):
    yi = ytr.map(idx).to_numpy(); w = compute_sample_weight("balanced", yi)
    if name == "LightGBM":
        m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31, reg_lambda=1.0, class_weight="balanced", random_state=0, verbose=-1).fit(Xtr, yi)
    elif name == "XGBoost":
        m = xgb.XGBClassifier(n_estimators=300, learning_rate=0.05, max_depth=6, reg_lambda=1.0, tree_method="hist", random_state=0, n_jobs=-1).fit(Xtr, yi, sample_weight=w)
    else:
        m = cb.CatBoostClassifier(iterations=300, learning_rate=0.05, depth=6, l2_leaf_reg=1.0, auto_class_weights="Balanced", random_seed=0, verbose=0, thread_count=-1).fit(Xtr, yi)
    p = np.asarray(m.predict(Xte)).ravel().astype(int); return np.array(CLASSES)[p]
res = {}
for name in ("LightGBM", "XGBoost", "CatBoost"):
    t = time.time(); preds = pd.Series(index=X.index, dtype=object); per = {}
    for r in sorted(groups[mask].unique()):
        tr = mask & (groups != r); te = mask & (groups == r)
        keep = X.columns[X[tr].notna().any()]
        preds[te] = fit_pred(name, X.loc[tr, keep], y_all[tr], X.loc[te, keep]); per[r] = accuracy_score(y_all[te], preds[te])
    yt, yp = y_all[mask], preds[mask]
    res[name] = dict(acc=accuracy_score(yt, yp), f1=f1_score(yt, yp, average="macro", labels=CLASSES), per_rec=per,
                     recall={c: float(((yt == c) & (yp == c)).sum() / (yt == c).sum()) for c in CLASSES}, sec=time.time() - t)
    print(name, round(res[name]["acc"], 4), round(res[name]["f1"], 4), f"{res[name]['sec']:.0f}s", flush=True)
    json.dump(res, open("clf2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
