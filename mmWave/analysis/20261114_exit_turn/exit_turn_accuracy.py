# Accuracy for the exit-turn recording, leave-one-trial-out (each trial held out once, logistic regression fit on the rest).
import json, os, sys, numpy as np, runpy
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
ses, bedf, out = sys.argv[1:4]
sys.argv = ["x", ses, bedf, "_tmp.json"]
g = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "exit_turn_analyze.py"))          # reuses point extraction (win, zp90, marks)
win, marks = g["win"], g["marks"]
steps = json.load(open("_tmp.json", encoding="utf-8"))["steps"]
def loo(X, y, grp):
    X, y, grp = np.array(X, float), np.array(y), np.array(grp); p = np.zeros_like(y)
    for t in np.unique(grp):
        tr, te = grp != t, grp == t
        if len(set(y[tr])) < 2: p[te] = y[tr][0]; continue
        p[te] = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(X[tr], y[tr]).predict(X[te])
    return p
res = {}
# 1) sitting vs lying, 1-s windows in the held part of each state
SIT_START = {"앉으며 안전바", "앉기만", "앉음"}
X, y, G = [], [], []
for i, m in enumerate(marks[:-1]):
    if m["label"] in SIT_START:
        end = next(mm["t"] for mm in marks[i + 1:] if mm["label"] in ("누움", "끝")); state = 1
    elif m["label"] in ("누움", "안전바만"):
        end = marks[i + 1]["t"]; state = 0
    else:
        continue
    for k in np.arange(m["t"] + 6, end - 1, 1.0):
        P = win(k, k + 1)
        if len(P) >= 5:
            X.append([np.percentile(P[:, 2], 90)]); y.append(state); G.append(m["trial"])
p = loo(X, y, G); y = np.array(y)
res["sit_vs_lie_1s"] = dict(n=len(y), sit=int(y.sum()), lie=int((1 - y).sum()), acc=round(float((p == y).mean()), 3),
                            sit_recall=round(float((p[y == 1] == 1).mean()), 3), lie_recall=round(float((p[y == 0] == 0).mean()), 3))
# 2) rail grab or not, per cue step
R = [s for s in steps if s["step"] in ("앉으며 안전바", "안전바만", "앉기만", "앉음")]
yr = [1 if s["step"] in ("앉으며 안전바", "안전바만") else 0 for s in R]
pr = loo([[s["edge_moving"]] for s in R], yr, [s["trial"] for s in R])
res["rail_grab_step"] = dict(n=len(R), correct=int((pr == np.array(yr)).sum()), acc=round(float((pr == np.array(yr)).mean()), 3),
                             detail=[(s["trial"], s["step"], s["edge_moving"], int(q)) for s, q in zip(R, pr)])
# 3) turn outward vs inward, per cue step
T = [s for s in steps if s["step"] in ("밖으로 돌기", "안쪽으로 돌기")]
yt = [1 if s["step"] == "밖으로 돌기" else 0 for s in T]
for name, cols in [("edge_moving", ["edge_moving"]), ("cx_shift", ["cx_shift"]), ("corr_x_dop", ["corr_x_dop"]), ("edge+shift", ["edge_moving", "cx_shift"])]:
    pt = loo([[s[c] for c in cols] for s in T], yt, [s["trial"] for s in T])
    res[f"turn_out_vs_in[{name}]"] = dict(n=len(T), correct=int((pt == np.array(yt)).sum()), acc=round(float((pt == np.array(yt)).mean()), 3))
# 4) whole trial: exit attempt or not (max over the trial's steps)
trials = sorted({s["trial"] for s in steps})
Xt = [[max(s["edge_moving"] for s in steps if s["trial"] == t), max((s["cx_shift"] or 0) for s in steps if s["trial"] == t and s["step"] != "누움")] for t in trials]
ytt = [1 if next(s["kind"] for s in steps if s["trial"] == t) == "exit" else 0 for t in trials]
ptt = loo(Xt, ytt, trials)
res["trial_exit_vs_not"] = dict(n=len(trials), correct=int((ptt == np.array(ytt)).sum()), acc=round(float((ptt == np.array(ytt)).mean()), 3),
                                detail=[(t, next(s["kind"] for s in steps if s["trial"] == t), x, int(q)) for t, x, q in zip(trials, Xt, ptt)])
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for k, v in res.items(): print(k, {a: b for a, b in v.items() if a != "detail"})
for k in ("rail_grab_step", "trial_exit_vs_not"):
    for d in res[k]["detail"]: print(" ", k, d)
