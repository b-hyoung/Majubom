"""
unified_reader.py — 보행(gait) + 자세/이탈(posture) 단일 리더 골격
====================================================================
왜 이 파일이 필요한가:
  - send_mmw.py 와 posture_probe.py 는 **둘 다 같은 DATA 포트**를 연다.
    UART는 한 프로세스만 열 수 있어 두 스크립트를 동시에 못 켠다.
  - 근데 같은 cfg(AOP_bed_2m7_d15.cfg)가 한 스트림에 TLV 1010(트랙)+1020(점군)을
    **둘 다** 내보낸다 → 한 프로세스가 프레임을 한 번 읽고 갈래만 나누면 동시 구동 가능.

구조:
  frame ─┬─ extract_targets(1010) → history → (6s) select_patient → gait → 서버 /mmw
         └─ extract_pointcloud(1020) → (1s) crop→classify → 자세/이탈

재사용(재구현 없음):
  send_mmw   : iter_frames, extract_targets, select_patient, send_config, 상수들
  posture_probe: extract_pointcloud, compute_features, classify, crop_to_roi,
                 load_roi, locate, DEFAULT_TH, STAND_HOLD

사용:
  python unified_reader.py --data COM6 --cli COM5 --roi mmWave/bed_roi.json
  python unified_reader.py --data COM6 --no-send        # 서버 없이 콘솔만
"""
import argparse
import time
from collections import defaultdict, deque

try:
    import serial
except ImportError:
    serial = None
try:
    import requests
except ImportError:
    requests = None

import send_mmw as G          # 보행 파이프라인
import posture_probe as P     # 자세 파이프라인

POSTURE_INTERVAL = 1.0        # 자세 판정 주기(초)
POSTURE_WINDOW = 1.0          # 점군 누적 창(초)
POSTURE_MIN_PTS = 10          # 크롭 후 이보다 적으면 빈 침대


def run(args):
    if serial is None:
        raise RuntimeError("pyserial 미설치")
    if args.cfg:
        G.send_config(args.cli, args.cfg)   # 스트리밍 보장(양쪽 TLV 다 나옴)

    data = serial.Serial(args.data, 921600, timeout=0.1)
    buf = b""

    # ── 보행(gait) 상태 ──
    history = defaultdict(lambda: deque(maxlen=2000))
    gait_window_start = time.time()

    # ── 자세(posture) 상태 ──
    roi = P.load_roi(args.roi) if args.roi else None
    z_offset = roi.get("z_offset", 0.0) if roi else 0.0
    th = dict(P.DEFAULT_TH)
    pc_window = deque()          # (t, points)
    last_posture = 0.0
    stand_until = 0.0

    send = (not args.no_send) and requests is not None
    print(f"[unified] {args.data} | gait {G.WINDOW_SEC}s + posture {POSTURE_INTERVAL}s "
          f"| roi={'on' if roi else 'off'} send={send}")

    try:
        while True:
            chunk = data.read(4096)
            now = time.time()
            if chunk:
                buf += chunk
                frames, buf = G.iter_frames(buf)
                for hdr, fb in frames:
                    # 갈래 1: 트랙(1010) → 보행 이력
                    for t in G.extract_targets(hdr, fb):
                        history[t["tid"]].append(
                            (now, t["posX"], t["posY"], t["posZ"], t["velX"], t["velY"]))
                    # 갈래 2: 점군(1020) → 자세 창
                    pts = P.extract_pointcloud(hdr, fb)
                    if pts:
                        pc_window.append((now, pts))

            # 오래된 점군 폐기
            while pc_window and now - pc_window[0][0] > POSTURE_WINDOW:
                pc_window.popleft()

            # ── 자세 판정 (1s 주기) ──
            if now - last_posture >= POSTURE_INTERVAL:
                last_posture = now
                stand_until = _do_posture(pc_window, roi, z_offset, th,
                                          args.crop_margin, stand_until, now)

            # ── 보행 지표 (6s 주기) ──
            if now - gait_window_start >= G.WINDOW_SEC:
                _prune_history(history, now)
                _do_gait(history, now, args.target, send)
                gait_window_start = now
    except KeyboardInterrupt:
        print("\n[unified] 종료")
    finally:
        data.close()


def _prune_history(history, now):
    """소속도 이력 유지: 오래된 샘플·죽은 track 정리 (send_mmw.run 과 동일)."""
    for tid in list(history.keys()):
        dq = history[tid]
        while dq and now - dq[0][0] > G.AFFINITY_HORIZON:
            dq.popleft()
        if not dq:
            del history[tid]


def _do_gait(history, now, target_id, send):
    raw, quality, presence = G.select_patient(history, now)
    tag = time.strftime('%H:%M:%S')
    if raw:
        print(f"[{tag}] GAIT raw={raw} lock={presence['patient_locked']} "
              f"walk={quality.get('walking')} n={presence['n_targets']}")
    else:
        print(f"[{tag}] GAIT 보류 presence={presence}")
    if send:
        payload = {"target_id": target_id, "timestamp": int(now * 1000),
                   "raw": raw or {"speed": 0.0, "speed_cv": 0.0, "sway": 0.0,
                                  "freeze_ratio": 0.0, "height_drop": 0.0},
                   "quality": quality, "presence": presence}
        try:
            requests.post(G.SERVER_URL, json=payload, timeout=3)
        except Exception as e:
            print(f"   -> /mmw POST 실패: {e}")


def _do_posture(pc_window, roi, z_offset, th, crop_margin, stand_until, now):
    """자세 판정 1회. 갱신된 stand_until 반환. (posture_probe.run 규칙과 동일)"""
    allpts = [p for _, ps in pc_window for p in ps]
    if roi:
        allpts = P.crop_to_roi(allpts, roi, crop_margin)
    feat = P.compute_features(allpts, z_offset=z_offset)
    if feat and feat["n"] < POSTURE_MIN_PTS:      # 빈 침대/모서리 잡음
        feat = None
    label = P.classify(feat, th)
    # 서기 latch
    if label == "stand(섬)":
        stand_until = now + P.STAND_HOLD
    elif now < stand_until and feat and feat["z_cent"] > th["lie_zcent"]:
        label = "stand(섬)"

    tag = time.strftime('%H:%M:%S')
    if feat is None:
        print(f"[{tag}] POSE (침대 비어있음/타겟없음)")
    else:
        loc = ""
        if roi:
            import numpy as np
            cx = float(np.median([p[0] for p in allpts]))
            cy = float(np.median([p[1] for p in allpts]))
            zone, side = P.locate(cx, cy, roi, 0.25)
            loc = f" pos=({cx:+.2f},{cy:+.2f}) {zone} {side}"
        print(f"[{tag}] POSE {label:<10} z_cent={feat['z_cent']:<6} "
              f"z_max={feat['z_max']:<6} n_hi={feat['n_hi']}{loc}")
    # TODO(연동): label/feat 를 서버 자세 엔드포인트로 POST, 또는
    #   점군 시퀀스를 이탈 LSTM(exit_seq)에 프레임 단위로 공급.
    # TODO(정밀화): presence['main_tid'] 의 트랙 위치로 점군을 연관시켜
    #   '침대 옆 밀착자'까지 배제 (ROI 크롭보다 정확). 좌표계 변환 필요.
    return stand_until


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="DATA 포트 (COM6 / /dev/ttyUSB1)")
    ap.add_argument("--cli", default=None, help="CLI 포트 (--cfg 보낼 때만)")
    ap.add_argument("--cfg", default=None, help="센서 cfg 재전송(스트리밍 보장)")
    ap.add_argument("--roi", default="mmWave/bed_roi.json", help="침대 ROI (자세 크롭용)")
    ap.add_argument("--crop-margin", type=float, default=0.15, dest="crop_margin")
    ap.add_argument("--target", default="room_01", help="서버 target_id")
    ap.add_argument("--no-send", action="store_true", help="서버 전송 없이 콘솔만")
    run(ap.parse_args())
