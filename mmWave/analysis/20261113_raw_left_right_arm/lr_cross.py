import json, sys, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
S = sys.argv[1]
D = {p: np.load(f"{S}/lr_{p}_feats.npz") for p in ("park", "lee")}
FN = list(D["park"]["FN"])
models = {"logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
          "random_forest": lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=3, random_state=0)}
out = {}
for tr, te in (("park", "lee"), ("lee", "park")):
    A, B = D[tr], D[te]
    for name, mk in models.items():
        m = mk().fit(A["X"], A["Y"]); p = m.predict(B["X"])
        seg = [float((p[B["G"] == g] == B["Y"][B["G"] == g]).mean()) for g in np.unique(B["G"])]
        out[f"{tr}->{te} {name}"] = dict(window_acc=round(float((p == B["Y"]).mean()), 3), segments_correct=f"{sum(s >= 0.5 for s in seg)}/{len(seg)}")
# single-feature check: do the two features that separated park also separate lee, same direction?
sep = {}
for f in ("x_std", "z_snrw", "n", "log_snr", "frac_left_of_bed_center"):
    j = FN.index(f); row = {}
    for p, d in D.items():
        L = [float(d["X"][d["G"] == g, j].mean()) for g in np.unique(d["G"]) if d["Y"][d["G"] == g][0] == 1]
        R = [float(d["X"][d["G"] == g, j].mean()) for g in np.unique(d["G"]) if d["Y"][d["G"] == g][0] == 0]
        row[p] = dict(L=[round(x, 3) for x in sorted(L)], R=[round(x, 3) for x in sorted(R)],
                      separates=bool(max(L) < min(R) or max(R) < min(L)), L_lower=bool(np.mean(L) < np.mean(R)))
    sep[f] = row
out["features"] = sep
json.dump(out, open(f"{S}/lr_cross.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
