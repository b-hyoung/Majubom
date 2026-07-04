"""
침대 폭 ROI — 각 존의 광선이 침대 폭 밖(|Y|>BED_WID/2)에 떨어지면 제외.
머리/발 센서의 좌우 FoV가 멀수록 넓게 퍼져 침대 밖까지 잡히는 것을 컷.
캘리브레이션(높이·기울기 등)은 뷰어 실측값과 동일. |Y| 대칭이라 좌우반전 무관.
"""
import math

# ── 센서 배치 (실측) ─────────────────────────────────────
SENSOR_H1, SENSOR_H2 = 0.30, 0.72    # 머리/발 높이 (m)
TILT1_DEG, TILT2_DEG = 90, 65        # 안쪽 기울기 (deg)
SEP      = 2.12                      # 센서 간 거리 (m, X)
BED_WID  = 0.95                      # 침대 폭 (m, Y)
FOV_DEG  = 45
GRID     = 4
Y_MARGIN = 0.08                      # 경계 여유(m) — 가장자리 누운 사람 보존

# ── 좌우 정렬 보정 (센서가 정중앙·정면이 아닐 때) ─────────
#  YAW_DEG: 센서 좌우 회전(도). 한쪽이 침대 밖으로 더 나가면 조정(거리 비례로 보정).
#  Y_OFF  : 센서가 침대 중심에서 벗어난 정도(m, 좌우 평행이동).
#  calibrate_roi.py 로 현재 프레임 보며 맞춘 뒤 이 값을 넣으면 됨.
YAW_DEG = {"tof1": 0.0, "tof2": 0.0}
Y_OFF   = {"tof1": 0.0, "tof2": 0.0}


def _norm(v):
    l = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / l for c in v]

def _cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]

def _sensor(sensor_id):
    t  = math.radians(TILT1_DEG if sensor_id == "tof1" else TILT2_DEG)
    hl = SEP / 2
    yoff = Y_OFF[sensor_id]
    if sensor_id == "tof1":
        pos = [-hl, yoff, SENSOR_H1]; d = [math.sin(t), 0, -math.cos(t)]
    else:
        pos = [ hl, yoff, SENSOR_H2]; d = [-math.sin(t), 0, -math.cos(t)]
    # 좌우 회전(yaw) 보정: 월드 수직(Z)축 기준 회전
    yw = math.radians(YAW_DEG[sensor_id]); cy, sy = math.cos(yw), math.sin(yw)
    d  = _norm([d[0]*cy - d[1]*sy, d[0]*sy + d[1]*cy, d[2]])
    ref   = [0, 1, 0] if abs(d[2]) > 0.9 else [0, 0, 1]
    right = _norm(_cross(d, ref))
    up    = _norm(_cross(right, d))
    return pos, d, right, up

def hit_y(sensor_id, zone, distance_mm):
    """존 광선이 distance_mm 에서 닿는 지점의 Y(좌우, m). 무효면 None."""
    if distance_mm is None or distance_mm <= 0:
        return None
    r, c = zone // GRID, zone % GRID
    pos, d, right, up = _sensor(sensor_id)
    step = math.radians(FOV_DEG / GRID)
    ac, ar = (c - 1.5) * step, (1.5 - r) * step
    zd = _norm([d[i] + math.tan(ac) * right[i] + math.tan(ar) * up[i] for i in range(3)])
    return pos[1] + zd[1] * (distance_mm / 1000.0)

def hit_point(sensor_id, zone, distance_mm):
    """존 광선이 닿는 3D 지점 [x,y,z] (m). X=머리(-)↔발(+), Y=좌우, Z=높이. 무효면 None."""
    if distance_mm is None or distance_mm <= 0:
        return None
    r, c = zone // GRID, zone % GRID
    pos, d, right, up = _sensor(sensor_id)
    step = math.radians(FOV_DEG / GRID)
    ac, ar = (c - 1.5) * step, (1.5 - r) * step
    zd = _norm([d[i] + math.tan(ac) * right[i] + math.tan(ar) * up[i] for i in range(3)])
    t = distance_mm / 1000.0
    return [pos[i] + zd[i] * t for i in range(3)]

def on_bed(sensor_id, zone, distance_mm):
    y = hit_y(sensor_id, zone, distance_mm)
    if y is None:
        return True                       # 무효는 그대로(판단 안 함)
    return abs(y) <= BED_WID / 2 + Y_MARGIN

def mask_offbed(sensor_id, distances):
    """침대 폭 밖 존은 -1(무효)로. distances: list[int]."""
    return [d if on_bed(sensor_id, z, d) else -1 for z, d in enumerate(distances)]
