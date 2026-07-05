"""
analyze_aug.py — 이탈 임박 예측: 증강 전/후 F1 정직 비교
====================================================================
analyze_timeseries.py 와 같은 태스크(점유 시퀀스 → 다음 K프레임 내 empty=임박),
같은 시간분할·LSTM. 차이는 '증강 효과 측정' 하나:

  · 시간분할(앞70% train / 뒤30% test) 후
  · train 의 임박(Y=1) 시퀀스만 강체변환+지터로 오버샘플 (augment_seq 재사용)
  · test 는 원본만 (증강 절대 안 섞음 → 정직)
  · baseline(증강 X) vs aug(증강 O) 를 5개 시드 평균으로 비교

센서 불필요 — seq_dataset(.gz) 파일만으로 오프라인 실행.
사용:  python analyze_aug.py
"""
import json, gzip, os, random
import numpy as np
import torch, torch.nn as nn
from sklearn.metrics import f1_score, precision_score, recall_score, accuracy_score
from augment_seq import augment_seq            # 강체변환+지터 재사용

HERE = os.path.dirname(os.path.abspath(__file__))
OCC = {'supine', 'sitting', 'side_left', 'side_right'}
K = 6
SEEDS = [0, 1, 2, 3, 4]


def load_rows():
    p = os.path.join(HERE, 'seq_dataset.jsonl')
    if os.path.exists(p):
        return [json.loads(l) for l in open(p, encoding='utf-8')]
    with gzip.open(os.path.join(HERE, 'seq_dataset.jsonl.gz'), 'rt', encoding='utf-8') as f:
        return [json.loads(l) for l in f]


def perframe(seq):
    """30프레임 점군 → 30x5 프레임특징 (median x,y,z / z확산 / 점수)."""
    a = np.array(seq); out = []
    for fr in a:
        v = fr[np.any(fr != 0, axis=1)]
        if len(v) == 0:
            out.append([0, 0, 0, 0, 0]); continue
        out.append([np.median(v[:, 0]), np.median(v[:, 1]), np.median(v[:, 2]),
                    np.percentile(v[:, 2], 90) - np.percentile(v[:, 2], 10), len(v)])
    return np.array(out, dtype=np.float32)


class M(nn.Module):
    def __init__(s): super().__init__(); s.l = nn.LSTM(5, 24, batch_first=True); s.f = nn.Linear(24, 2)
    def forward(s, x): o, _ = s.l(x); return s.f(o[:, -1])


def train_eval(Str, ytr, Ste, yte, seed):
    torch.manual_seed(seed); np.random.seed(seed)
    mu = Str.reshape(-1, 5).mean(0); sd = Str.reshape(-1, 5).std(0) + 1e-6
    Xtr = torch.tensor((Str - mu) / sd); Xte = torch.tensor((Ste - mu) / sd)
    yt = torch.tensor(ytr)
    w = torch.tensor([1.0, (ytr == 0).sum() / max((ytr == 1).sum(), 1)], dtype=torch.float32)
    m = M(); opt = torch.optim.Adam(m.parameters(), lr=0.01, weight_decay=1e-3)
    lossf = nn.CrossEntropyLoss(weight=w)
    for _ in range(120):
        m.train(); opt.zero_grad(); lossf(m(Xtr), yt).backward(); opt.step()
    m.eval()
    with torch.no_grad():
        pl = m(Xte).argmax(1).numpy()
    return (f1_score(yte, pl, zero_division=0),
            recall_score(yte, pl, zero_division=0),
            precision_score(yte, pl, zero_division=0))


def time_warp(seq, rng):
    """이탈 동작 속도차 모사 — 프레임 축을 임의 배속으로 리샘플(고정 30프레임)."""
    f = rng.uniform(0.8, 1.25)
    L = len(seq)
    src = np.clip(np.round(np.arange(L) * f).astype(int), 0, L - 1)
    return [seq[i] for i in src]


def aug_safe(seq, rng):
    """기하-안전 증강: 지터+타임워프만 (고정 셋업이라 회전/스케일 금지)."""
    return augment_seq(time_warp(seq, rng), rng, 0.02, 0.0, 1.0, 1.0, 0.0)


def build_train(raw, Y, cut, mode, seed, ratio=1.0):
    """train 원본(+ 증강 Y=1 오버샘플) → (S,y). 증강은 train 에만.
    mode: 'off' | 'rigid'(회전+스케일+지터) | 'safe'(지터+타임워프)."""
    tr_raw = [raw[i] for i in range(cut)]; tr_y = [int(Y[i]) for i in range(cut)]
    if mode != 'off':
        rng = random.Random(1000 + seed)
        pos = [raw[i] for i in range(cut) if Y[i] == 1]
        n_pos = len(pos); n_neg = cut - n_pos
        need = int(n_neg * ratio) - n_pos
        for _ in range(max(0, need)):
            base = pos[rng.randrange(len(pos))]
            aug = augment_seq(base, rng, 0.025, 12.0, 0.95, 1.05, 0.10) if mode == 'rigid' \
                else aug_safe(base, rng)
            tr_raw.append(aug); tr_y.append(1)
    return np.array([perframe(s) for s in tr_raw]), np.array(tr_y)


def main():
    rows = load_rows(); rows.sort(key=lambda r: r['ts'])
    post = [r.get('tof_posture') for r in rows]
    raw, Y = [], []
    for i, p in enumerate(post):
        if p not in OCC:
            continue
        raw.append(rows[i]['seq']); Y.append(1 if 'empty' in post[i + 1:i + 1 + K] else 0)
    Y = np.array(Y); n = len(Y); cut = int(n * 0.7)
    Ste = np.array([perframe(raw[i]) for i in range(cut, n)]); yte = Y[cut:]
    print(f"점유 {n}  |  train {cut}(임박 {Y[:cut].sum()})  test {n-cut}(임박 {yte.sum()})")
    print(f"베이스라인(다수예측): {max(yte.mean(),1-yte.mean())*100:.1f}%\n")

    trials = [("증강 X (baseline)", 'off', 1.0),
              ("증강 O 회전+스케일 1:1", 'rigid', 1.0),
              ("증강 O 기하안전 1:1", 'safe', 1.0),
              ("증강 O 기하안전 0.5배", 'safe', 0.5)]
    for tag, mode, ratio in trials:
        F, R, P = [], [], []
        for s in SEEDS:
            Str, ytr = build_train(raw, Y, cut, mode, s, ratio)
            f, r, p = train_eval(Str, ytr, Ste, yte, s)
            F.append(f); R.append(r); P.append(p)
        print(f"[LSTM] {tag:<24}  train={len(ytr):3d}(임박 {int(ytr.sum()):3d})  "
              f"F1={np.mean(F)*100:4.1f}±{np.std(F)*100:4.1f}  "
              f"recall={np.mean(R)*100:3.0f}%  prec={np.mean(P)*100:3.0f}%")


if __name__ == "__main__":
    main()
