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
import math
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

WINDOW_SEC = 10.0        # 보행 지표 계산 윈도우
FREEZE_SPEED_THRESH = 0.15
SERVER_URL = "http://127.0.0.1:5002/mmw"   # 같은 Pi 안의 mmw_server
LIVE_URL = "http://127.0.0.1:5002/mmw/live" # 실시간 포인트 클라우드
LIVE_INTERVAL = 0.3      # snapshot POST 주기 (초)

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

    # 보행 판정: 평균속도 + 윈도우 내 순이동 (제자리 미동/누움을 baseline 에서 배제)
    arr = np.array(recent)
    disp = float(np.hypot(arr[-1, 1] - arr[0, 1], arr[-1, 2] - arr[0, 2]))
    walking = (raw["speed"] >= WALK_SPEED_MIN) and (disp >= WALK_DISP_MIN)
    quality = {"reliable": True, "walking": walking, "n_samples": len(recent)}
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


def run(cli_port, data_port, cfg_path, do_send=True, target_id="room_01"):
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
    latest_targets = []  # 최신 프레임의 target 목록

    print(f"[run] reading {data_port} ... (window={WINDOW_SEC}s, send={do_send})")
    try:
        while True:
            chunk = data.read(4096)
            if chunk:
                buf += chunk
                frames, buf = iter_frames(buf)
                now = time.time()
                for hdr, fb in frames:
                    targets = extract_targets(hdr, fb)
                    latest_targets = targets  # 매 프레임 갱신
                    for t in targets:
                        history[t["tid"]].append(
                            (now, t["posX"], t["posY"], t["posZ"],
                             t["velX"], t["velY"])
                        )

            # 실시간 snapshot 전송 (LIVE_INTERVAL 마다)
            now = time.time()
            if do_send and now - last_live >= LIVE_INTERVAL and latest_targets:
                try:
                    snap = [{"tid": t["tid"],
                             "x": round(t["posX"], 3),
                             "y": round(t["posY"], 3),
                             "z": round(t["posZ"], 3),
                             "vx": round(t["velX"], 3),
                             "vy": round(t["velY"], 3)}
                            for t in latest_targets]
                    requests.post(LIVE_URL, json={"targets": snap}, timeout=0.5)
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
        run(args.cli, args.data, args.cfg, do_send=not args.no_send, target_id=args.target)
