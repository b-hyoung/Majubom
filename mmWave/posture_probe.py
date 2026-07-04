"""
posture_probe.py — mmWave 자세 프로브 + 침대 ROI + 데이터셋 축적기
====================================================================
목적:
  1) 침대 ROI 캘리브레이션: 사람이 누우면 그때 점 범위로 '침대 영역'을 자동으로 잡는다.
     - 센서 높이를 몰라도, 누운 몸 중심을 바닥기준 --bed-surface(기본 0.5m)로 맞추는
       z_offset 을 자동 추정해 저장한다.
  2) 실시간 판정: 규칙기반 눕/앉/서 + 침대 안/가장자리(전조)/밖(이탈) 위치.
  3) 데이터셋 축적: 특징 + 위치 + 원시 점군을 라벨과 함께 JSONL로 쌓아 B단계 학습셋으로.

핵심(논문 ① Sensors 2024.11): 자세 = 점군 높이(z) 분포. 희소하니 --window 초 누적해 안정화.
좌표는 모두 '센서 원점' 기준: x=좌우, y=앞뒤(거리), z=높이. z는 --z-offset 로 바닥기준 보정.

사용:
  # 1) 침대 캘리브레이션 (누운 채 10초)
  python3 posture_probe.py --data /dev/ttyACM1 --cli /dev/ttyACM0 --cfg AOP_v_C_legs_safe.cfg --calibrate
  # 2) 이후 판정 + 데이터 축적 (ROI·z_offset 자동 로드)
  python3 posture_probe.py --data /dev/ttyACM1 --label lie
  python3 posture_probe.py --data /dev/ttyACM1 --label sit
  python3 posture_probe.py --data /dev/ttyACM1           # 현장: 규칙예측을 약라벨로
"""
import argparse
import json
import math
import os
import struct
import time
from collections import deque

import numpy as np

try:
    import serial
except ImportError:
    serial = None

# 검증된 프레임 파싱을 재사용 (같은 폴더의 send_mmw.py)
from send_mmw import (
    FRAME_HEADER_LEN, TLV_HEADER_LEN,
    iter_frames, extract_targets, send_config,
)

# ── 점군 TLV(1020) ─────────────────────────────────────────────────────
# 3D People Counting/Tracking '압축 점군' 포맷:
#   payload = pointUnit(20B) + N x compressedPoint(8B)
#   pointUnit  : <5f  = elevationUnit, azimuthUnit, dopplerUnit, rangeUnit, snrUnit
#   compressed : <bbhHH = elevation(i8), azimuth(i8), doppler(i16), range(u16), snr(u16)
# ※ 펌웨어에 따라 elevation/azimuth 순서가 뒤바뀔 수 있음 → z가 이상하면 SWAP_ELAZ=True.
POINT_CLOUD_TLV = 1020
POINT_UNIT_FMT = '<5f'
POINT_UNIT_SIZE = struct.calcsize(POINT_UNIT_FMT)   # 20
POINT_FMT = '<bbhHH'
POINT_SIZE = struct.calcsize(POINT_FMT)             # 8
SWAP_ELAZ = False


def parse_point_cloud(payload):
    """압축 점군 payload → [(x,y,z,doppler,snr), ...] (센서 좌표계, z=높이)."""
    if len(payload) < POINT_UNIT_SIZE:
        return []
    eU, aU, dU, rU, sU = struct.unpack_from(POINT_UNIT_FMT, payload, 0)
    body = payload[POINT_UNIT_SIZE:]
    n = len(body) // POINT_SIZE
    pts = []
    for i in range(n):
        a, b, dop, rng, snr = struct.unpack_from(POINT_FMT, body, i * POINT_SIZE)
        el_i, az_i = (b, a) if SWAP_ELAZ else (a, b)
        r = rng * rU
        if r <= 0 or r > 20:
            continue
        az = az_i * aU
        el = el_i * eU
        x = r * math.cos(el) * math.sin(az)
        y = r * math.cos(el) * math.cos(az)
        z = r * math.sin(el)
        pts.append((x, y, z, dop * dU, snr * sU))
    return pts


def extract_pointcloud(hdr, frame_bytes):
    """프레임에서 점군 TLV(1020)를 찾아 점 리스트 반환. 없으면 []."""
    data = frame_bytes[FRAME_HEADER_LEN:]
    pts = []
    for _ in range(hdr["numTLVs"]):
        if len(data) < TLV_HEADER_LEN:
            break
        tlv_type, tlv_len = struct.unpack_from("<2I", data, 0)
        payload = data[TLV_HEADER_LEN:TLV_HEADER_LEN + tlv_len]
        if tlv_type == POINT_CLOUD_TLV:
            pts = parse_point_cloud(payload)
        data = data[TLV_HEADER_LEN + tlv_len:]
    return pts


def frame_points(hdr, fb):
    """점군(1020) 우선, 없으면 track 중심점으로 대체. (points, used_pc: bool)"""
    pts = extract_pointcloud(hdr, fb)
    if pts:
        return pts, True
    pts = [(t["posX"], t["posY"], t["posZ"], 0.0, 0.0) for t in extract_targets(hdr, fb)]
    return pts, False


# ── 자세 특징 & 규칙 분류 ───────────────────────────────────────────────
def compute_features(points, z_offset=0.0):
    if not points:
        return None
    zs = [p[2] + z_offset for p in points]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    n = len(points)
    z_max, z_min = max(zs), min(zs)
    return {
        "n": n,
        "z_max": round(z_max, 3),
        "z_min": round(z_min, 3),
        "z_spread": round(z_max - z_min, 3),   # 세로 퍼짐 (서기=큼, 눕기=작음)
        "z_cent": round(sum(zs) / n, 3),
        "x_spread": round(max(xs) - min(xs), 3),
        "y_spread": round(max(ys) - min(ys), 3),
    }


# 기본 임계 = "추정치". 첫 실행 때 값 보고 같이 조정한다. (바닥기준 높이 m)
DEFAULT_TH = {
    "stand_zmax": 1.20, "stand_spread": 0.80,
    "lie_zmax": 0.75,  "lie_spread": 0.45,
}


def classify(feat, th):
    if feat is None:
        return "no-target"
    if feat["z_max"] >= th["stand_zmax"] and feat["z_spread"] >= th["stand_spread"]:
        return "stand(섬)"
    if feat["z_max"] <= th["lie_zmax"] and feat["z_spread"] <= th["lie_spread"]:
        return "lie(눕음)"
    return "sit(앉음)"


# ── 침대 ROI ────────────────────────────────────────────────────────────
def load_roi(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def locate(cx, cy, roi, margin):
    """중심점(cx,cy) → (zone, side). side 는 센서축 기준(x-/x+/y-/y+)."""
    xmin, xmax, ymin, ymax = roi["x_min"], roi["x_max"], roi["y_min"], roi["y_max"]
    outside = cx < xmin or cx > xmax or cy < ymin or cy > ymax
    side = []
    if cx <= xmin + margin: side.append("x-")
    if cx >= xmax - margin: side.append("x+")
    if cy <= ymin + margin: side.append("y-")
    if cy >= ymax - margin: side.append("y+")
    sidestr = "/".join(side) if side else "center"
    if outside:
        return "OUTSIDE(이탈)", sidestr
    if side:
        return "edge(전조)", sidestr
    return "inside(취침)", "center"


def calibrate(data, args):
    """누운 상태를 캡처해 침대 ROI + z_offset(자동) 저장."""
    secs = args.calib_secs
    print(f"\n[calib] 침대에 누워주세요 — {secs:.0f}초간 캡처합니다...")
    xs, ys, zs = [], [], []
    buf = b""
    start = time.time()
    while time.time() - start < secs:
        chunk = data.read(4096)
        if not chunk:
            continue
        buf += chunk
        frames, buf = iter_frames(buf)
        for hdr, fb in frames:
            for p in extract_pointcloud(hdr, fb):
                xs.append(p[0]); ys.append(p[1]); zs.append(p[2])
        print(f"\r[calib] 수집 중… {len(xs)}점  (남은 {secs-(time.time()-start):4.1f}s)", end="")
    print()
    if len(xs) < 30:
        print(f"⚠ 점이 너무 적습니다({len(xs)}). 점군(1020) 출력/센서 각도 확인 후 재시도.")
        return None
    ax, ay, az = np.array(xs), np.array(ys), np.array(zs)
    x_min, x_max = np.percentile(ax, [3, 97])
    y_min, y_max = np.percentile(ay, [3, 97])
    z_cent_raw = float(np.median(az))
    z_offset = round(args.bed_surface - z_cent_raw, 3)   # 누운 몸 중심 → bed_surface 높이로
    roi = {
        "x_min": round(float(x_min), 3), "x_max": round(float(x_max), 3),
        "y_min": round(float(y_min), 3), "y_max": round(float(y_max), 3),
        "z_offset": z_offset, "calib_z_cent_raw": round(z_cent_raw, 3),
        "calib_points": len(xs), "ts": int(time.time() * 1000),
    }
    with open(args.roi, "w") as f:
        json.dump(roi, f, ensure_ascii=False, indent=2)
    w, l = x_max - x_min, y_max - y_min
    print(f"[calib] 침대 ROI 저장 → {args.roi}")
    print(f"        x:[{roi['x_min']}, {roi['x_max']}]  y:[{roi['y_min']}, {roi['y_max']}]  (폭 {w:.2f}m × 길이 {l:.2f}m)")
    print(f"        z_offset(자동)={z_offset}  ← 누운 몸 중심을 {args.bed_surface}m 로 맞춤")
    if w < 0.4 or l < 0.4:
        print("⚠ ROI가 너무 작습니다 — 침대 전체가 안 잡혔을 수 있어요(각도/가림 확인).")
    return roi


# ── 메인 루프 ──────────────────────────────────────────────────────────
def run(args):
    if serial is None:
        raise RuntimeError("pyserial 미설치: pip install pyserial")
    if args.cfg:
        if not args.cli:
            raise RuntimeError("--cfg 를 주려면 --cli 포트도 필요")
        send_config(args.cli, args.cfg)

    data = serial.Serial(args.data, 921600, timeout=0.1)

    if args.calibrate:
        try:
            calibrate(data, args)
        finally:
            data.close()
        return

    roi = load_roi(args.roi)
    z_offset = args.z_offset
    if roi:
        if args.z_offset == 0.0 and "z_offset" in roi:
            z_offset = roi["z_offset"]
        print(f"[probe] 침대 ROI 로드: x:[{roi['x_min']},{roi['x_max']}] y:[{roi['y_min']},{roi['y_max']}] z_offset={z_offset}")
    else:
        print("[probe] 침대 ROI 없음 — 위치판정 생략(자세만). 먼저 --calibrate 권장")

    th = dict(DEFAULT_TH)
    for k in th:
        v = getattr(args, k, None)
        if v is not None:
            th[k] = v

    buf = b""
    window = deque()
    last_print = 0.0
    frames_seen = 0
    pc_frames = 0
    pc_checked = False
    logf = None if args.no_log else open(args.log, "a")

    print(f"[probe] window={args.window}s label={args.label or '(예측)'} log={'off' if args.no_log else args.log}")
    print(f"[probe] 임계(추정): {th}  ※ 값 보고 조정")
    print("[probe] 시작 — 자세를 몇 초씩 유지하며 값이 갈리는지 보세요\n")

    try:
        while True:
            chunk = data.read(4096)
            now = time.time()
            if chunk:
                buf += chunk
                frames, buf = iter_frames(buf)
                for hdr, fb in frames:
                    frames_seen += 1
                    pts, used_pc = frame_points(hdr, fb)
                    if used_pc:
                        pc_frames += 1
                    window.append((now, pts))

            while window and now - window[0][0] > args.window:
                window.popleft()

            if not pc_checked and frames_seen >= 40:
                pc_checked = True
                if pc_frames == 0:
                    print("⚠ 점군(TLV 1020)이 안 옵니다 → track 중심점만 사용(자세분리 약함). .cfg 의 point cloud 출력을 켜세요.\n")
                else:
                    print(f"✓ 점군 수신 확인 ({pc_frames}/{frames_seen} 프레임)\n")

            if now - last_print >= 0.5:
                last_print = now
                allpts = [p for _, ps in window for p in ps]
                feat = compute_features(allpts, z_offset=z_offset)
                label_pred = classify(feat, th)
                label = args.label or label_pred
                src = "manual" if args.label else "rule"

                loc, zone, side, cx, cy = "", None, None, None, None
                if feat and roi:
                    cx = float(np.median([p[0] for p in allpts]))
                    cy = float(np.median([p[1] for p in allpts]))
                    zone, side = locate(cx, cy, roi, args.edge_margin)
                    loc = f" | pos=({cx:+.2f},{cy:+.2f}) {zone} {side}"

                if feat:
                    print(f"[{time.strftime('%H:%M:%S')}] label={label:<10} posture={label_pred:<10} "
                          f"N={feat['n']:<4} z_max={feat['z_max']:<6} z_spread={feat['z_spread']:<6} "
                          f"z_cent={feat['z_cent']:<6} pc={'1020' if pc_frames else 'trk'}{loc}")
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] (타겟 없음)")

                if logf and feat:
                    rec = {"ts": int(now * 1000), "label": label, "label_source": src,
                           "posture_pred": label_pred, "feat": feat}
                    if roi:
                        rec["pos"] = [round(cx, 3), round(cy, 3)]
                        rec["zone"] = zone
                        rec["side"] = side
                    if not args.no_raw:
                        rec["points"] = [[round(v, 3) for v in p] for p in allpts]
                    logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    logf.flush()
    except KeyboardInterrupt:
        print("\n[probe] 종료")
    finally:
        data.close()
        if logf:
            logf.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="DATA 포트 (예: /dev/ttyACM1, COM3)")
    ap.add_argument("--cli", default=None, help="CLI 포트 (--cfg 보낼 때만)")
    ap.add_argument("--cfg", default=None, help="전송할 .cfg (생략 시 이미 구동 중이라 가정)")
    ap.add_argument("--calibrate", action="store_true", help="침대 ROI 캘리브레이션 (누운 채 캡처)")
    ap.add_argument("--calib-secs", type=float, default=10.0, dest="calib_secs", help="캘리브레이션 캡처 시간(초)")
    ap.add_argument("--roi", default="bed_roi.json", help="침대 ROI 파일 (저장/로드)")
    ap.add_argument("--bed-surface", type=float, default=0.5, dest="bed_surface", help="누운 몸 중심의 바닥기준 높이(m) — z_offset 자동추정용")
    ap.add_argument("--edge-margin", type=float, default=0.25, dest="edge_margin", help="가장자리(전조) 밴드 폭(m)")
    ap.add_argument("--label", default=None, help="이 세션 자세 라벨 (lie/sit/stand/edge/exit…). 생략 시 규칙 예측을 약라벨로")
    ap.add_argument("--window", type=float, default=1.0, help="점군 누적 창(초) — 정지자세 안정화 (기본 1.0)")
    ap.add_argument("--z-offset", type=float, default=0.0, dest="z_offset", help="센서 높이 보정(m). ROI에 저장된 값이 있으면 자동 사용")
    ap.add_argument("--log", default="posture_dataset.jsonl", help="데이터셋 파일 (append)")
    ap.add_argument("--no-log", action="store_true", help="로그 안 쌓고 판정만")
    ap.add_argument("--no-raw", action="store_true", help="원시 점군 없이 특징만 로그(가벼움)")
    ap.add_argument("--stand-zmax", type=float, dest="stand_zmax")
    ap.add_argument("--stand-spread", type=float, dest="stand_spread")
    ap.add_argument("--lie-zmax", type=float, dest="lie_zmax")
    ap.add_argument("--lie-spread", type=float, dest="lie_spread")
    run(ap.parse_args())
