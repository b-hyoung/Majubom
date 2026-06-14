"""
CSI → HR/HRV (위상 기반, v3)  ※ 2026-06-14 진단 결과 반영
v2와의 차이:
  - 진폭(amplitude) → 위상(phase, 패킷별 오프셋 제거) 사용  [핵심]
  - 숨참기 구간 자동검출(호흡 envelope 최소창) → 그 구간 우선 분석
  - HR = 위상 자기상관(애플워치 ±2bpm 검증된 방법)
  - HRV = 적응형 peak(μ+kσ, RRI≥400ms) + R-R median 이상치제거
  - 자기상관 강도로 신뢰도 플래그 (강도<0.3이면 심박 신뢰 불가 = 노이즈/에어컨 의심)
사용: python3 vital_csi3.py csi_hold4.csv [--full]
  --full : 숨참기 구간 검출 안 하고 전체 신호 사용
"""
import sys, re
import numpy as np
from scipy.signal import butter, filtfilt, detrend, welch, hilbert, find_peaks
from sklearn.decomposition import PCA

LOG = sys.argv[1] if len(sys.argv) > 1 else "csi_hold4.csv"
USE_FULL = "--full" in sys.argv
FS = 100.0


def parse(path):
    rec = []
    for line in open(path, errors="ignore"):
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
        if len(arr) < 128:
            continue
        p = line.split(",", 24)
        try:
            ts = int(p[18])
        except (ValueError, IndexError):
            continue
        rec.append((ts, arr[:128]))
    ts = np.array([r[0] for r in rec], float)
    raw = np.array([r[1] for r in rec])
    I = raw[:, 0::2].astype(float)
    Q = raw[:, 1::2].astype(float)
    return ts, I + 1j * Q          # (N, 64) 복소 CSI


def to_pca(feat, ts, tu, k=3):
    """서브캐리어 특징 → 균일리샘플 → detrend/정규화 → PCA"""
    std = feat.std(0)
    feat = feat[:, std > std.max() * 0.1]
    u = np.vstack([np.interp(tu, ts, feat[:, c]) for c in range(feat.shape[1])]).T
    u = detrend(u, axis=0)
    u = (u - u.mean(0)) / (u.std(0) + 1e-9)
    return PCA(min(k, u.shape[1])).fit_transform(u)


def band(s, lo, hi):
    b, a = butter(4, [lo, hi], btype="band", fs=FS)
    return filtfilt(b, a, s)


def hr_autocorr(sig, lo_bpm=45, hi_bpm=110):
    """자기상관 HR + 강도(0~1). 강도>0.3이면 진짜 주기성."""
    s = (sig - sig.mean()) / (sig.std() + 1e-9)
    ac = np.correlate(s, s, "full")[len(s) - 1:]
    lo = int(FS * 60 / hi_bpm)
    hi = int(FS * 60 / lo_bpm)
    lag = lo + np.argmax(ac[lo:hi])
    return 60 * FS / lag, ac[lag] / ac[0], lag


def hrv_adaptive(sig, T_lag):
    """자기상관 주기(T_lag) 기반 peak + RRI≥400ms + median 이상치제거 → SDNN/RMSSD.
    HRV는 모든 박동이 필요하므로 높은 임계 대신 '주기간격+약한 prominence'로 검출."""
    s = band(sig, 0.8, 2.0)
    s = (s - s.mean()) / (s.std() + 1e-9)
    dist = max(int(FS * 0.40), int(T_lag * 0.6))   # RRI≥400ms & 자기상관 주기의 60%
    pk, _ = find_peaks(s, distance=dist, prominence=0.3)
    if len(pk) < 5:
        return None
    rr = np.diff(pk) * (1000 / FS)
    med = np.median(rr)
    rr = rr[(rr > med * 0.7) & (rr < med * 1.3)]
    if len(rr) < 4:
        return None
    return {
        "beats": len(rr) + 1,
        "hr_peak": 60000 / np.mean(rr),
        "sdnn": np.std(rr, ddof=1),
        "rmssd": np.sqrt(np.mean(np.diff(rr) ** 2)),
        "kept": len(rr),
    }


def detect_hold(amp_pc0, tu):
    """호흡 envelope 최소 16초창 = 숨참기 구간. (없으면 None)"""
    env = np.abs(hilbert(band(amp_pc0, 0.1, 0.5)))
    win = int(FS * 16)
    if len(env) < win:
        return None
    best = None
    for s in range(0, len(env) - win, int(FS)):
        mn = env[s:s + win].mean()
        if best is None or mn < best[0]:
            best = (mn, s)
    # 최소창 호흡진폭이 전체평균의 30% 미만이면 진짜 숨참기로 간주
    if best[0] < env.mean() * 0.3:
        return best[1], best[1] + win
    return None


# ---- 실행 ----
ts, comp = parse(LOG)
tu = np.arange(ts[0], ts[-1], 1e6 / FS)
tsec = (tu - tu[0]) / 1e6
print(f"[{LOG}] {len(ts)}패킷, {tsec[-1]:.0f}초")

# 호흡 (진폭 PC0로 검출 — 호흡은 진폭에 강하게 나옴)
amp_pcs = to_pca(np.abs(comp), ts, tu)
fr, pw = welch(amp_pcs[:, 0], fs=FS, nperseg=int(FS * 20))
rb = (fr >= 0.1) & (fr <= 0.5)
print(f"호흡 : {fr[rb][np.argmax(pw[rb])] * 60:.1f} rpm")

# 분석 구간 결정
seg = slice(None)
hold = None if USE_FULL else detect_hold(amp_pcs[:, 0], tu)
if hold:
    seg = slice(hold[0], hold[1])
    print(f"숨참기 구간 검출: {tsec[hold[0]]:.0f}~{tsec[hold[1]]:.0f}초 → 이 구간 분석")
else:
    print("숨참기 구간 없음 → 전체 신호 분석 (호흡 간섭 가능)")

# 위상 신호 (패킷별 오프셋 제거 후 시간축 unwrap) → 핵심
ph = np.angle(comp) - np.angle(comp).mean(1, keepdims=True)
ph_pcs = to_pca(np.unwrap(ph, axis=0), ts, tu)

# HR: 위상 PC들 중 자기상관 강도 최강
best = None
for c in range(ph_pcs.shape[1]):
    s = band(ph_pcs[:, c][seg], 0.8, 2.0)
    bpm, strg, lag = hr_autocorr(s)
    if best is None or strg > best[1]:
        best = (bpm, strg, lag, c, s)
bpm, strg, lag, c, s = best

print(f"\n── 결과 (위상 PC{c}) ──")
reliable = strg > 0.30
print(f"HR (자기상관) : {bpm:.0f} bpm   [강도 {strg:.2f} {'✅신뢰' if reliable else '❌노이즈의심(에어컨/움직임)'}]")

hrv = hrv_adaptive(ph_pcs[:, c][seg], lag)
if hrv and reliable:
    sane = 10 < hrv["sdnn"] < 120 and 10 < hrv["rmssd"] < 120
    print(f"HR (peak)     : {hrv['hr_peak']:.0f} bpm")
    print(f"SDNN          : {hrv['sdnn']:.1f} ms")
    print(f"RMSSD         : {hrv['rmssd']:.1f} ms   ({hrv['beats']}박)")
    print("→ " + ("HRV 생리범위 ✅ (단 ms정밀도는 ground truth로 재검증 필요)"
                  if sane else "HRV 범위밖 — 신호 더 정밀화 필요"))
else:
    print("HRV          : 산출 보류 " +
          ("(자기상관 강도 낮음 = 심박 신호 불충분)" if not reliable else "(peak 부족)"))
