"""침대 가운데 레이더 좌표를 재서 mmWave/bed_center.json에 저장한다(2026-10-31 천장 이전).

사람 한 명이 침대 가운데에 누워(또는 걸터앉아) 가만히 있는 동안 실행한다. 다른 사람은 레이더 시야 밖에 있는다.
서버(:5002)와 레이더 리더(send_mmw.py)가 돌고 있어야 한다. 저장하면 send_mmw·mmw_server·tof_server가 2초 안에 새 값을 쓴다.

    .venv/bin/python tools/set_bed_center.py            # 10초 동안 재고 저장
    .venv/bin/python tools/set_bed_center.py --xy 0.1,0.3   # 손으로 지정
    .venv/bin/python tools/set_bed_center.py --clear    # 지우기(침대 위치 규칙 끔)
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
from bed_position import PATH  # noqa: E402


def measure(seconds, url):
    samples = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                d = json.load(r)
            fresh = time.time() * 1000 - (d.get("ts") or 0) <= 2000
            people = [t for t in d.get("targets") or [] if t.get("role") not in ("shadow", "dup")]
            if fresh and len(people) == 1:
                t = people[0]
                samples.append((t["x"], t["y"], t.get("z")))
        except Exception as exc:
            print(f"[bed] 읽기 실패: {exc}")
        time.sleep(0.2)
    return samples


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--xy", help="손으로 지정 'x,y'(m)")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:5002/mmw/live")
    args = ap.parse_args()
    if args.clear:
        if os.path.exists(PATH):
            os.remove(PATH)
        print("[bed] 침대 위치를 지웠습니다(침대 위치 규칙 꺼짐).")
        return
    if args.xy:
        x, y = (float(v) for v in args.xy.split(","))
        note, n = "수동 지정", 0
    else:
        print(f"[bed] {args.seconds:.0f}초 동안 잽니다. 한 사람만 침대 가운데에 가만히 있어 주세요.")
        samples = measure(args.seconds, args.url)
        need = max(5, int(args.seconds / 0.2 * 0.5))
        if len(samples) < need:
            sys.exit(f"[bed] 사람 한 명이 잡힌 프레임이 {len(samples)}개뿐입니다(최소 {need}). 레이더·인원을 확인하세요.")
        x = statistics.median(s[0] for s in samples)
        y = statistics.median(s[1] for s in samples)
        spread = max(((s[0] - x) ** 2 + (s[1] - y) ** 2) ** 0.5 for s in samples)
        zs = [s[2] for s in samples if isinstance(s[2], (int, float))]
        note = f"측정 {len(samples)}프레임, 중앙값에서 최대 {spread:.2f}m, 몸 높이 중앙값 {statistics.median(zs):.2f}m" if zs else f"측정 {len(samples)}프레임"
        n = len(samples)
        if spread > 0.6:
            print(f"[bed] 경고: 위치가 {spread:.2f}m까지 흔들렸습니다. 가만히 있었는지 확인하세요.")
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump({"x": round(x, 2), "y": round(y, 2), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "frames": n, "note": note}, fh, ensure_ascii=False, indent=1)
    print(f"[bed] 침대 가운데 ({x:.2f}, {y:.2f}) 저장 → {PATH} ({note})")


if __name__ == "__main__":
    main()
