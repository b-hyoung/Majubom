"""
CSI raw 로그 → 진폭 히트맵 + 시계열 + 기본 통계
사용: python parse_csi.py csi_log.csv
"""
import sys
import re
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

LOG_PATH = sys.argv[1] if len(sys.argv) > 1 else "csi_log.csv"

records = []
with open(LOG_PATH, "r", errors="ignore") as f:
    for line in f:
        if "CSI_DATA" not in line:
            continue
        line = line[line.index("CSI_DATA"):]
        m = re.search(r'"\[(.*?)\]"', line)
        if not m:
            continue
        try:
            arr = [int(x) for x in m.group(1).split(",")]
        except ValueError:
            continue
        if len(arr) < 64:
            continue
        parts = line.split(",", 24)
        try:
            seq = int(parts[1])
            rssi = int(parts[3])
            ts = int(parts[18])
        except (ValueError, IndexError):
            continue
        records.append({"seq": seq, "rssi": rssi, "ts": ts, "csi": arr})

if not records:
    print("X 유효한 CSI 레코드 없음. CSV 파일 확인 필요.")
    sys.exit(1)

df = pd.DataFrame(records)
csi_arrays = np.array(df["csi"].tolist())
I = csi_arrays[:, 0::2]
Q = csi_arrays[:, 1::2]
amp = np.sqrt(I.astype(float) ** 2 + Q.astype(float) ** 2)

ts = df["ts"].values
dt_us = np.diff(ts)
dt_us = dt_us[(dt_us > 0) & (dt_us < 1_000_000)]
fps = 1_000_000 / np.median(dt_us) if len(dt_us) else 0

print("=" * 50)
print(f"패킷 수            : {len(df)}")
print(f"CSI subcarrier 수  : {amp.shape[1]}")
print(f"RSSI (평균/min/max): {df.rssi.mean():.1f} / {df.rssi.min()} / {df.rssi.max()} dBm")
print(f"패킷 간격 중앙값   : {np.median(dt_us)/1000:.1f} ms")
print(f"실측 샘플레이트     : {fps:.1f} Hz")
print(f"전체 캡처 시간     : {(ts[-1]-ts[0])/1_000_000:.1f} s")
print("=" * 50)

plt.figure(figsize=(14, 6))
plt.imshow(amp.T, aspect="auto", cmap="viridis", origin="lower")
plt.xlabel("Packet index (time →)")
plt.ylabel("Subcarrier index")
plt.title(f"CSI Amplitude Heatmap  ({len(df)} packets, {amp.shape[1]} subcarriers, ~{fps:.0f} Hz)")
plt.colorbar(label="Amplitude")
plt.tight_layout()
plt.savefig("csi_heatmap.png", dpi=120)

plt.figure(figsize=(14, 4))
plt.plot(amp.mean(axis=1), linewidth=0.7)
plt.xlabel("Packet index")
plt.ylabel("Mean amplitude")
plt.title("Mean amplitude over time (모든 subcarrier 평균)")
plt.tight_layout()
plt.savefig("csi_timeseries.png", dpi=120)

print("\n저장 완료:")
print("  csi_heatmap.png    — subcarrier × 시간 히트맵")
print("  csi_timeseries.png — 시계열 (호흡/움직임 패턴 보이는 곳)")
