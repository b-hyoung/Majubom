#!/usr/bin/env python3
"""
retrain_with_tabpfn.py — TabPFN 교정 라벨로 RF를 재학습하는 실제 메커니즘 (2026-09-19)
========================================================================================
설계 근거: docs/ai_plan.html N1 탭 "후속 설계 — 구조②(자기개선 루프)". 이 스크립트는 그 설계를
처음으로 코드화한 것이다 — RF가 저신뢰로 판정한 프레임을 TabPFN에게 다시 물어보고(단, TabPFN
자신도 확신할 때만 채택), 그 교정 라벨을 학습데이터에 더해 RF를 재학습한 뒤, "재학습된 RF가
원래 평가셋에서 더 잘하는가"를 실제로 검증한다.

정직하게 명시할 것 — 이 스크립트가 증명하는 것과 증명하지 못하는 것
------------------------------------------------------------------
우리에겐 실제 "라이브로 들어오는 신규 프레임"이 없다(라즈베리파이 배포·데이터 축적 전). 대신
기존 학습셋(2,320프레임, 한 사람·한 세션)을 시간순으로 셋으로 다시 쪼갠다:

  init_train (앞 56%) → pool(다음 14%, "신규 프레임"으로 가정) → holdout(마지막 30%, 원래 test셋과 동일)

`holdout`은 원래 train_posture.py의 test 분할과 **정확히 같은 999건**이라 기존 헤드라인 수치
(97.9%)와 비교 가능하다. `pool`은 이 스크립트 안에서만 "신규 데이터" 역할을 하는, 실제로는
같은 세션의 뒷부분 프레임이다 — 그래서 이 실험이 보여주는 건 "메커니즘이 올바르게 동작하는가"와
"이 특정 세션에서 방향성이 맞는가"이지, "다른 사람·다른 세션에서도 통한다"는 보장이 아니다
(이 프로젝트가 반복해서 명시해온 "단일세션 한계"가 여기도 그대로 적용됨).

또한 pool 프레임의 실제 정답 라벨(캡처 시 지시된 자세)을 알고 있지만, **재학습에는 절대 쓰지
않는다** — 실제 배포 상황에서는 새 프레임의 정답을 모르기 때문에, TabPFN이 예측한 라벨만 학습에
넣는다. 실제 정답은 오직 "TabPFN 교정 라벨이 실제로 맞았는가"를 사후 검증(QA)하는 데만 쓴다.

이중 게이트(더블 게이트) — RF 오류를 TabPFN 오류로 맞바꾸는 것을 막는 장치
------------------------------------------------------------------
  1) RF 신뢰도 < RF_CONF_TH(기본 0.60) 인 pool 프레임만 대상으로 삼는다.
  2) 그중에서도 TabPFN 자신의 신뢰도 >= TABPFN_CONF_TH(기본 0.90) 인 것만 "교정 라벨"로 채택한다.
     TabPFN도 100%가 아니므로(2026-09-18 실측 99.8%), 확신 없는 TabPFN 답까지 정답처럼 넣으면
     안 된다.

회귀 가드 — 재학습된 모델은 "기존보다 나쁘지 않을 때만" 채택
------------------------------------------------------------------
holdout 정확도가 기존(init_train만으로 학습한 RF) 대비 떨어지면 새 모델을 저장하지 않고
{"success": False, "error_type": "regression", ...}를 정직하게 반환한다(정직한 실패 원칙).

사용: python retrain_with_tabpfn.py
"""
import glob
import json
import os
import time
from datetime import datetime

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

import roi

HERE = os.path.dirname(os.path.abspath(__file__))
DDIR = os.path.join(HERE, "..", "dataset")
ZONES = 64
EXCLUDE = {"lying"}
RF_CONF_TH = 0.60
TABPFN_CONF_TH = 0.90
OUT_MODEL = os.path.join(HERE, "posture_model_retrained.joblib")
LOG_PATH = os.path.join(HERE, "retrain_log.jsonl")


def _ts(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def load_three_way(init_frac=0.56, pool_frac=0.70):
    """train_posture.load_dataset()와 동일한 페어링·ROI 로직을, 시간순으로 3구간
    (init_train/pool/holdout)으로 나누도록 확장한 버전. pool_frac 지점까지가 기존
    load_dataset()의 train(70%)과 정확히 같고, 그 뒤(holdout)가 기존 test(30%)와 정확히 같다
    — 그래야 "재학습 전/후" 비교가 기존 헤드라인 수치(97.9%)와 그대로 이어진다."""
    files = [f for f in sorted(glob.glob(os.path.join(DDIR, "*.jsonl"))) if "filtered" not in os.path.basename(f)]
    frames = {}
    for f in files:
        for ln in open(f, encoding="utf-8"):
            o = json.loads(ln)
            lbl = o["label"]
            if lbl in EXCLUDE:
                continue
            d = [(-1 if (v is None or v < 0) else v) for v in o["distances_mm"]]
            d = (d + [-1] * ZONES)[:ZONES]
            d = roi.mask_offbed(o["sensor"], d)
            frames.setdefault(lbl, {"tof1": [], "tof2": []})
            frames[lbl][o["sensor"]].append((_ts(o["t"]), d))

    Xi, yi, Xp, yp, Xh, yh = [], [], [], [], [], []
    per_class = {}
    for lbl, dd in sorted(frames.items()):
        t1 = [d for _, d in sorted(dd["tof1"])]
        t2 = [d for _, d in sorted(dd["tof2"])]
        n = min(len(t1), len(t2))
        if n < 20:
            continue
        cut_i, cut_p = int(n * init_frac), int(n * pool_frac)
        for k in range(n):
            vec = t1[k] + t2[k]
            if k < cut_i:
                Xi.append(vec); yi.append(lbl)
            elif k < cut_p:
                Xp.append(vec); yp.append(lbl)
            else:
                Xh.append(vec); yh.append(lbl)
        per_class[lbl] = n
    return (np.array(Xi), np.array(yi), np.array(Xp), np.array(yp),
            np.array(Xh), np.array(yh), per_class)


def main():
    print("[1/6] 데이터 3분할 로드 (init_train / pool=\"신규 프레임\" / holdout=기존 test와 동일)")
    Xi, yi, Xp, yp, Xh, yh, per_class = load_three_way()
    print(f"  init_train={len(Xi)}  pool={len(Xp)}  holdout={len(Xh)}  (클래스별 원본 프레임: {per_class})")

    print("\n[2/6] RF_v1 학습 (init_train만) + holdout 기준선 측정")
    rf1 = RandomForestClassifier(n_estimators=200, max_depth=None, n_jobs=-1,
                                  random_state=42, class_weight="balanced")
    rf1.fit(Xi, yi)
    acc_holdout_before = accuracy_score(yh, rf1.predict(Xh))
    print(f"  RF_v1 holdout 정확도 = {acc_holdout_before*100:.2f}%")

    print(f"\n[3/6] pool({len(Xp)}건)에서 RF_v1 신뢰도 < {RF_CONF_TH} 인 프레임 탐색")
    proba_pool = rf1.predict_proba(Xp)
    conf_pool = proba_pool.max(axis=1)
    pred_pool = rf1.classes_[proba_pool.argmax(axis=1)]
    low_mask = conf_pool < RF_CONF_TH
    n_low = int(low_mask.sum())
    print(f"  저신뢰 프레임: {n_low}/{len(Xp)} ({n_low/max(len(Xp),1)*100:.1f}%)")

    if n_low == 0:
        print("  저신뢰 프레임이 없어 TabPFN 교정 없이 종료(재학습 불필요).")
        return

    print(f"\n[4/6] TabPFN으로 저신뢰 {n_low}건 재판정 (TabPFN 자신의 신뢰도 >= {TABPFN_CONF_TH} 만 채택)")
    from tabpfn import TabPFNClassifier
    t0 = time.time()
    tpfn = TabPFNClassifier(device="cpu", ignore_pretraining_limits=True)
    tpfn.fit(Xi, yi)
    proba_tp = tpfn.predict_proba(Xp[low_mask])
    t_infer = time.time() - t0
    conf_tp = proba_tp.max(axis=1)
    pred_tp = tpfn.classes_[proba_tp.argmax(axis=1)]
    accept_mask = conf_tp >= TABPFN_CONF_TH
    n_accept = int(accept_mask.sum())
    print(f"  TabPFN 추론 {t_infer:.1f}s, 이중게이트 통과(채택) {n_accept}/{n_low}건")

    # QA(정직성 검증용) — 실제 정답과 비교. 재학습 입력으로는 절대 쓰지 않음.
    true_low = yp[low_mask]
    tp_vs_truth = accuracy_score(true_low[accept_mask], pred_tp[accept_mask]) if n_accept else float("nan")
    rf_vs_truth_same_frames = accuracy_score(true_low[accept_mask], pred_pool[low_mask][accept_mask]) if n_accept else float("nan")
    print(f"  [QA, 재학습에 미사용] 채택된 교정 라벨이 실제 정답과 일치: {tp_vs_truth*100:.1f}%  "
          f"(같은 프레임에서 RF 원래 예측의 정답 일치율: {rf_vs_truth_same_frames*100:.1f}%)")

    if n_accept == 0:
        print("  이중게이트를 통과한 교정 라벨이 없어 재학습 없이 종료.")
        return

    print(f"\n[5/6] 교정 라벨 {n_accept}건을 학습데이터에 추가해 RF_v2 재학습")
    X_correct = Xp[low_mask][accept_mask]
    y_correct = pred_tp[accept_mask]           # TabPFN 예측 라벨(실제 정답 아님 — 배포와 동일 조건)
    X_aug = np.vstack([Xi, X_correct])
    y_aug = np.concatenate([yi, y_correct])
    rf2 = RandomForestClassifier(n_estimators=200, max_depth=None, n_jobs=-1,
                                  random_state=42, class_weight="balanced")
    rf2.fit(X_aug, y_aug)
    acc_holdout_after = accuracy_score(yh, rf2.predict(Xh))
    print(f"  RF_v2 holdout 정확도 = {acc_holdout_after*100:.2f}%  (RF_v1 대비 {acc_holdout_after-acc_holdout_before:+.2%}p)")

    print("\n[6/6] 회귀 가드 판정")
    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "n_init_train": len(Xi), "n_pool": len(Xp), "n_holdout": len(Xh),
        "rf_conf_threshold": RF_CONF_TH, "tabpfn_conf_threshold": TABPFN_CONF_TH,
        "n_low_confidence_in_pool": n_low, "n_corrections_accepted": n_accept,
        "correction_label_accuracy_vs_true_QA_ONLY": round(tp_vs_truth, 4),
        "rf_original_accuracy_on_same_frames_QA_ONLY": round(rf_vs_truth_same_frames, 4),
        "holdout_acc_before": round(acc_holdout_before, 4),
        "holdout_acc_after": round(acc_holdout_after, 4),
    }
    if acc_holdout_after + 1e-9 < acc_holdout_before:
        result.update(success=False, error_type="regression",
                       message="재학습 후 holdout 정확도가 떨어져 모델을 교체하지 않음.")
        print(f"  [정직한 실패] {result['message']} ({acc_holdout_before*100:.2f}% → {acc_holdout_after*100:.2f}%)")
    else:
        result.update(success=True, model_path=OUT_MODEL)
        joblib.dump({"model": rf2, "labels": sorted(set(y_aug)), "zones": ZONES,
                     "source": "retrain_with_tabpfn.py", "base_train_n": len(Xi),
                     "tabpfn_corrections_n": n_accept}, OUT_MODEL)
        print(f"  [통과] holdout 정확도 유지/개선 — {OUT_MODEL} 저장 완료")

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"\n결과 로그: {LOG_PATH}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
