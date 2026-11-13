"""Per-person view of the chosen posture pipeline: LORO accuracy by person, and leave-one-person-out."""
import sys, json
BEST = sys.argv[1]
sys.argv = ["x", "unused.json"]
src = open("mmw_compare.py", encoding="utf-8").read().split("res = {")[0]
exec(src)
from sklearn.metrics import accuracy_score
who = json.load(open(r"C:\Majubom\camera-pose\recordings\cam-mmw\participants.json", encoding="utf-8"))
X = prep(BEST)
person = groups.map(lambda r: who.get(r, "?"))
def fit_eval(tr, te):
    keep = X.columns[X[tr].notna().any()]
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, l2_regularization=1.0, class_weight="balanced", random_state=0)
    m.fit(X.loc[tr, keep], y_all[tr]); p = m.predict(X.loc[te, keep]); return p
out = {"loro_by_person": {}, "lopo": {}, "lopo_recall": {}}
pred = pd.Series(index=X.index, dtype=object)
for r in sorted(groups[mask].unique()):
    tr = mask & (groups != r); te = mask & (groups == r); pred[te] = fit_eval(tr, te)
for p in ("lee", "vic", "kim"):
    sel = mask & (person == p); out["loro_by_person"][p] = [float(accuracy_score(y_all[sel], pred[sel])), int(sel.sum())]
for p in ("lee", "vic", "kim"):
    tr = mask & (person != p); te = mask & (person == p); pp = fit_eval(tr, te)
    out["lopo"][p] = [float(accuracy_score(y_all[te], pp)), int(te.sum())]
    yt = y_all[te].to_numpy(); out["lopo_recall"][p] = {c: float(((yt == c) & (pp == c)).sum() / max(1, (yt == c).sum())) for c in CLASSES}
json.dump(out, open("posture_person.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
