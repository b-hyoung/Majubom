"""
train_posture_v2.py — 새 설치 위치(2026-10-08~) 자세 모델 학습
==============================================================
입력: ../dataset_v2/tof_*.jsonl (record_posture.py 로 녹화, 세션마다 label/subject/cond/rep 포함)
출력: posture_model.joblib (운영 서버가 읽는 경로; 이전 모델은 archive/ 에 보관)
      posture_model_v2_report.json (평가 결과)

train_posture.py(이전 설치용)와 다른 점:
  - 두 센서 프레임을 순번이 아니라 시각 기준(250ms 이내, 한 번씩만)으로 짝짓는다.
  - 세션 앞뒤 1초는 자세가 흔들리는 구간이라 뺀다.
  - 평가는 시간 70/30 분할이 아니라 묶음 단위로 하나씩 빼고 학습/평가한다.
      --group rep     : 회차 단위(같은 사람·같은 날이면 낙관적)
      --group subject : 사람 단위(LOSO, 2명 이상일 때 의미 있음). 피험자 '없음'(빈 침대)은 회차로 나눠 각 묶음에 배분.
  - 추론(predict_posture.frame_vec)과 같게 roi.mask_offbed 를 적용한다(--no-roi 로 끔).

사용:
  python train_posture_v2.py                 # 회차 단위 평가 후 전체 데이터로 학습·저장
  python train_posture_v2.py --group subject
  python train_posture_v2.py --no-save       # 평가만
"""
import argparse, collections, glob, json, os
from datetime import datetime

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

import roi

HERE = os.path.dirname(os.path.abspath(__file__))
DDIR = os.path.join(HERE, "..", "dataset_v2")
ZONES = 64
TRIM_SEC = 1.0
PAIR_SEC = 0.25
LABELS = ["empty", "supine", "side_left", "side_right", "sitting", "edge_sit"]


def _ts(s):
    return datetime.fromisoformat(s).timestamp()


def _clean(d):
    return ([(-1 if (v is None or v < 0) else v) for v in d] + [-1] * ZONES)[:ZONES]


def load(use_roi):
    X, meta = [], []
    for f in sorted(glob.glob(os.path.join(DDIR, "tof_*.jsonl"))):
        rows = [json.loads(l) for l in open(f, encoding="utf-8")]
        if not rows:
            continue
        t0, t1 = min(_ts(r["t"]) for r in rows), max(_ts(r["t"]) for r in rows)
        a = sorted((_ts(r["t"]), _clean(r["distances_mm"])) for r in rows if r["sensor"] == "tof1")
        b = sorted((_ts(r["t"]), _clean(r["distances_mm"])) for r in rows if r["sensor"] == "tof2")
        j, used = 0, set()
        for t, d1 in a:
            if t < t0 + TRIM_SEC or t > t1 - TRIM_SEC or not b:
                continue
            while j + 1 < len(b) and abs(b[j + 1][0] - t) <= abs(b[j][0] - t):
                j += 1
            if j in used or abs(b[j][0] - t) > PAIR_SEC:
                continue
            used.add(j)
            d2 = b[j][1]
            if use_roi:
                d1, d2 = roi.mask_offbed("tof1", d1), roi.mask_offbed("tof2", d2)
            X.append(d1 + d2)
            r0 = rows[0]
            meta.append({k: r0.get(k) for k in ("label", "subject", "cond", "rep", "session")})
    return np.array(X, float), meta


def groups_for(meta, mode):
    if mode == "rep":
        return np.array([(m["rep"] - 1) % 3 for m in meta])
    people = sorted({m["subject"] for m in meta if m["subject"] != "없음"})
    idx = {p: i for i, p in enumerate(people)}
    n = max(len(people), 1)
    return np.array([idx[m["subject"]] if m["subject"] != "없음" else (m["rep"] - 1) % n for m in meta])


def make_model():
    return RandomForestClassifier(n_estimators=200, class_weight="balanced", n_jobs=-1, random_state=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["rep", "subject"], default="rep")
    ap.add_argument("--no-roi", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    X, meta = load(not args.no_roi)
    y = np.array([m["label"] for m in meta])
    g = groups_for(meta, args.group)
    print(f"샘플 {len(y)}쌍, 클래스 {dict(collections.Counter(y))}, 묶음({args.group}) {sorted(set(g.tolist()))}")

    pred = np.empty(len(y), dtype=object)
    folds = []
    for k in sorted(set(g.tolist())):
        tr, te = g != k, g == k
        if len(set(y[tr])) < 2 or te.sum() == 0:
            continue
        p = make_model().fit(X[tr], y[tr]).predict(X[te])
        pred[te] = p
        folds.append({"held_out": int(k), "n": int(te.sum()), "accuracy": accuracy_score(y[te], p),
                      "macro_f1": f1_score(y[te], p, average="macro")})
        print(f"  묶음 {k} 시험: 정확도 {folds[-1]['accuracy']:.3f}, macro-F1 {folds[-1]['macro_f1']:.3f}")
    done = np.array([p is not None for p in pred])
    labels = [l for l in LABELS if l in set(y)]
    cm = confusion_matrix(y[done], pred[done], labels=labels)
    print("혼동행렬(행=정답, 열=예측):", labels)
    print(cm)

    report = {"trained_at": datetime.now().isoformat(timespec="seconds"), "group": args.group, "roi": not args.no_roi,
              "n_pairs": int(len(y)), "class_counts": dict(collections.Counter(y.tolist())),
              "subjects": sorted({m["subject"] for m in meta}), "folds": folds,
              "overall_accuracy": accuracy_score(y[done], pred[done]) if done.any() else None,
              "confusion_labels": labels, "confusion": cm.tolist(),
              "mount": {"height_m": 1.2, "down_tilt_deg": {"tof1_foot": 50, "tof2_head": 30}},
              "note": "회차 단위 평가는 같은 사람·같은 날이라 일반화 성능이 아니다. 사람 단위(LOSO)가 기준."}
    with open(os.path.join(HERE, "posture_model_v2_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if args.no_save:
        return
    clf = make_model().fit(X, y)
    out = os.path.join(HERE, "posture_model.joblib")
    joblib.dump({"model": clf, "labels": sorted(set(y)), "zones": ZONES,
                 "features": "tof1_d0..63 + tof2_d0..63 (mm, -1=무효)", "filtered": False,
                 "roi": not args.no_roi, "version": "v2", "dataset": "TOF/dataset_v2",
                 "subjects": report["subjects"], "trained_at": report["trained_at"],
                 "mount": report["mount"]}, out)
    print(f"모델 저장: {out}")


if __name__ == "__main__":
    main()
