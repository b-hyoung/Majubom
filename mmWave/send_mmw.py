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
"""
import argparse
import base64
import json
import math
import os
import struct
import time
from collections import defaultdict, deque

import numpy as np

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
# TLV 1011 = target INDEX (포인트별 소속 track ID), target list가 아님
# TLV 1012 = track height (I2f, 12 bytes/target)
# TLV 1020 = compressed point cloud
# TLV 1021 = presence/flag
TARGET_STRUCT = '<I27f'  # tid(uint32) + 27 floats (pos/vel/acc/ec/g/conf) = 112 bytes
TARGET_SIZE = struct.calcsize(TARGET_STRUCT)  # 112

WINDOW_SEC = 6.0         # 보행 지표 계산 윈도우 (좁은 방: 한 직진 패스가 한 윈도우에 맞게 10→6 축소)
FREEZE_SPEED_THRESH = 0.15
SERVER_URL = "http://127.0.0.1:5002/mmw"   # 같은 Pi 안의 mmw_server
LIVE_URL = "http://127.0.0.1:5002/mmw/live" # 실시간 포인트 클라우드
LIVE_INTERVAL = 0.1      # snapshot POST 주기 (초). 2026-10-12 0.3→0.1: 대시보드 평면도가 0.2초마다 위치를 받는다
FALL_URL = "http://127.0.0.1:5002/mmw/fall"  # 프레임 단위 낙상 사건(fall_detect.py)
FALL_CONFIRM_SEC = 1.0   # 10-22 시연용 사용자 결정: 바닥에 1초 머물면 낙상(판정기 기본·테스트는 3초). 되돌리려면 3.0
# 2026-10-22 부스 마스킹(사용자 결정): 레이더 바닥 좌표의 다각형 밖 추적은 아예 버린다(통로 관객·옆 부스).
# 18:0x 사용자가 부스 가장자리를 1분 걸은 경로(레이더 좌표, 거리 약 0.6m 멀게 보이는 오차 그대로 포함)의
# 네 모서리 (0,-0.1)(-1.4,0.8)(0.7,3.7)(2.5,2.5)를 바깥으로 0.3m 넓힌 것. 설정되면 MAX_RANGE_M 원형 제한 대신 쓴다.
# 부스 값: [(2.96, 2.55), (0.63, 4.11), (-1.83, 0.72), (0.05, -0.49)]
# 10-29 간호학과 방: 사용자가 방 가장자리를 약 80초 걸은 경로(레이더 30° 좌표) + 누운 자리(침대, 걷지 않은 오른쪽 아래)를
# 합친 6각형 (-0.9,-0.1)(-1.4,1.65)(1.5,3.85)(1.75,2.0)(1.8,0.5)(0.3,-0.15)을 바깥으로 0.3m 넓힘
ROI_POLY = [(-1.13, -0.39), (-1.74, 1.76), (1.73, 4.4), (2.05, 2.03), (2.11, 0.31), (0.36, -0.45)]
REC_MAX_BYTES = 3 * 1024 ** 3        # 녹화 파일 하나 최대 3GB(넘으면 자동 중지, 10-28)
REC_MIN_FREE_BYTES = 2 * 1024 ** 3   # 디스크 남은 공간이 이보다 적으면 녹화 자동 중지
# 부스 침대 가운데(레이더 바닥 좌표): 18:1x 사용자가 침대에 누운 자리 실측(-0.15, 0.5), ToF 침대 위 확인.
# 랩실은 (0.1, 1.2). server/mmw_server.py BED_CENTER_XY·server/tof_autocal.py BED_CENTER와 같게 둔다
BED_CENTER = (1.0, 1.4)    # 10-29 레이더 30°에서 누운 자리 실측(0.98, 1.42) — 계산값 (0.5, 2.2)은 0.9m 어긋남(부스 (-0.15, 0.5), 랩실 (0.1, 1.2))


def in_poly(x, y, poly):
    """점이 다각형 안에 있나(짝홀 규칙)."""
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside
MAX_RANGE_M = 0     # 10-29 사용자 결정: 간호학과 방은 거리 제한 없이(현장에서 다시 잼). 0이면 제한 없음.
                    # 2026-10-21 결정은 3.8m(그날 사용자가 서서 정한 경계 3.68m+여유). 원시 진단 화면(:5012)은 그대로 전부 보여준다.

# 정지 물체 기준(2026-10-29): 정지 반사점(staticRangeAngleCfg)을 켠 뒤 빈 방에서도 키 큰 물건이 추적으로 몇 시간째
# 같은 자리에 남는다(간호학과 방 레이더 옆 0.9·1.4m, 높이 1.8~1.9m). 빈 방일 때 남아 있는 추적 자리를 기억해 두고,
# 그 자리 0.5m 안에서 생겨(생긴 뒤 1.5초 위치) 0.3m 넘게 움직인 적이 없는 추적만 버린다. 걸어 들어와 그 자리에 선 사람은 다른 데서
# 생긴 추적이라 안 숨는다. 클릭한 환자와 침대 가운데 1.2m 안 자리는 기준에 넣지 않는다(누우면 침대에서 새 추적이 생김).
# 기준 잡기: mmWave/.clutter_capture 파일을 만들면 20초 동안 모아 mmWave/clutter_baseline.json에 저장한다.
CLUTTER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clutter_baseline.json")
CLUTTER_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".clutter_capture")
CLUTTER_R = 0.5
CLUTTER_MOVE_M = 0.3
CLUTTER_CAPTURE_SEC = 20.0
CLUTTER_BED_M = 1.2
CLUTTER_FROZEN_M = 0.1
CLUTTER_FROZEN_SEC = 20.0
CLUTTER_SETTLE_SEC = 1.5   # 10-29: 유령 추적이 생긴 직후 0.3m 넘게 튀어 '움직인 적 있음'으로 남아 안 숨겨지던 문제


class ClutterMask:
    def __init__(self):
        self.spots, self.mtime, self.checked = [], None, 0.0
        self.born = {}        # tid → [태어난 x, y, 움직였나, 마지막으로 본 시각]
        self.capture = None   # 기준 잡는 중: {"until": 시각, "seen": {tid: [[x, y, z], ...]}}

    def _reload(self, now):
        if now - self.checked < 1.0:
            return
        self.checked = now
        try:
            m = os.path.getmtime(CLUTTER_FILE)
            if m != self.mtime:
                with open(CLUTTER_FILE, encoding="utf-8") as fh:
                    self.spots = json.load(fh).get("spots", [])
                self.mtime = m
                print(f"[clutter] 정지 물체 기준 {len(self.spots)}곳 읽음")
        except FileNotFoundError:
            self.spots, self.mtime = [], None
        except Exception as e:
            print(f"[clutter] 기준 읽기 실패: {e}")
        if self.capture is None and os.path.exists(CLUTTER_FLAG):
            self.capture = {"until": now + CLUTTER_CAPTURE_SEC, "seen": {}}
            print(f"[clutter] 정지 물체 기준 잡는 중({CLUTTER_CAPTURE_SEC:.0f}초, 방을 비워 두세요)")

    def _finish_capture(self):
        spots = []
        for tid, ps in self.capture["seen"].items():
            if len(ps) < 10:
                continue
            xs, ys, zs = (sorted(v[i] for v in ps) for i in range(3))
            x, y, z = xs[len(xs) // 2], ys[len(ys) // 2], zs[len(zs) // 2]
            if max(math.hypot(v[0] - x, v[1] - y) for v in ps) > CLUTTER_MOVE_M:
                continue   # 움직이는 추적(사람)은 기준에 넣지 않는다
            if math.hypot(x - BED_CENTER[0], y - BED_CENTER[1]) <= CLUTTER_BED_M:
                continue
            spots.append({"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)})
        with open(CLUTTER_FILE, "w", encoding="utf-8") as fh:
            json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "r": CLUTTER_R, "spots": spots}, fh, ensure_ascii=False, indent=1)
        try:
            os.remove(CLUTTER_FLAG)
        except FileNotFoundError:
            pass
        print(f"[clutter] 정지 물체 기준 저장: {spots}")
        self.capture, self.mtime = None, None

    def filter(self, targets, now, keep_tid=None):
        self._reload(now)
        if self.capture is not None:
            for t in targets:
                self.capture["seen"].setdefault(t["tid"], []).append([t["posX"], t["posY"], t.get("posZ", 0.0)])
            if now >= self.capture["until"]:
                self._finish_capture()
        out = []
        for t in targets:
            b = self.born.get(t["tid"])
            if b is None or now - b[3] > 2.0:   # 2초 넘게 안 보였던 번호는 새 추적
                b = self.born[t["tid"]] = [t["posX"], t["posY"], False, now, now, t["posX"], t["posY"], now]
            b[3] = now
            if now - b[4] < CLUTTER_SETTLE_SEC:   # 생긴 직후 칩이 위치를 잡는 동안 튀는 건 움직임으로 안 친다
                b[0], b[1] = t["posX"], t["posY"]
            elif not b[2] and math.hypot(t["posX"] - b[0], t["posY"] - b[1]) > CLUTTER_MOVE_M:
                b[2] = True
            # 10-29: 사람이 옆을 지날 때 유령 추적이 한 번 끌려갔다 돌아오면 '움직인 적 있음'으로 남아 안 숨겨졌다 →
            # 0.1m 안에 20초 넘게 꼼짝 않으면(사람은 가만히 있어도 이보다 흔들림) 그 자리에서 새로 생긴 것으로 본다
            if math.hypot(t["posX"] - b[5], t["posY"] - b[6]) > CLUTTER_FROZEN_M:
                b[5], b[6], b[7] = t["posX"], t["posY"], now
            elif b[2] and now - b[7] >= CLUTTER_FROZEN_SEC:
                b[0], b[1], b[2] = t["posX"], t["posY"], False
            if (not b[2] and t["tid"] != keep_tid and self.spots
                    and any(math.hypot(b[0] - s["x"], b[1] - s["y"]) <= CLUTTER_R for s in self.spots)):
                continue
            out.append(t)
        for tid in [k for k, v in self.born.items() if now - v[3] > 10.0]:
            del self.born[tid]
        return out
TOF_PRESENCE_URL = "http://127.0.0.1:5001/tof/presence"  # 침상 재실 → 침대에서 내려온 사람을 환자로 고정(2026-10-16)
PATIENT_URL = "http://127.0.0.1:5002/mmw/patient"        # 화면에서 누른 환자 지정

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


# 레이더를 거꾸로(칩 면 안에서 180°) 달면 방위각(x)·고도각(z)의 부호만 바뀌므로 회전 전에 둘 다 뒤집는다.
# 2026-10-12 보정 측정(키 170cm, 바닥 거리 2m·3m에 서기): 사람이 시야 중심보다 위(+11~+32°)에 있어야 하는데
# 레이더는 −24.6°·−27.5°로 보고했다. 부호를 뒤집으면 몸 중심 1.23m·1.05m로 맞는다 → 기본값 거꾸로 보정.
# 바로 달면 --upright로 실행한다.
MOUNT_UPSIDE_DOWN = True


def track_to_world(t, height=2.5, tilt=50.0, upside_down=None):
    """레이더 기준 track 좌표 → 바닥 기준 월드 좌표(x 오른쪽, y 바닥 수평거리, z 바닥 높이).

    2026-10-12 실측: cfg의 sensorPosition 2.5 0 50이 OK로 적용돼도 이 펌웨어의 TLV 1010 track은
    레이더 시선 기준 좌표로 나온다(침대에 앉은 사람 z≈-0.3, y≈2.5). 포인트 클라우드와 같은 회전을
    적용하면 y≈1.4, z≈0.4로 포인트 클라우드 월드 좌표와 일치한다. 위치와 속도 모두 회전한다.
    """
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


POINT_TLV, INDEX_TLV, HEIGHT_TLV = 1020, 1011, 1012


def extract_extras(hdr, frame_bytes, height=2.5, tilt=50.0):
    """추적 목록 외 나머지 데이터(2026-10-13, '버리지 말고 다 쓰기'):
    반사점(1020, 바닥 기준 월드 좌표·도플러·세기), 반사점별 소속 추적 번호(1011), 추적별 높이(1012: tid·maxZ·minZ, 펌웨어 좌표 그대로).
    반사점 좌표 변환은 diagnostic_decode와 같다(거꾸로 장착이면 방위각·고도각 부호 반전 후 하향각 회전)."""
    data = frame_bytes[FRAME_HEADER_LEN:]
    points, index, heights = [], b"", {}
    a = math.radians(tilt)
    flip = -1.0 if MOUNT_UPSIDE_DOWN else 1.0
    for _ in range(hdr["numTLVs"]):
        if len(data) < TLV_HEADER_LEN:
            break
        tlv_type, tlv_len = struct.unpack_from("<2I", data, 0)
        payload = data[TLV_HEADER_LEN:TLV_HEADER_LEN + tlv_len]
        if tlv_type == POINT_TLV and len(payload) >= 20:
            e_u, a_u, d_u, r_u, s_u = struct.unpack_from("<5f", payload)
            for i in range((len(payload) - 20) // 8):
                el, az, dop, ran, snr = struct.unpack_from("<bbhHH", payload, 20 + i * 8)
                r, elev, azim = ran * r_u, flip * el * e_u, flip * az * a_u
                x = r * math.cos(elev) * math.sin(azim)
                y = r * math.cos(elev) * math.cos(azim)
                z = r * math.sin(elev)
                points.append((x, y * math.cos(a) + z * math.sin(a), height - y * math.sin(a) + z * math.cos(a),
                               dop * d_u, snr * s_u))
        elif tlv_type == INDEX_TLV:
            index = bytes(payload)
        elif tlv_type == HEIGHT_TLV:
            for i in range(len(payload) // 12):
                tid, max_z, min_z = struct.unpack_from("<I2f", payload, i * 12)
                heights[tid] = (max_z, min_z)
        data = data[TLV_HEADER_LEN + tlv_len:]
    return points, index, heights


class PointLinker:
    """반사점 ↔ 추적 연결. TI 사람 추적 데모는 소속 번호(1011)를 '직전 프레임' 반사점 기준으로 낸다고 알려져 있어
    길이가 맞는 쪽(직전 → 이번 순)으로 연결하고, 어느 쪽이 맞았는지 센다(로그로 확인)."""

    def __init__(self):
        self.prev = []
        self.stats = {"prev": 0, "same": 0, "mismatch": 0, "no_index": 0}

    def link(self, points, index, targets):
        if not index:
            self.stats["no_index"] += 1
            pts = None
        elif len(index) == len(self.prev):
            self.stats["prev"] += 1
            pts = self.prev
        elif len(index) == len(points):
            self.stats["same"] += 1
            pts = points
        else:
            self.stats["mismatch"] += 1
            pts = None
        self.prev = points
        per = {}
        if pts is not None:
            for p, tid in zip(pts, index):
                if tid < 250:                                # 253·254·255 = 어느 추적에도 속하지 않음
                    per.setdefault(tid, []).append(p)
        for t in targets:
            ps = per.get(t["tid"], [])
            zs = sorted(p[2] for p in ps)
            t["npts"] = len(ps)                              # 이 프레임에 이 사람에게 속한 반사점 수(0이면 측정 없음 = 값 고정 의심)
            t["top_z"] = zs[min(len(zs) - 1, int(len(zs) * 0.9))] if zs else None   # 상위 10% 반사점 높이 ≈ 머리 쪽
            t["pz_med"] = zs[len(zs) // 2] if zs else None
            t["dop"] = (sum(abs(p[3]) for p in ps) / len(ps)) if ps else None       # 반사점 평균 속도 크기(움직임 정도)
        return targets


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

    # height_drop: z 최대 하강폭(기록용, 2026-10-12부터 낙상 판정에 쓰지 않음)
    height_drop = float(np.max(z) - np.min(z)) if np.ptp(z) > 0 else 0.0
    # low_z: 최근 1초 몸 높이 중앙값 — 서버가 바닥 근처(0.4m 미만)면 낙상 의심으로 판정
    low_z = float(np.median(z[t >= t[-1] - 1.0]))

    # stride_length / stride_cv: 속도 FFT → cadence → 보폭 추정
    stride_length, stride_cv = _estimate_stride(speed, t, speed_mean)

    return {
        "speed": round(speed_mean, 3),
        "speed_cv": round(speed_cv, 3),
        "sway": round(sway, 3),
        "freeze_ratio": round(freeze_ratio, 3),
        "height_drop": round(height_drop, 3),
        "low_z": round(low_z, 3),
        "stride_length": round(stride_length, 3),
        "stride_cv": round(stride_cv, 3),
    }


def gait_diagnostic_inputs(track_history, presence, now):
    """Explain the exact selected 6-second window; metadata is never DB input."""
    samples = [s for s in track_history.get(presence.get('main_tid'), ())
               if now - s[0] <= WINDOW_SEC]
    out = {'window_sec': WINDOW_SEC, 'main_tid': presence.get('main_tid'),
           'n_samples': len(samples), 'minimum_samples': 10,
           'sample_columns': ['timestamp_s', 'x_m', 'y_m', 'z_m', 'vx_m_s', 'vy_m_s'],
           'sample_preview': [list(s) for s in samples[-10:]],
           'preview_note': '최신 10개만 표시; 통계는 선택 트랙의 전체 6초 표본 사용',
           'storage': 'latest RAM only; DB insert arguments exclude this metadata'}
    if not samples:
        out['status'] = 'no_samples'
        return out
    arr = np.asarray(samples)
    speed = np.hypot(arr[:, 4], arr[:, 5])
    mean = float(np.mean(speed))
    std = float(np.std(speed))
    out.update(status='computed' if len(samples) >= 10 else 'insufficient_samples',
               speed={'n': len(speed), 'sum': float(np.sum(speed)),
                      'sumsq': float(np.sum(speed**2)), 'mean': mean, 'std_population': std,
                      'cv': std / mean if mean > 1e-6 else 0.0,
                      'stored_speed_if_ready': round(mean, 3) if len(samples) >= 10 else None},
               freeze={'threshold_m_s': FREEZE_SPEED_THRESH,
                       'count': int(np.count_nonzero(speed < FREEZE_SPEED_THRESH)),
                       'ratio': float(np.mean(speed < FREEZE_SPEED_THRESH))},
               height={'min_z': float(np.min(arr[:, 3])), 'max_z': float(np.max(arr[:, 3])),
                       'range_m': float(np.ptp(arr[:, 3])),
                       'note': '현재 height_drop은 max(z)−min(z); 시간 순서의 하강만 구분하지 않음'},
               gates={'walk_speed_min': WALK_SPEED_MIN, 'walk_displacement_min': WALK_DISP_MIN,
                      'straightness_min': STRAIGHT_MIN, 'patient_locked': presence.get('patient_locked')})
    dt = np.diff(arr[:, 0]); dt = dt[dt > 0]
    out['stride'] = {'minimum_samples': 20, 'minimum_speed': 0.1,
                     'sampling_hz': float(1/np.median(dt)) if len(dt) else None,
                     'frequency_band_hz': [0.5, 3.0], 'peak_to_mean_min': 2,
                     'note': '실제 기존 FFT 결과 stride_length/stride_cv는 raw에 표시; 추정 실패 0은 보행 실측 0과 구분'}
    return out


_NO_PREF = object()


def select_patient(track_history, now, preferred=_NO_PREF, preferred_lock=None):
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

    if preferred is not _NO_PREF:
        # 2026-10-16(10-20 확인): 낙상 판정기(fall_detect)가 고른 환자를 그대로 쓴다 — 침대에서 내려온 사람 고정·화면 지정·
        # 번호 바뀜 이어받기·침대 3m 안. 예전 BED_X/BED_Y(7월 랩실 좌표)로는 새 설치에서 소속도가 늘 0이라 2명 이상이면
        # 하루 내내 '환자 구분 보류'였고, 혼자 있으면 멀리 있는 방문객도 환자로 저장됐다.
        if preferred is not None and preferred in alive:
            main_tid, locked = preferred, True
        else:
            main_tid = preferred if preferred in alive else max(aff, key=aff.get)
            locked = False
    elif n_targets == 1:
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
                "bed_candidates": n_bed, "patient_locked": locked,
                "patient_source": "fall_detect" if preferred is not _NO_PREF else "bed_box", "patient_lock": preferred_lock}

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
def stream_active(data_port, seconds=2.0, baud=921600):
    """DATA 포트에서 seconds 동안 magic word로 시작하는 프레임이 보이면 True."""
    if serial is None:
        return False
    try:
        with serial.Serial(data_port, baud, timeout=0.1, exclusive=True) as port:
            buf, end = b"", time.time() + seconds
            while time.time() < end:
                buf += port.read(4096)
                if find_magic(buf) >= 0:
                    return True
    except Exception as e:
        print(f"[cfg] DATA 포트 확인 실패({type(e).__name__}) — 설정을 보낸다")
    return False


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


def run(cli_port, data_port, cfg_path, do_send=True, target_id="room_01", diagnostics_url="http://127.0.0.1:5012/api/ingest", height=2.5, tilt=50.0, min_snr=0.0, max_range=None, fall_confirm_sec=None, roi=None):
    fall_confirm_sec = FALL_CONFIRM_SEC if fall_confirm_sec is None else fall_confirm_sec
    max_range = MAX_RANGE_M if max_range is None else max_range
    if serial is None:
        raise RuntimeError("pyserial 미설치: pip install pyserial")
    if do_send and requests is None:
        raise RuntimeError("requests 미설치: pip install requests")

    if cfg_path:
        # 이미 프레임이 나오는 레이더에 설정을 다시 보내면 출력이 멈춘다(2026-10-06, 10-12 재현).
        # 전원을 새로 켜서 데이터가 없을 때만 보낸다.
        if stream_active(data_port):
            print("[cfg] 이미 데이터가 나오고 있어 설정 전송을 건너뜀")
        else:
            send_config(cli_port, cfg_path)

    import sys
    from pathlib import Path
    diagnostics = frame_stream = None
    if diagnostics_url:
        try:
            sys.path.append(str(Path(__file__).resolve().parents[1] / 'server'))
            from live_diagnostics import LatestPublisher, FrameStreamPublisher
            from diagnostic_decode import decode_frame
            diagnostics = LatestPublisher(diagnostics_url)
            frame_stream = FrameStreamPublisher(diagnostics_url.rsplit('/api/', 1)[0] + '/api/stream-ingest')
        except Exception as exc:
            print(f'[diagnostics] unavailable: {type(exc).__name__}')
    data = serial.Serial(data_port, 921600, timeout=0.1, exclusive=True)
    diagnostic_bytes = diagnostic_frames = 0
    diagnostic_frame = None
    diagnostic_pending = None
    diagnostic_error = None
    diagnostic_at = None
    diagnostic_sent = 0.0
    buf = b""
    history = defaultdict(lambda: deque(maxlen=2000))
    window_start = time.time()
    last_live = 0  # 마지막 live snapshot 전송 시각
    latest_targets = []  # 최신 프레임의 target 목록
    from fall_detect import FallDetector
    import fall_detect as _fd
    _fd.BED_CENTER = BED_CENTER       # 부스 침대 위치(판정기 기본값은 랩실 — 예전 기록 재생·테스트용)
    fall_detector = FallDetector(manual_only=True, confirm_sec=fall_confirm_sec, auto_bed=True)   # 10-22: 환자는 화면 클릭으로만(10-29: 클릭 전엔 침대에 있던 사람 자동). 2026-10-12: 본인 평소 높이 대비 하강으로 프레임마다 낙상 판정
    # 2026-10-16: 환자 고정에 쓰는 바깥 정보 — ToF 침상 재실(침대에서 내려온 사람 = 환자)과 화면에서 누른 환자 지정.
    # 0.5초마다 별도 스레드가 받아 둔다(레이더 수신 루프를 HTTP 대기로 막지 않게).
    ctx = {"in_bed": None, "manual_tid": None, "manual_seq": None}
    ctx_started = time.time()

    def _ctx_worker():
        while True:
            try:
                ctx["in_bed"] = requests.get(TOF_PRESENCE_URL, timeout=0.5).json().get("in_bed")
            except Exception:
                ctx["in_bed"] = None
            try:
                m = requests.get(PATIENT_URL, params={"target": target_id}, timeout=0.5).json() or {}
                # 10-22 저녁: 리더가 다시 켜지기 전에 누른 클릭은 쓰지 않는다 — 재시작 뒤 레이더가 번호를 새로 매겨
                # 같은 번호가 다른 사람일 수 있다(16:42 재시작 직후 예전 '12번'이 다시 적용됨)
                if m.get("seq") is not None and m["seq"] > ctx_started:
                    ctx["manual_tid"], ctx["manual_seq"] = m.get("tid"), m.get("seq")
            except Exception:
                pass
            time.sleep(0.5)
    if do_send:
        import threading as _threading
        _threading.Thread(target=_ctx_worker, daemon=True, name="mmw-ctx").start()
    clutter = ClutterMask()
    point_linker = PointLinker()     # 2026-10-13: 반사점·소속·추적 높이까지 모두 사용
    linker_logged = time.time()
    # 라벨 녹화(2026-10-13): mmWave/recordings/.active 파일({"file":..., "label":...})이 있는 동안 모든 프레임을 저장한다.
    # 녹화 진행자가 동작을 바꿀 때마다 파일의 label을 바꾼다. 판정·전송에는 영향 없음.
    import json as _json, os as _os, shutil as _shutil
    rec_flag = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "recordings", ".active")
    rec = {"checked": 0.0, "cfg": None, "fh": None, "path": None}
    # 낙상 판정 전후 기록(2026-10-13): 판정 6초 전 ~ 3초 뒤의 모든 추적을 mmWave/fall_traces/에 저장해 오탐 원인을 장면째로 본다.
    import json as _json, os as _os
    trace_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "fall_traces")
    trace_buf = deque()              # (시각, [[tid, x, y, z, vx, vy], ...]) 최근 15초
    trace_pending = []               # 저장 대기: 판정 뒤 3초를 더 모은다

    print(f"[run] reading {data_port} ... (window={WINDOW_SEC}s, send={do_send})")
    try:
        while True:
            chunk = data.read(4096)
            if chunk:
                diagnostic_bytes += len(chunk)
                buf += chunk
                frames, buf = iter_frames(buf)
                now = time.time()
                for hdr, fb in frames:
                    diagnostic_frames += 1
                    diagnostic_at = now
                    diagnostic_pending = (hdr, fb)
                    targets = [track_to_world(t, height, tilt) for t in extract_targets(hdr, fb)]
                    pts_now, idx_now, heights_now = extract_extras(hdr, fb, height, tilt)
                    targets = point_linker.link(pts_now, idx_now, targets)
                    if roi:   # 부스 다각형 밖 추적은 이 뒤 어디에도 전달하지 않는다(낙상·환자·보행·평면도·녹화 모두에서 없음)
                        targets = [t for t in targets if in_poly(t["posX"], t["posY"], roi)]
                    elif max_range and max_range > 0:   # 범위 밖 추적은 이 뒤 어디에도 전달하지 않는다
                        targets = [t for t in targets if math.hypot(t["posX"], t["posY"]) <= max_range]
                    targets = clutter.filter(targets, now, keep_tid=fall_detector.patient_tid)   # 정지 물체 추적 버림(10-29)
                    if frame_stream:   # 노트북 스트림(2026-10-31): 원본 프레임 바이트 전부 + 이 리더가 쓴 바닥 좌표 추적. Pi에는 저장 안 함
                        frame_stream.publish('mmwave', {
                            'frame_number': hdr['frameNum'], 'raw_b64': base64.b64encode(fb).decode('ascii'),
                            'mount': {'height_m': height, 'down_tilt_deg': tilt, 'upside_down': MOUNT_UPSIDE_DOWN},
                            'patient_tid': fall_detector.patient_tid,
                            'tracks': [{'tid': t['tid'], 'npts': t.get('npts'),
                                        **{k: round(t[k], 4) for k in ('posX', 'posY', 'posZ', 'velX', 'velY', 'velZ')
                                           if isinstance(t.get(k), (int, float)) and math.isfinite(t[k])}}
                                       for t in targets]})
                    for t in targets:
                        t["fw_height"] = heights_now.get(t["tid"])   # 펌웨어 추적 높이(1012, 기록용)
                    if now - linker_logged > 60:
                        print(f"[points] 반사점 연결 통계(1분): {point_linker.stats}")
                        point_linker.stats = {k: 0 for k in point_linker.stats}
                        linker_logged = now
                    latest_targets = targets  # 매 프레임 갱신
                    if now - rec["checked"] > 0.3:
                        rec["checked"] = now
                        try:
                            with open(rec_flag, encoding="utf-8") as fh:
                                rec["cfg"] = _json.load(fh)
                        except Exception:
                            rec["cfg"] = None
                        want = (rec["cfg"] or {}).get("file")
                        # 10-28: 10-23 시연 녹화를 끄지 않아 5일 동안 9.8GB가 쌓여 디스크가 99% 찼다 →
                        # 파일 3GB 초과 또는 디스크 남은 공간 2GB 미만이면 녹화를 스스로 끈다(.active 삭제)
                        if want and rec["path"] == want and rec["fh"]:
                            try:
                                too_big = _os.path.getsize(want) > REC_MAX_BYTES
                                low_disk = _shutil.disk_usage(_os.path.dirname(want)).free < REC_MIN_FREE_BYTES
                            except OSError:
                                too_big = low_disk = False
                            if too_big or low_disk:
                                print(f"[rec] 녹화 자동 중지: {'파일 3GB 초과' if too_big else '디스크 남은 공간 2GB 미만'} — {want}")
                                try:
                                    _os.remove(rec_flag)
                                except OSError:
                                    pass
                                rec["cfg"], want = None, None
                        if want != rec["path"]:
                            if rec["fh"]:
                                rec["fh"].close()
                            rec["fh"] = open(want, "a", encoding="utf-8") if want else None
                            rec["path"] = want
                    if rec["fh"]:
                        roles_now = fall_detector.roles()
                        rec["fh"].write(_json.dumps({
                            "t": round(now, 3), "label": (rec["cfg"] or {}).get("label"),
                            "tracks": [[t["tid"], round(t["posX"], 3), round(t["posY"], 3), round(t["posZ"], 3), round(t["velX"], 3),
                                        round(t["velY"], 3), round(t.get("velZ", 0.0), 3), t.get("npts"),
                                        None if t.get("top_z") is None else round(t["top_z"], 3), roles_now.get(t["tid"]),
                                        None if t.get("fw_height") is None else [round(v, 3) for v in t["fw_height"]]] for t in targets],
                            "points": [[round(p[0], 3), round(p[1], 3), round(p[2], 3), round(p[3], 3), round(p[4], 1)] for p in pts_now],
                            "index": list(idx_now)}, ensure_ascii=False) + "\n")
                    frame_rec = (round(now, 3), [[t["tid"], round(t["posX"], 3), round(t["posY"], 3), round(t["posZ"], 3),
                                                  round(t["velX"], 3), round(t["velY"], 3), round(t.get("velZ", 0.0), 3),
                                                  t.get("npts"), None if t.get("top_z") is None else round(t["top_z"], 3),
                                                  None if t.get("dop") is None else round(t["dop"], 3)] for t in targets])
                    trace_buf.append(frame_rec)
                    while trace_buf and now - trace_buf[0][0] > 15.0:   # 10-22: 6초 → 15초(늦은 판정 분석용)
                        trace_buf.popleft()
                    for pend in list(trace_pending):
                        pend["after"].append(frame_rec)
                        if now >= pend["until"]:
                            trace_pending.remove(pend)
                            try:
                                _os.makedirs(trace_dir, exist_ok=True)
                                name = time.strftime("%Y%m%d_%H%M%S", time.localtime(pend["event"]["at"])) + f"_tid{pend['event']['tid']}.json"
                                with open(_os.path.join(trace_dir, name), "w", encoding="utf-8") as fh:
                                    _json.dump(pend, fh, ensure_ascii=False)
                            except Exception as exc:
                                print(f"[fall] 전후 기록 저장 실패: {exc}")
                    prev_patient = fall_detector.patient_tid
                    fall_detector.set_bed(now, ctx["in_bed"])
                    if ctx["manual_seq"] is not None and ctx["manual_seq"] != fall_detector.manual_seq:
                        print(f"[click] {time.strftime('%H:%M:%S')} 화면에서 {ctx['manual_tid']}번 지정")   # 클릭과 자동 이어받기 구분(10-22)
                    fall_detector.set_manual(now, ctx["manual_tid"], ctx["manual_seq"])
                    if fall_detector.manual_reject:
                        print(f"[click] {time.strftime('%H:%M:%S')} {ctx['manual_tid']}번 지정 안 됨: {fall_detector.manual_reject}")
                        fall_detector.manual_reject = None
                    for ev in fall_detector.update(now, targets):
                        if ev["stage"] in ("fallen", "caution"):   # 10-22: 주의도 전후 기록(놓친 낙상 분석용)
                            trace_pending.append({"event": ev, "track": fall_detector.debug(ev["tid"]),
                                                  "before": list(trace_buf), "after": [], "until": now + 3.0})
                        print(f"[fall] {time.strftime('%H:%M:%S')} {ev['stage']} tid={ev['tid']} "
                              f"z={ev['z']} ref={ev['ref']} {ev.get('drop_sec', ev.get('low_sec', ''))}")
                        if do_send:
                            try:
                                requests.post(FALL_URL, json={**ev, "target_id": target_id,
                                                              "at": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(ev['at']))},
                                              timeout=0.5)
                            except Exception:
                                pass  # 서버가 잠시 꺼져 있어도 수신은 계속한다
                    if fall_detector.patient_tid != prev_patient:   # 10-22: 환자가 바뀐 때를 로그로(놓친 낙상 분석용)
                        pt = next((t for t in targets if t["tid"] == fall_detector.patient_tid), None)
                        print(f"[patient] {time.strftime('%H:%M:%S')} {prev_patient} → {fall_detector.patient_tid} "
                              f"고정={fall_detector.locked} 침대ToF={ctx['in_bed']} 사람={len(targets)}"
                              + (f" 위치=({pt['posX']:.2f},{pt['posY']:.2f}) z={pt['posZ']:.2f}" if pt else ""))
                    for t in targets:
                        history[t["tid"]].append(
                            (now, t["posX"], t["posY"], t["posZ"],
                             t["velX"], t["velY"])
                        )

            if diagnostics and time.monotonic() - diagnostic_sent >= 0.2:
                if diagnostic_pending is not None:
                    try:
                        diagnostic_frame = decode_frame(*diagnostic_pending, height, tilt, min_snr)
                        diagnostic_error = None
                    except Exception as exc:
                        diagnostic_frame = None
                        diagnostic_error = f'diagnostic decode failed: {type(exc).__name__}'
                    diagnostic_pending = None
                diagnostics.publish('mmwave', {
                    'status': 'streaming' if diagnostic_at else 'waiting', 'error': diagnostic_error,
                    'bytes': diagnostic_bytes, 'frames': diagnostic_frames,
                    'last_frame_at': diagnostic_at, 'frame': diagnostic_frame,
                    'mount': {'height_m': height, 'down_tilt_deg': tilt},
                })
                diagnostic_sent = time.monotonic()

            # 실시간 snapshot 전송 (LIVE_INTERVAL 마다)
            now = time.time()
            # 레이더 프레임이 2초 넘게 없으면 실시간 위치를 보내지 않는다. 빈 목록을 계속 보내면 대시보드가
            # '연결됨·사람 없음'으로 보여 출력이 멈춘 것을 숨겼다(2026-10-12).
            if do_send and now - last_live >= LIVE_INTERVAL and diagnostic_at is not None and now - diagnostic_at <= 2:
                try:
                    roles = fall_detector.roles()
                    snap = [{"tid": t["tid"], "role": roles.get(t["tid"]), "npts": t.get("npts"),
                             "x": round(t["posX"], 3),
                             "y": round(t["posY"], 3),
                             "z": round(t["posZ"], 3),
                             "vx": round(t["velX"], 3),
                             "vy": round(t["velY"], 3)}
                            for t in latest_targets]
                    requests.post(LIVE_URL, json={"targets": snap, "target_id": target_id, "patient_tid": fall_detector.patient_tid,
                                                  "patient": fall_detector.patient_info(),
                                                  "falls": fall_detector.state()}, timeout=0.5)
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

                raw, quality, presence = select_patient(history, now, preferred=fall_detector.patient_tid,
                                                        preferred_lock=fall_detector.locked)
                payload = {
                    "target_id": target_id,
                    "timestamp": int(time.time() * 1000),
                    "raw": raw or {"speed": 0.0, "speed_cv": 0.0, "sway": 0.0,
                                   "freeze_ratio": 0.0, "height_drop": 0.0},
                    "quality": quality,
                    "presence": presence,
                }
                try:
                    payload['diagnostic_inputs'] = gait_diagnostic_inputs(history, presence, now)
                except Exception:
                    pass  # Observability cannot interrupt the original sensor delivery.
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
def capture_zone(data_port, seconds, label="zone", margin=0.2):
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
                for t in extract_targets(hdr, fb):
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
    ap.add_argument("--diagnostics-url", default="http://127.0.0.1:5012/api/ingest", help="latest-only raw dashboard relay; empty disables")
    ap.add_argument("--height", type=float, default=2.5)
    ap.add_argument("--tilt", type=float, default=50.0)
    ap.add_argument("--fall-confirm-sec", type=float, default=FALL_CONFIRM_SEC, help="바닥 높이에 이만큼 머물면 낙상(초)")
    ap.add_argument("--roi", default=None, help='부스 다각형 "x,y x,y ..."(레이더 바닥 좌표 m). 기본은 ROI_POLY, "none"이면 끄고 --max-range 사용')
    ap.add_argument("--max-range", type=float, default=MAX_RANGE_M, help="레이더에서 수평 이 거리(m) 밖 추적은 버림. 0이면 제한 없음")
    ap.add_argument("--upright", action="store_true", help="레이더를 바로 달았을 때(기본은 거꾸로 장착 보정: x·고도각 부호 반전)")
    ap.add_argument("--min-snr", type=float, default=0.0, help="diagnostic point filter only")
    ap.add_argument("--no-send", action="store_true", help="POST 없이 콘솔 출력만")
    ap.add_argument("--capture-zone", type=float, default=0.0, metavar="SEC",
                    help="측정 모드: SEC초 동안 target x,y,z를 모아 존 사각형 추정 후 종료")
    ap.add_argument("--capture-label", default="zone", help="캡처 라벨 (bed/door/walk 등)")
    args = ap.parse_args()
    if args.capture_zone > 0:
        # 측정 모드: (cfg 있으면 먼저 적용 후) 존만 캡처하고 종료 — 서버 불필요
        if args.cfg:
            send_config(args.cli, args.cfg)
        capture_zone(args.data, args.capture_zone, args.capture_label)
    else:
        MOUNT_UPSIDE_DOWN = not args.upright
        import diagnostic_decode   # 진단 디코더는 send_mmw를 별도 모듈로 import하므로 같은 장착 방향을 넘긴다
        diagnostic_decode.protocol.MOUNT_UPSIDE_DOWN = MOUNT_UPSIDE_DOWN
        run(args.cli, args.data, args.cfg, do_send=not args.no_send, target_id=args.target, diagnostics_url=args.diagnostics_url, height=args.height, tilt=args.tilt, min_snr=args.min_snr, max_range=args.max_range, fall_confirm_sec=args.fall_confirm_sec,
            roi=None if (args.roi or "").lower() == "none" else
            ([tuple(float(v) for v in p.split(",")) for p in args.roi.split()] if args.roi else ROI_POLY))
