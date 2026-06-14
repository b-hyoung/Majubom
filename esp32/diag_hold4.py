"""45초 숨참기: envelope로 hold구간 검출 → 그 구간 모든 방법으로 심박 정밀분석"""
import re, numpy as np
from scipy.signal import butter, filtfilt, detrend, welch, hilbert
from sklearn.decomposition import PCA
FS=100.0
rec=[]
for line in open("csi_hold2.csv",errors="ignore"):
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

def feat_pca(feat):
    std=feat.std(0); feat=feat[:,std>std.max()*0.1]
    if feat.shape[1]==0: return None
    u=np.vstack([np.interp(tu,ts,feat[:,c]) for c in range(feat.shape[1])]).T
    u=detrend(u,axis=0); u=(u-u.mean(0))/(u.std(0)+1e-9)
    return PCA(min(3,u.shape[1])).fit_transform(u)
def band(s,lo,hi):
    b,a=butter(4,[lo,hi],btype="band",fs=FS); return filtfilt(b,a,s)

# 1) 호흡 envelope로 hold 구간 찾기
amppca=feat_pca(np.abs(comp))
resp=band(amppca[:,0],0.1,0.5); env=np.abs(hilbert(resp))
print("호흡진폭 시간축 (작을수록 숨참음):")
for t in range(0,45,3):
    mm=(tsec>=t)&(tsec<t+3)
    if mm.any():
        v=env[mm].mean()/env.max()
        print(f"  {t:2d}-{t+3:2d}s | {v:.2f} {'#'*int(v*40)}")
# 가장 조용한 연속 구간
win=int(FS*16); best=None
for s in range(0,len(env)-win,int(FS)):
    mn=env[s:s+win].mean()
    if best is None or mn<best[0]: best=(mn,s)
hs=best[1]; he=hs+win
print(f"\n→ 검출된 숨참기 구간: {tsec[hs]:.0f}~{tsec[he]:.0f}초 ({(he-hs)/FS:.0f}초)\n")

def autocorr_bpm(sig):
    s=(sig-sig.mean())/(sig.std()+1e-9)
    ac=np.correlate(s,s,"full")[len(s)-1:]
    lo=int(FS*60/110); hi=int(FS*60/45); seg=ac[lo:hi]; lag=lo+np.argmax(seg)
    return 60*FS/lag, ac[lag]/ac[0]
def spec_dom(sig):
    fr,pw=welch(sig,fs=FS,nperseg=min(len(sig),int(FS*12)))
    mm=(fr>=0.8)&(fr<=2.0); frm,pwm=fr[mm],pw[mm]
    idx=np.argsort(pwm)[::-1]; return frm[idx[0]]*60, pwm[idx[0]]/(pwm[idx[1]]+1e-12)

methods={
 "진폭":np.abs(comp),
 "위상(오프셋제거)":np.unwrap(np.angle(comp)-np.angle(comp).mean(1,keepdims=True),axis=0),
 "서브캐리어비":np.abs(comp[:,1:]/(comp[:,:-1]+1e-9)),
}
print("숨참기 구간 심박 추출 (강도>0.3 & 65~95bpm & 우세비>1.8 = 성공):")
for name,feat in methods.items():
    pcs=feat_pca(feat)
    if pcs is None: print(f"  {name}: 특징없음"); continue
    best=None
    for c in range(pcs.shape[1]):
        s=band(pcs[:,c][hs:he],0.8,2.0)
        bpm,strg=autocorr_bpm(s); topbpm,dom=spec_dom(s)
        score=strg*(dom>1.5)
        if best is None or strg>best[0]: best=(strg,bpm,topbpm,dom,c)
    strg,bpm,topbpm,dom,c=best
    ok="✅성공" if (strg>0.3 and 65<=bpm<=95 and dom>1.8) else "❌"
    print(f"  {name:14s}: PC{c} 자기상관={bpm:.0f}bpm(강도{strg:.2f}) 스펙트럼={topbpm:.0f}bpm(우세{dom:.1f}) {ok}")
