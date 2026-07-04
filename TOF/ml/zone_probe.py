#!/usr/bin/env python3
"""
존→방향 실측 매핑 도우미
========================
시작 시 '빈 침대' baseline 을 잡고, 이후 baseline과 크게 달라진 존(=손/물체 위치)을
실시간으로 표시한다. 침대의 특정 지점(머리-왼쪽 등)을 짚으면 어느 z 가 반응하는지
확인해서 z0~z15 ↔ 실제 방향 지도를 만든다.

사용:
  python zone_probe.py                 # 기본(192.168.6.10:5001), 임계 200mm
  python zone_probe.py --thr 250
반드시 시작 직후 몇 초는 '빈 침대'여야 baseline 이 잡힘.
"""
import urllib.request, json, time, argparse
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("--server", default="http://192.168.6.10:5001")
ap.add_argument("--thr", type=int, default=200)          # baseline과 이만큼(mm) 다르면 활성
ap.add_argument("--base-frames", type=int, default=10)
a = ap.parse_args()
URL = a.server.rstrip("/") + "/tof/latest"


def frame():
    return json.loads(urllib.request.urlopen(URL, timeout=3).read())


# ── baseline 캡처 (빈 침대) ──
print(f"[baseline] 빈 침대 {a.base_frames}프레임 캡처중… (침대 비워주세요)", flush=True)
acc = {"tof1": defaultdict(list), "tof2": defaultdict(list)}
seen = {"tof1": set(), "tof2": set()}
while len(seen["tof1"]) < a.base_frames or len(seen["tof2"]) < a.base_frames:
    o = frame()
    for sid in ("tof1", "tof2"):
        s = o.get(sid) or {}
        at, d = s.get("received_at"), s.get("distances_mm")
        if not at or at in seen[sid] or not d:
            continue
        seen[sid].add(at)
        for z, v in enumerate(d):
            if v and v > 0:
                acc[sid][z].append(v)
    time.sleep(0.05)
base = {sid: {z: (sum(vs) / len(vs)) for z, vs in acc[sid].items() if vs}
        for sid in ("tof1", "tof2")}
print("[baseline] 완료. 이제 침대 지점을 짚으세요 — 반응 존이 아래에 뜹니다.\n", flush=True)

# ── 실시간 활성 존 표시 ──
while True:
    try:
        o = frame()
    except Exception:
        time.sleep(0.2); continue
    line = []
    for sid in ("tof1", "tof2"):
        d = (o.get(sid) or {}).get("distances_mm") or []
        act = []
        for z, v in enumerate(d):
            b = base[sid].get(z)
            if v and v > 0 and b and abs(v - b) > a.thr:
                act.append(f"z{z}(r{z // 4}c{z % 4} {v}mm Δ{int(v - b):+})")
        if act:
            line.append(f"{sid}: " + "  ".join(act))
    if line:
        print(" | ".join(line), flush=True)
    time.sleep(0.35)
