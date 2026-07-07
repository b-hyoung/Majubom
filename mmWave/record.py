"""
record.py — mmWave 자세 데이터셋 반복 녹화기 (음성 안내 + 센서 자동 재시작)
====================================================================
자세를 지정하고 실행하면 여러 회차를 자동 녹화한다.
누워 있어 화면을 못 봐도 되게 macOS `say` 로 음성 안내한다.
시작 시 cfg 를 재전송해 센서 스트리밍을 보장하고, 회차가 0프레임이면
센서를 재시작해 1회 재시도한다. 회차 사이에 위치/방향을 조금씩 바꾼다(일반화).

저장: posture_dataset.jsonl 에 프레임별 {ts,label,subject,take,points} append.

사용:
  python3 record.py --label lie --subject parkhs --takes 10
  python3 record.py --label sit --takes 10
  python3 record.py --label stand --takes 8
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial
from posture_probe import extract_pointcloud
from send_mmw import iter_frames, send_config

DATA_PORT = "/dev/cu.usbserial-011D1AD61"
CLI_PORT = "/dev/cu.usbserial-011D1AD60"


def say(text):
    """한국어 음성 안내(Yuna). 없으면 기본 음성. 실패해도 무시."""
    try:
        r = subprocess.run(["say", "-v", "Yuna", text], timeout=8)
        if r.returncode != 0:
            subprocess.run(["say", text], timeout=8)
    except Exception:
        pass


def ensure_stream(cli, cfg):
    """cfg 재전송으로 센서 스트리밍 보장(sensorStop→config→sensorStart)."""
    try:
        send_config(cli, cfg)
        time.sleep(1.0)   # sensorStart 후 프레임 나올 때까지 안정화
        return True
    except Exception as e:
        print("cfg 전송 실패:", e)
        return False


def record_take(ser, secs, label, subject, take, logf):
    """secs 초간 점군 프레임 저장. (프레임수, 점수) 반환."""
    buf = b""
    t0 = time.time()
    nframes = npts = 0
    while time.time() - t0 < secs:
        buf += ser.read(8192)
        now = time.time()
        fr, buf = iter_frames(buf)
        for hdr, fb in fr:
            p = extract_pointcloud(hdr, fb)
            if not p:
                continue
            nframes += 1
            npts += len(p)
            logf.write(json.dumps({
                "ts": int(now * 1000), "label": label, "subject": subject,
                "take": take, "points": [[round(v, 3) for v in q] for q in p],
            }, ensure_ascii=False) + "\n")
    logf.flush()
    return nframes, npts


def run(args):
    print("[record] 센서 시작(cfg 전송)...")
    ensure_stream(args.cli, args.cfg)
    ser = serial.Serial(DATA_PORT, 921600, timeout=0.2)
    logf = open("posture_dataset.jsonl", "a")
    print(f"[record] label={args.label} subject={args.subject} takes={args.takes} "
          f"(각 {args.rec}초 녹화, {args.prep}초 준비)")
    say(f"{args.label} 자세, {args.takes}회 녹화를 시작합니다")
    try:
        for k in range(1, args.takes + 1):
            say(f"{k}회차. 자세를 잡으세요")
            time.sleep(args.prep)
            say("녹화 시작")
            nf, npts = record_take(ser, args.rec, args.label, args.subject, k, logf)
            if nf == 0:   # 센서 멎음 → 재시작 후 1회 재시도
                print(f"  회차 {k}: 0프레임 → 센서 재시작 후 재시도")
                say("센서를 재시작합니다. 자세 유지하세요")
                ser.close()
                ensure_stream(args.cli, args.cfg)
                ser = serial.Serial(DATA_PORT, 921600, timeout=0.2)
                say("녹화 시작")
                nf, npts = record_take(ser, args.rec, args.label, args.subject, k, logf)
            ppf = npts / nf if nf else 0
            flag = "   ⚠ 점 적음" if ppf < 10 else ""
            print(f"  회차 {k:2d}/{args.takes}: 프레임 {nf:3d}, 점 {npts:5d} (평균 {ppf:.1f}/프레임){flag}")
            say("완료. 자세를 조금 바꾸세요")
            time.sleep(args.gap)
    except KeyboardInterrupt:
        print("\n[record] 중단")
    finally:
        logf.close()
        ser.close()
        say("수집 끝")
        print("[record] 저장 → posture_dataset.jsonl")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="lie/sit/stand/exit …")
    ap.add_argument("--subject", default="unknown", help="피험자 식별자")
    ap.add_argument("--takes", type=int, default=10, help="회차 수")
    ap.add_argument("--rec", type=float, default=12.0, help="회차당 녹화 초")
    ap.add_argument("--prep", type=float, default=5.0, help="자세 잡을 준비 초")
    ap.add_argument("--gap", type=float, default=3.0, help="회차 사이 바꾸는 시간 초")
    ap.add_argument("--cli", default=CLI_PORT, help="CLI 포트 (cfg 전송용)")
    ap.add_argument("--cfg", default="AOP_v_C_legs_safe.cfg", help="센서 cfg")
    run(ap.parse_args())
