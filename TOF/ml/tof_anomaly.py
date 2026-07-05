#!/usr/bin/env python3
"""
ToF LSTM-AE 실시간 이상탐지 (서버 통합용 모듈)
==============================================
feature_logger 와 '동일한' 특징 추출을 서버 안에서 재사용해, 프레임마다
최근 SEQ(≈4초) 특징으로 LSTM-AE 재구성오차를 계산한다. 오차가 임계보다
크면 '이상'(=평소와 다른 움직임 = 낙상·발버둥 등 후보).

  · frame_feature(latest, base, presence)  → 12차원 특징 벡터
  · Detector.push(feat) / Detector.score() → 재구성오차·상태 dict

torch/lstm_ae.pt 가 없으면 import 자체가 실패 → 서버가 try/except 로 감싸
이상탐지만 조용히 비활성(서버는 정상 동작). CLI(lstm_infer.py)와 임계·정규화
동일(lstm_ae.pt 공유).
"""
import os
import statistics as st
from collections import deque

import numpy as np
import torch

import roi
from lstm_anomaly import LSTMAE, FEATURES   # 학습과 동일한 구조·특징 순서

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "lstm_ae.pt")

# feature_logger 와 동일한 전경(사람) 임계 — 학습 데이터와 맞춰야 함
FG_THR_MM   = 200    # 빈 침대 baseline 대비 이만큼(mm) 가까우면 사람 점
STATE_MIN_PTS = 3


def frame_feature(latest, base, presence, thr=FG_THR_MM):
    """한 프레임의 12차원 특징 (FEATURES 순서와 정확히 일치).

    latest   : {"tof1":{"distances_mm":[...]}, "tof2":{...}}  (서버 in-memory)
    base     : {"tof1":[존별 빈침대 mm], "tof2":[...]}          (서버 baseline)
    presence : 서버 presence dict (in_bed, tof1.occupied, tof2.occupied)
    """
    pts = []
    for sid in ("tof1", "tof2"):
        d = (latest.get(sid) or {}).get("distances_mm") or []
        b = base.get(sid)
        if not b:
            continue
        for z, v in enumerate(d):
            bz = b[z] if z < len(b) else None
            if v and v > 0 and bz and bz > 0 and abs(v - bz) > thr:
                if roi.on_bed(sid, z, v):
                    p = roi.hit_point(sid, z, v)
                    if p:
                        pts.append(p)

    n = len(pts)
    if n < STATE_MIN_PTS:
        cx = cy = cz = sx = sy = xr = yr = zr = 0.0
    else:
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
        cx, cy, cz = st.mean(xs), st.mean(ys), st.mean(zs)
        sx = st.pstdev(xs) if n > 1 else 0.0
        sy = st.pstdev(ys) if n > 1 else 0.0
        xr = max(xs) - min(xs); yr = max(ys) - min(ys); zr = max(zs) - min(zs)

    in_bed = 1 if presence.get("in_bed") else 0
    occ1 = (presence.get("tof1") or {}).get("occupied", 0) or 0
    occ2 = (presence.get("tof2") or {}).get("occupied", 0) or 0
    # FEATURES = n_pts,cx,cy,cz,sx,sy,x_range,y_range,z_range,in_bed,occ1,occ2
    return [float(n), cx, cy, cz, sx, sy, xr, yr, zr,
            float(in_bed), float(occ1), float(occ2)]


class Detector:
    """lstm_ae.pt 로드 + 최근 SEQ 프레임 버퍼 + 재구성오차 점수."""

    def __init__(self, model_path=MODEL_PATH):
        # 모델이 GPU에서 저장됐어도 파이(CPU)에서 로드 가능하게 map_location 지정
        d = torch.load(model_path, weights_only=False, map_location="cpu")
        self.model = LSTMAE(len(FEATURES), d["hid"])
        self.model.load_state_dict(d["model"])
        self.model.eval()
        self.mu = np.asarray(d["mu"], dtype=np.float32)
        self.sd = np.asarray(d["sd"], dtype=np.float32)
        self.seq = int(d["seq"])
        self.thr = float(d["threshold"])
        self.buf = deque(maxlen=self.seq)

    def push(self, feat):
        self.buf.append([float(x) for x in feat])

    def score(self):
        """버퍼가 SEQ 만큼 차면 {err,threshold,ratio,status} 반환, 아니면 None."""
        if len(self.buf) < self.seq:
            return None
        X = np.asarray(self.buf, dtype=np.float32)
        Xn = (X - self.mu) / self.sd
        with torch.no_grad():
            x = torch.tensor(Xn[None]).float()
            err = float(((self.model(x) - x) ** 2).mean().item())
        ratio = err / self.thr if self.thr else 0.0
        if err > self.thr:
            status = "anomaly"
        elif ratio > 0.7:
            status = "warn"
        else:
            status = "normal"
        return {"err": round(err, 4), "threshold": round(self.thr, 4),
                "ratio": round(ratio, 3), "status": status,
                "filled": len(self.buf), "seq": self.seq}
