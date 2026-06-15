"""
CSI 송신부 — 분석 결과를 서버(/csi)로 POST
=================================================
vital_csi3.analyze()로 HR/호흡/강도를 뽑아 JSON으로 패키징 후 전송.

CSI는 측정값(raw + quality)만 보냄.
baseline·z-score·alert_level은 서버가 DB에 쌓인 과거로 계산(공식은 서버연동_변경사항.md).

사용:
  python3 send_csi.py csi_rest.csv              # CSV 분석 → 1회 POST
  python3 send_csi.py csi_rest.csv --dry        # POST 안 함, JSON만 출력(검증용)
  python3 send_csi.py csi_rest.csv --bed bed_02 # 침대 지정
  python3 send_csi.py --loop                    # ESP32 실시간 90초 캡처→분석→POST 반복
  python3 send_csi.py --loop --port /dev/cu.usbmodemXXX

의존성 추가 없음(stdlib urllib). 분석엔 numpy/scipy/sklearn 필요(vital_csi3와 동일 env).
"""
import sys
import json
import tempfile
import time
from datetime import datetime, timezone
from urllib import request, error

from vital_csi3 import analyze

# ── 설정 ──────────────────────────────────────────────
SERVER_URL = "http://192.168.0.48:5001/csi"
BED_ID = "bed_01"
DURATION = 90          # 실시간 모드 측정 길이(초)
BAUD = 921600


# ── 서버로 가는 JSON 형태 (참고) ─────────────────────────────────────
# POST /csi 본문은 아래 형태. CSI는 측정값(raw + quality)만 보냄.
# baseline·z-score·alert_level 은 서버가 DB에 쌓인 과거로 계산함 (CSI가 안 보냄).
#
# {
#   "timestamp": "2026-06-15T14:30:00Z",  # 측정 시각 (UTC ISO8601)
#   "bed_id": "bed_03",                    # 침대 식별자
#   "sensor": "csi",                       # 센서 종류 (항상 "csi")
#   "raw": {
#     "hr_bpm": 78,                        # 심박수(분당)
#     "resp_rpm": 16,                      # 호흡수(분당)
#     "autocorr_strength": 0.41            # 신호 품질 0~1
#   },
#   "quality": {
#     "reliable": True,                    # 믿을 만한 측정인가
#     "samples_count": 8800,               # 받은 패킷 수
#     "duration_sec": 90                   # 측정 시간(초)
#   }
# }
# ───────────────────────────────────────────────────────────────────
def build_payload(metrics, bed_id):
    """analyze() 결과 → /csi JSON 본문 (raw + quality만).
    z-score/baseline/alert_level은 서버가 DB로 계산하므로 보내지 않음."""
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bed_id": bed_id,
        "sensor": "csi",
        "raw": {
            "hr_bpm": round(metrics["hr_bpm"]),
            "resp_rpm": round(metrics["resp_rpm"]),
            "autocorr_strength": round(metrics["autocorr_strength"], 2),
        },
        "quality": {
            "reliable": metrics["reliable"],
            "samples_count": metrics["samples_count"],
            "duration_sec": round(metrics["duration_sec"]),
        },
    }


def post(payload):
    """서버로 POST. 성공/실패를 한 줄로 보고."""
    data = json.dumps(payload).encode()
    req = request.Request(SERVER_URL, data=data,
                          headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=5) as resp:
            print(f"POST {SERVER_URL} → {resp.status} {resp.read().decode().strip()}")
    except error.URLError as e:
        print(f"POST 실패: {e}  (서버 켜져있나? {SERVER_URL})")


def send_csv(path, bed_id, dry):
    # use_full=True: 숨참기 구간 검출 안 함(그건 발표·검증용). 운영은 전체 신호 분석.
    payload = build_payload(analyze(path, use_full=True), bed_id)
    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        post(payload)


# ── 실시간 모드 ───────────────────────────────────────
def detect_port():
    import serial.tools.list_ports as list_ports
    keys = ["CP210", "Silicon Labs", "CH340", "CH9102", "USB Serial", "USB-SERIAL", "usbmodem"]
    ports = list(list_ports.comports())
    if not ports:
        return None
    for p in ports:
        desc = (p.description or "") + " " + (p.manufacturer or "") + " " + p.device
        if any(k in desc for k in keys):
            return p.device
    return ports[0].device


def capture(port, seconds):
    """시리얼에서 seconds초 동안 raw를 임시 CSV로 모아 경로 반환."""
    import serial
    s = serial.Serial(port, BAUD, timeout=0.1)
    tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False)
    t0 = time.time()
    with tmp:
        while time.time() - t0 < seconds:
            chunk = s.read(32768)
            if chunk:
                tmp.write(chunk)
    s.close()
    return tmp.name


def loop(port, bed_id, dry):
    if port is None:
        port = detect_port()
        if port is None:
            print("시리얼 포트를 찾을 수 없음 → --port 로 지정")
            sys.exit(1)
    print(f"실시간 모드: port={port}, {DURATION}초마다 측정→전송. Ctrl+C 종료.")
    try:
        while True:
            path = capture(port, DURATION)
            try:
                send_csv(path, bed_id, dry)
            except Exception as e:
                print(f"분석/전송 건너뜀: {e}")
    except KeyboardInterrupt:
        print("\n종료.")


# ── 진입점 ────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry" in args
    is_loop = "--loop" in args
    bed_id = BED_ID
    port = None
    csv_path = None
    i = 0
    rest = [a for a in args if a not in ("--dry", "--loop")]
    while i < len(rest):
        a = rest[i]
        if a == "--bed":
            bed_id = rest[i + 1]; i += 2
        elif a == "--port":
            port = rest[i + 1]; i += 2
        elif a.endswith(".csv"):
            csv_path = a; i += 1
        else:
            print(f"알 수 없는 인자 무시: {a}"); i += 1

    if is_loop:
        loop(port, bed_id, dry)
    elif csv_path:
        send_csv(csv_path, bed_id, dry)
    else:
        print(__doc__)
        sys.exit(1)
