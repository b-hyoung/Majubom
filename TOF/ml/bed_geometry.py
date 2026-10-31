"""빈 침대 ToF 프레임으로 침대 높이·가로 폭을 추정한다(읽기 전용, 설정 파일을 바꾸지 않음).

원리: roi.hit_point(센서 높이·하향각·시야각 45°/8칸)로 각 존이 닿은 3D 점을 구한다.
매트리스 윗면 점은 거의 같은 높이(z)에 모이고, 침대 옆 바닥 점은 z≈0이다. 한 줄(row) 안에서
'매트리스 점 중 가장 바깥'과 '그 바깥 바닥 점' 사이를 침대 가장자리로 본다. 시야 안에 바닥이 안
보이는 쪽은 가장자리를 못 보므로 '최소 이만큼'(하한)으로만 보고한다.

사용:
  .venv/bin/python TOF/ml/bed_geometry.py TOF/dataset_v2/tof_없음_empty_*_20261012_*.jsonl
  .venv/bin/python TOF/ml/bed_geometry.py --live            # :5001/tof/latest를 10초 모아 계산(빈 침대일 때)
"""
import argparse
import glob
import json
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import roi  # noqa: E402

FLOOR_Z = 0.20      # 이보다 낮으면 바닥
BAND = 0.10         # 매트리스 높이 ± 이 범위면 매트리스 윗면


def median_frames(frames):
    out = []
    for z in range(64):
        v = [f[z] for f in frames if f[z] > 0]
        out.append(st.median(v) if len(v) > len(frames) * 0.5 else -1)
    return out


def analyse(sensor, dist):
    pts = {}
    for z, d in enumerate(dist):
        p = roi.hit_point(sensor, z, d)
        if p is not None:
            pts[z] = p
    centre = [p[2] for z, p in pts.items() if z % 8 in (3, 4) and p[2] > FLOOR_Z]
    if not centre:
        return None
    top = st.median(centre)
    edges = {"-": [], "+": []}     # 확인된 가장자리 y
    bounds = {"-": 0.0, "+": 0.0}  # 매트리스가 이어지는 가장 바깥 y(하한)
    for r in range(8):
        row = sorted((p[1], p[2]) for z, p in pts.items() if z // 8 == r)
        bed = [y for y, h in row if abs(h - top) <= BAND]
        if not bed:
            continue
        lo, hi = min(bed), max(bed)
        bounds["-"] = min(bounds["-"], lo)
        bounds["+"] = max(bounds["+"], hi)
        floor_lo = [y for y, h in row if h < FLOOR_Z and y < lo]
        floor_hi = [y for y, h in row if h < FLOOR_Z and y > hi]
        if floor_lo:
            edges["-"].append((lo + max(floor_lo)) / 2)
        if floor_hi:
            edges["+"].append((hi + min(floor_hi)) / 2)
    return {"top": top, "edges": edges, "bounds": bounds}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    frames = {"tof1": [], "tof2": []}
    if a.live:
        t0 = time.time()
        while time.time() - t0 < 10:
            d = json.load(urllib.request.urlopen("http://127.0.0.1:5001/tof/latest", timeout=3))
            for s in frames:
                if (d.get(s) or {}).get("distances_mm"):
                    frames[s].append(d[s]["distances_mm"])
            time.sleep(0.2)
    for f in [g for pat in a.files for g in glob.glob(pat)]:
        for line in open(f, encoding="utf-8"):
            d = json.loads(line)
            if d.get("sensor") in frames:
                frames[d["sensor"]].append(d["distances_mm"])
    sides = {"-": [], "+": []}
    lows = {"-": 0.0, "+": 0.0}
    for s, fr in frames.items():
        if not fr:
            continue
        r = analyse(s, median_frames(fr))
        if not r:
            print(f"{s}: 매트리스 면을 못 찾음({len(fr)}프레임)")
            continue
        print(f"{s}: {len(fr)}프레임 · 매트리스 윗면 높이 {r['top']:.2f}m · "
              f"가장자리 -쪽 {['%.2f' % e for e in r['edges']['-']] or '안 보임'} / +쪽 {['%.2f' % e for e in r['edges']['+']] or '안 보임'} · "
              f"매트리스 보이는 범위 {r['bounds']['-']:+.2f}~{r['bounds']['+']:+.2f}m")
        for k in sides:
            sides[k] += r["edges"][k]
            lows[k] = min(lows[k], r["bounds"][k]) if k == "-" else max(lows[k], r["bounds"][k])
    left = st.median(sides["-"]) if sides["-"] else None
    right = st.median(sides["+"]) if sides["+"] else None
    if left is not None and right is not None:
        print(f"→ 침대 폭 약 {right - left:.2f}m (가장자리 {left:+.2f} ~ {right:+.2f}m)")
    else:
        known = f"{left:+.2f}" if left is not None else f"{right:+.2f}" if right is not None else None
        lo = (lows["+"] - (left if left is not None else lows["-"])) if left is not None else \
             ((right if right is not None else lows["+"]) - lows["-"])
        print(f"→ 침대 폭 최소 {lo:.2f}m 이상 (확인된 가장자리: {known or '없음'}; 반대쪽 가장자리는 ToF 시야 밖)")
        if known is not None:
            print(f"  센서가 침대 가운데에 있다고 가정하면 폭 ≈ {2 * abs(float(known)):.2f}m")


if __name__ == "__main__":
    main()
