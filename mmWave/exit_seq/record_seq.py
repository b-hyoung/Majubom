"""
record_seq.py — mmWave 점군 "시계열" 수집기 (PNHM/mPCT 입력 형식)
====================================================================
목적: 자세 전이(나가려는 행동)를 시계열 모델로 학습하기 위한 데이터 수집.
  - 논문 방식 그대로: 중심점(centroid) 아니라 **점군 전체**를 30프레임 윈도우로.
  - 프레임마다 점 개수가 들쭉날쭉(희소) → **FPS(Farthest-Point Sampling)로 고정 N개**.
  - 한 윈도우(30프레임 × N점) = 시계열 샘플 하나 → seq_dataset.jsonl 에 저장.

참고 근거(오늘 정리):
  - 30프레임 = PNHM(Sensors 2023, IWR6843ISK)이 최적이라고 보고한 값
  - FPS 고정샘플 = PNHM/mPCT 공통 전처리
  - ⚠️ mPCT 세부는 미검증(스니펫). 내일 원문 받아 대조.

라벨: --label 로 수동(lie/sit/edge/stand/exit/toss...). 나중에 ToF 상태로 auto-label 예정.

사용:
  python3 record_seq.py --label exit --frames 30 --npts 64
  (기존 mmWave/ 의 posture_probe·send_mmw 파싱을 그대로 재사용)
"""
import argparse
import json
import os
import sys
import time
from collections import deque

import numpy as np

# 상위 mmWave/ 의 파싱 재사용
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import serial
from posture_probe import extract_pointcloud
from send_mmw import iter_frames, send_config

DATA_PORT = "/dev/cu.usbserial-011D1AD61"
CLI_PORT = "/dev/cu.usbserial-011D1AD60"


def fps_idx(xyz, n):
    """Farthest-Point Sampling 인덱스. 점이 n보다 적으면 반복 채움."""
    m = len(xyz)
    if m == 0:
        return []
    if m <= n:
        return (list(range(m)) * (n // m + 1))[:n]
    sel = [0]
    dist = np.full(m, np.inf)
    for _ in range(1, n):
        d = np.linalg.norm(xyz - xyz[sel[-1]], axis=1)
        dist = np.minimum(dist, d)
        sel.append(int(np.argmax(dist)))
    return sel


def frame_fixed(points, npts):
    """한 프레임 점군 → 고정 npts개 [x,y,z,doppler]. 빈 프레임은 0으로."""
    if not points:
        return [[0.0, 0.0, 0.0, 0.0] for _ in range(npts)]
    a = np.array(points)                      # (m,5): x,y,z,doppler,snr
    idx = fps_idx(a[:, :3], npts)
    return [[round(float(a[i, 0]), 3), round(float(a[i, 1]), 3),
             round(float(a[i, 2]), 3), round(float(a[i, 3]), 3)] for i in idx]


def run(args):
    if args.cfg:
        send_config(args.cli, args.cfg); time.sleep(1.0)   # 센서 스트리밍 보장(USB 대비)

    ser = serial.Serial(args.data, 921600, timeout=0.2)
    logf = open(args.log, "a")
    buf = b""
    window = deque(maxlen=args.frames)   # 최근 frames개 프레임(각 고정 npts점)
    since_save = 0
    n_seq = 0
    frames_seen = 0

    print(f"[seq] label={args.label} frames={args.frames} npts={args.npts} stride={args.stride} → {args.log}")
    print("[seq] 수집 시작 (Ctrl+C 종료). 자세 전이를 반복 연기하세요.\n")
    try:
        while True:
            chunk = ser.read(8192)
            if not chunk:
                continue
            buf += chunk
            frames, buf = iter_frames(buf)
            now = time.time()
            for hdr, fb in frames:
                pts = extract_pointcloud(hdr, fb)
                window.append(frame_fixed(pts, args.npts))
                frames_seen += 1
                since_save += 1
                # 윈도우가 꽉 차고 stride 만큼 지나면 한 시퀀스 저장
                if len(window) == args.frames and since_save >= args.stride:
                    since_save = 0
                    n_seq += 1
                    logf.write(json.dumps({
                        "ts": int(now * 1000), "label": args.label,
                        "frames": args.frames, "npts": args.npts,
                        "seq": list(window),          # frames × npts × [x,y,z,dop]
                    }) + "\n")
                    logf.flush()
                    if n_seq % 5 == 0:
                        print(f"  시퀀스 {n_seq}개 저장 (프레임 {frames_seen})")
    except KeyboardInterrupt:
        print(f"\n[seq] 종료 — 총 {n_seq}개 시퀀스 저장 → {args.log}")
    finally:
        ser.close(); logf.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="시퀀스 라벨 (exit/toss/sit/stand/lie…)")
    ap.add_argument("--frames", type=int, default=30, help="윈도우 프레임 수 (PNHM 최적=30)")
    ap.add_argument("--npts", type=int, default=64, help="프레임당 고정 점 수 (FPS)")
    ap.add_argument("--stride", type=int, default=10, help="저장 간격(프레임) — 슬라이딩 윈도우")
    ap.add_argument("--data", default=DATA_PORT)
    ap.add_argument("--cli", default=CLI_PORT)
    ap.add_argument("--cfg", default=None, help="센서 cfg 재전송(USB 불안정 시)")
    ap.add_argument("--log", default="seq_dataset.jsonl")
    run(ap.parse_args())
