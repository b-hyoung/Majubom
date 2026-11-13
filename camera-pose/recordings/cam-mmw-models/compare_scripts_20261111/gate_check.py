"""Walking-gate thresholds: pass rate on real walking vs non-walking (posture recordings)."""
import json, glob, os, sys, collections, datetime as dt, numpy as np, pandas as pd
sys.path.insert(0, r"C:\Majubom\mmWave"); import send_mmw as M
SES = r"C:\Users\tmdal\Downloads\mmw_viewer\sessions"; CAM = r"C:\Majubom\camera-pose\recordings\cam-mmw"
def frames(ses):
    for line in open(f"{SES}/{ses}/frames.jsonl", encoding="utf-8"):
        f = json.loads(line); yield f
def windows(seq):
    """seq: sorted [(t,x,y,speed)] of one track; split on gaps >0.5s; 109-frame windows hop 55."""
    out, run = [], []
    for p in seq:
        if run and p[0] - run[-1][0] > 0.5:
            out += cut(run); run = []
        run.append(p)
    return out + cut(run)
def cut(run):
    res = []
    for s in range(0, len(run) - 109 + 1, 55):
        p = run[s:s + 109]; xy = np.array([[q[1], q[2]] for q in p]); sp = np.array([q[3] for q in p])
        seglen = np.linalg.norm(np.diff(xy, axis=0), axis=1); pl = float(seglen.sum()); disp = float(np.linalg.norm(xy[-1] - xy[0]))
        h = 54; st3 = []
        for a, b in ((0, h), (h, 108)):
            pl3 = float(seglen[a:b].sum()); d3 = float(np.linalg.norm(xy[b] - xy[a])); st3.append(d3 / pl3 if pl3 > 0 else 0)
        g = M._gait_from_samples([(q[0], q[1], q[2], 0.0, q[3], 0.0) for q in p])
        res.append(dict(speed=float(sp.mean()), disp=disp, straight=disp / pl if pl > 0 else 0, st3=min(st3), sway=g["sway"]))
    return res
W = collections.defaultdict(list)
# walking: gait recordings (lee today) + 11-05 S3 (lee): walker = longest-path track inside marked/known ranges
def walker(ses, ranges, rel=False):
    st = json.load(open(f"{SES}/{ses}/session.json", encoding="utf-8"))["started_at"]
    per = collections.defaultdict(list)
    for f in frames(ses):
        t = f["t"] - (st if rel else 0)
        if any(a <= t < b for a, b in ranges):
            for tr in f.get("tracks") or []: per[tr["tid"]].append((f["t"], tr["world"][0], tr["world"][1], tr["speed"]))
    def path(v): xy = np.array([[q[1], q[2]] for q in sorted(v)]); return np.linalg.norm(np.diff(xy, axis=0), axis=1).sum() if len(v) > 1 else 0
    tid = max(per, key=lambda k: path(per[k])); return windows(sorted(per[tid]))
def mark_ranges(ses):
    mk = json.load(open(f"{SES}/{ses}/marks.json", encoding="utf-8-sig")); day = ses.split("_")[1]
    ts = lambda h: dt.datetime.strptime(day + " " + h, "%Y%m%d %H:%M:%S.%f").timestamp()
    seg = sorted((ts(v), k) for k, v in mk["marks"].items()); return [(a + 3, b) for (a, _), (b, _) in zip(seg, seg[1:])]
W["걷기: 오늘 고리 (lee)"] += walker("TEST_20261112_164724", mark_ranges("TEST_20261112_164724"))
W["걷기: 오늘 고리 (lee)"] += walker("TEST_20261112_165534", mark_ranges("TEST_20261112_165534"))
W["걷기: 11-05 원 (lee)"] += walker("S3_20261105_121146", [(76.5, 138.5), (163.3, 225.3)], rel=True)
W["걷기: 11-05 원 (lee)"] += walker("S3_20261105_124521", [(9.1, 93.2)], rel=True)
# non-walking: posture recordings, person track from matched.csv
for d in sorted(glob.glob(CAM + r"\TEST_*")):
    ses = os.path.basename(d)
    m = pd.read_csv(d + r"\matched.csv", usecols=["mmw_frame", "mmw_tid"], low_memory=False).dropna().drop_duplicates("mmw_frame")
    f2t = dict(zip(m.mmw_frame.astype(int), m.mmw_tid.astype(int))); seq = []
    for f in frames(ses):
        tid = f2t.get(f.get("frame"))
        if tid is None: continue
        for tr in f.get("tracks") or []:
            if tr["tid"] == tid: seq.append((f["t"], tr["world"][0], tr["world"][1], tr["speed"]))
    W["걷지 않음: 자세 녹화 (침대·서 있기·자세 바꾸기)"] += windows(sorted(seq))
rows = []
rules = [("지금: 직진성 ≥ 0.5", lambda w: w["straight"] >= 0.5), ("직진성 ≥ 0.4", lambda w: w["straight"] >= 0.4),
         ("직진성 ≥ 0.3", lambda w: w["straight"] >= 0.3), ("직진성 ≥ 0.2", lambda w: w["straight"] >= 0.2),
         ("직진성 검사 없음", lambda w: True), ("3초 단위 직진성 ≥ 0.5 (두 절반 모두)", lambda w: w["st3"] >= 0.5)]
base = lambda w: w["speed"] >= 0.2 and w["disp"] >= 0.3
res = {"n": {k: len(v) for k, v in W.items()}, "rules": {}}
for name, f in rules:
    r = {}
    for k, v in W.items():
        ps = [w for w in v if base(w) and f(w)]
        r[k] = dict(rate=len(ps) / len(v), n=len(ps), sway=float(np.median([w["sway"] for w in ps])) if ps else None)
    res["rules"][name] = r
json.dump(res, open("gate_check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
