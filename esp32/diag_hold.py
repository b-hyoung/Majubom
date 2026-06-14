"""숨참기 구간 vs 호흡 구간: 심박이 깨끗이 잡히나"""
import re, numpy as np
from scipy.signal import butter, filtfilt, detrend, welch, find_peaks
from sklearn.decomposition import PCA
FS=100.0
rec=[]
for line in open("csi_hold.csv",errors="ignore"):
    if "CSI_DATA" not in line: continue
    line=line[line.index("CSI_DATA"):]
    m=re.search(r'"\[(.*?)\]"',line)
    if not m: continue
    try: arr=[int(x) for x in m.group(1).split(",")]
    except: continue
    if len(arr)<128: continue
    p=line.split(",",24)
    try: ts=int(p[18])
    except: continue
    rec.append((ts,arr[:128]))
ts=np.array([r[0] for r in rec],float)
raw=np.array([r[1] for r in rec])
amp=np.sqrt(raw[:,0::2].astype(float)**2+raw[:,1::2].astype(float)**2)
t0=ts[0]; tsec=(ts-t0)/1e6
print(f"총 {len(ts)}샘플, {tsec[-1]:.0f}초")

def analyze(mask,label):
    seg_ts=ts[mask]; seg=amp[mask]
    if len(seg_ts)<200: print(f"{label}: 샘플부족"); return
    tu=np.arange(seg_ts[0],seg_ts[-1],1e6/FS)
    std=seg.std(0); segk=seg[:,std>std.max()*0.1]
    u=np.vstack([np.interp(tu,seg_ts,segk[:,c]) for c in range(segk.shape[1])]).T
    u=detrend(u,axis=0); u=(u-u.mean(0))/(u.std(0)+1e-9)
    pcs=PCA(3).fit_transform(u)
    print(f"\n=== {label} ({len(tu)}샘플, {len(tu)/FS:.0f}초) ===")
    # 호흡대역 확인
    b,a=butter(4,[0.1,0.6],btype="band",fs=FS); rs=filtfilt(b,a,pcs[:,0])
    fr,pw=welch(pcs[:,0],fs=FS,nperseg=min(len(tu),int(FS*8)))
    rb=(fr>=0.1)&(fr<=0.6)
    if rb.any(): print(f"  호흡대역 최강: {fr[rb][np.argmax(pw[rb])]*60:.1f} rpm")
    # 심박대역: 각 PC에서 단일피크 우세도
    for c in range(3):
        b,a=butter(4,[0.8,2.0],btype="band",fs=FS); s=filtfilt(b,a,pcs[:,c])
        fr,pw=welch(s,fs=FS,nperseg=min(len(s),int(FS*8)))
        m=(fr>=0.8)&(fr<=2.0); frm,pwm=fr[m],pw[m]
        idx=np.argsort(pwm)[::-1]
        top1=frm[idx[0]]*60; p1=pwm[idx[0]]
        top2=frm[idx[1]]*60; p2=pwm[idx[1]]
        dom=p1/(p2+1e-12)   # 1등/2등 우세비 (클수록 깨끗한 단일심박)
        flag="✅깨끗" if dom>1.8 else ("△" if dom>1.3 else "❌혼탁")
        print(f"  PC{c} 심박: 1등={top1:.0f}bpm 2등={top2:.0f}bpm 우세비={dom:.2f} {flag}")

analyze(tsec<=14, "숨참기 구간 (0~14초)")
analyze(tsec>=16, "정상호흡 구간 (16~30초)")
