import json, numpy as np, collections
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score
rows=[json.loads(l) for l in open('seq_dataset.jsonl')]

def feats(seq):
    a=np.array(seq)  # 30x64x4 (x,y,z,dop)
    cen=[]; az=[]; npts=[]
    for fr in a:
        v=fr[np.any(fr!=0,axis=1)]; npts.append(len(v))
        if len(v)==0: cen.append([np.nan]*3); continue
        cen.append([np.median(v[:,0]),np.median(v[:,1]),np.median(v[:,2])]); az+=list(v[:,2])
    cen=np.array(cen); valid=~np.isnan(cen[:,0])
    if valid.sum()<6 or len(az)<5: return None
    cv=cen[valid]; vel=np.diff(cv,axis=0)          # 중심점 프레임간 이동 = 속도
    velz=vel[:,2]; velxy=np.linalg.norm(vel[:,:2],axis=1)
    az=np.array(az); zf=cv[:,2]
    # --- 높이/위치 (속도 아님) ---
    H=dict(z_mean=zf.mean(), z_spread=np.percentile(az,90)-np.percentile(az,10),
           z_max=az.max(), z_net=zf[-1]-zf[0],
           x_sp=np.ptp(cv[:,0]), y_sp=np.ptp(cv[:,1]), npts=np.mean(npts))
    # --- 유도 속도 (핵심) ---
    V=dict(velz_mean=velz.mean(), velz_max=velz.max(), velz_absmax=np.abs(velz).max(),
           velxy_mean=velxy.mean(), velxy_max=velxy.max(),
           xy_net=np.linalg.norm(cv[-1,:2]-cv[0,:2]))
    return H,V

Hs=[];Vs=[];y=[]
for r in rows:
    f=feats(r['seq'])
    if f is None or r.get('tof_posture') is None: continue
    H,V=f; Hs.append(list(H.values())); Vs.append(list(V.values())); y.append(r['tof_posture'])
Hs=np.array(Hs); Vs=np.array(Vs); y=np.array(y); Both=np.hstack([Hs,Vs])
VN=['velz_mean','velz_max','velz_absmax','velxy_mean','velxy_max','xy_net']

print("== 라벨별 유도 속도 특징 ==")
for lab in sorted(set(y)):
    m=Vs[y==lab].mean(0)
    print(f"  {lab:<11} velZ최대={m[1]:+.3f} velXY평균={m[3]:.3f} 수평이동={m[5]:.2f}")

def cv(X,yy,k=5): return cross_val_score(RandomForestClassifier(200,random_state=0),X,yy,cv=k).mean()*100

# 핵심 비교: 이탈전조(sitting) vs 뒤척임(side)
m=np.isin(y,['sitting','side_left','side_right']); y2=np.where(y[m]=='sitting','전조','뒤척임')
print(f"\n== 이탈전조 vs 뒤척임 정확도 ==")
print(f"  높이/위치만        : {cv(Hs[m],y2):.1f}%")
print(f"  속도만             : {cv(Vs[m],y2):.1f}%")
print(f"  높이+속도(둘다)    : {cv(Both[m],y2):.1f}%")
# 전체 5클래스도
print(f"\n== 전체 5클래스 ==")
print(f"  높이/위치만: {cv(Hs,y):.1f}%   속도만: {cv(Vs,y):.1f}%   둘다: {cv(Both,y):.1f}%")
imp=RandomForestClassifier(200,random_state=0).fit(Vs[m],y2).feature_importances_
print("\n속도특징 중요도:", [(VN[i],round(imp[i],2)) for i in np.argsort(imp)[::-1][:4]])
