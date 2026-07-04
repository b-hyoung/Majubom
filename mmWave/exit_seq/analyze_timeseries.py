import json, numpy as np, collections, torch, torch.nn as nn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, precision_score, recall_score, accuracy_score
torch.manual_seed(0); np.random.seed(0)
rows=[json.loads(l) for l in open('seq_dataset.jsonl')]; rows.sort(key=lambda r:r['ts'])
post=[r.get('tof_posture') for r in rows]; OCC={'supine','sitting','side_left','side_right'}; K=6
def perframe(seq):
    a=np.array(seq); out=[]
    for fr in a:
        v=fr[np.any(fr!=0,axis=1)]
        if len(v)==0: out.append([0,0,0,0,0]); continue
        out.append([np.median(v[:,0]),np.median(v[:,1]),np.median(v[:,2]),
                    np.percentile(v[:,2],90)-np.percentile(v[:,2],10),len(v)])
    return np.array(out,dtype=np.float32)  # 30x5
S=[];Y=[]
for i,p in enumerate(post):
    if p not in OCC: continue
    y=1 if 'empty' in post[i+1:i+1+K] else 0
    S.append(perframe(rows[i]['seq'])); Y.append(y)
S=np.array(S); Y=np.array(Y)                 # S:(N,30,5)
# 시간 분할 (앞 70% train)
n=len(S); cut=int(n*0.7); tr=slice(0,cut); te=slice(cut,n)
print(f"train {cut} (임박 {Y[tr].sum()}) / test {n-cut} (임박 {Y[te].sum()})")
base=max(Y[te].mean(),1-Y[te].mean())*100
print(f"베이스라인(다수 예측): {base:.1f}%\n")
# 표준화
mu=S[tr].reshape(-1,5).mean(0); sd=S[tr].reshape(-1,5).std(0)+1e-6
Sn=(S-mu)/sd
# ── RF (뭉갬: 프레임요약) ──
def agg(x): return np.concatenate([x.mean(0),x.std(0),x.max(0),x.min(0),x[-1]-x[0]])
Xrf=np.array([agg(s) for s in Sn])
rf=RandomForestClassifier(300,class_weight='balanced',random_state=0).fit(Xrf[tr],Y[tr])
pr=rf.predict(Xrf[te])
print(f"[RF 뭉갬]  acc={accuracy_score(Y[te],pr)*100:.1f}%  임박 recall={recall_score(Y[te],pr,zero_division=0)*100:.0f}% prec={precision_score(Y[te],pr,zero_division=0)*100:.0f}% F1={f1_score(Y[te],pr,zero_division=0)*100:.0f}")
# ── LSTM (시간순) ──
Xtr=torch.tensor(Sn[tr]); Xte=torch.tensor(Sn[te]); ytr=torch.tensor(Y[tr])
w=torch.tensor([1.0, (Y[tr]==0).sum()/max((Y[tr]==1).sum(),1)],dtype=torch.float32)
class M(nn.Module):
    def __init__(s): super().__init__(); s.l=nn.LSTM(5,24,batch_first=True); s.f=nn.Linear(24,2)
    def forward(s,x): o,_=s.l(x); return s.f(o[:,-1])
m=M(); opt=torch.optim.Adam(m.parameters(),lr=0.01,weight_decay=1e-3); lossf=nn.CrossEntropyLoss(weight=w)
for e in range(120):
    m.train(); opt.zero_grad(); out=m(Xtr); l=lossf(out,ytr); l.backward(); opt.step()
m.eval();
with torch.no_grad(): pl=m(Xte).argmax(1).numpy()
print(f"[LSTM 시간순] acc={accuracy_score(Y[te],pl)*100:.1f}%  임박 recall={recall_score(Y[te],pl,zero_division=0)*100:.0f}% prec={precision_score(Y[te],pl,zero_division=0)*100:.0f}% F1={f1_score(Y[te],pl,zero_division=0)*100:.0f}")
