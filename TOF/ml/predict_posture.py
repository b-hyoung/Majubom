#!/usr/bin/env python3
"""
자세 실시간 예측 — 학습된 모델로 서버의 /tof/latest 를 읽어 자세를 출력.
================================================================
사용:
  python predict_posture.py                          # localhost:5001 폴링(파이에서)
  python predict_posture.py --server http://192.168.6.10:5001
  python predict_posture.py --once                   # 한 번만 예측하고 종료
  python predict_posture.py --interval 0.3           # 폴링 주기(초)

다른 코드에서 함수로 쓰기:
  from predict_posture import load_model, predict
  model = load_model()
  label, conf = predict(model, latest_json)   # latest_json = {"tof1":{...},"tof2":{...}}
"""
import json, os, time, argparse, urllib.request
import numpy as np
import joblib
import roi                # 침대 폭 ROI (학습과 동일하게 추론에도 적용)

HERE  = os.path.dirname(os.path.abspath(__file__))
ZONES = 64
MODEL_PATH = os.path.join(HERE, "posture_model.joblib")
# 2026-11-04: 새 침상(Bed C) S01+S02로 노트북에서 학습한 CNN 특징 + Random Forest(posture_cnn_bedC_20261104/README.md).
# 폴더가 있고 torch를 읽을 수 있으면 이것을 쓰고, 아니면 예전 RF(posture_model.joblib, archive/에도 사본)로 돌아간다.
CNN_BUNDLE = os.path.join(HERE, "posture_cnn_bedC_20261104")
# 2026-11-04 저녁: S01 재촬영·S02·S05 세 사람 실측으로 학습한 원시 거리 RF(사람 단위 61%, CNN+XGBoost 47%보다 높음).
# 21시에 걸터앉음 추가 학습 _edge로 바꿨다가 이불·베개 있는 빈 침대를 걸터앉음(0.95~1.0)으로 오판해 21시 40분 _3p로 되돌림(_edge 폴더는 보관만).
RF3_BUNDLE = os.path.join(HERE, "posture_rf_bedC_20261104_edge")
RF3_PREV_BUNDLE = os.path.join(HERE, "posture_rf_bedC_20261104_3p")   # 현재 운영 모델(세 사람 실측 RF)


class RawRfPosture:
    """원시 128거리(tof1 발치 64 + tof2 머리맡 64, 실패 -1) → RF. 클래스 번호는 preprocessing.json 라벨 순서."""
    majubom_roi = False

    def __init__(self, bundle):
        cfg = json.load(open(os.path.join(bundle, "preprocessing.json"), encoding="utf-8"))
        if cfg.get("roi_mask"):
            raise ValueError("ROI 마스킹 묶음은 지원하지 않음")
        self.classes_ = np.array(cfg["labels"])
        self.rf = joblib.load(os.path.join(bundle, "raw_rf.joblib"))
        if hasattr(self.rf, "n_jobs"):
            self.rf.n_jobs = 1
        if [int(c) for c in self.rf.classes_] != list(range(len(self.classes_))):
            raise ValueError("분류기 클래스 순서가 preprocessing.json과 다름")

    def predict_proba(self, v):
        return self.rf.predict_proba(np.asarray(v, dtype=float).reshape(1, -1))


class CnnPosture:
    """CNN 특징(64) → RF. predict()가 넘기는 128칸 벡터(-1 = 실패)를 학습과 같은 [1,2,3,8,8] 입력으로 바꾼다.
    빈 침대 기준 차이 채널은 학습 때 쓴 training_baseline.json으로 고정한다 — 운영 기준(tof_baseline.json)이
    자동 갱신돼도 모델 입력은 바뀌지 않는다(모델의 빈 침대 판정으로 기준을 다시 잡으므로 순환을 막음)."""
    majubom_roi = False

    def __init__(self, bundle):
        import torch
        torch.set_num_threads(1)
        self._torch = torch
        cfg = json.load(open(os.path.join(bundle, "preprocessing.json"), encoding="utf-8"))
        if cfg.get("roi_mask"):
            raise ValueError("ROI 마스킹 묶음은 지원하지 않음")
        self.classes_ = np.array(cfg["labels"])
        self.base = np.array([cfg["baseline"][s] for s in ("tof1", "tof2")], dtype=np.float32)
        self.cnn = torch.jit.load(os.path.join(bundle, "cnn_cpu.pt"), map_location="cpu").eval()
        self.rf = joblib.load(os.path.join(bundle, "cnn_rf.joblib"))
        if hasattr(self.rf, "n_jobs"):
            self.rf.n_jobs = 1
        if [int(c) for c in self.rf.classes_] != list(range(len(self.classes_))):
            raise ValueError("분류기 클래스 순서가 preprocessing.json과 다름")

    def predict_proba(self, v):
        raw = np.asarray(v, dtype=np.float32).reshape(2, 64)
        valid = np.isfinite(raw) & (raw > 0) & (raw <= 4000)
        bv = (self.base > 0) & (self.base <= 4000)
        dist = np.where(valid, np.clip(raw, 0, 4000) / 4000, 0)
        delta = np.where(valid & bv, np.clip(self.base - raw, -4000, 4000) / 4000, 0)
        x = np.stack([dist, delta, valid.astype(np.float32)], axis=1).reshape(1, 2, 3, 8, 8).astype(np.float32)
        with self._torch.inference_mode():
            f = self.cnn.features(self._torch.from_numpy(x)).numpy()
        return self.rf.predict_proba(f)


def load_model(path=MODEL_PATH):
    if path == MODEL_PATH:
        for cls, bundle in ((RawRfPosture, RF3_PREV_BUNDLE), (CnnPosture, CNN_BUNDLE)):
            if not os.path.isdir(bundle):
                continue
            try:
                return cls(bundle)
            except Exception as e:
                print(f"[posture] {os.path.basename(bundle)}를 못 읽어 다음 모델로: {e}")
    d = joblib.load(path)
    model = d["model"]
    # 한 프레임씩 예측하므로 병렬(n_jobs=-1)은 스레드 생성 비용만 커진다. Pi 서버에서 응답이 0.5초대로 느려져
    # ESP 수신까지 밀렸다(2026-10-08) → 추론은 단일 스레드. 결과는 같다.
    if hasattr(model, "n_jobs"):
        model.n_jobs = 1
    # 새 침상(dataset_v3, 2026-11-02~)은 roi.py가 예전 설치 기하라 ROI 없이 학습했다 → 모델에 저장된 설정을 따른다
    model.majubom_roi = d.get("roi", True)
    return model


def _pad(a):
    a = [(-1 if (v is None or v < 0) else v) for v in (a or [])]
    return (a + [-1] * ZONES)[:ZONES]


def frame_vec(latest, use_roi=True):
    """latest = {"tof1":{"distances_mm":[..]}, "tof2":{...}} → 128차원 벡터(없으면 None).
    학습과 동일하게 침대 밖 존은 ROI로 -1 처리(use_roi=False면 원본 그대로)."""
    t1 = (latest.get("tof1") or {}).get("distances_mm")
    t2 = (latest.get("tof2") or {}).get("distances_mm")
    if not t1 or not t2:
        return None
    d1, d2 = _pad(t1), _pad(t2)
    if use_roi:
        d1, d2 = roi.mask_offbed("tof1", d1), roi.mask_offbed("tof2", d2)
    return np.array(d1 + d2).reshape(1, -1)


def predict(model, latest):
    """(label, confidence) 반환. 데이터 없으면 (None, None)."""
    v = frame_vec(latest, getattr(model, "majubom_roi", True))
    if v is None:
        return None, None
    proba = model.predict_proba(v)[0]
    i = int(np.argmax(proba))
    return str(model.classes_[i]), float(proba[i])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=os.environ.get("TOF_SERVER", "http://localhost:5001"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=0.5)
    a = ap.parse_args()

    model = load_model()
    url = a.server.rstrip("/") + "/tof/latest"
    print(f"[predict] 모델 로드됨. 서버 {url} 폴링 (Ctrl+C 종료)\n")
    while True:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                latest = json.loads(r.read())
            lbl, p = predict(model, latest)
            print(f"자세: {lbl:<11} (확신 {p*100:4.1f}%)" if lbl else "데이터 없음", flush=True)
        except Exception as e:
            print(f"오류: {e}", flush=True)
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
