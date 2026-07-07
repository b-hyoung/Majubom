#!/usr/bin/env python3
"""
자세/재실 분류 모델 학습 (로컬 RandomForest)
================================================
입력: ../dataset/tof_<label>_*.jsonl  (capture_dataset.py 로 수집한 것)
특징: 한 시점의 [tof1 d0..d15, tof2 d0..d15] = 32차원 거리(mm)
      (두 센서를 시간순으로 짝지어 32차원 벡터 구성; 무효값 -1 그대로 사용)
출력: posture_model.joblib  (모델 + 클래스 목록 + 메타)

사용:
  python train_posture.py                # dataset 전체로 학습/평가
  python train_posture.py --filtered     # median 필터본(w5)으로 학습
  python train_posture.py --split random # (기본은 시간분할; random은 낙관적)

주의: 지금 데이터는 "한 사람, 한 세션"이라 프레임끼리 상관이 큽니다.
      → 시간분할 평가라도 실제 일반화 성능보다 낙관적일 수 있음.
      배포 정확도는 '다른 사람/다른 세션' 데이터로 재평가해야 함.
"""
import json, glob, os, sys, argparse
import numpy as np
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
import joblib
import roi                # 침대 폭 ROI (침대 밖 존 마스킹)

HERE  = os.path.dirname(os.path.abspath(__file__))
DDIR  = os.path.join(HERE, "..", "dataset")
ZONES = 64
EXCLUDE = {"lying"}       # 초기 캡처(라벨 모호) 제외. 포함하려면 비우면 됨.
USE_ROI = True            # 침대 밖 존 -1 처리 (main에서 --no-roi 로 끔)


def _ts(s):
    try: return datetime.fromisoformat(s).timestamp()
    except Exception: return 0.0


def load_dataset(use_filtered=False):
    """클래스별로 tof1/tof2 프레임을 시간순 정렬 후 인덱스로 짝지어 32차원 샘플 생성."""
    pat = "*_filtered_w5.jsonl" if use_filtered else "*.jsonl"
    files = sorted(glob.glob(os.path.join(DDIR, pat)))
    if not use_filtered:
        files = [f for f in files if "filtered" not in os.path.basename(f)]
    frames = {}   # label -> {tof1:[(t,dist)], tof2:[(t,dist)]}
    for f in files:
        for ln in open(f, encoding="utf-8"):
            o = json.loads(ln)
            lbl = o["label"]
            if lbl in EXCLUDE:
                continue
            d = [(-1 if (v is None or v < 0) else v) for v in o["distances_mm"]]
            d = (d + [-1] * ZONES)[:ZONES]
            if USE_ROI:
                d = roi.mask_offbed(o["sensor"], d)   # 침대 밖 존 → -1
            frames.setdefault(lbl, {"tof1": [], "tof2": []})
            frames[lbl][o["sensor"]].append((_ts(o["t"]), d))

    Xtr, ytr, Xte, yte = [], [], [], []
    per_class = {}
    for lbl, dd in sorted(frames.items()):
        t1 = [d for _, d in sorted(dd["tof1"])]
        t2 = [d for _, d in sorted(dd["tof2"])]
        n = min(len(t1), len(t2))
        if n < 20:
            print(f"[warn] {lbl}: 짝지을 프레임 부족({n}) — 건너뜀"); continue
        cut = int(n * 0.7)                      # 앞 70% 학습 / 뒤 30% 평가 (시간분할)
        for k in range(n):
            vec = t1[k] + t2[k]                 # 32차원
            if k < cut: Xtr.append(vec); ytr.append(lbl)
            else:       Xte.append(vec); yte.append(lbl)
        per_class[lbl] = n
    return (np.array(Xtr), np.array(ytr), np.array(Xte), np.array(yte), per_class)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filtered", action="store_true", help="median 필터본으로 학습")
    ap.add_argument("--split", choices=["time", "random"], default="time")
    ap.add_argument("--no-roi", action="store_true", help="침대 ROI 마스킹 끄기")
    args = ap.parse_args()
    global USE_ROI
    USE_ROI = not args.no_roi
    print(f"[설정] 침대 ROI 마스킹: {'ON' if USE_ROI else 'OFF'}")

    Xtr, ytr, Xte, yte, per_class = load_dataset(args.filtered)
    print("클래스별 프레임(짝지은 수):", per_class)
    print(f"학습 {len(Xtr)} / 평가 {len(Xte)} 샘플, 특징 {Xtr.shape[1]}차원\n")

    if args.split == "random":
        from sklearn.model_selection import train_test_split
        X = np.vstack([Xtr, Xte]); y = np.concatenate([ytr, yte])
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3,
                                              stratify=y, random_state=42)
        print("[주의] random 분할 — 프레임 상관으로 정확도가 낙관적임\n")

    clf = RandomForestClassifier(n_estimators=200, max_depth=None,
                                 n_jobs=-1, random_state=42, class_weight="balanced")
    clf.fit(Xtr, ytr)
    pred = clf.predict(Xte)

    acc = accuracy_score(yte, pred)
    print(f"=== 정확도(테스트): {acc*100:.1f}% ===\n")
    print("분류 리포트:")
    print(classification_report(yte, pred, digits=3))
    labels = sorted(set(ytr))
    print("혼동행렬 (행=정답, 열=예측):")
    print("labels:", labels)
    print(confusion_matrix(yte, pred, labels=labels))

    out = os.path.join(HERE, "posture_model.joblib")
    joblib.dump({"model": clf, "labels": labels, "zones": ZONES,
                 "features": "tof1_d0..15 + tof2_d0..15 (mm, -1=무효)",
                 "filtered": args.filtered, "roi": USE_ROI}, out)
    print(f"\n모델 저장: {out}")


if __name__ == "__main__":
    main()
