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


def load_model(path=MODEL_PATH):
    d = joblib.load(path)
    return d["model"]


def _pad(a):
    a = [(-1 if (v is None or v < 0) else v) for v in (a or [])]
    return (a + [-1] * ZONES)[:ZONES]


def frame_vec(latest):
    """latest = {"tof1":{"distances_mm":[..]}, "tof2":{...}} → 32차원 벡터(없으면 None).
    학습과 동일하게 침대 밖 존은 ROI로 -1 처리."""
    t1 = (latest.get("tof1") or {}).get("distances_mm")
    t2 = (latest.get("tof2") or {}).get("distances_mm")
    if not t1 or not t2:
        return None
    d1 = roi.mask_offbed("tof1", _pad(t1))
    d2 = roi.mask_offbed("tof2", _pad(t2))
    return np.array(d1 + d2).reshape(1, -1)


def predict(model, latest):
    """(label, confidence) 반환. 데이터 없으면 (None, None)."""
    v = frame_vec(latest)
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
