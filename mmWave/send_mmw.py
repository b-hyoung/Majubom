"""
send_mmw.py — mmWave 송신부 (Pi에서 USB 직결로 상시 구동)
====================================================================
IWR6843 (3D People Tracking 데모) 의 UART(DATA 포트)에서 TLV 프레임을 직접 파싱하여
track 을 모으고, WINDOW_SEC 초마다 보행 지표를 계산해 mmw_server 로 POST 한다.

흐름:
  IWR6843 --USB--> Pi : CLI 포트로 .cfg 전송, DATA 포트에서 TLV 수신
  이 스크립트 = Visualizer 없이 헤드리스로 TLV 파싱 → 지표 계산 → HTTP POST

TLV 구조 (3D People Counting/Tracking, little-endian):
  Frame Header (40 bytes): magic(8) version(4) totalPacketLen(4) platform(4)
                           frameNum(4) timeCpuCycles(4) numDetectedObj(4)
                           numTLVs(4) subFrameNum(4)
  각 TLV: tlvType(uint32) tlvLength(uint32) + payload
  우리가 쓰는 것 = Target List TLV (type 1010): track 별 tid/pos/vel/...

펌웨어 버전마다 target 구조체 크기가 다를 수 있어, TLV 길이에서 target 크기를
역산해 자동 적응한다. (공통: 맨 앞 tid(uint32), 이어서 posX,posY,posZ,velX,velY,velZ float32)

사용:
  python3 send_mmw.py --cli /dev/ttyACM0 --data /dev/ttyACM1 --cfg AOP_v_C_legs_safe.cfg
  (윈도우에서 테스트 시: --cli COM4 --data COM3)
  --no-send 를 주면 POST 없이 콘솔에만 지표 출력 (디버그)

사람 판단은 people_pipeline.py가 한다. 대시보드에서 번호를 눌러야 사람 ID가 생기고,
마지막으로 누른 사람의 보행 지표만 서버로 보낸다. 낙상은 사람 ID마다 프레임 단위로 판정해 /mmw/fall로 보낸다.
"""
import argparse
import math
import struct
import threading
import time
from collections import defaultdict, deque

import numpy as np

from people_pipeline import RoomPipeline

try:
    import serial  # pyserial
except ImportError:
    serial = None

try:
    import requests
except ImportError:
    requests = None


# ── 상수 ───────────────────────────────────────────────────────────────
MAGIC = bytes([2, 1, 4, 3, 6, 5, 8, 7])
FRAME_HEADER_LEN = 40
TLV_HEADER_LEN = 8
TARGET_LIST_TLV = 1010   # 3D People Tracking target list (confirmed from parseTLVs.py)
TARGET_INDEX_TLV = 1011  # target INDEX (포인트별 소속 track ID, 직전 프레임의 점을 가리킴), target list가 아님
# TLV 1012 = track height (I2f, 12 bytes/target)
POINT_CLOUD_TLV = 1020   # compressed point cloud
# TLV 1021 = presence/flag
KNOWN_TLVS = {TARGET_LIST_TLV, TARGET_INDEX_TLV, 1012, POINT_CLOUD_TLV, 1021}
TARGET_STRUCT = '<I27f'  # tid(uint32) + 27 floats (pos/vel/acc/ec/g/conf) = 112 bytes
TARGET_SIZE = struct.calcsize(TARGET_STRUCT)  # 112

WINDOW_SEC = 6.0         # 보행 지표 계산 윈도우 (좁은 방: 한 직진 패스가 한 윈도우에 맞게 10→6 축소)
FREEZE_SPEED_THRESH = 0.15
SERVER_URL = "http://127.0.0.1:5002/mmw"   # 같은 Pi 안의 mmw_server
LIVE_URL = "http://127.0.0.1:5002/mmw/live" # 실시간 포인트 클라우드
LIVE_INTERVAL = 0.3      # snapshot POST 주기 (초)
FALL_URL = "http://127.0.0.1:5002/mmw/fall"       # 프레임 단위 낙상 사건
PATIENT_URL = "http://127.0.0.1:5002/mmw/patient" # 화면에서 누른 번호
PATIENT_POLL_SEC = 0.5
LIVE_STALE_SEC = 2.0     # 레이더 프레임이 이만큼 없으면 실시간 위치를 보내지 않는다

# ── 침대존 & 환자 소속도(bed affinity) ─────────────────────────────────
# 여러 사람이 있을 때 "환자(메인 타겟)"를 순간위치가 아니라 track 이력으로 특정한다.
# 환자 = 최근 이력이 침대 footprint 안에 많이 머문 track(= 침대에서 일어나 걷는 사람).
# 방문자 = 문에서 들어와 침대에 안 머무는 track(소속도 ~0).
# BED_X/BED_Y 는 'bed' 캡처 실측(고스트 X≈-2.25 꼬리 제거)에서 도출. (X=좌우, Y=거리, m)
BED_X = (-0.9, 0.4)          # 침대 footprint 좌우 (매트리스 코어로 축소 — 옆 의자/고스트 배제, 실측 튜닝 2026-07-03)
BED_Y = (1.0, 3.6)           # 침대 footprint 거리(머리~발치)
AFFINITY_HORIZON = 60.0      # 소속도 산정에 쓰는 이력 길이 (초)
ALIVE_SEC = 2.0              # 최근 이 시간 내 샘플이 있으면 track '생존'으로 간주
AFFINITY_MIN = 0.25          # 2명 이상일 때 환자로 특정할 최소 소속도
WALK_SPEED_MIN = 0.20        # 이 속도(m/s) 이상이어야 '보행'으로 baseline 반영
WALK_DISP_MIN = 0.30         # 윈도우 내 순이동(m) 최소 — 제자리 미동/누움 배제
MERGE_DIST = 0.7             # 침대 후보끼리 이 거리(m) 이내면 같은 사람(track 분리)로 병합
STRAIGHT_MIN = 0.5           # 경로 직진성(순이동/이동거리) 최소 — 회전 많은 굽은 윈도우는 gait 왜곡 → 보행에서 제외 (좁은 방 실측 튜닝: 0.7→0.5)


# ── TLV 파싱 ───────────────────────────────────────────────────────────
def find_magic(buf):
    """버퍼에서 magic word 위치를 찾음. 없으면 -1."""
    return buf.find(MAGIC)


def parse_frame_header(data):
    """40바이트 헤더 파싱 → dict. 데이터 부족하면 None."""
    if len(data) < FRAME_HEADER_LEN:
        return None
    # magic(8) 이후부터: version, totalPacketLen, platform, frameNum,
    #                    timeCpuCycles, numDetectedObj, numTLVs, subFrameNum
    fields = struct.unpack("<8I", data[8:FRAME_HEADER_LEN])
    return {
        "version": fields[0],
        "totalPacketLen": fields[1],
        "platform": fields[2],
        "frameNum": fields[3],
        "timeCpuCycles": fields[4],
        "numDetectedObj": fields[5],
        "numTLVs": fields[6],
        "subFrameNum": fields[7],
    }


MOUNT_UPSIDE_DOWN = True   # 레이더가 칩 면 안에서 180° 뒤집혀 설치된 경우


def track_to_world(t, height=2.7, tilt=90.0, upside_down=None):
    """레이더 기준 track 좌표 → 바닥 기준 좌표(x 오른쪽, y 수평거리, z 바닥 높이)."""
    a = math.radians(tilt)
    c, s = math.cos(a), math.sin(a)
    flip = -1.0 if (MOUNT_UPSIDE_DOWN if upside_down is None else upside_down) else 1.0
    y, z, vy, vz = t["posY"], flip * t["posZ"], t["velY"], flip * t["velZ"]
    w = dict(t)
    w["posX"], w["velX"] = flip * t["posX"], flip * t["velX"]
    w["posY"] = y * c + z * s
    w["posZ"] = height - y * s + z * c
    w["velY"] = vy * c + vz * s
    w["velZ"] = -vy * s + vz * c
    return w


# 2026-11-01 사용자가 뷰어에서 확정한 측정 범위(바닥 좌표 x, y m). 레이더나 가구를 옮기면 다시 정해야 한다.
ROOM_POLYGON = [(-1.79, -1.86), (2.8, -1.9), (2.8, 2.0), (-1.7, 2.0)]
BED_POLYGON = [(-1.58, -1.46), (-0.05, -1.48), (-0.07, 0.45), (-1.56, 0.45)]
DOOR_POLYGON = [(-1.83, 1.66), (-0.43, 1.66), (-0.43, 2.49), (-1.83, 2.51)]


def in_room(x, y, polygon=ROOM_POLYGON):
    """점이 측정 범위 다각형 안에 있으면 True (짝홀 규칙)."""
    inside = False
    for i in range(len(polygon)):
        (x1, y1), (x2, y2) = polygon[i], polygon[i - 1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def parse_target_list(payload):
    """
    Target List TLV payload → track 리스트.
    구조: 'I27f' (112 bytes) per target — parseTLVs.py 의 parseTrackTLV 과 동일.
    펌웨어가 항상 고정 슬롯(예: 4개)을 보내므로, 사용되지 않는 슬롯은
    nan/inf/비현실적 값을 가진다. 이를 필터링해야 한다.
    """
    n = len(payload)
    if n < TARGET_SIZE or n % TARGET_SIZE != 0:
        return []
    count = n // TARGET_SIZE
    targets = []
    for i in range(count):
        off = i * TARGET_SIZE
        data = struct.unpack_from(TARGET_STRUCT, payload, off)
        tid = data[0]
        posX, posY, posZ = data[1], data[2], data[3]
        velX, velY, velZ = data[4], data[5], data[6]
        # 쓰레기 슬롯 필터: nan/inf 또는 비현실적 좌표(>100m) 제거
        vals = [posX, posY, posZ, velX, velY, velZ]
        if any(math.isnan(v) or math.isinf(v) for v in vals):
            continue
        if any(abs(v) > 100 for v in [posX, posY, posZ]):
            continue
        if tid > 252:  # track ID 는 0~252 범위
            continue
        targets.append({
            "tid": tid,
            "posX": posX, "posY": posY, "posZ": posZ,
            "velX": velX, "velY": velY, "velZ": velZ,
        })
    return targets


def iter_frames(buf):
    """
    버퍼에서 완전한 프레임을 하나씩 yield 하고, 소비한 바이트만큼 자른 잔여 버퍼를 반환.
    반환: (frames(list), leftover(bytes))
    """
    frames = []
    while True:
        idx = find_magic(buf)
        if idx < 0:
            # magic 없음 → 마지막 7바이트만 남기고 버림(걸친 magic 보존)
            buf = buf[-7:] if len(buf) > 7 else buf
            break
        buf = buf[idx:]  # magic 앞 쓰레기 제거
        hdr = parse_frame_header(buf)
        if hdr is None:
            break  # 헤더 채워질 때까지 대기
        total = hdr["totalPacketLen"]
        if total <= 0 or total > 1_000_000:
            buf = buf[len(MAGIC):]  # 비정상 → magic 건너뛰고 재탐색
            continue
        if len(buf) < total:
            break  # 프레임 다 안 옴 → 대기
        frame_bytes = buf[:total]
        frames.append((hdr, frame_bytes))
        buf = buf[total:]
    return frames, buf


def extract_targets(hdr, frame_bytes):
    """프레임에서 target list TLV 를 찾아 track 리스트 반환."""
    data = frame_bytes[FRAME_HEADER_LEN:]
    targets = []
    for _ in range(hdr["numTLVs"]):
        if len(data) < TLV_HEADER_LEN:
            break
        tlv_type, tlv_len = struct.unpack_from("<2I", data, 0)
        payload = data[TLV_HEADER_LEN:TLV_HEADER_LEN + tlv_len]
        if tlv_type == TARGET_LIST_TLV:
            parsed = parse_target_list(payload)
            if parsed:
                targets = parsed
        data = data[TLV_HEADER_LEN + tlv_len:]
    return targets


def frame_is_valid(hdr, frame_bytes):
    """TLV 타입과 길이가 정상인지 본다. 손상된 프레임의 값은 없는 번호나 튀는 좌표로 나타나 사람 ID를 끊는다."""
    off = FRAME_HEADER_LEN
    for _ in range(hdr["numTLVs"]):
        if off + TLV_HEADER_LEN > len(frame_bytes):
            return False
        tlv_type, tlv_len = struct.unpack_from("<2I", frame_bytes, off)
        off += TLV_HEADER_LEN + tlv_len
        if tlv_type not in KNOWN_TLVS or off > len(frame_bytes):
            return False
    return True


def extract_points(hdr, frame_bytes, height=2.7, tilt=90.0):
    """프레임에서 반사점의 바닥 기준 높이 목록과 점별 소속 번호(bytes)를 꺼낸다."""
    data = frame_bytes[FRAME_HEADER_LEN:]
    zs, index = [], b""
    for _ in range(hdr["numTLVs"]):
        if len(data) < TLV_HEADER_LEN:
            break
        tlv_type, tlv_len = struct.unpack_from("<2I", data, 0)
        payload = data[TLV_HEADER_LEN:TLV_HEADER_LEN + tlv_len]
        if tlv_type == TARGET_INDEX_TLV:
            index = payload
        elif tlv_type == POINT_CLOUD_TLV and len(payload) >= 20:
            e_unit, a_unit, _, r_unit, _ = struct.unpack_from("<5f", payload, 0)
            for i in range((len(payload) - 20) // 8):
                el_i, az_i, _, rng, _ = struct.unpack_from("<bbhHH", payload, 20 + i * 8)
                r, el, az = rng * r_unit, el_i * e_unit, az_i * a_unit
                zs.append(track_to_world({"posX": r * math.cos(el) * math.sin(az), "posY": r * math.cos(el) * math.cos(az),
                                          "posZ": r * math.sin(el), "velX": 0.0, "velY": 0.0, "velZ": 0.0}, height, tilt)["posZ"])
        data = data[TLV_HEADER_LEN + tlv_len:]
    return zs, index


def link_points(targets, index, prev_zs):
    """추적마다 소속 반사점 수와 머리 쪽 높이(상위 90%)를 붙인다. 1011은 직전 프레임의 점을 가리킨다."""
    linked = defaultdict(list)
    if prev_zs is not None and len(index) == len(prev_zs):
        for owner, z in zip(index, prev_zs):
            linked[owner].append(z)
    for t in targets:
        zs = sorted(linked.get(t["tid"], []))
        t["npts"] = index.count(t["tid"])
        t["linked_npts"] = len(zs)
        t["top_z"] = zs[min(len(zs) - 1, int(len(zs) * 0.9))] if zs else None


# ── 보폭 추정 (FFT → cadence → stride) ────────────────────────────────
def _estimate_stride(speed, t, speed_mean):
    """
    속도 시계열에서 FFT로 걸음 주기(cadence)를 내부적으로 추출하고,
    보폭(stride_length)과 보폭 변동성(stride_cv)을 추정한다.

    원리: 걸을 때 몸통 속도가 한 걸음마다 빨라졌다 느려졌다를 반복한다.
    이 주기적 변동의 지배 주파수 = cadence(Hz, 걸음/초).
    stride_length = speed / cadence.

    보폭 변동성: 속도 시계열을 걸음 주기 단위로 잘라 구간별 평균 속도를 구하고,
    각 구간의 보폭을 추정해 CV(변동계수)를 구한다.
    """
    n = len(speed)
    if n < 20 or speed_mean < 0.1:
        return 0.0, 0.0

    # 샘플링 주파수 추정
    dt = np.diff(t)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return 0.0, 0.0
    fs = 1.0 / np.median(dt)
    if fs < 1.0:
        return 0.0, 0.0

    # 속도에서 평균 제거 후 FFT
    sig = speed - speed_mean
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    fft_mag = np.abs(np.fft.rfft(sig))

    # 걸음 주파수 범위: 0.5~3.0 Hz (분당 30~180걸음)
    mask = (freqs >= 0.5) & (freqs <= 3.0)
    if not np.any(mask):
        return 0.0, 0.0

    valid_freqs = freqs[mask]
    valid_mag = fft_mag[mask]
    peak_idx = np.argmax(valid_mag)

    # 피크가 노이즈보다 충분히 커야 유의미 (평균 대비 2배 이상)
    if valid_mag[peak_idx] < np.mean(valid_mag) * 2:
        return 0.0, 0.0

    cadence_hz = float(valid_freqs[peak_idx])

    # 보폭 추정: stride = speed / cadence
    stride_length = speed_mean / cadence_hz

    # 보폭 변동성: 걸음 주기 단위로 구간을 나눠 각 구간 보폭을 추정
    step_period = 1.0 / cadence_hz  # 한 걸음에 걸리는 시간 (초)
    samples_per_step = max(int(fs * step_period), 1)

    if n >= samples_per_step * 2:
        n_steps = n // samples_per_step
        step_strides = []
        for i in range(n_steps):
            seg = speed[i * samples_per_step:(i + 1) * samples_per_step]
            seg_mean = float(np.mean(seg))
            if seg_mean > 0.05:
                step_strides.append(seg_mean / cadence_hz)
        if len(step_strides) >= 2:
            arr_s = np.array(step_strides)
            s_mean = np.mean(arr_s)
            if s_mean > 1e-6:
                stride_cv = float(np.std(arr_s) / s_mean)
            else:
                stride_cv = 0.0
        else:
            stride_cv = 0.0
    else:
        stride_cv = 0.0

    return stride_length, stride_cv


# ── 보행 지표 계산 (features.py 로직 통합) ─────────────────────────────
def _in_bed(x, y):
    """(x, y) 가 침대 footprint 안이면 True."""
    return BED_X[0] <= x <= BED_X[1] and BED_Y[0] <= y <= BED_Y[1]


def _cluster_candidates(tids, alive, dist):
    """침대 후보 track들을 최신 위치 기준 근접(dist 이내) 연결성분으로 묶는다.
    한 사람이 여러 track으로 쪼개지는(split ghost) 경우를 한 덩어리로 카운트하기 위함.
    반환: 클러스터(각각 tid 리스트) 리스트."""
    pos = {t: (alive[t][-1][1], alive[t][-1][2]) for t in tids}  # 최신 (x, y)
    d2 = dist * dist
    unvisited = set(tids)
    clusters = []
    for t in tids:
        if t not in unvisited:
            continue
        unvisited.discard(t)
        stack, comp = [t], []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in list(unvisited):
                if (pos[u][0] - pos[v][0]) ** 2 + (pos[u][1] - pos[v][1]) ** 2 <= d2:
                    unvisited.discard(v)
                    stack.append(v)
        clusters.append(comp)
    return clusters


def _gait_from_samples(samples):
    """샘플 리스트 [(t,x,y,z,vx,vy), ...] → gait raw dict. 10건 미만이면 None."""
    if len(samples) < 10:
        return None

    arr = np.array(samples)  # columns: t,x,y,z,vx,vy
    t, x, y, z, vx, vy = (arr[:, i] for i in range(6))

    # 속도 크기
    speed = np.sqrt(vx ** 2 + vy ** 2)
    speed_mean = float(np.mean(speed))
    speed_std = float(np.std(speed))
    speed_cv = float(speed_std / speed_mean) if speed_mean > 1e-6 else 0.0

    # sway: 시작-끝 진행축에 수직인 이탈의 표준편차
    pts = np.column_stack([x, y])
    axis = pts[-1] - pts[0]
    norm = np.linalg.norm(axis)
    if norm > 1e-6:
        axis = axis / norm
        rel = pts - pts[0]
        perp = np.abs(rel[:, 0] * axis[1] - rel[:, 1] * axis[0])
        sway = float(np.std(perp))
    else:
        sway = 0.0

    # freeze: 거의 멈춰 있던 비율
    freeze_ratio = float(np.mean(speed < FREEZE_SPEED_THRESH))

    # height_drop: z 최대 하강폭 (낙상 신호)
    height_drop = float(np.max(z) - np.min(z)) if np.ptp(z) > 0 else 0.0

    # stride_length / stride_cv: 속도 FFT → cadence → 보폭 추정
    stride_length, stride_cv = _estimate_stride(speed, t, speed_mean)

    return {
        "speed": round(speed_mean, 3),
        "speed_cv": round(speed_cv, 3),
        "sway": round(sway, 3),
        "freeze_ratio": round(freeze_ratio, 3),
        "height_drop": round(height_drop, 3),
        "stride_length": round(stride_length, 3),
        "stride_cv": round(stride_cv, 3),
    }


def select_patient(track_history, now):
    """롤링 track 이력에서 환자(메인 타겟)를 '침대 소속도'로 특정하고 gait 계산.

    track_history: dict[tid] -> deque of (t, x, y, z, vx, vy)  (이미 시간 프루닝됨)
    반환: (raw|None, quality, presence)

    선택 규칙:
      - 생존 track = 최근 ALIVE_SEC 내 샘플이 있는 track
      - n_targets == 1 : 그 track 을 환자로 (평소엔 환자 혼자) → locked
      - n_targets >= 2 : 침대 소속(aff>=AFFINITY_MIN) track이 '정확히 1명'일 때만 그 track을 locked.
                         0명(침대 소속 없음) 또는 2명+(침대 위 복수 → 환자 구분 불가)면 보류(locked=False).
                         → 애매할 땐 추측하지 않고 보류(의료: 틀린 데이터 > 데이터 없음).
      - locked 여부와 무관하게 best track 의 raw(=height_drop 포함)는 계산 → 서버가 낙상 감시 가능
      - walking : 실제 보행(속도·순이동)일 때만 True → 서버가 gait baseline 에 반영
    """
    alive = {tid: dq for tid, dq in track_history.items()
             if dq and (now - dq[-1][0]) <= ALIVE_SEC}
    n_targets = len(alive)
    if n_targets == 0:
        return None, {"reliable": False}, {
            "n_targets": 0, "main_tid": None, "bed_affinity": None,
            "patient_locked": False}

    # 각 track 소속도 = 이력 내 (x,y)가 침대 footprint 안이던 비율
    aff = {}
    for tid, dq in alive.items():
        pts = [(s[1], s[2]) for s in dq]
        aff[tid] = sum(_in_bed(px, py) for px, py in pts) / len(pts) if pts else 0.0

    # 침대 소속 후보 = 소속도 AFFINITY_MIN 이상인 track
    candidates = [tid for tid in alive if aff[tid] >= AFFINITY_MIN]
    # 근접 병합: 한 사람이 track 여러 개로 쪼개진(split ghost) 경우 → 같은 사람으로 묶음
    clusters = _cluster_candidates(candidates, alive, MERGE_DIST)
    n_bed = len(clusters)   # 침대 위 '사람 수'(병합 후)

    if n_targets == 1:
        main_tid = next(iter(alive))
        locked = True
    elif n_bed == 1:
        # 침대에 사람 1명(분리 track 포함) → 그 클러스터의 소속도 최고 track을 환자로
        main_tid = max(clusters[0], key=lambda t: aff[t])
        locked = True
    else:
        # 침대 사람 0명(소속 없음) 또는 2명+(구분 불가) → 보류
        main_tid = max(aff, key=aff.get)   # 낙상 raw 용 best-guess (반영은 안 함)
        locked = False

    presence = {"n_targets": n_targets, "main_tid": int(main_tid),
                "bed_affinity": round(aff[main_tid], 2),
                "bed_candidates": n_bed, "patient_locked": locked}

    # 낙상 안전을 위해 locked 여부와 무관하게 best track raw 는 계산
    recent = [s for s in alive[main_tid] if now - s[0] <= WINDOW_SEC]
    raw = _gait_from_samples(recent)
    if raw is None:
        return None, {"reliable": False, "n_samples": len(recent)}, presence

    # 보행 판정: 평균속도 + 윈도우 내 순이동 + 경로 직진성
    #   - 제자리 미동/누움 배제(속도·순이동)
    #   - 회전 많은 굽은 경로 배제(직진성) → sway·stride(FFT cadence) 왜곡으로 인한 거짓 위험 방지
    arr = np.array(recent)
    xy = arr[:, 1:3]
    disp = float(np.hypot(xy[-1, 0] - xy[0, 0], xy[-1, 1] - xy[0, 1]))  # 순이동(직선거리)
    seg = np.diff(xy, axis=0)
    path_len = float(np.sum(np.hypot(seg[:, 0], seg[:, 1])))            # 실제 이동거리(경로 길이)
    straightness = disp / path_len if path_len > 1e-6 else 0.0          # 1=직선, 작을수록 굽음/왕복
    walking = (raw["speed"] >= WALK_SPEED_MIN) and (disp >= WALK_DISP_MIN) \
        and (straightness >= STRAIGHT_MIN)
    quality = {"reliable": True, "walking": walking,
               "straightness": round(straightness, 2), "n_samples": len(recent)}
    return raw, quality, presence


# ── 메인 루프 ──────────────────────────────────────────────────────────
def send_config(cli_port, cfg_path, baud=115200):
    """CLI 포트로 .cfg 한 줄씩 전송하고, 각 줄에 대한 센서 응답을 확인한다.

    IWR6843 CLI 는 명령 처리 후 'Done'(성공) 또는 'Error'(실패) 를 회신한다.
    응답을 버리지 않고 파싱해, 거부된 줄(예: 소수점 sensorPosition 미지원)을
    바로 눈으로 확인할 수 있게 한다.
    """
    if serial is None:
        raise RuntimeError("pyserial 미설치: pip install pyserial")
    errors = []  # (line, response) 거부된 명령들
    with serial.Serial(cli_port, baud, timeout=1) as cli:
        with open(cfg_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("%"):
                    continue
                cli.write((line + "\n").encode())
                time.sleep(0.03)
                resp = cli.readall().decode(errors="ignore")
                low = resp.lower()
                if "error" in low or "not recognized" in low:
                    errors.append((line, resp.strip()))
                    print(f"[cfg]  REJECTED: {line}")
                    # 센서가 회신한 원문(들여쓰기해서)도 같이 보여줌
                    for r in resp.splitlines():
                        r = r.strip()
                        if r and r not in ("mmwDemo:/>",):
                            print(f"           | {r}")
                else:
                    print(f"[cfg]  ok: {line}")
    if errors:
        print(f"[cfg] sent: {cfg_path}  —  ⚠ {len(errors)}줄 거부됨 (위 REJECTED 확인)")
        for line, _ in errors:
            print(f"        거부: {line}")
    else:
        print(f"[cfg] sent: {cfg_path}  —  모든 줄 적용 OK")


def poll_patient_clicks(target_id, started, click):
    """화면에서 누른 번호를 주기적으로 읽는다. 리더가 켜지기 전의 클릭은 번호가 달라졌을 수 있어 쓰지 않는다."""
    while True:
        try:
            m = requests.get(PATIENT_URL, params={"target": target_id}, timeout=0.5).json() or {}
            if m.get("seq") is not None and m["seq"] > started:
                click["tid"], click["seq"] = m.get("tid"), m["seq"]
        except Exception:
            pass  # 서버가 잠시 꺼져 있어도 수신은 계속한다
        time.sleep(PATIENT_POLL_SEC)


def apply_click(now, pipeline, click, applied_seq):
    """새 클릭이 있으면 사람으로 표시한다. 반환: 적용한 seq"""
    if click["seq"] is None or click["seq"] == applied_seq:
        return applied_seq
    try:
        pid = pipeline.designate(now, click["tid"])
        print(f"[click] {time.strftime('%H:%M:%S')} {click['tid']}번 → {pid or '없는 번호라 지정 안 됨'}")
    except ValueError as exc:
        print(f"[click] {time.strftime('%H:%M:%S')} {click['tid']}번 지정 안 됨: {exc}")
    return click["seq"]


def send_fall_event(ev, target_id, do_send):
    print(f"[fall] {time.strftime('%H:%M:%S')} {ev['pid']} {ev['stage']} door_check={ev.get('door_check', False)} "
          f"tid={ev['tid']} z={ev.get('z')} ref={ev.get('ref')}")
    if do_send:
        try:
            requests.post(FALL_URL, json={**ev, "target_id": target_id,
                                          "at": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(ev["at"]))}, timeout=0.5)
        except Exception:
            pass  # 서버가 잠시 꺼져 있어도 수신은 계속한다


def run(cli_port, data_port, cfg_path, do_send=True, target_id="room_01", height=2.7, tilt=90.0, room_filter=True, max_people=3):
    if serial is None:
        raise RuntimeError("pyserial 미설치: pip install pyserial")
    if do_send and requests is None:
        raise RuntimeError("requests 미설치: pip install requests")

    if cfg_path:
        send_config(cli_port, cfg_path)

    data = serial.Serial(data_port, 921600, timeout=0.1)
    buf = b""
    history = defaultdict(lambda: deque(maxlen=2000))
    window_start = time.time()
    last_live = 0  # 마지막 live snapshot 전송 시각
    latest = None  # 최신 프레임의 처리 결과
    latest_at = 0.0
    prev_zs = None
    history_tid = None
    pipeline = RoomPipeline(ROOM_POLYGON, BED_POLYGON, DOOR_POLYGON, max_people=max_people, room_filter=room_filter, tilt=tilt)
    click = {"tid": None, "seq": None}
    applied_seq = None
    if do_send:
        threading.Thread(target=poll_patient_clicks, args=(target_id, window_start, click), daemon=True, name="mmw-click").start()

    print(f"[run] reading {data_port} ... (window={WINDOW_SEC}s, send={do_send}, max_people={max_people})")
    try:
        while True:
            chunk = data.read(4096)
            if chunk:
                buf += chunk
                frames, buf = iter_frames(buf)
                now = time.time()
                for hdr, fb in frames:
                    if not frame_is_valid(hdr, fb):
                        prev_zs = None   # 다음 프레임의 1011이 가리킬 점을 알 수 없다
                        continue
                    # 사람 ID는 범위 밖으로 나가는 것도 봐야 하므로 범위 필터 전의 추적을 전부 넘긴다
                    targets = [track_to_world(t, height, tilt) for t in extract_targets(hdr, fb)]
                    zs, index = extract_points(hdr, fb, height, tilt)
                    link_points(targets, index, prev_zs)
                    prev_zs = zs
                    applied_seq = apply_click(now, pipeline, click, applied_seq)
                    latest = pipeline.update(now, targets)
                    latest_at = now
                    for ev in latest["events"]:
                        send_fall_event(ev, target_id, do_send)
                    t = latest["patient_target"]
                    if t:   # 보행 지표는 환자 번호로만 계산한다. 번호가 바뀌면 옛 번호의 이력은 버린다
                        if t["tid"] != history_tid:
                            history.clear()
                            history_tid = t["tid"]
                        history[t["tid"]].append(
                            (now, t["posX"], t["posY"], t["posZ"],
                             t["velX"], t["velY"])
                        )

            # 실시간 snapshot 전송 (LIVE_INTERVAL 마다)
            now = time.time()
            if do_send and now - last_live >= LIVE_INTERVAL and latest and now - latest_at <= LIVE_STALE_SEC:
                try:
                    requests.post(LIVE_URL, json={"targets": latest["targets"], "target_id": target_id,
                                                  "patient_tid": latest["patient_tid"], "patient": latest["patient"],
                                                  "falls": latest["falls"]}, timeout=0.5)
                except Exception:
                    pass  # 시각화 실패는 무시
                last_live = now

            # 윈도우 경과 → 지표 계산 + 전송
            if time.time() - window_start >= WINDOW_SEC:
                now = time.time()
                # 소속도 이력 유지: 오래된 샘플(> AFFINITY_HORIZON)·죽은 track 정리.
                # (윈도우마다 clear 하지 않는다 — 이력이 있어야 환자 소속도를 판정)
                for tid in list(history.keys()):
                    dq = history[tid]
                    while dq and now - dq[0][0] > AFFINITY_HORIZON:
                        dq.popleft()
                    if not dq:
                        del history[tid]

                raw, quality, presence = select_patient(history, now)
                if latest:   # history에는 환자만 있으므로 방 안의 인원은 따로 넣는다
                    presence["n_targets"] = max(presence["n_targets"], latest["n_people"])
                payload = {
                    "target_id": target_id,
                    "timestamp": int(time.time() * 1000),
                    "raw": raw or {"speed": 0.0, "speed_cv": 0.0, "sway": 0.0,
                                   "freeze_ratio": 0.0, "height_drop": 0.0},
                    "quality": quality,
                    "presence": presence,
                }
                if raw:
                    print(f"[{time.strftime('%H:%M:%S')}] raw={raw} | "
                          f"tid={presence['main_tid']} aff={presence['bed_affinity']} "
                          f"cand={presence.get('bed_candidates')} "
                          f"lock={presence['patient_locked']} walk={quality.get('walking')} "
                          f"str={quality.get('straightness')} "
                          f"n={presence['n_targets']}")
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] 데이터 부족/보류 presence={presence}")

                if do_send:
                    try:
                        r = requests.post(SERVER_URL, json=payload, timeout=3)
                        print(f"   -> POST {r.status_code} {r.json()}")
                    except Exception as e:
                        print(f"   -> POST 실패: {e}")

                window_start = time.time()
    except KeyboardInterrupt:
        print("\n[run] 종료")
    finally:
        data.close()


# ── 존 캡처 (측정 도구) ────────────────────────────────────────────────
def capture_zone(data_port, seconds, label="zone", margin=0.2, height=2.7, tilt=90.0):
    """SECONDS 초 동안 프레임을 읽어 감지된 target들의 (x,y,z)를 모아
    존(zone) 사각형을 추정해 출력한다. baseline/서버와 무관한 순수 측정 도구.

    사용 예:
      침대에 누운 채로:  python3 send_mmw.py --cli COM4 --data COM3 \
                          --cfg AOP_bed_2m7_d15.cfg --capture-zone 30 --capture-label bed
    로버스트하게 p5~p95 범위(+여유 margin m)를 '권장 존'으로 제시한다.
    """
    if serial is None:
        raise RuntimeError("pyserial 미설치: pip install pyserial")
    data = serial.Serial(data_port, 921600, timeout=0.1)
    buf = b""
    xs, ys, zs = [], [], []
    frames_seen = 0
    t0 = time.time()
    print(f"[capture] '{label}' {seconds:.0f}초 측정 시작 — 지금 해당 위치에서 움직이세요...")
    try:
        while time.time() - t0 < seconds:
            chunk = data.read(4096)
            if not chunk:
                continue
            buf += chunk
            frames, buf = iter_frames(buf)
            for hdr, fb in frames:
                frames_seen += 1
                for t in (track_to_world(x, height, tilt) for x in extract_targets(hdr, fb)):
                    xs.append(t["posX"])
                    ys.append(t["posY"])
                    zs.append(t["posZ"])
    except KeyboardInterrupt:
        print("\n[capture] 중단")
    finally:
        data.close()

    n = len(xs)
    print(f"[capture] '{label}' 완료 — 프레임 {frames_seen}, target 포인트 {n}")
    if n == 0:
        print("[capture] target 0 — 해당 위치에서 움직였는지 / 존 밖은 아닌지 확인 후 재시도")
        return

    ax, ay, az = np.array(xs), np.array(ys), np.array(zs)
    for name, a in (("X(좌우)", ax), ("Y(거리)", ay), ("Z(높이)", az)):
        print(f"  {name}: min={np.min(a):.2f} p5={np.percentile(a,5):.2f} "
              f"mean={np.mean(a):.2f} p95={np.percentile(a,95):.2f} max={np.max(a):.2f}")
    x0, x1 = np.percentile(ax, 5) - margin, np.percentile(ax, 95) + margin
    y0, y1 = np.percentile(ay, 5) - margin, np.percentile(ay, 95) + margin
    z0, z1 = np.percentile(az, 5) - margin, np.percentile(az, 95) + margin
    print(f"[capture] '{label}' 권장 존(p5~p95, 여유 {margin}m): "
          f"X[{x0:.1f} ~ {x1:.1f}]  Y[{y0:.1f} ~ {y1:.1f}]  Z[{z0:.1f} ~ {z1:.1f}]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", required=True, help="CLI 포트 (예: /dev/ttyACM0, COM4)")
    ap.add_argument("--data", required=True, help="DATA 포트 (예: /dev/ttyACM1, COM3)")
    ap.add_argument("--cfg", default=None, help="전송할 .cfg 경로 (생략 시 cfg 전송 안 함)")
    ap.add_argument("--target", default="room_01", help="target_id")
    ap.add_argument("--no-send", action="store_true", help="POST 없이 콘솔 출력만")
    ap.add_argument("--capture-zone", type=float, default=0.0, metavar="SEC",
                    help="측정 모드: SEC초 동안 target x,y,z를 모아 존 사각형 추정 후 종료")
    ap.add_argument("--capture-label", default="zone", help="캡처 라벨 (bed/door/walk 등)")
    ap.add_argument("--height", type=float, default=2.7, help="레이더 설치 높이(m)")
    ap.add_argument("--tilt", type=float, default=90.0, help="수평에서 아래로 기운 각도")
    ap.add_argument("--upright", action="store_true", help="레이더를 바로 달았을 때")
    ap.add_argument("--no-room-filter", action="store_true", help="측정 범위 밖 추적도 전부 사용(범위를 다시 잴 때)")
    ap.add_argument("--max-people", type=int, default=3, help="사람 ID를 줄 최대 인원")
    args = ap.parse_args()
    MOUNT_UPSIDE_DOWN = not args.upright
    if args.capture_zone > 0:
        # 측정 모드: (cfg 있으면 먼저 적용 후) 존만 캡처하고 종료 — 서버 불필요
        if args.cfg:
            send_config(args.cli, args.cfg)
        capture_zone(args.data, args.capture_zone, args.capture_label, height=args.height, tilt=args.tilt)
    else:
        run(args.cli, args.data, args.cfg, do_send=not args.no_send, target_id=args.target,
            height=args.height, tilt=args.tilt, room_filter=not args.no_room_filter, max_people=args.max_people)
