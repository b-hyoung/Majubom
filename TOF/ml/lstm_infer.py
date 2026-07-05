#!/usr/bin/env python3
"""
LSTM-AE 실시간 이상탐지 추론
============================
feature_logger 가 tof_features.db 에 실시간으로 쓰는 최신 특징(마지막 SEQ 프레임)을
읽어 LSTM-AE 로 재구성오차 계산 → 임계 초과면 '이상'. (feature_logger 가 돌고 있어야 함)

사용: python lstm_infer.py
검증: 평소 활동 → 정상(낮은 오차) / 낙상 흉내(바닥에 눕기 등) → 이상(오차 급등) 확인
"""
import sqlite3, os, time
import numpy as np
import torch
from lstm_anomaly import LSTMAE, FEATURES

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "tof_features.db")
DEV = "cuda" if torch.cuda.is_available() else "cpu"

d = torch.load(os.path.join(HERE, "lstm_ae.pt"), weights_only=False)
model = LSTMAE(len(FEATURES), d["hid"]).to(DEV)
model.load_state_dict(d["model"]); model.eval()
mu, sd, seq, thr = d["mu"], d["sd"], d["seq"], d["threshold"]
print(f"[model] seq={seq} 임계={thr:.3f} device={DEV}\n"
      f"실시간 이상탐지 시작 (Ctrl+C 종료)\n", flush=True)

cols = ",".join(FEATURES)
last_ts = None
while True:
    try:
        c = sqlite3.connect(DB)
        rows = c.execute(f"select timestamp,{cols} from tof_features "
                         f"order by id desc limit {seq}").fetchall()
        c.close()
    except Exception:
        time.sleep(0.3); continue
    if len(rows) < seq:
        time.sleep(0.3); continue
    rows = rows[::-1]                        # 시간순
    ts = rows[-1][0]
    if ts == last_ts:
        time.sleep(0.2); continue
    last_ts = ts
    X = np.array([[float(v) if v is not None else 0.0 for v in r[1:]] for r in rows],
                 dtype=np.float32)
    Xn = (X - mu) / sd
    with torch.no_grad():
        x = torch.tensor(Xn[None]).float().to(DEV)
        err = ((model(x) - x) ** 2).mean().item()
    ratio = err / thr
    if err > thr:      status = "🔴 이상"
    elif ratio > 0.7:  status = "🟠 경계"
    else:              status = "🟢 정상"
    bar = "#" * min(int(ratio * 20), 40)
    print(f"{status}  오차 {err:.3f} / 임계 {thr:.3f}  ({ratio*100:3.0f}%) {bar}", flush=True)
    time.sleep(0.3)
