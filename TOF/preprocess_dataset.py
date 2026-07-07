# ToF 데이터셋 스파이크 제거 (존별 causal median) — 원본 보존, 필터본 별도 생성
# 사용: python preprocess_dataset.py [입력.jsonl] [window]
#   입력 생략 시 dataset/ 에서 가장 최근 *_ (단, *_filtered 제외) .jsonl 사용
#   window 기본 5 (현재 프레임 포함 지난 W프레임의 median = causal → 실시간과 동일 방식)
# 무효값(-1)은 "측정 실패"라 그대로 둠(스파이크 제거 대상 아님).
import json, os, sys, glob, statistics

ZONES = 64
HERE  = os.path.dirname(os.path.abspath(__file__))
DDIR  = os.path.join(HERE, "dataset")

def pick_input():
    cands = [p for p in glob.glob(os.path.join(DDIR, "*.jsonl"))
             if "_filtered" not in os.path.basename(p)]
    if not cands:
        print("입력 jsonl 없음"); sys.exit(1)
    return max(cands, key=os.path.getmtime)

INP = sys.argv[1] if len(sys.argv) > 1 else pick_input()
WIN = int(sys.argv[2]) if len(sys.argv) > 2 else 5

# 1) 로드 후 센서별로 시간순 정렬
rows = [json.loads(ln) for ln in open(INP, encoding="utf-8")]
by_sensor = {}
for r in rows:
    by_sensor.setdefault(r["sensor"], []).append(r)
for s in by_sensor:
    by_sensor[s].sort(key=lambda r: r["t"])

# 2) 존별 causal median (윈도우 내 유효값만; 없으면 -1 유지)
changed = 0; total_valid = 0
for s, frames in by_sensor.items():
    hist = [[] for _ in range(ZONES)]        # 존별 최근 유효값 버퍼
    for fr in frames:
        d = fr["distances_mm"]
        out = list(d)
        for z in range(ZONES):
            v = d[z]
            if v is not None and v > 0:
                hist[z].append(v)
                if len(hist[z]) > WIN:
                    hist[z].pop(0)
                med = int(round(statistics.median(hist[z])))
                total_valid += 1
                if med != v:
                    changed += 1
                out[z] = med
            # 무효(-1/None)는 그대로, 버퍼도 안 건드림
        fr["distances_mm"] = out

# 3) 원래 도착 순서(interleave) 복원해서 저장
merged = sorted(
    [fr for frames in by_sensor.values() for fr in frames],
    key=lambda r: r["t"])

base = os.path.splitext(INP)[0]
outj = base + f"_filtered_w{WIN}.jsonl"
outc = base + f"_filtered_w{WIN}.csv"
with open(outj, "w", encoding="utf-8") as fj, open(outc, "w", encoding="utf-8") as fc:
    fc.write("timestamp,sensor,resolution,label,"
             + ",".join(f"d{i}" for i in range(ZONES)) + ","
             + ",".join(f"t{i}" for i in range(ZONES)) + "\n")
    for r in merged:
        r["filter"] = f"median_causal_w{WIN}"
        fj.write(json.dumps(r, ensure_ascii=False) + "\n")
        d = (r["distances_mm"] + [-1]*ZONES)[:ZONES]
        t = (r.get("targets") or []); t = (t + [-1]*ZONES)[:ZONES]
        fc.write(f'{r["t"]},{r["sensor"]},{r.get("resolution")},{r["label"]},'
                 + ",".join(map(str, d)) + "," + ",".join(map(str, t)) + "\n")

pct = (changed/total_valid*100) if total_valid else 0
print(f"입력: {INP}")
print(f"프레임 {len(merged)} | 유효값 {total_valid} | median으로 보정된 값 {changed} ({pct:.1f}%)")
print(f"출력: {outj}")
print(f"      {outc}")
