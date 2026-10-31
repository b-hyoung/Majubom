"""
train_lstm_ae_v2.py — 새 설치(2026-10-08~)용 ToF LSTM-AE 재학습
================================================================
입력: ../dataset_v2/tof_*.jsonl(record_posture.py 녹화) + server/tof_baseline.json(빈 침대 기준)
출력: lstm_ae.pt(서버 tof_anomaly.Detector가 읽음; 이전 모델은 archive/), lstm_ae_v2_report.json

이전 lstm_anomaly.py와 다른 점:
  - 특징은 서버 추론과 같은 tof_anomaly.frame_feature로 만든다(새 각도의 roi.py, 지금 빈 침대 기준값).
  - 두 센서 프레임을 시각 기준(250ms 이내)으로 짝지어 한 시점 = 한 특징 벡터. 서버도 tof2(머리 쪽)
    프레임이 들어올 때만 특징을 넣어 같은 속도(약 9~10Hz)로 맞춘다.
  - 재실·사람 칸 특징은 서버 occupied_zones와 같은 식으로 계산한다(빈 침대 대비 150mm 이상 가까움).
  - 검증: 회차(rep) 3으로 나눈 나머지가 0인 세션을 검증용으로 떼고, 검증 오차의 99백분위를 임계로 쓴다.
  - 센서 이상 점검: 학습에 안 쓴 '발 쪽 센서가 가려진 세션'(dataset_v2_lowrate_excluded의 S02)과
    '옛 각도 세션'(dataset_v2_tilt20_archive)의 이상 비율을 보고서에 남긴다.

정상 데이터는 1명(S01)·하루·정지 자세 위주다. "평소와 다름"을 찾는 참고 신호이며 낙상 판정이 아니다.
"""
import glob, json, os, sys, time
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import roi  # noqa: E402
from lstm_anomaly import LSTMAE, FEATURES  # noqa: E402
from tof_anomaly import frame_feature  # noqa: E402

TOF = os.path.join(HERE, "..")
BASELINE = os.path.join(TOF, "..", "server", "tof_baseline.json")
PAIR_SEC, TRIM_SEC = 0.25, 1.0
PRESENCE_DELTA_MM, ROI_MAX_MM = 150, 2300      # tof_server.occupied_zones 와 같은 값
SEQ, STRIDE, HID, EPOCHS = 36, 2, 48, 40      # 36쌍 ≈ 4초(약 9쌍/초)
torch.manual_seed(0); np.random.seed(0)


def _ts(s):
    return datetime.fromisoformat(s).timestamp()


def occupied(dist, base):
    return sum(1 for d, b in zip(dist, base)
               if d and d > 0 and b and 0 < b <= ROI_MAX_MM and d < b - PRESENCE_DELTA_MM)


def session_features(path, base):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    if not rows:
        return None, None
    t0, t1 = min(_ts(r["t"]) for r in rows), max(_ts(r["t"]) for r in rows)
    a = sorted((_ts(r["t"]), r["distances_mm"]) for r in rows if r["sensor"] == "tof1")
    b = sorted((_ts(r["t"]), r["distances_mm"]) for r in rows if r["sensor"] == "tof2")
    in_bed = 0 if rows[0]["label"] == "empty" else 1
    feats, j, used = [], 0, set()
    for t, d1 in a:
        if t < t0 + TRIM_SEC or t > t1 - TRIM_SEC or not b:
            continue
        while j + 1 < len(b) and abs(b[j + 1][0] - t) <= abs(b[j][0] - t):
            j += 1
        if j in used or abs(b[j][0] - t) > PAIR_SEC:
            continue
        used.add(j)
        d2 = b[j][1]
        latest = {"tof1": {"distances_mm": d1}, "tof2": {"distances_mm": d2}}
        presence = {"in_bed": in_bed, "tof1": {"occupied": occupied(d1, base["tof1"])},
                    "tof2": {"occupied": occupied(d2, base["tof2"])}}
        feats.append(frame_feature(latest, base, presence))
    return np.asarray(feats, dtype=np.float32), rows[0]


def windows(X, seq=SEQ, stride=STRIDE):
    return [X[i:i + seq] for i in range(0, len(X) - seq + 1, stride)]


def load_dir(folder, base):
    out = []
    for f in sorted(glob.glob(os.path.join(folder, "tof_*.jsonl"))):
        X, meta = session_features(f, base)
        if X is not None and len(X) >= SEQ:
            out.append((X, meta, os.path.basename(f)))
    return out


def errors(model, W, mu, sd):
    if not len(W):
        return np.array([])
    x = torch.tensor((np.asarray(W) - mu) / sd).float()
    with torch.no_grad():
        return ((model(x) - x) ** 2).mean(dim=(1, 2)).numpy()


def main():
    base = json.load(open(BASELINE, encoding="utf-8"))
    sessions = load_dir(os.path.join(TOF, "dataset_v2"), base)
    train_w, val_w = [], []
    for X, meta, _ in sessions:
        (val_w if meta["rep"] % 3 == 0 else train_w).extend(windows(X))
    allX = np.concatenate([X for X, _, _ in sessions])
    mu, sd = allX.mean(0), allX.std(0)
    sd[sd < 1e-6] = 1.0
    Wtr = torch.tensor((np.asarray(train_w) - mu) / sd).float()
    model = LSTMAE(len(FEATURES), HID)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    t = time.time()
    for ep in range(EPOCHS):
        model.train()
        perm = torch.randperm(len(Wtr))
        for i in range(0, len(Wtr), 64):
            xb = Wtr[perm[i:i + 64]]
            opt.zero_grad()
            loss = ((model(xb) - xb) ** 2).mean()
            loss.backward(); opt.step()
        if (ep + 1) % 10 == 0:
            model.eval()
            print(f"  epoch {ep + 1:3d}  train {float(loss):.4f}  val {errors(model, val_w, mu, sd).mean():.4f}")
    model.eval()
    ev = errors(model, val_w, mu, sd)
    thr = float(np.percentile(ev, 99))

    def ratio_over(folder, pick=lambda m: True):
        ss = [(X, m) for X, m, _ in load_dir(folder, base) if pick(m)]
        e = errors(model, [w for X, _ in ss for w in windows(X)], mu, sd)
        return {"sessions": len(ss), "windows": int(len(e)),
                "over_threshold": round(float((e > thr).mean()), 3) if len(e) else None,
                "median_err_ratio": round(float(np.median(e) / thr), 2) if len(e) else None}

    report = {
        "trained_at": datetime.now().isoformat(timespec="seconds"), "seconds": round(time.time() - t),
        "train_windows": len(train_w), "val_windows": len(val_w), "seq": SEQ, "threshold": round(thr, 4),
        "val_over_threshold": round(float((ev > thr).mean()), 3),
        "check_foot_sensor_blocked_S02": ratio_over(os.path.join(TOF, "dataset_v2_lowrate_excluded"),
                                                   lambda m: m.get("subject") == "S02"),
        "check_old_20deg_mount": ratio_over(os.path.join(TOF, "dataset_v2_tilt20_archive")),
        "note": "정상 데이터: S01 1명·하루·정지 자세. 검증 99백분위 임계라 정상에서도 약 1%는 이상으로 나온다.",
    }
    torch.save({"model": model.state_dict(), "features": FEATURES, "hid": HID, "seq": SEQ,
                "mu": mu.tolist(), "sd": sd.tolist(), "threshold": thr, "version": "v2",
                "dataset": "TOF/dataset_v2", "baseline": "server/tof_baseline.json"}, os.path.join(HERE, "lstm_ae.pt"))
    json.dump(report, open(os.path.join(HERE, "lstm_ae_v2_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
