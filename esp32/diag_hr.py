"""진단: 왜 CSI가 85bpm에 고정되나. 추측 금지, 증거 수집용."""
import sys, re
import numpy as np
from scipy.signal import butter, filtfilt, detrend, welch, find_peaks
from sklearn.decomposition import PCA

FS = 100.0
GT = {"csi_watch1.csv": 79, "csi_watch2.csv": 73, "csi_watch3.csv": 71}  # 워치 정답 HR

def load(LOG):
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
    tu = np.arange(ts[0], ts[-1], 1e6/FS)
    std = amp.std(0); ampk = amp[:, std > std.max()*0.1]
    au = np.vstack([np.interp(tu, ts, ampk[:, c]) for c in range(ampk.shape[1])]).T
    au = detrend(au, axis=0)
    an = (au - au.mean(0)) / (au.std(0)+1e-9)
    return PCA(3).fit_transform(an)

def band(s, lo, hi):
    b,a = butter(4, [lo,hi], btype="band", fs=FS); return filtfilt(b,a,s)

def autocorr_period(sig, lo_bpm=48, hi_bpm=120):
    s = (sig-sig.mean())/(sig.std()+1e-9)
    ac = np.correlate(s,s,"full")[len(s)-1:]
    lo = int(FS*60/hi_bpm); hi = int(FS*60/lo_bpm)
    seg = ac[lo:hi]
    lag = lo + np.argmax(seg)
    return lag, ac

for f, gt in GT.items():
    print(f"\n{'='*60}\n{f}  (워치 정답 HR = {gt}bpm = {gt/60:.3f}Hz)")
    pcs = load(f)
    for pc in range(3):
        sig = band(pcs[:,pc], 0.9, 1.8)
        # 스펙트럼: 0.8~2.0Hz 안의 피크들
        fr, pw = welch(sig, fs=FS, nperseg=int(FS*20))
        m = (fr>=0.8)&(fr<=2.0)
        frm, pwm = fr[m], pw[m]
        order = np.argsort(pwm)[::-1][:3]
        peaks_bpm = [(frm[i]*60, pwm[i]/pwm.max()) for i in order]
        # 워치 정답 주파수 근처 파워 비중
        gt_hz = gt/60
        gi = np.argmin(np.abs(frm-gt_hz))
        gt_power = pwm[gi]/pwm.max()
        # 자기상관이 고르는 주기
        lag,_ = autocorr_period(sig)
        ac_bpm = 60*FS/lag
        tag = " <<< 자기상관 선택" if pc==0 else ""
        print(f"  PC{pc}: 스펙트럼 상위3 = " +
              ", ".join(f"{b:.0f}bpm({p:.2f})" for b,p in peaks_bpm) +
              f"  | 워치값({gt}bpm)자리 파워={gt_power:.2f}  | 자기상관={ac_bpm:.0f}bpm")
