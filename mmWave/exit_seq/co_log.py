"""
co_log.py — ToF 라벨 + mmWave 점군 시퀀스 동시 로깅 (auto-label)
====================================================================
- mmWave: 이 맥 USB(DATA 포트)에서 점군 → 30프레임 FPS 고정 시퀀스
- ToF   : 파이(192.168.6.10:5001) /tof/latest 의 posture 를 라벨로 폴링
- 둘을 타임스탬프로 묶어 seq_dataset.jsonl 저장 → 시계열 모델 학습셋(사람 태깅 0)

핵심: ToF의 자세분류(누움/걸터앉음/앉음/이탈/empty)가 정답 라벨.
      나중에 "이탈 시각 T" 기준 T-N초를 '이탈 임박'으로 재라벨하면 예측용.

사용:
  python3 co_log.py                       # 기본값(파이 5001, 30프레임, 64점)
  python3 co_log.py --cfg AOP_v_C_legs_safe.cfg   # 센서 cfg 재전송(USB 불안정 시)
"""
import argparse
import json
import os
import sys
import time
from collections import deque

import numpy as np
import serial
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posture_probe import extract_pointcloud
from send_mmw import iter_frames, send_config
from record_seq import fps_idx, frame_fixed   # 같은 폴더 재사용

DATA_PORT = "/dev/cu.usbserial-011D1AD61"
CLI_PORT = "/dev/cu.usbserial-011D1AD60"
TOF_URL = "http://192.168.6.10:5001/tof/latest"


def poll_tof(url, timeout=1.5):
    """ToF posture 라벨 가져오기. 실패하면 (None, None, None)."""
    try:
        r = requests.get(url, timeout=timeout)
        j = r.json()
        return j.get("posture"), j.get("posture_conf"), j
    except Exception:
        return None, None, None


def run(args):
    if args.cfg:
        send_config(args.cli, args.cfg); time.sleep(1.0)

    ser = serial.Serial(args.data, 921600, timeout=0.2)
    logf = open(args.log, "a")
    buf = b""
    window = deque(maxlen=args.frames)
    since_save = 0
    n_seq = 0
    last_tof = 0.0
    tof_label, tof_conf = None, None
    last_print = 0.0
    frames_seen = 0

    print(f"[co_log] mmWave={args.data}  ToF={args.tof_url}")
    print(f"[co_log] frames={args.frames} npts={args.npts} stride={args.stride} → {args.log}")
    print("[co_log] 시작 — ToF posture 라벨이 자동으로 붙어요. Ctrl+C 종료\n")
    try:
        while True:
            now = time.time()
            # ToF 라벨 폴링 (0.3초마다)
            if now - last_tof >= 0.3:
                last_tof = now
                lb, cf, _ = poll_tof(args.tof_url)
                if lb is not None:
                    tof_label, tof_conf = lb, cf

            chunk = ser.read(8192)
            if chunk:
                buf += chunk
                frames, buf = iter_frames(buf)
                now = time.time()
                for hdr, fb in frames:
                    pts = extract_pointcloud(hdr, fb)
                    window.append(frame_fixed(pts, args.npts))
                    frames_seen += 1
                    since_save += 1
                    if len(window) == args.frames and since_save >= args.stride:
                        since_save = 0
                        n_seq += 1
                        logf.write(json.dumps({
                            "ts": int(now * 1000),
                            "tof_posture": tof_label, "tof_conf": tof_conf,
                            "frames": args.frames, "npts": args.npts,
                            "seq": list(window),
                        }) + "\n")
                        logf.flush()

            # 0.5초마다 현황 출력
            if now - last_print >= 0.5:
                last_print = now
                npf = len([p for p in (window[-1] if window else []) if any(p)])
                print(f"\r[{time.strftime('%H:%M:%S')}] ToF={str(tof_label):<10} "
                      f"conf={tof_conf}  mmWave점(마지막프레임)={npf:<3} "
                      f"시퀀스={n_seq}", end="")
    except KeyboardInterrupt:
        print(f"\n[co_log] 종료 — {n_seq}개 시퀀스 (ToF 라벨 포함) → {args.log}")
    finally:
        ser.close(); logf.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tof-url", default=TOF_URL, dest="tof_url")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--npts", type=int, default=64)
    ap.add_argument("--stride", type=int, default=10)
    ap.add_argument("--data", default=DATA_PORT)
    ap.add_argument("--cli", default=CLI_PORT)
    ap.add_argument("--cfg", default=None)
    ap.add_argument("--log", default="seq_dataset.jsonl")
    run(ap.parse_args())
