"""최종판정: 깨끗한 숨참기 구간에서 어떤 방법이든 안정적 심박(~70-90)이 나오나"""
import re, numpy as np
from scipy.signal import butter, filtfilt, detrend, welch
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
I=raw[:,0::2].astype(float); Q=raw[:,1::2].astype(float); comp=I+1j*Q
tu=np.arange(ts[0],ts[-1],1e6/FS); tsec=(tu-tu[0])/1e6
# 호흡 낮은 구간: 11~25초 (14초)
def feat_pca(feat):
    std=feat.std(0); feat=feat[:,std>std.max()*0.1]
    if feat.shape[1]==0: return None
    u=np.vstack([np.interp(tu,ts,feat[:,c]) for c in range(feat.shape[1])]).T
    u=detrend(u,axis=0); u=(u-u.mean(0))/(u.std(0)+1e-9)
    return PCA(min(3,u.shape[1])).fit_transform(u)
def band(s,lo,hi):
    b,a=butter(4,[lo,hi],btype="band",fs=FS); return filtfilt(b,a,s)
def autocorr_bpm(sig):
    s=(sig-sig.mean())/(sig.std()+1e-9)
    ac=np.correlate(s,s,"full")[len(s)-1:]
    lo=int(FS*60/110); hi=int(FS*60/45)
    seg=ac[lo:hi]; lag=lo+np.argmax(seg)
    peakval=ac[lag]/ac[0]   # 자기상관 피크 강도(0~1, 클수록 진짜 주기성)
    return 60*FS/lag, peakval

m=(tsec>=11)&(tsec<=25)
methods={
 "진폭":np.abs(comp),
 "위상(오프셋제거)":np.unwrap(np.angle(comp)-np.angle(comp).mean(1,keepdims=True),axis=0),
 "인접켤레곱":np.unwrap(np.angle(comp[:,1:]*np.conj(comp[:,:-1])),axis=0),
 "서브캐리어비":np.abs(comp[:,1:]/(comp[:,:-1]+1e-9)),
}
print("숨참기 구간(11~25초, 호흡≈0)에서 심박 추출:")
print("(자기상관강도 >0.3 이면 진짜 주기성 있음. bpm이 안정적이고 70~95면 성공)")
for name,feat in methods.items():
    pcs=feat_pca(feat)
    if pcs is None: print(f"  {name}: 특징없음"); continue
    res=[]
    for c in range(pcs.shape[1]):
        s=band(pcs[:,c][m],0.8,2.0)
        bpm,strength=autocorr_bpm(s)
        res.append((strength,bpm,c))
    res.sort(reverse=True)
    strength,bpm,c=res[0]
    flag="✅" if strength>0.3 and 65<=bpm<=95 else "❌"
    print(f"  {name:14s}: 최강PC{c} {bpm:.0f}bpm 자기상관강도={strength:.2f} {flag}")
print("\n참고: 워치 세션 HR 71~79bpm. 숨참으면 보통 비슷하거나 약간 낮아짐.")
