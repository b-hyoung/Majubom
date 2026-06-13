"""
CSI raw 로그 → HRV(SDNN, RMSSD)만 추출
사용: python vital_csi.py csi_log.csv

전제(중요): 누워있는(정지) 환자 + 근거리(<1m) + 손/팔 움직임 없는 구간에서 측정해야
            신뢰할 수 있는 HRV가 나옴. 움직임 섞이면 SDNN/RMSSD가 비정상적으로 커짐.
"""
import sys
import re
import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, find_peaks, detrend
from sklearn.decomposition import PCA

LOG_PATH = sys.argv[1] if len(sys.argv) > 1 else "csi_log.csv"
FS = 50.0  # 리샘플 목표 Hz

# ---------- 파싱 ----------
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
            ts = int(parts[18])
        except (ValueError, IndexError):
            continue
        records.append({"ts": ts, "csi": arr})

if len(records) < 100:
    print("HRV: 데이터 부족"); sys.exit(1)

df = pd.DataFrame(records)
csi = np.array(df["csi"].tolist())
amp = np.sqrt(csi[:, 0::2].astype(float) ** 2 + csi[:, 1::2].astype(float) ** 2)
ts = df["ts"].values.astype(float)

# ---------- 균일 리샘플 + PCA ----------
t_uni = np.arange(ts[0], ts[-1], 1_000_000 / FS)
std = amp.std(axis=0)
amp_k = amp[:, std > std.max() * 0.1]
amp_uni = np.vstack([np.interp(t_uni, ts, amp_k[:, c]) for c in range(amp_k.shape[1])]).T
amp_uni = detrend(amp_uni, axis=0)
amp_norm = (amp_uni - amp_uni.mean(0)) / (amp_uni.std(0) + 1e-9)
pcs = PCA(n_components=3).fit_transform(amp_norm)

# ---------- 심박 대역에서 R-peak 가장 잘 잡히는 PC 선택 ----------
b, a = butter(4, [0.8, 2.0], btype="band", fs=FS)


def hrv_from(sig):
    s = filtfilt(b, a, sig)
    s = s / (np.std(s) + 1e-9)
    peaks, _ = find_peaks(s, distance=int(FS * 0.4), prominence=0.5)
    if len(peaks) < 6:
        return None
    rr = np.diff(peaks) * (1000.0 / FS)
    rr = rr[(rr > 300) & (rr < 1500)]  # 생리적 범위만
    if len(rr) < 4:
        return None
    return {
        "beats": len(rr) + 1,
        "hr": 60000 / np.mean(rr),
        "sdnn": np.std(rr, ddof=1),
        "rmssd": np.sqrt(np.mean(np.diff(rr) ** 2)),
    }


best = None
for i in range(pcs.shape[1]):
    r = hrv_from(pcs[:, i])
    if r and (best is None or r["beats"] > best["beats"]):
        best = r

# ---------- HRV만 출력 ----------
if best is None:
    print("HRV: 검출 실패 (정지·근거리 재측정 필요)")
else:
    plausible = 5 < best["sdnn"] < 200 and 5 < best["rmssd"] < 200
    print(f"HR    {best['hr']:.0f} bpm")
    print(f"SDNN  {best['sdnn']:.1f} ms")
    print(f"RMSSD {best['rmssd']:.1f} ms")
    print(f"beats {best['beats']}")
    if not plausible:
        print("주의: 생리적 범위 밖 → 움직임 혼입 의심, 정지 구간 재측정 권장")
