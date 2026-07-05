#!/usr/bin/env python3
"""
ToF Feature 추출 + 시계열 로깅 (LSTM-AE 학습용 데이터 축적)
==========================================================
빈 침대 baseline 을 잡고, 프레임마다 '전경(사람)' 3D 점의 공간 특징 + 상태를
SQLite(tof_features.db) 에 시계열로 저장한다. 이 데이터가 이상탐지(LSTM-AE) 입력.

  특징: 점수 / 중심 X,Y,Z / 퍼짐(std) X,Y / 범위 X,Y,Z / 상태 / 재실 / 점유
  상태 규칙(뷰어와 동일): 점<3=이탈 / X범위>0.9=누움 / |cy|>0.10=걸터앉음 / 그외=앉음

사용:
  python feature_logger.py                       # 시작 시 빈 침대 baseline
  python feature_logger.py --db my.db --thr 200
  python feature_logger.py --recal               # 실행 중 재보정은 Ctrl+C 후 재실행
"""
import urllib.request, json, time, argparse, sqlite3, os
import statistics as st
from collections import defaultdict
import roi

# ── 상태 판정 임계 (viewer 와 동일) ──
STATE_MIN_PTS = 3
STATE_X_LIE   = 0.90
STATE_Y_EDGE  = 0.10

HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument("--server", default="http://192.168.6.10:5001")
ap.add_argument("--db", default=os.path.join(HERE, "tof_features.db"))
ap.add_argument("--thr", type=int, default=200)         # baseline 대비 전경 임계(mm)
ap.add_argument("--base-frames", type=int, default=10)
a = ap.parse_args()
LATEST = a.server.rstrip("/") + "/tof/latest"
PRESENCE = a.server.rstrip("/") + "/tof/presence"

# 자세 RF 모델 (있으면)
_posture = None
try:
    from predict_posture import load_model, predict as _predict
    _posture = load_model()
    print("[posture] RF 모델 로드됨", flush=True)
except Exception as e:
    print(f"[posture] 모델 없음({e}) — posture=null", flush=True)


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=3).read())


# ── DB ──
db = sqlite3.connect(a.db)
db.execute("""CREATE TABLE IF NOT EXISTS tof_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT, n_pts INTEGER,
    cx REAL, cy REAL, cz REAL, sx REAL, sy REAL,
    x_range REAL, y_range REAL, z_range REAL,
    state TEXT, in_bed INTEGER, occ1 INTEGER, occ2 INTEGER, posture TEXT,
    created_at TEXT DEFAULT (datetime('now')) )""")
db.commit()
print(f"[db] {a.db}", flush=True)

# ── baseline (빈 침대) ──
print(f"[baseline] 빈 침대 {a.base_frames}프레임 캡처중… (침대 비워주세요)", flush=True)
acc = {"tof1": defaultdict(list), "tof2": defaultdict(list)}
seen = {"tof1": set(), "tof2": set()}
while len(seen["tof1"]) < a.base_frames or len(seen["tof2"]) < a.base_frames:
    o = get(LATEST)
    for sid in ("tof1", "tof2"):
        s = o.get(sid) or {}
        at, d = s.get("received_at"), s.get("distances_mm")
        if not at or at in seen[sid] or not d:
            continue
        seen[sid].add(at)
        for z, v in enumerate(d):
            if v and v > 0:
                acc[sid][z].append(v)
    time.sleep(0.05)
base = {sid: {z: sum(vs) / len(vs) for z, vs in acc[sid].items() if vs}
        for sid in ("tof1", "tof2")}
print("[baseline] 완료. 로깅 시작 (Ctrl+C 종료).\n", flush=True)


def features(o):
    pts = []
    for sid in ("tof1", "tof2"):
        d = (o.get(sid) or {}).get("distances_mm") or []
        for z, v in enumerate(d):
            b = base[sid].get(z)
            if v and v > 0 and b and abs(v - b) > a.thr:
                p = roi.hit_point(sid, z, v)
                if p and roi.on_bed(sid, z, v):
                    pts.append(p)
    if len(pts) < STATE_MIN_PTS:
        return dict(n=len(pts), cx=0, cy=0, cz=0, sx=0, sy=0,
                    xr=0, yr=0, zr=0, state="empty")
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
    xr = max(xs) - min(xs); yr = max(ys) - min(ys); zr = max(zs) - min(zs)
    cy = st.mean(ys)
    if xr > STATE_X_LIE:        state = "lying"
    elif abs(cy) > STATE_Y_EDGE: state = "edge_sit"
    else:                        state = "sit"
    return dict(n=len(pts), cx=st.mean(xs), cy=cy, cz=st.mean(zs),
                sx=(st.pstdev(xs) if len(xs) > 1 else 0),
                sy=(st.pstdev(ys) if len(ys) > 1 else 0),
                xr=xr, yr=yr, zr=zr, state=state)


last = {"tof1": None, "tof2": None}
count = 0
while True:
    try:
        o = get(LATEST)
    except Exception:
        time.sleep(0.2); continue
    at1 = (o.get("tof1") or {}).get("received_at")
    at2 = (o.get("tof2") or {}).get("received_at")
    if at1 == last["tof1"] and at2 == last["tof2"]:
        time.sleep(0.05); continue        # 새 프레임 없음
    last["tof1"], last["tof2"] = at1, at2

    f = features(o)
    in_bed = occ1 = occ2 = None
    try:
        p = get(PRESENCE)
        in_bed = 1 if p.get("in_bed") else 0
        occ1 = (p.get("tof1") or {}).get("occupied")
        occ2 = (p.get("tof2") or {}).get("occupied")
    except Exception:
        pass
    posture = None
    if _posture is not None:
        try:
            posture, _ = _predict(_posture, o)
        except Exception:
            pass

    ts = at1 or at2 or time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""INSERT INTO tof_features
        (timestamp,n_pts,cx,cy,cz,sx,sy,x_range,y_range,z_range,state,in_bed,occ1,occ2,posture)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, f["n"], f["cx"], f["cy"], f["cz"], f["sx"], f["sy"],
         f["xr"], f["yr"], f["zr"], f["state"], in_bed, occ1, occ2, posture))
    db.commit()
    count += 1
    if count % 50 == 0:
        print(f"[{count}] {f['state']:8} pts{f['n']:2} "
              f"cxyz({f['cx']*100:+.0f},{f['cy']*100:+.0f},{f['cz']*100:+.0f})cm "
              f"in_bed={in_bed} posture={posture}", flush=True)
    time.sleep(0.05)
