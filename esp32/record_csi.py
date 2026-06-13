"""
CSI 측정 기록기 (csi_recv_router용)
사용: python record_csi.py [출력파일.csv] [측정초]
예:  python record_csi.py csi_rest.csv 90

측정 중 완전 정지(누워서) 유지할 것. 끝나면 vital_csi.py로 분석.
"""
import sys, serial, time

PORT = "/dev/cu.usbmodem5B5E0761861"
BAUD = 921600
out = sys.argv[1] if len(sys.argv) > 1 else "csi_rest.csv"
dur = int(sys.argv[2]) if len(sys.argv) > 2 else 90

s = serial.Serial(PORT, BAUD, timeout=0.1)
print(f"포트 {PORT} 열림. {dur}초 측정 시작 — 지금부터 완전 정지!")
t0 = time.time()
csi = 0
with open(out, "wb") as f:
    last = t0
    while time.time() - t0 < dur:
        chunk = s.read(32768)
        if chunk:
            f.write(chunk)
            csi += chunk.count(b"CSI_DATA")
        now = time.time()
        if now - last >= 5:
            el = now - t0
            print(f"  {el:.0f}s | CSI {csi}개 | {csi/el:.0f} Hz")
            last = now
s.close()
el = time.time() - t0
print(f"완료: {out} | CSI {csi}개 | 평균 {csi/el:.0f} Hz")
print(f"분석: python vital_csi.py {out}")
