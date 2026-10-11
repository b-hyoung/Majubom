# ToF 자세 학습용 세션 녹화 (2026-10 새 설치 위치용)
# 시작 후 DELAY초 대기(자세 잡는 시간) → SEC초 동안 /tof/latest 의 새 프레임만 저장.
# 사용: python record_posture.py <label> [--subject S01] [--cond 이불없음] [--rep 1]
#   label: empty, supine, side_left, side_right, sitting, edge_sit
# 출력: dataset_v2/tof_<subject>_<label>_<cond>_r<rep>_<timestamp>.jsonl
#   한 줄 = 한 센서의 한 프레임. capture_dataset.py 형식에 subject/cond/rep/session 을 더했다.
#   원본 거리 그대로 저장한다(침대 영역 마스킹은 학습 때 다시 계산할 수 있도록).
# 세션 목록: dataset_v2/sessions.csv 에 한 줄씩 추가.
import argparse, json, os, time, urllib.request

LABELS = ("empty", "supine", "side_left", "side_right", "sitting", "edge_sit")
ZONES = 64
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "dataset_v2")

ap = argparse.ArgumentParser()
ap.add_argument("label", choices=LABELS)
ap.add_argument("--subject", default="S01")
ap.add_argument("--cond", default="기본", help="조건 메모(예: 이불없음, 이불덮음, 가장자리, 발쪽)")
ap.add_argument("--rep", type=int, default=1, help="반복 회차")
ap.add_argument("--delay", type=float, default=5.0)
ap.add_argument("--sec", type=float, default=30.0)
ap.add_argument("--server", default=os.environ.get("TOF_SERVER", "http://localhost:5001/tof/latest"))
args = ap.parse_args()

os.makedirs(OUT_DIR, exist_ok=True)
ts = time.strftime("%Y%m%d_%H%M%S")
session = f"{args.subject}_{args.label}_{args.cond}_r{args.rep}_{ts}"
path = os.path.join(OUT_DIR, f"tof_{session}.jsonl")


def pad(a):
    return (list(a or []) + [-1] * ZONES)[:ZONES]


def fetch():
    with urllib.request.urlopen(args.server, timeout=2) as r:
        return json.loads(r.read())


print(f"[녹화] {args.label} · {args.subject} · {args.cond} · {args.rep}회차", flush=True)
for k in range(int(args.delay), 0, -1):
    print(f"  {k}초 뒤 시작...", flush=True)
    time.sleep(1)

last = {"tof1": None, "tof2": None}
count = {"tof1": 0, "tof2": 0}
start = time.time()
started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
print(f"[녹화] 시작 ({args.sec:.0f}초)", flush=True)
with open(path, "w", encoding="utf-8") as f:
    while time.time() - start < args.sec:
        try:
            obj = fetch()
        except Exception:
            time.sleep(0.05)
            continue
        for sid in ("tof1", "tof2"):
            s = obj.get(sid) or {}
            if not s.get("received_at") or not s.get("distances_mm") or s["received_at"] == last[sid]:
                continue
            last[sid] = s["received_at"]
            f.write(json.dumps({"t": s["received_at"], "sensor": sid, "resolution": s.get("resolution"),
                                "distances_mm": pad(s.get("distances_mm")), "targets": pad(s.get("targets")),
                                "label": args.label, "subject": args.subject, "cond": args.cond,
                                "rep": args.rep, "session": session}, ensure_ascii=False) + "\n")
            count[sid] += 1
        time.sleep(0.02)

elapsed = time.time() - start
hz = {sid: round(n / elapsed, 1) for sid, n in count.items()}
print(f"[녹화] 끝 — tof1 {count['tof1']}프레임({hz['tof1']}Hz), tof2 {count['tof2']}프레임({hz['tof2']}Hz)")
print(f"[녹화] 저장: {path}")
if min(count.values()) == 0:
    print("[경고] 한 센서의 프레임이 0개입니다. 보드 전원·Wi-Fi를 확인하고 다시 녹화하세요.")

idx = os.path.join(OUT_DIR, "sessions.csv")
new = not os.path.exists(idx)
with open(idx, "a", encoding="utf-8") as f:
    if new:
        f.write("session,started_at,label,subject,cond,rep,sec,tof1_frames,tof2_frames,file\n")
    f.write(f"{session},{started_at},{args.label},{args.subject},{args.cond},{args.rep},"
            f"{args.sec:.0f},{count['tof1']},{count['tof2']},{os.path.basename(path)}\n")
