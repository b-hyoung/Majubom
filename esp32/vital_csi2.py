"""
CSI → HRV (robust 버전)
개선: 자기상관으로 평균 심박주기 추정 → 그 주기 ±30%에서만 peak → R-R median 이상치 제거
사용: python vital_csi2.py csi_rest.csv
"""
import sys, re
import numpy as np
from scipy.signal import butter, filtfilt, find_peaks, detrend, welch
from sklearn.decomposition import PCA

LOG = sys.argv[1] if len(sys.argv) > 1 else "csi_rest.csv"
FS = 100.0

# 파싱
recs = []
for line in open(LOG, errors="ignore"):
    if "CSI_DATA" not in line: continue
    line = line[line.index("CSI_DATA"):]
    m = re.search(r'"\[(.*?)\]"', line)
    if not m: continue
    try: arr = [int(x) for x in m.group(1).split(",")]
    except: continue
    if len(arr) < 64: continue
    p = line.split(",", 24)
    try: ts = int(p[18])
    except: continue
    recs.append((ts, arr))

ts = np.array([r[0] for r in recs], float)
csi = np.array([r[1] for r in recs])
amp = np.sqrt(csi[:, 0::2].astype(float)**2 + csi[:, 1::2].astype(float)**2)

# 균일 리샘플 + PCA
tu = np.arange(ts[0], ts[-1], 1e6 / FS)
std = amp.std(0); ampk = amp[:, std > std.max() * 0.1]
au = np.vstack([np.interp(tu, ts, ampk[:, c]) for c in range(ampk.shape[1])]).T
au = detrend(au, axis=0)
an = (au - au.mean(0)) / (au.std(0) + 1e-9)
pcs = PCA(3).fit_transform(an)


def band(s, lo, hi, fs=FS):
    b, a = butter(4, [lo, hi], btype="band", fs=fs); return filtfilt(b, a, s)


# 호흡
f, p = welch(pcs[:, 0], fs=FS, nperseg=int(FS * 30))
rb = (f >= 0.1) & (f <= 0.5)
rr_rpm = f[rb][np.argmax(p[rb])] * 60


def autocorr_period(sig, fs=FS, lo_bpm=48, hi_bpm=120):
    s = (sig - sig.mean()) / (sig.std() + 1e-9)
    ac = np.correlate(s, s, "full")[len(s) - 1:]
    lo = int(fs * 60 / hi_bpm); hi = int(fs * 60 / lo_bpm)
    seg = ac[lo:hi]
    if len(seg) == 0: return None
    lag = lo + np.argmax(seg)
    return lag  # 샘플 단위 평균 주기


def hrv_robust(sig):
    hr = band(sig, 0.9, 1.8); hr /= hr.std() + 1e-9
    T = autocorr_period(hr)
    if T is None: return None
    # 주기 T의 ±35% 안에서만 peak (가짜/놓침 억제)
    pk, _ = find_peaks(hr, distance=int(T * 0.65), prominence=0.3)
    if len(pk) < 8: return None
    rr = np.diff(pk) * (1000 / FS)
    # median 기반 이상치 제거 (놓침/가짜 R-R 제거)
    med = np.median(rr)
    rr = rr[(rr > med * 0.7) & (rr < med * 1.3)]
    if len(rr) < 5: return None
    return {
        "beats": len(rr) + 1, "hr": 60000 / np.mean(rr),
        "sdnn": np.std(rr, ddof=1),
        "rmssd": np.sqrt(np.mean(np.diff(rr) ** 2)),
        "T_bpm": 60 * FS / T, "kept": len(rr),
    }


best = None
for pc in range(3):
    r = hrv_robust(pcs[:, pc])
    if r and (best is None or r["kept"] > best["kept"]):
        best = r

print(f"호흡 RR : {rr_rpm*60:.0f} rpm" if rr_rpm < 1 else f"호흡 RR : {rr_rpm:.1f} rpm")
if best is None:
    print("HRV: 검출 실패")
else:
    ok = 10 < best["sdnn"] < 150 and 10 < best["rmssd"] < 150
    print(f"자기상관 HR: {best['T_bpm']:.0f} bpm  (주기 기반, 가장 신뢰도 높음)")
    print(f"HR    {best['hr']:.0f} bpm")
    print(f"SDNN  {best['sdnn']:.1f} ms")
    print(f"RMSSD {best['rmssd']:.1f} ms")
    print(f"유효비트 {best['beats']}")
    print("→ " + ("정상범위 ✅" if ok else "아직 범위 밖 — 추가 정밀화 필요"))
