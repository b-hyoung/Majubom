"""카메라 측정 기록과 mmWave 뷰어 녹화를 같은 PC 시각으로 맞춰 본다(2026-11-08).

카메라: camera-pose 측정 폴더(control_events.jsonl, live_frames.jsonl). 프레임 시각은 perf_counter_ns이고,
        측정 시작·종료 요청에 perf_counter와 UTC가 함께 있어 그 차이로 실제 시각으로 바꾼다.
mmWave: 테스트 뷰어 녹화 폴더(frames.jsonl). 프레임마다 PC 시각(t, 초)이 있다.
두 프로그램이 같은 노트북에서 돌 때만 의미가 있다(같은 PC 시계).

사용법:
  python tools/cam_mmw_join.py <카메라 측정 폴더> <mmWave 녹화 폴더> [--marks 자세=HH:MM:SS.fff ...] [--day YYYY-MM-DD]
  예) --marks 걸터앉음=12:47:34.446 "침대 위 앉음=12:48:12.850" 섬=12:48:56.694 끝=12:49:52.369
"""
import argparse
import bisect
import collections
import json
from datetime import datetime
from pathlib import Path


def load_camera(folder):
    events = [json.loads(line) for line in open(folder / "control_events.jsonl", encoding="utf-8")]
    offsets = {}
    for e in events:
        r = e["request"]
        offsets.setdefault(e["clock_id"], []).append(int(r["requested_utc_ns"]) - int(r["requested_time_ns"]))
    frames = []
    for line in open(folder / "live_frames.jsonl", encoding="utf-8"):
        r = json.loads(line)
        pa = r.get("posture_assessment") or {}
        clock, received = pa.get("clock_id") or r.get("clock_id"), pa.get("received_ns") or r.get("received_ns")
        if clock in offsets and received:
            frames.append(((int(received) + offsets[clock][0]) / 1e9, clock[:8], (r.get("stable_posture") or {}).get("label")))
    return sorted(frames), offsets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", type=Path)
    ap.add_argument("mmwave", type=Path)
    ap.add_argument("--marks", nargs="*", default=[], help="자세=HH:MM:SS.fff, 마지막은 끝 시각")
    ap.add_argument("--day", default=None, help="표시 시각의 날짜(기본: mmWave 첫 프레임의 날짜)")
    args = ap.parse_args()

    cam, offsets = load_camera(args.camera)
    mm = [json.loads(line)["t"] for line in open(args.mmwave / "frames.jsonl", encoding="utf-8") if line.startswith('{"t"')]
    print("카메라 시계별 perf→UTC 차이(초):", {k[:8]: [round(o / 1e9, 4) for o in v] for k, v in offsets.items()})
    print("카메라 프레임 수(시계별):", dict(collections.Counter(c for _, c, _ in cam)))
    if not cam or not mm:
        raise SystemExit("맞출 프레임이 없습니다.")
    fmt = lambda t: datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]
    print(f"카메라 {fmt(cam[0][0])} ~ {fmt(cam[-1][0])} / mmWave {len(mm)}프레임 {fmt(mm[0])} ~ {fmt(mm[-1])}")

    gaps = []
    for t, _, _ in cam:
        i = bisect.bisect_left(mm, t)
        gaps.append(min(abs(mm[j] - t) for j in (i - 1, i) if 0 <= j < len(mm)))
    gaps.sort()
    print(f"카메라 프레임 → 가장 가까운 mmWave 프레임: 중앙 {gaps[len(gaps) // 2] * 1000:.0f}ms, "
          f"95% {gaps[int(.95 * len(gaps))] * 1000:.0f}ms, 150ms 안 {100 * sum(g <= .15 for g in gaps) / len(gaps):.0f}%")

    if args.marks:
        day = args.day or datetime.fromtimestamp(mm[0]).strftime("%Y-%m-%d")
        marks = [(m.split("=", 1)[0], datetime.strptime(f"{day} {m.split('=', 1)[1]}", "%Y-%m-%d %H:%M:%S.%f").timestamp()) for m in args.marks]
        print("자세 구간별 카메라 안정 판정(바꾸는 동작 3초 제외):")
        for (name, a), (_, b) in zip(marks, marks[1:]):
            for clock in sorted({c for _, c, _ in cam}):
                cnt = collections.Counter(label for t, c, label in cam if a + 3 <= t < b and c == clock)
                print(f"  {name} [{clock}] " + ", ".join(f"{k}:{v}" for k, v in cnt.most_common()))


if __name__ == "__main__":
    main()
