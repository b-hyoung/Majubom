import sys, json, itertools, random, numpy as np
sys.argv = ["x", "unused.json"]
src = open("mmw_compare.py", encoding="utf-8").read().split("res = {")[0]
exec(src)
from sklearn.metrics import accuracy_score
who = json.load(open(r"C:\Majubom\camera-pose\recordings\cam-mmw\participants.json", encoding="utf-8"))
X = prep("P3 지수 평활(EMA α=0.1)")
lee = sorted(r for r in groups[mask].unique() if who.get(r) == "lee")
vic = [r for r in groups[mask].unique() if who.get(r) == "vic"]
rng = random.Random(0)
def fit_eval(train, test):
    tr = mask & groups.isin(train); te = mask & groups.isin(test)
    keep = X.columns[X[tr].notna().any()]
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, l2_regularization=1.0, class_weight="balanced", random_state=0)
    m.fit(X.loc[tr, keep], y_all[tr]); return accuracy_score(y_all[te], m.predict(X.loc[te, keep]))
out = {}
for k in (1, 2, 4, 6):
    accL, accV = [], []
    trials = []
    for held in lee:
        rest = [r for r in lee if r != held]
        combos = list(itertools.combinations(rest, k)); rng.shuffle(combos)
        trials += [(held, c) for c in combos[:1]]
    for held, c in trials:
        accL.append(fit_eval(list(c), [held])); accV.append(fit_eval(list(c), vic))
    out[k] = (np.mean(accL), np.mean(accV), len(trials))
    print(f"lee {k}개 학습: lee(안 본 녹화) {np.mean(accL):.3f} | vic {np.mean(accV):.3f} | 시험 {len(trials)}회", flush=True)
print(f"lee 전부({len(lee)}개) 학습 -> vic {fit_eval(lee, vic):.3f}")
