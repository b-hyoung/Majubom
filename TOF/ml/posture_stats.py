#!/usr/bin/env python3
"""
전경 공간통계 — 걸터앉음 vs 누움 구분 특징 찾기
==============================================
빈 침대 baseline 잡고, 이후 baseline과 다른 존(=사람)의 3D 점들의
중심(X/Y/Z)·퍼짐을 실시간 출력. 걸터앉음/누움 값을 비교해 판정 규칙을 정한다.
  X = 머리(-)↔발(+),  Y = 좌↔우,  Z = 매트리스 위 높이
"""
import urllib.request, json, time, argparse
import statistics as st
from collections import defaultdict
import roi

ap = argparse.ArgumentParser()
ap.add_argument("--server", default="http://192.168.6.10:5001")
ap.add_argument("--thr", type=int, default=200)
ap.add_argument("--base-frames", type=int, default=10)
a = ap.parse_args()
URL = a.server.rstrip("/") + "/tof/latest"


def frame():
    return json.loads(urllib.request.urlopen(URL, timeout=3).read())


print("[baseline] 빈 침대 캡처중…", flush=True)
acc = {"tof1": defaultdict(list), "tof2": defaultdict(list)}
seen = {"tof1": set(), "tof2": set()}
while len(seen["tof1"]) < a.base_frames or len(seen["tof2"]) < a.base_frames:
    o = frame()
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
print("[baseline] 완료. 자세 취하면 공간통계 출력.\n", flush=True)

C = lambda v: round(v * 100)   # m→cm
while True:
    try:
        o = frame()
    except Exception:
        time.sleep(0.2); continue
    pts = []
    for sid in ("tof1", "tof2"):
        d = (o.get(sid) or {}).get("distances_mm") or []
        for z, v in enumerate(d):
            b = base[sid].get(z)
            if v and v > 0 and b and abs(v - b) > a.thr:
                p = roi.hit_point(sid, z, v)
                if p and roi.on_bed(sid, z, v):
                    pts.append(p)
    if len(pts) >= 2:
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
        sx = st.pstdev(xs) if len(xs) > 1 else 0
        sy = st.pstdev(ys) if len(ys) > 1 else 0
        print(f"점 {len(pts):2} | 중심 X{C(st.mean(xs)):+4} Y{C(st.mean(ys)):+4} "
              f"Z{C(st.mean(zs)):+4}cm | 퍼짐 X{C(sx):3} Y{C(sy):3}cm | "
              f"X범위[{C(min(xs)):+4}~{C(max(xs)):+4}] Y범위[{C(min(ys)):+4}~{C(max(ys)):+4}]",
              flush=True)
    else:
        print("전경 거의 없음", flush=True)
    time.sleep(0.4)
