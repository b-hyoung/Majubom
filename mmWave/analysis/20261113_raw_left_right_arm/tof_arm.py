# ToF: arm on rail vs down (right side lying). Raw 64 distances per sensor; 1-s windows, median per zone.
import json, sys, datetime, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
S, ses = sys.argv[1], sys.argv[2]
OFF = 0.29                                           # Pi clock ahead of laptop (s)
T = json.load(open(f"{S}/tof_arm.json")); M = json.load(open(f"{ses}/marks.json", encoding="utf-8-sig"))
day = "2026-11-13"
ts = lambda s: datetime.datetime.fromisoformat(s).timestamp()
marks = [(m["label"], ts(f"{day}T{m['time']}")) for m in M["marks"]]
rows = [(ts(r[0]) - OFF, r[1], np.array(r[2:66], float)) for r in T["rows"]]
def lab_at(t):
    l, s, i = None, None, None
    for k, (lab, tt) in enumerate(marks):
        if tt <= t: l, s, i = lab, tt, k
    return l, (t - s) if s else None, i
X, Y, G = [], [], []
for c, (lab, tt) in enumerate(marks):
    if lab not in ("팔 안전바", "팔 내림"): continue
    end = marks[c + 1][1]
    for k in range(int((end - tt - 4) // 1)):
        a = tt + 4 + k; w = [r for r in rows if a <= r[0] < a + 1]
        v = []
        for sen in ("tof1", "tof2"):
            d = [r[2] for r in w if r[1] == sen]
            if not d: break
            D = np.array(d); D[D < 0] = np.nan
            v.append(np.nan_to_num(np.nanmedian(D, 0), nan=-1))
        if len(v) < 2: continue
        X.append(np.concatenate(v)); Y.append(1 if lab == "팔 안전바" else 0); G.append(c)
X, Y, G = np.array(X), np.array(Y), np.array(G); cyc = (G - G.min()) // 2
res = {"windows": {"on_rail": int(Y.sum()), "down": int((1 - Y).sum())}}
for name, mk in (("logistic", lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=0.1))),
                 ("random_forest", lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=3, random_state=0))):
    p = np.zeros_like(Y)
    for c in np.unique(cyc):
        tr, te = cyc != c, cyc == c; p[te] = mk().fit(X[tr], Y[tr]).predict(X[te])
    seg = [float((p[G == g] == Y[G == g]).mean()) for g in np.unique(G)]
    res[name] = dict(acc=round(float((p == Y).mean()), 3), segments_correct=f"{sum(s >= 0.5 for s in seg)}/{len(seg)}",
                     per_segment={int(g): round(s, 3) for g, s in zip(np.unique(G), seg)})
# which zones differ most (mean of up minus down, mm), consistent across all cycles?
diff = []
for j in range(128):
    ups = [X[(G == g), j].mean() for g in np.unique(G) if Y[G == g][0]]; dns = [X[(G == g), j].mean() for g in np.unique(G) if not Y[G == g][0]]
    diff.append((j, np.mean(ups) - np.mean(dns), max(ups) < min(dns) or max(dns) < min(ups)))
res["zones_fully_separating"] = [dict(sensor="tof1" if j < 64 else "tof2", zone=j % 64, row=(j % 64) // 8, col=j % 8, up_minus_down_mm=round(float(d), 1)) for j, d, s in diff if s]
json.dump(res, open(f"{S}/tof_arm_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1))
