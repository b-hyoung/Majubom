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
def compute_gait_features(track_history):
    """
    track_history: dict[tid] -> deque of (t, x, y, z, vx, vy)
    가장 오래 추적된 track 을 주 보행자로 보고 지표 계산.
    반환: (raw dict, quality dict, presence dict)  또는 (None, ..., ..) 데이터 부족 시.
    """
    n_targets = len(track_history)
    if n_targets == 0:
        return None, {"reliable": False}, {"n_targets": 0}

    # 주 보행자 = 샘플이 가장 많은 track
    main_tid = max(track_history, key=lambda k: len(track_history[k]))
    samples = list(track_history[main_tid])
    if len(samples) < 10:
        return None, {"reliable": False}, {"n_targets": n_targets}

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

    raw = {
        "speed": round(speed_mean, 3),
        "speed_cv": round(speed_cv, 3),
        "sway": round(sway, 3),
        "freeze_ratio": round(freeze_ratio, 3),
        "height_drop": round(height_drop, 3),
        "stride_length": round(stride_length, 3),
        "stride_cv": round(stride_cv, 3),
    }
    quality = {"reliable": True, "n_samples": len(samples)}
    presence = {"n_targets": n_targets, "main_tid": int(main_tid)}
    return raw, quality, presence


# ── 메인 루프 ──────────────────────────────────────────────────────────
def send_config(cli_port, cfg_path, baud=115200):
    """CLI 포트로 .cfg 한 줄씩 전송."""
    if serial is None:
        raise RuntimeError("pyserial 미설치: pip install pyserial")
    with serial.Serial(cli_port, baud, timeout=1) as cli:
        with open(cfg_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("%"):
                    continue
                cli.write((line + "\n").encode())
                time.sleep(0.03)
                cli.readall()  # echo 비우기
    print(f"[cfg] sent: {cfg_path}")


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
                raw, quality, presence = compute_gait_features(history)
                payload = {
                    "target_id": target_id,
                    "timestamp": int(time.time() * 1000),
                    "raw": raw or {"speed": 0.0, "speed_cv": 0.0, "sway": 0.0,
                                   "freeze_ratio": 0.0, "height_drop": 0.0},
                    "quality": quality,
                    "presence": presence,
                }
                if raw:
                    print(f"[{time.strftime('%H:%M:%S')}] raw={raw} presence={presence}")
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] 데이터 부족/보류 presence={presence}")

                if do_send:
                    try:
                        r = requests.post(SERVER_URL, json=payload, timeout=3)
                        print(f"   -> POST {r.status_code} {r.json()}")
                    except Exception as e:
                        print(f"   -> POST 실패: {e}")

                history.clear()
                window_start = time.time()
    except KeyboardInterrupt:
        print("\n[run] 종료")
    finally:
        data.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", required=True, help="CLI 포트 (예: /dev/ttyACM0, COM4)")
    ap.add_argument("--data", required=True, help="DATA 포트 (예: /dev/ttyACM1, COM3)")
    ap.add_argument("--cfg", default=None, help="전송할 .cfg 경로 (생략 시 cfg 전송 안 함)")
    ap.add_argument("--target", default="room_01", help="target_id")
    ap.add_argument("--no-send", action="store_true", help="POST 없이 콘솔 출력만")
    args = ap.parse_args()
    run(args.cli, args.data, args.cfg, do_send=not args.no_send, target_id=args.target)
