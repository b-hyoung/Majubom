"""가설검증: (1) 85클러스터=호흡고조파? (2) 위상/quotient가 진짜HR을 살리나?"""
import re, numpy as np
from scipy.signal import butter, filtfilt, detrend, welch
from sklearn.decomposition import PCA
FS=100.0
GT={"csi_watch1.csv":79,"csi_watch2.csv":73,"csi_watch3.csv":71}

def parse(LOG):
    rec=[]
    for line in open(LOG,errors="ignore"):
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
    I=raw[:,0::2].astype(float); Q=raw[:,1::2].astype(float)
    comp=I+1j*Q                      # (N,64) 복소 CSI
    tu=np.arange(ts[0],ts[-1],1e6/FS)
    return ts,tu,comp

def resample_pca(feat,ts,tu,k=3):
    std=feat.std(0); feat=feat[:,std>std.max()*0.1]
    u=np.vstack([np.interp(tu,ts,feat[:,c]) for c in range(feat.shape[1])]).T
    u=detrend(u,axis=0); u=(u-u.mean(0))/(u.std(0)+1e-9)
    return PCA(min(k,u.shape[1])).fit_transform(u)

def band(s,lo,hi):
    b,a=butter(4,[lo,hi],btype="band",fs=FS); return filtfilt(b,a,s)

def topbpm(sig,gt):
    sig=band(sig,0.8,2.0)
    fr,pw=welch(sig,fs=FS,nperseg=int(FS*20))
    m=(fr>=0.8)&(fr<=2.0); frm,pwm=fr[m],pw[m]
    top=frm[np.argmax(pwm)]*60
    gi=np.argmin(np.abs(frm-gt/60)); gtp=pwm[gi]/pwm.max()
    return top,gtp

def resp_harmonics(comp,ts,tu,gt):
    amp=np.abs(comp)
    pc=resample_pca(amp,ts,tu,1)[:,0]
    fr,pw=welch(pc,fs=FS,nperseg=int(FS*30))
    rb=(fr>=0.08)&(fr<=0.4); rf=fr[rb][np.argmax(pw[rb])]
    harm=[(rf*n*60) for n in range(1,18) if 0.8<=rf*n<=2.0]
    return rf*60, harm

for f,gt in GT.items():
    ts,tu,comp=parse(f)
    rrpm,harm=resp_harmonics(comp,ts,tu,gt)
    print(f"\n{'='*64}\n{f} (진짜HR {gt}bpm)  호흡 {rrpm:.1f}rpm")
    print(f"  호흡 고조파가 심박대역에 떨어지는 위치: {[f'{h:.0f}' for h in harm]} bpm")
    # 방법별 추출 → 진짜HR이 top인가
    methods={}
    methods["진폭(현재)"]=resample_pca(np.abs(comp),ts,tu)
    # 위상: 패킷별 평균위상 제거(공통오프셋 제거) 후 unwrap
    ph=np.angle(comp); ph=ph-ph.mean(1,keepdims=True)
    methods["위상(오프셋제거)"]=resample_pca(np.unwrap(ph,axis=0),ts,tu)
    # 인접 서브캐리어 켤레곱(conjugate product): 공통 위상잡음 상쇄
    cp=comp[:,1:]*np.conj(comp[:,:-1])
    methods["인접켤레곱 위상"]=resample_pca(np.unwrap(np.angle(cp),axis=0),ts,tu)
    # CSI ratio: 두 서브캐리어 복소비 (quotient model)
    ratio=comp[:,1:]/(comp[:,:-1]+1e-9)
    methods["서브캐리어비 |·|"]=resample_pca(np.abs(ratio),ts,tu)
    for name,pcs in methods.items():
        best=None
        for c in range(pcs.shape[1]):
            top,gtp=topbpm(pcs[:,c],gt)
            err=abs(top-gt)
            if best is None or err<best[0]: best=(err,top,gtp,c)
        ok="✅" if best[0]<=4 else ("△" if best[0]<=8 else "❌")
        print(f"  {name:16s}: 최적PC top={best[1]:.0f}bpm (오차{best[0]:.0f}) 진짜HR파워={best[2]:.2f} {ok}")
