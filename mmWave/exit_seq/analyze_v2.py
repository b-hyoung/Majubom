"""
analyze_v2.py — 이탈 임박 예측: GRU 대안 + 추가 증강기법 정직 비교 (2026-09 문헌 반영)
====================================================================================
analyze_timeseries.py / analyze_aug.py 와 같은 태스크(점유 시퀀스 → 다음 K프레임 내
empty=임박), 같은 시간분할(앞70% train/뒤30% test), 같은 5개 시드 평균 비교 방식.

이 스크립트가 추가로 확인하는 것 — 2026-09 문헌 조사에서 나온 두 가지 제안의 실측:

  1) LSTM → GRU 교체가 실제로 이 데이터에서 도움이 되는가?
     근거: Sykes, Maghsoudimehrabani & Al-Shanoon (2026), "Benchmarking Time-Series
     AI Architectures for Wearable Sensor-Based Fall Prediction", Sensors 26(11):3326.
     — 같은 '소수클래스·임박이벤트 예측' 과제 유형에서, 경보 임계값 운용 시 GRU가
     LSTM/Transformer보다 이벤트를 더 많이 잡아냈다는 벤치마크 결과(파라미터 수가
     적어 극소 양성표본에서 과적합이 덜함). 이 논문은 우리 데이터가 아닌 별도의
     합성 낙상데이터 벤치마크이므로, "그러니 GRU가 낫다"가 아니라 "우리 데이터로
     직접 재보자"가 정직한 다음 단계 — 이 스크립트가 그 실측이다.

  2) 기존 지터+타임워프 증강 외에, Iwana & Uchida (2021), "An Empirical Survey of
     Data Augmentation for Time Series Classification with Neural Networks",
     PLOS ONE 16(7):e0254841 이 12개 기법 중 평균 순위가 가장 높다고 보고한
     window warping / window slicing을 추가하면 더 도움이 되는가?
     (이 논문은 128개 UCR 데이터셋 기준이며 우리 점군 시퀀스와 과제가 다르므로
     이 역시 "그러니 더 낫다"가 아니라 "우리 데이터로 직접 재보자"가 정직한 확인.)

⚠️ 정직 평가 원칙(기존과 동일): 증강 샘플은 train에만, test는 원본만. 평가 표본이
   28개(n-cut)로 매우 작아 시드 간 분산이 크다 — 여기 나온 숫자도 "확정적 향상"이
   아니라 이 프로젝트의 다른 실험 기록과 같은 수준의 잠정치로 취급할 것.

사용: python analyze_v2.py
"""
import json, gzip, os, random
import numpy as np
import torch, torch.nn as nn
from sklearn.metrics import f1_score, precision_score, recall_score, accuracy_score
from augment_seq import augment_seq

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
    a = np.array(seq); out = []
    for fr in a:
        v = fr[np.any(fr != 0, axis=1)]
        if len(v) == 0:
            out.append([0, 0, 0, 0, 0]); continue
        out.append([np.median(v[:, 0]), np.median(v[:, 1]), np.median(v[:, 2]),
                    np.percentile(v[:, 2], 90) - np.percentile(v[:, 2], 10), len(v)])
    return np.array(out, dtype=np.float32)


class LSTMModel(nn.Module):
    def __init__(s): super().__init__(); s.l = nn.LSTM(5, 24, batch_first=True); s.f = nn.Linear(24, 2)
    def forward(s, x): o, _ = s.l(x); return s.f(o[:, -1])


class GRUModel(nn.Module):
    """LSTM과 은닉크기(24)·구조를 동일하게 맞춘 GRU 버전 — 게이트가 적어 파라미터 수가
    더 적다(극소 양성표본 과적합 완화 가설의 실측 대상)."""
    def __init__(s): super().__init__(); s.g = nn.GRU(5, 24, batch_first=True); s.f = nn.Linear(24, 2)
    def forward(s, x): o, _ = s.g(x); return s.f(o[:, -1])


def n_params(model_cls):
    return sum(p.numel() for p in model_cls().parameters())


def train_eval(model_cls, Str, ytr, Ste, yte, seed):
    torch.manual_seed(seed); np.random.seed(seed)
    mu = Str.reshape(-1, 5).mean(0); sd = Str.reshape(-1, 5).std(0) + 1e-6
    Xtr = torch.tensor((Str - mu) / sd); Xte = torch.tensor((Ste - mu) / sd)
    yt = torch.tensor(ytr)
    w = torch.tensor([1.0, (ytr == 0).sum() / max((ytr == 1).sum(), 1)], dtype=torch.float32)
    m = model_cls(); opt = torch.optim.Adam(m.parameters(), lr=0.01, weight_decay=1e-3)
    lossf = nn.CrossEntropyLoss(weight=w)
    for _ in range(120):
        m.train(); opt.zero_grad(); lossf(m(Xtr), yt).backward(); opt.step()
    m.eval()
    with torch.no_grad():
        pl = m(Xte).argmax(1).numpy()
    return (accuracy_score(yte, pl), f1_score(yte, pl, zero_division=0),
            recall_score(yte, pl, zero_division=0), precision_score(yte, pl, zero_division=0))


def time_warp(seq, rng):
    f = rng.uniform(0.8, 1.25)
    L = len(seq)
    src = np.clip(np.round(np.arange(L) * f).astype(int), 0, L - 1)
    return [seq[i] for i in src]


def aug_safe(seq, rng):
    return augment_seq(time_warp(seq, rng), rng, 0.02, 0.0, 1.0, 1.0, 0.0)


def _resample(seq, index_path):
    """가변 길이 index_path(프레임 인덱스 목록)를 원래 길이 L로 재표본화(최근접)."""
    L = len(seq)
    NL = len(index_path)
    if NL == L:
        return [seq[i] for i in index_path]
    if NL == 1:
        return [seq[index_path[0]]] * L
    out_idx = [index_path[min(NL - 1, round(i * (NL - 1) / (L - 1)))] for i in range(L)]
    return [seq[i] for i in out_idx]


def window_slice(seq, rng, min_ratio=0.7):
    """Iwana & Uchida 2021 — 임의 구간을 잘라 원 길이로 재표본화(랜덤 크롭)."""
    L = len(seq)
    w = max(2, int(round(L * rng.uniform(min_ratio, 1.0))))
    start = rng.randrange(0, L - w + 1)
    return _resample(seq, list(range(start, start + w)))


def window_warp(seq, rng, window_ratio=0.3, warp_lo=0.5, warp_hi=2.0):
    """Iwana & Uchida 2021 — 시퀀스 내 일부 구간만 국소적으로 늘이거나 줄임
    (전체를 배속하는 time_warp와 달리 '한 구간'만 왜곡)."""
    L = len(seq)
    w = max(2, int(round(L * window_ratio)))
    start = rng.randrange(0, L - w + 1)
    ratio = rng.uniform(warp_lo, warp_hi)
    inner_len = max(1, round(w * ratio))
    inner = [start + (round(i * (w - 1) / (inner_len - 1)) if inner_len > 1 else 0)
             for i in range(inner_len)]
    path = list(range(0, start)) + inner + list(range(start + w, L))
    return _resample(seq, path)


def aug_v2(seq, rng):
    """기존 안전 증강(지터+타임워프) 뒤에 window-slice/warp를 절반 확률로 추가."""
    seq = aug_safe(seq, rng)
    if rng.random() < 0.5:
        seq = window_slice(seq, rng)
    else:
        seq = window_warp(seq, rng)
    return seq


def build_train(raw, Y, cut, mode, seed, ratio=1.0):
    tr_raw = [raw[i] for i in range(cut)]; tr_y = [int(Y[i]) for i in range(cut)]
    if mode != 'off':
        rng = random.Random(1000 + seed)
        pos = [raw[i] for i in range(cut) if Y[i] == 1]
        n_pos = len(pos); n_neg = cut - n_pos
        need = int(n_neg * ratio) - n_pos
        fn = aug_safe if mode == 'safe' else aug_v2
        for _ in range(max(0, need)):
            base = pos[rng.randrange(len(pos))]
            tr_raw.append(fn(base, rng)); tr_y.append(1)
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
    print(f"베이스라인(다수예측): {max(yte.mean(),1-yte.mean())*100:.1f}%")
    print(f"파라미터 수: LSTM={n_params(LSTMModel)}  GRU={n_params(GRUModel)}\n")

    print("=== 1) 아키텍처 비교 (증강 없음, 원본 그대로) ===")
    for name, cls in [("LSTM", LSTMModel), ("GRU", GRUModel)]:
        A, F, R, P = [], [], [], []
        for s in SEEDS:
            Str, ytr = build_train(raw, Y, cut, 'off', s)
            a, f, r, p = train_eval(cls, Str, ytr, Ste, yte, s)
            A.append(a); F.append(f); R.append(r); P.append(p)
        print(f"[{name:5s}] acc={np.mean(A)*100:4.1f}±{np.std(A)*100:4.1f}  "
              f"F1={np.mean(F)*100:4.1f}±{np.std(F)*100:4.1f}  "
              f"recall={np.mean(R)*100:3.0f}%  prec={np.mean(P)*100:3.0f}%")

    print("\n=== 2) 증강기법 비교 (모델 고정: 각각 LSTM/GRU) ===")
    for model_name, cls in [("LSTM", LSTMModel), ("GRU", GRUModel)]:
        for tag, mode in [("증강 X", 'off'), ("기하안전(지터+타임워프, 기존)", 'safe'),
                           ("기하안전+window-slice/warp(신규)", 'v2')]:
            A, F, R, P = [], [], [], []
            for s in SEEDS:
                Str, ytr = build_train(raw, Y, cut, mode, s)
                a, f, r, p = train_eval(cls, Str, ytr, Ste, yte, s)
                A.append(a); F.append(f); R.append(r); P.append(p)
            print(f"[{model_name:4s}] {tag:<32} train={len(ytr):3d}(임박{int(ytr.sum()):3d})  "
                  f"F1={np.mean(F)*100:4.1f}±{np.std(F)*100:4.1f}  "
                  f"recall={np.mean(R)*100:3.0f}%  prec={np.mean(P)*100:3.0f}%")


if __name__ == "__main__":
    main()
