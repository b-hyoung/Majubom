"""
CSI 측정 기록기 — 윈도우 버전
=====================================

사용:
  python record_csi_win.py                       # 자동 포트 검출 + 기본값
  python record_csi_win.py COM3                  # 포트 지정
  python record_csi_win.py COM3 csi_test.csv 90  # 포트/파일/시간 모두 지정

기본값:
  - 포트  : 자동 검출 (Silicon Labs CP210x, CH340, USB Serial 우선)
  - 파일  : csi_test_YYYYMMDD_HHMMSS.csv
  - 시간  : 90초

측정 중:
  - 완전 정지 (누움 자세)
  - 5초마다 진행률 + CSI 패킷 수 + Hz 출력
  - 100Hz 이상 나오면 정상
"""
import sys, time, datetime, re
from pathlib import Path

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    print("ERROR: pyserial 패키지 없음")
    print("설치: pip install pyserial")
    sys.exit(1)


BAUD = 921600


def detect_port():
    """ESP32 자주 쓰는 USB-Serial 칩 우선 검출."""
    keys = ["CP210", "Silicon Labs", "CH340", "CH9102", "USB Serial", "USB-SERIAL"]
    ports = list(list_ports.comports())
    if not ports:
        return None
    # 우선순위 칩 매칭
    for p in ports:
        desc = (p.description or "") + " " + (p.manufacturer or "")
        if any(k in desc for k in keys):
            return p.device
    # 못 찾으면 첫 번째
    return ports[0].device


def list_all_ports():
    ports = list(list_ports.comports())
    if not ports:
        print("  (포트 없음 — 보드가 USB에 꽂혀있는지 확인)")
        return
    for p in ports:
        print(f"  {p.device}  |  {p.description}  |  {p.manufacturer or '-'}")


# ──────────────────────────────────────────────
# 인자 파싱
# ──────────────────────────────────────────────
args = sys.argv[1:]
port = None
out_file = None
duration = None

for a in args:
    if re.match(r"^COM\d+$", a, re.I) or a.startswith("/dev/"):
        port = a.upper() if a.upper().startswith("COM") else a
    elif a.isdigit():
        duration = int(a)
    elif a.endswith(".csv"):
        out_file = a
    else:
        print(f"WARNING: 알 수 없는 인자 무시: {a}")

if port is None:
    print("🔍 시리얼 포트 자동 검출 중...")
    print("\n현재 연결된 포트 목록:")
    list_all_ports()
    port = detect_port()
    if port is None:
        print("\nERROR: 시리얼 포트를 찾을 수 없음")
        print("→ ESP32 보드가 USB에 꽂혀있는지 확인")
        print("→ 또는 직접 지정: python record_csi_win.py COM3")
        sys.exit(1)
    print(f"\n→ 자동 선택: {port}")

if out_file is None:
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = f"csi_test_{now}.csv"

if duration is None:
    duration = 90

# ──────────────────────────────────────────────
# 측정
# ──────────────────────────────────────────────
print()
print("=" * 60)
print(f"  포트     : {port}")
print(f"  baud     : {BAUD}")
print(f"  출력     : {out_file}")
print(f"  측정시간 : {duration}초")
print("=" * 60)

try:
    s = serial.Serial(port, BAUD, timeout=0.1)
except serial.SerialException as e:
    print(f"\nERROR: 포트 열기 실패 — {e}")
    print("  → 다른 프로그램이 포트 점유 중일 수 있음 (시리얼 모니터 닫기)")
    print("  → 또는 포트 이름 확인")
    sys.exit(1)

print(f"\n✅ 포트 열림. 3초 후 측정 시작...")
print("⚠️  지금부터 완전 정지! (누움 자세)")
for i in [3, 2, 1]:
    print(f"  {i}...")
    time.sleep(1)
print(f"\n🎬 측정 시작!\n")

t0 = time.time()
csi_count = 0
last_print = t0

with open(out_file, "wb") as f:
    while time.time() - t0 < duration:
        try:
            chunk = s.read(32768)
        except serial.SerialException as e:
            print(f"\nERROR: 읽기 실패 — {e}")
            break
        if chunk:
            f.write(chunk)
            csi_count += chunk.count(b"CSI_DATA")
        now = time.time()
        if now - last_print >= 5:
            el = now - t0
            hz = csi_count / el if el > 0 else 0
            print(f"  {el:>3.0f}s / {duration}s  |  CSI {csi_count:>5}개  |  {hz:>5.1f} Hz")
            last_print = now

s.close()
el = time.time() - t0
hz = csi_count / el if el > 0 else 0

print()
print("=" * 60)
print(f"✅ 완료: {out_file}")
print(f"   파일 크기 : {Path(out_file).stat().st_size / 1024:.1f} KB")
print(f"   CSI 패킷  : {csi_count}개")
print(f"   평균 Hz   : {hz:.1f}")

if hz < 50:
    print(f"\n⚠️  Hz가 낮음 (50Hz 미만). 가능한 원인:")
    print(f"  - TX(softAP) 보드 전원 X 또는 부팅 실패")
    print(f"  - RX 보드가 softAP에 연결 안 됨")
    print(f"  - 두 보드 거리 너무 멈")
elif hz < 100:
    print(f"\n⚠️  Hz 적정선(~100Hz)보다 낮음. 가능하면 환경 점검.")
else:
    print(f"\n🎯 Hz 정상. 분석 가능.")

print("=" * 60)
print(f"\n📊 분석: python vital_csi3.py {out_file}")
