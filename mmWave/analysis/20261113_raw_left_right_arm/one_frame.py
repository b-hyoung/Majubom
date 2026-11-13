# Real EMA + XGBoost example for one 누움 frame from a recording left out of training.
# Run from compare_scripts_20261111 (reuses sgxgb.py setup). Writes JSON to argv[1].
import sys, json
import numpy as np
from sklearn.metrics import log_loss
OUT = sys.argv[1]
exec(open("sgxgb.py", encoding="utf-8").read().split("R = {")[0])
XS = prep("P3 지수 평활(EMA α=0.1)")
TEST = "TEST_20261112_155312"  # vic, not used for training here
te = mask & (groups == TEST); tr = mask & ~te
keep = XS.columns[XS[tr].notna().any()]
yi = y_all[tr].map(idx).to_numpy()
m = boost("XGBoost")
m.fit(XS.loc[tr, keep], yi, sample_weight=compute_sample_weight("balanced", yi))

rows = XS.index[te & (y_all == "누움")]
i = rows[len(rows) // 2]
x = XS.loc[[i], keep]
res = dict(test=TEST, truth="누움", classes=CLASSES, label_index=int(idx["누움"]),
           one_hot=[int(c == "누움") for c in CLASSES],
           raw={k: (None if pd.isna(data.at[i, k]) else float(data.at[i, k])) for k in RAW},
           ema={k: (None if pd.isna(XS.at[i, k]) else float(XS.at[i, k])) for k in RAW},
           n_train=int(tr.sum()), n_test=int(te.sum()), by_trees={})
yte = y_all[te].map(idx).to_numpy()
for n in (1, 10, 50, 100, 300):
    p = m.predict_proba(x, iteration_range=(0, n))[0]
    pt = m.predict_proba(XS.loc[te, keep], iteration_range=(0, n))
    res["by_trees"][n] = dict(proba={c: round(float(v), 3) for c, v in zip(CLASSES, p)},
                              frame_loss=round(float(-np.log(max(p[idx["누움"]], 1e-12))), 3),
                              test_logloss=round(float(log_loss(yte, pt, labels=list(range(len(CLASSES))))), 3),
                              test_acc=round(float((pt.argmax(1) == yte).mean()), 3))
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok")
