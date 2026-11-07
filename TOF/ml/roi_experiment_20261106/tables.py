import json, numpy as np, sys
sys.path.insert(0,'.')
from run_roi import cache
from sklearn.metrics import accuracy_score, f1_score, recall_score
S=json.load(open('summary.json'))['v4']
M=['raw_rf','raw_xgboost','raw_lightgbm','cnn','cnn_rf','cnn_xgboost','cnn_lightgbm']
V=['none','roi_py','cols','base_valid','base_off','act2','act5']
D=cache('v4'); y=D['y']; subj=D['subject']; FOUR=np.array([0,1,1,1,2,3])
SE=[42,0,1]
def load(v,s,m): return np.load(f'res/v4/{v}_{s}.npz')[m].astype(int)
print('## acc mean±sd (min–max) seeds 42,0,1')
print('| 변형 | '+' | '.join(M)+' |'); print('|---|'+'---:|'*7)
for v in V:
    row=[]
    for m in M:
        a=[accuracy_score(y,load(v,s,m)) for s in SE]
        row.append(f'{np.mean(a)*100:.2f}±{np.std(a)*100:.2f}')
    print(f'| {v} | '+' | '.join(row)+' |')
print('## minmax'); 
for v in V:
    print(v,' '.join(f'{m}:{min(accuracy_score(y,load(v,s,m)) for s in SE)*100:.1f}-{max(accuracy_score(y,load(v,s,m)) for s in SE)*100:.1f}' for m in M))
print('## paired diff vs none (pp) and wins')
print('| 변형 | '+' | '.join(M)+' | 평균 |'); print('|---|'+'---:|'*8)
for v in V[1:]:
    row=[];allm=[]
    for m in M:
        d=[(accuracy_score(y,load(v,s,m))-accuracy_score(y,load('none',s,m)))*100 for s in SE]
        allm.append(np.mean(d)); row.append(f'{np.mean(d):+.2f} ({sum(x>0 for x in d)}/3)')
    print(f'| {v} | '+' | '.join(row)+f' | {np.mean(allm):+.2f} |')
for name,fn in [('f1',lambda p:f1_score(y,p,labels=range(6),average='macro')),('acc4',lambda p:accuracy_score(FOUR[y],FOUR[p]))]:
    print('##',name); print('| 변형 | '+' | '.join(M)+' |'); print('|---|'+'---:|'*7)
    for v in V:
        print(f'| {v} | '+' | '.join(f'{np.mean([fn(load(v,s,m)) for s in SE])*100:.2f}' for m in M)+' |')
people=['S01','S02','S03','S04','S05','S06']
for m in ['raw_rf','cnn','cnn_xgboost']:
    print('## per subject',m); print('| 변형 | '+' | '.join(people)+' |'); print('|---|'+'---:|'*6)
    for v in V:
        print(f'| {v} | '+' | '.join(f'{np.mean([accuracy_score(y[subj==q],load(v,s,m)[subj==q]) for s in SE])*100:.1f}' for q in people)+' |')
L=['empty','supine','side_left','side_right','sitting','edge_sit']
for m in ['raw_rf','cnn']:
    print('## recall',m); print('| 변형 | '+' | '.join(L)+' |'); print('|---|'+'---:|'*6)
    for v in ['none','roi_py','cols']:
        r=np.mean([recall_score(y,load(v,s,m),labels=range(6),average=None) for s in SE],0)
        print(f'| {v} | '+' | '.join(f'{x*100:.1f}' for x in r)+' |')
print('## none seed2', {m:round(accuracy_score(y,load('none',2,m))*100,2) for m in M})
for v in V: print(v, S[v]['kept_zones'])
