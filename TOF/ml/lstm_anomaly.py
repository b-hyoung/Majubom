#!/usr/bin/env python3
"""
LSTM Autoencoder 이상탐지 (평소 패턴 학습 → 재구성오차로 이상 감지)
================================================================
tof_features.db 의 시계열 특징으로 '평소'를 학습하고, 재구성 오차가 큰
구간(=평소와 다른=이상)을 감지한다. 낙상 등 이상 라벨 없이도 동작(비지도).

  입력 특징: n_pts, cx,cy,cz, sx,sy, x_range,y_range,z_range, in_bed, occ1, occ2
  시퀀스: 최근 SEQ_LEN 프레임(≈4초). 시간 간격 큰 곳(수집 끊김)은 시퀀스 분리.

사용:
  python lstm_anomaly.py                 # 학습 + 임계 설정 + 저장
  python lstm_anomaly.py --epochs 60
출력: lstm_ae.pt (모델+정규화+임계) — 추론에 사용
"""
import sqlite3, os, argparse, json
import numpy as np
import torch, torch.nn as nn
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = ["n_pts", "cx", "cy", "cz", "sx", "sy",
            "x_range", "y_range", "z_range", "in_bed", "occ1", "occ2"]
DEV = "cuda" if torch.cuda.is_available() else "cpu"


def load(db):
    c = sqlite3.connect(db)
    q = "select timestamp," + ",".join(FEATURES) + " from tof_features order by timestamp"
    ts, X = [], []
    for r in c.execute(q):
        ts.append(r[0])
        X.append([float(v) if v is not None else 0.0 for v in r[1:]])
    return ts, np.array(X, dtype=np.float32)


def _sec(t):
    try: return datetime.fromisoformat(t).timestamp()
    except Exception: return 0.0


def segments(ts, gap):
    segs, start = [], 0
    for i in range(1, len(ts)):
        if _sec(ts[i]) - _sec(ts[i - 1]) > gap:
            segs.append((start, i)); start = i
    segs.append((start, len(ts)))
    return segs


def windows(X, segs, seq, stride):
    W = []
    for s, e in segs:
        for i in range(s, e - seq + 1, stride):
            W.append(X[i:i + seq])
    return np.array(W, dtype=np.float32)


class LSTMAE(nn.Module):
    def __init__(self, feat, hid=48, layers=1):
        super().__init__()
        self.enc = nn.LSTM(feat, hid, layers, batch_first=True)
        self.dec = nn.LSTM(hid, hid, layers, batch_first=True)
        self.out = nn.Linear(hid, feat)

    def forward(self, x):
        T = x.shape[1]
        _, (h, _) = self.enc(x)
        z = h[-1]                                   # (B, hid) 잠재
        d, _ = self.dec(z.unsqueeze(1).repeat(1, T, 1))
        return self.out(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "tof_features.db"))
    ap.add_argument("--seq", type=int, default=20)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--hid", type=int, default=48)
    a = ap.parse_args()

    ts, X = load(a.db)
    print(f"[data] {len(X)}행, 특징 {X.shape[1]}차원, device={DEV}")
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Xn = (X - mu) / sd
    segs = segments(ts, a.gap)
    print(f"[data] 연속 구간 {len(segs)}개 (끊김 기준 {a.gap}s)")
    W = windows(Xn, segs, a.seq, a.stride)
    if len(W) < 50:
        print("윈도우가 너무 적음 — 데이터 더 필요"); return
    np.random.seed(0)
    idx = np.random.permutation(len(W))
    ntr = int(len(W) * 0.85)
    Wtr = torch.tensor(W[idx[:ntr]]).to(DEV)
    Wval = torch.tensor(W[idx[ntr:]]).to(DEV)
    print(f"[data] 윈도우 {len(W)} (학습 {len(Wtr)} / 검증 {len(Wval)}), seq={a.seq}")

    model = LSTMAE(X.shape[1], a.hid).to(DEV)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    lossf = nn.MSELoss()
    bs = 128
    for ep in range(a.epochs):
        model.train()
        perm = torch.randperm(len(Wtr))
        tl = 0.0
        for i in range(0, len(Wtr), bs):
            b = Wtr[perm[i:i + bs]]
            opt.zero_grad()
            loss = lossf(model(b), b)
            loss.backward(); opt.step()
            tl += loss.item() * len(b)
        if (ep + 1) % 10 == 0 or ep == 0:
            model.eval()
            with torch.no_grad():
                vl = lossf(model(Wval), Wval).item()
            print(f"  epoch {ep+1:3d}  train {tl/len(Wtr):.4f}  val {vl:.4f}")

    # 재구성 오차 분포 → 임계(정상 99%)
    model.eval()
    with torch.no_grad():
        err = ((model(torch.tensor(W).to(DEV)) - torch.tensor(W).to(DEV)) ** 2
               ).mean(dim=(1, 2)).cpu().numpy()
    thr = float(np.percentile(err, 99))
    print(f"\n[error] 평균 {err.mean():.4f}  95% {np.percentile(err,95):.4f}  "
          f"99%(임계) {thr:.4f}  최대 {err.max():.4f}")
    print(f"[error] 임계 초과(이상 후보) 윈도우: {(err>thr).sum()} / {len(err)} "
          f"({(err>thr).mean()*100:.1f}%)")

    out = os.path.join(HERE, "lstm_ae.pt")
    torch.save({"model": model.state_dict(), "features": FEATURES,
                "mu": mu, "sd": sd, "seq": a.seq, "hid": a.hid,
                "threshold": thr}, out)
    print(f"\n모델 저장: {out}")
    print("→ 재구성오차 > 임계 면 '이상'. 실시간 추론은 predict 함수로 연결 예정.")


if __name__ == "__main__":
    main()
