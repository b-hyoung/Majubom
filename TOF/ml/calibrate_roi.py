#!/usr/bin/env python3
"""
ROI 좌우 정렬 캘리브레이션 도우미
=================================
현재 프레임의 존별 Y위치(좌우, m)와 침대 안/밖 판정을 출력한다.
한쪽이 침대 밖으로 나가면 --yaw/--off 값을 바꿔가며 경계에 맞을 때까지 조정한 뒤,
그 값을 roi.py 의 YAW_DEG / Y_OFF 에 넣으면 된다.

사용 예 (라즈베리파이 왕복 없이 로컬에서 반복):
  python calibrate_roi.py                          # 현재 roi.py 값으로
  python calibrate_roi.py --yaw1 -6                # tof1 을 -6° 돌려보기
  python calibrate_roi.py --yaw1 -6 --off1 0.03    # 회전+오프셋 조합
  python calibrate_roi.py --server http://192.168.6.10:5001

한쪽만 계속 '밖'으로 나오면:
  - YAW 부호를 바꿔보기(+ ↔ -)  → 어느 쪽을 당길지 결정
  - 값 크기를 키우면 더 많이 당겨짐 (20cm ≈ 2m에서 yaw 약 5~6°)
맞았으면 그 --yaw1/--off1 값을 roi.py 상단 YAW_DEG/Y_OFF 에 반영 → 재학습.
"""
import argparse, json, urllib.request
import roi

ap = argparse.ArgumentParser()
ap.add_argument("--server", default="http://192.168.6.10:5001")
ap.add_argument("--yaw1", type=float); ap.add_argument("--yaw2", type=float)
ap.add_argument("--off1", type=float); ap.add_argument("--off2", type=float)
a = ap.parse_args()
if a.yaw1 is not None: roi.YAW_DEG["tof1"] = a.yaw1
if a.yaw2 is not None: roi.YAW_DEG["tof2"] = a.yaw2
if a.off1 is not None: roi.Y_OFF["tof1"]  = a.off1
if a.off2 is not None: roi.Y_OFF["tof2"]  = a.off2

half = roi.BED_WID / 2 + roi.Y_MARGIN
print(f"YAW={roi.YAW_DEG}  Y_OFF={roi.Y_OFF}")
print(f"침대 허용 Y: {-half:+.2f} ~ {+half:+.2f} m  (반폭 {roi.BED_WID/2}, 여유 {roi.Y_MARGIN})")

obj = json.loads(urllib.request.urlopen(a.server.rstrip('/') + "/tof/latest", timeout=4).read())
for sid in ("tof1", "tof2"):
    d = (obj.get(sid) or {}).get("distances_mm") or []
    print(f"\n[{sid}]  (행=위/아래, 열=좌/우 방향)")
    print("  존  거리mm   Y(m)   판정")
    for z, dist in enumerate(d):
        if dist and dist > 0:
            y = roi.hit_y(sid, z, dist)
            flag = "침대안" if roi.on_bed(sid, z, dist) else "★ 밖"
            print(f"  z{z:<2} r{z//roi.GRID}c{z%roi.GRID}  {dist:5}  {y:+.2f}  {flag}")
    # 열(좌우)별 최대 |Y| 요약
    cols = {c: [] for c in range(roi.GRID)}
    for z, dist in enumerate(d):
        if dist and dist > 0:
            cols[z % roi.GRID].append(roi.hit_y(sid, z, dist))
    summ = {c: (round(min(v), 2), round(max(v), 2)) for c, v in cols.items() if v}
    print("  열별 Y범위(min,max):", summ)
