"""mmWave-only posture: preprocessing comparison then classifier comparison.
Leave-one-recording-out. Causal (past-only) filters, reset at track change."""
import glob, json, os, sys, time
import numpy as np, pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import KNeighborsClassifier
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier
from sklearn.neural_network import MLPClassifier

ROOT = r"C:\Majubom\camera-pose\recordings\cam-mmw"
OUT = sys.argv[1]
CLASSES = ["섬", "침대 위 앉음", "걸터앉음", "누움", "옆누움"]
RAW = ["mmw_z", "mmw_head_z", "mmw_speed", "mmw_vz", "mmw_npts", "mmw_bed_dist", "mmw_x", "mmw_y"]
W = 18  # ~1 s at 55 ms frames

recs = []
for d in sorted(glob.glob(os.path.join(ROOT, "TEST_*"))):
    df = pd.read_csv(os.path.join(d, "matched.csv"), usecols=["camera", "truth", "transition", "mmw_frame", "mmw_tid"] + RAW, low_memory=False)
    df = df.dropna(subset=["mmw_frame"]).drop_duplicates("mmw_frame").sort_values("mmw_frame").reset_index(drop=True)
    df["rec"] = os.path.basename(d)
    recs.append(df)
data = pd.concat(recs, ignore_index=True)
data["seg"] = (data["rec"] + "_" + data["mmw_tid"].astype(str))
# new segment whenever tid changes or frame gap > 20
brk = (data["seg"] != data["seg"].shift()) | (data["mmw_frame"].diff() > 20)
data["seg"] = brk.cumsum()


def kalman1d(x, q=0.01, r=0.05):
    out = np.empty_like(x); xh = np.nan; p = 1.0
    for i, v in enumerate(x):
        if np.isnan(xh):
            xh = v; p = 1.0
        else:
            p += q
            if not np.isnan(v):
                k = p / (p + r); xh = xh + k * (v - xh); p *= (1 - k)
        out[i] = xh
    return out


def prep(name):
    g = data.groupby("seg", sort=False)
    f = pd.DataFrame(index=data.index)
    if name == "P0 없음(순간값)":
        for c in RAW: f[c] = data[c]
    elif name == "P1 이동 평균(1초)":
        for c in RAW: f[c] = g[c].transform(lambda s: s.rolling(W, min_periods=1).mean())
    elif name == "P2 이동 중앙값(1초)":
        for c in RAW: f[c] = g[c].transform(lambda s: s.rolling(W, min_periods=1).median())
    elif name == "P3 지수 평활(EMA α=0.1)":
        for c in RAW: f[c] = g[c].transform(lambda s: s.ewm(alpha=0.1, ignore_na=True).mean())
    elif name == "P4 칼만 필터":
        for c in RAW: f[c] = g[c].transform(lambda s: pd.Series(kalman1d(s.to_numpy(float)), index=s.index))
    elif name == "P5 구간 통계(2초 평균·표준편차·최소·최대)":
        for c in RAW:
            r = g[c].transform(lambda s: s.rolling(2 * W, min_periods=1).mean()); f[c + "_mean"] = r
            f[c + "_std"] = g[c].transform(lambda s: s.rolling(2 * W, min_periods=2).std())
            f[c + "_min"] = g[c].transform(lambda s: s.rolling(2 * W, min_periods=1).min())
            f[c + "_max"] = g[c].transform(lambda s: s.rolling(2 * W, min_periods=1).max())
    elif name == "P6 이동 중앙값 + 구간 통계":
        med = {c: g[c].transform(lambda s: s.rolling(W, min_periods=1).median()) for c in RAW}
        tmp = pd.DataFrame(med); tmp["seg"] = data["seg"]; gg = tmp.groupby("seg", sort=False)
        for c in RAW:
            f[c + "_med"] = med[c]
            f[c + "_std"] = gg[c].transform(lambda s: s.rolling(2 * W, min_periods=2).std())
            f[c + "_min"] = gg[c].transform(lambda s: s.rolling(2 * W, min_periods=1).min())
            f[c + "_max"] = gg[c].transform(lambda s: s.rolling(2 * W, min_periods=1).max())
    return f


mask = data["truth"].isin(CLASSES) & (data["transition"] == 0)
y_all = data["truth"]
groups = data["rec"]


def models():
    imp = lambda: SimpleImputer(strategy="median")
    return {
        "규칙 판정(높이 문턱)": None,
        "로지스틱 회귀": make_pipeline(imp(), StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced")),
        "k-최근접 이웃(k=15)": make_pipeline(imp(), StandardScaler(), KNeighborsClassifier(15)),
        "SVM(RBF)": make_pipeline(imp(), StandardScaler(), SVC(C=1.0, class_weight="balanced")),
        "결정 트리": make_pipeline(imp(), DecisionTreeClassifier(max_depth=8, class_weight="balanced", random_state=0)),
        "랜덤 포레스트": make_pipeline(imp(), RandomForestClassifier(300, min_samples_leaf=5, class_weight="balanced", n_jobs=-1, random_state=0)),
        "HistGradientBoosting": HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, l2_regularization=1.0, class_weight="balanced", random_state=0),
        "MLP 신경망(64-32)": make_pipeline(imp(), StandardScaler(), MLPClassifier((64, 32), max_iter=500, early_stopping=True, random_state=0)),
    }


def rule_predict(Xtr, ytr, Xte):
    # learn 1-D height thresholds on the training fold: class order by median z
    zc = [c for c in Xtr.columns if c.startswith("mmw_z")][0]
    med = pd.Series(Xtr[zc].to_numpy(), index=ytr.index).groupby(ytr).median().sort_values()
    cuts = (med.values[:-1] + med.values[1:]) / 2
    idx = np.searchsorted(cuts, Xte[zc].fillna(med.median()).to_numpy())
    return med.index.to_numpy()[idx]


def loro(X, model_name, model):
    preds = pd.Series(index=X.index, dtype=object); per = {}
    for r in sorted(groups[mask].unique()):
        tr = mask & (groups != r); te = mask & (groups == r)
        Xtr = X[tr]; Xte = X[te]
        keep = Xtr.columns[Xtr.notna().any()]
        Xtr, Xte = Xtr[keep], Xte[keep]
        if model is None:
            p = rule_predict(Xtr, y_all[tr], Xte)
        else:
            Xf, yf = Xtr, y_all[tr]
            if model_name.startswith("SVM") and len(Xf) > 8000:
                Xf = Xf.sample(8000, random_state=0); yf = yf[Xf.index]
            m = __import__("sklearn").base.clone(model); m.fit(Xf, yf); p = m.predict(Xte)
        preds[te] = p
        per[r] = accuracy_score(y_all[te], p)
    yt, yp = y_all[mask], preds[mask]
    rec = {c: float(((yt == c) & (yp == c)).sum() / max(1, (yt == c).sum())) for c in CLASSES}
    return dict(acc=accuracy_score(yt, yp), f1=f1_score(yt, yp, average="macro", labels=CLASSES),
                per_rec=per, recall=rec, cm=confusion_matrix(yt, yp, labels=CLASSES).tolist())


res = {"n_frames": int(mask.sum()), "per_class_n": y_all[mask].value_counts().to_dict(), "prep": {}, "clf": {}}
PREPS = ["P0 없음(순간값)", "P1 이동 평균(1초)", "P2 이동 중앙값(1초)", "P3 지수 평활(EMA α=0.1)", "P4 칼만 필터",
         "P5 구간 통계(2초 평균·표준편차·최소·최대)", "P6 이동 중앙값 + 구간 통계"]
feats = {}
ref = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, l2_regularization=1.0, class_weight="balanced", random_state=0)
for p in PREPS:
    t = time.time(); feats[p] = prep(p)
    res["prep"][p] = loro(feats[p], "HGB", ref)
    print(p, round(res["prep"][p]["acc"], 4), round(res["prep"][p]["f1"], 4), f"{time.time()-t:.0f}s", flush=True)
best = max(PREPS, key=lambda p: res["prep"][p]["f1"])
res["best_prep"] = best
print("best prep:", best, flush=True)
for name, m in models().items():
    t = time.time(); res["clf"][name] = loro(feats[best], name, m)
    print(name, round(res["clf"][name]["acc"], 4), round(res["clf"][name]["f1"], 4), f"{time.time()-t:.0f}s", flush=True)
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
