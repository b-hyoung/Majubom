import json, sys, numpy as np, pandas as pd
sys.argv = ["x", "unused.json"]
src = open("mmw_compare.py", encoding="utf-8").read().split("res = {")[0]
exec(src)
X = prep("P3 지수 평활(EMA α=0.1)")
out = []
rng = np.random.default_rng(0)
for r in sorted(groups[mask].unique()):
    tr = mask & (groups != r); te = mask & (groups == r)
    keep = X.columns[X[tr].notna().any()]
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, l2_regularization=1.0, class_weight="balanced", random_state=0).fit(X.loc[tr, keep], y_all[tr])
    P = m.predict_proba(X.loc[te, keep])
    pdf = pd.DataFrame(P, index=X.index[te], columns=m.classes_)
    for c in CLASSES:
        rows = pdf.index[(y_all[te] == c).to_numpy()]
        if len(rows) == 0: continue
        i = rows[len(rows)//2]
        out.append(dict(rec=r, truth=c, raw={k: data.at[i, k] for k in RAW}, ema={k: X.at[i, k] for k in RAW},
                        proba={k: round(float(v), 3) for k, v in pdf.loc[i].items()}, pred=pdf.loc[i].idxmax()))
json.dump(out, open("mmw_examples.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
