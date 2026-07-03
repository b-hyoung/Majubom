# ToF 자세 학습용 raw 데이터 캡처
# 라즈베리파이 /tof/latest 를 폴링해 새 프레임(received_at 기준)만 라벨 붙여 저장.
# 사용: python capture_dataset.py [label] [count]
#   기본: label="lying", count=4000
# 출력: dataset/tof_<label>_<timestamp>.jsonl  (한 줄 = 한 프레임)
#        + dataset/tof_<label>_<timestamp>.csv  (d0..d15,t0..t15 펼침 — DB/판다스용)
import urllib.request, json, time, os, sys

RPI    = "http://192.168.6.10:5001/tof/latest"
LABEL  = sys.argv[1] if len(sys.argv) > 1 else "lying"
TARGET = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
ZONES  = 16

HERE    = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "dataset")
os.makedirs(OUT_DIR, exist_ok=True)
ts   = time.strftime("%Y%m%d_%H%M%S")
base = os.path.join(OUT_DIR, f"tof_{LABEL}_{ts}")
jsonl_path, csv_path = base + ".jsonl", base + ".csv"

def pad(a, n):
    a = list(a or [])
    return (a + [-1] * n)[:n]

last  = {"tof1": None, "tof2": None}
count = 0
print(f"[capture] label={LABEL} target={TARGET} -> {jsonl_path}", flush=True)

with open(jsonl_path, "w", encoding="utf-8") as fj, open(csv_path, "w", encoding="utf-8") as fc:
    # CSV 헤더
    dcols = ",".join(f"d{i}" for i in range(ZONES))
    tcols = ",".join(f"t{i}" for i in range(ZONES))
    fc.write(f"timestamp,sensor,resolution,label,{dcols},{tcols}\n")

    while count < TARGET:
        try:
            with urllib.request.urlopen(RPI, timeout=3) as r:
                obj = json.loads(r.read())
        except Exception:
            time.sleep(0.1); continue

        for sid in ("tof1", "tof2"):
            s = obj.get(sid)
            if not s or not s.get("received_at") or not s.get("distances_mm"):
                continue
            if s["received_at"] == last[sid]:
                continue                      # 새 프레임만
            last[sid] = s["received_at"]

            d = pad(s.get("distances_mm"), ZONES)
            t = pad(s.get("targets"), ZONES)
            row = {"t": s["received_at"], "sensor": sid,
                   "resolution": s.get("resolution"),
                   "distances_mm": d, "targets": t, "label": LABEL}
            fj.write(json.dumps(row, ensure_ascii=False) + "\n"); fj.flush()
            fc.write(f'{s["received_at"]},{sid},{s.get("resolution")},{LABEL},'
                     + ",".join(map(str, d)) + "," + ",".join(map(str, t)) + "\n")
            fc.flush()
            count += 1
            if count % 200 == 0:
                print(f"[capture] {count}/{TARGET}", flush=True)

        time.sleep(0.04)

print(f"[capture] DONE {count} frames", flush=True)
print(f"[capture] JSONL: {jsonl_path}", flush=True)
print(f"[capture] CSV  : {csv_path}", flush=True)
