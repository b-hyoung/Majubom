"""숨 실제로 언제 참았나: 호흡 진폭 envelope를 시간축으로. 그 구간만 시간영역 심박."""
import re, numpy as np
from scipy.signal import butter, filtfilt, detrend, find_peaks, hilbert
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
tu=np.arange(ts[0],ts[-1],1e6/FS)
std=amp.std(0); ampk=amp[:,std>std.max()*0.1]
u=np.vstack([np.interp(tu,ts,ampk[:,c]) for c in range(ampk.shape[1])]).T
u=detrend(u,axis=0); u=(u-u.mean(0))/(u.std(0)+1e-9)
pcs=PCA(3).fit_transform(u)
tsec=(tu-tu[0])/1e6

# 호흡대역(0.1~0.5Hz) 진폭 envelope를 2초창으로
b,a=butter(4,[0.1,0.5],btype="band",fs=FS)
resp=filtfilt(b,a,pcs[:,0])
env=np.abs(hilbert(resp))
print("시간(초) | 호흡진폭(클수록 호흡중, 작을수록 숨참음)")
for t in range(0,30,2):
    m=(tsec>=t)&(tsec<t+2)
    if m.any():
        bar="#"*int(env[m].mean()/env.max()*40)
        print(f"  {t:2d}-{t+2:2d}s | {env[m].mean()/env.max():.2f} {bar}")

# 호흡진폭 가장 낮은 연속 8초 = 진짜 숨참기 구간 추정
win=int(FS*8); best=None
for s in range(0,len(env)-win,int(FS)):
    mn=env[s:s+win].mean()
    if best is None or mn<best[0]: best=(mn,s)
hs=best[1]; he=hs+win
print(f"\n→ 추정 숨참기 구간: {tsec[hs]:.0f}~{tsec[he]:.0f}초 (호흡진폭 최소)")

# 그 구간 시간영역 심박: 0.8~2.0 밴드 후 peak 세기
for c in range(3):
    b,a=butter(4,[0.8,2.0],btype="band",fs=FS); s=filtfilt(b,a,pcs[:,c])[hs:he]
    s=s/(s.std()+1e-9)
    pk,_=find_peaks(s,distance=int(FS*0.4),prominence=0.5)
    if len(pk)>=3:
        rr=np.diff(pk)*(1000/FS); hr=60000/np.mean(rr)
        print(f"  PC{c}: 8초간 {len(pk)}박 → {hr:.0f}bpm (peak간격 {np.mean(rr):.0f}±{np.std(rr):.0f}ms)")
    else:
        print(f"  PC{c}: peak {len(pk)}개 (부족)")
