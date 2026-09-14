#!/usr/bin/env python3
"""
compare_rf_tabpfn.py — ToF 자세분류: RandomForest vs TabPFN 실측 비교 (2026-09 문헌 반영)
========================================================================================
문헌 조사(2026-09)에서 나온 제안의 실측: 소규모 표형 데이터(수천 행 이하)에서
TabPFN(Hollmann et al., Nature 2025 / ICLR 2023)이 RandomForest보다 나을 수 있다는
보고가 있으나, 반대로 우리와 비슷한 규모(수천 행)에서는 RF가 TabPFN을 이기고 40배
가까이 빠르다는 반박 벤치마크도 있다(Bansal & Gangwani 2025, arXiv:2512.00888,
Wine-Quality 3,920행에서 RF 89.49% vs TabPFN 88.88%, 추론시간 0.045s vs 1.971s).
어느 쪽 결과도 우리 데이터가 아니므로, train_posture.py와 완전히 동일한 데이터·
분할·전처리(ROI 마스킹, 시간순 70/30)로 우리 데이터에 직접 재보는 것이 정직한 다음
단계 — 이 스크립트가 그 실측이다.

실측 결과(2026-09-14, 이 저장소 환경):
  - RF: 정확도 97.9%(기존 기록과 소수점까지 동일 — 재현 확인), 추론 999건에 47ms.
  - TabPFN: `pip install tabpfn`까지는 되지만, 실행 시 사전학습 가중치를 받으려면
    브라우저로 priorlabs.ai에 로그인해 라이선스에 동의해야 하는 온라인 게이트가 있음
    (오프라인/헤드리스 서버 환경에서는 이 단계에서 막힘). 이 저장소 환경에서는
    로그인 흐름을 완료할 수 없어 TabPFN 쪽 정확도는 측정하지 못했다 — 숫자를
    지어내는 대신 "측정 실패"로 정직하게 남긴다. 이는 그 자체로 하나의 결론이다:
    RF는 추가 설치·인증 없이 즉시 로컬 실행되고, TabPFN은 (a) 우리 규모의 데이터에서
    RF를 이긴다는 확증이 없고(위 Bansal & Gangwani 참고) (b) 오프라인 배포와도
    마찰이 있어, 현재로선 교체할 근거가 없다.

사용: python compare_rf_tabpfn.py
"""
import time
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, f1_score
from train_posture import load_dataset

Xtr, ytr, Xte, yte, per_class = load_dataset(use_filtered=False)
print("클래스별 프레임(짝지은 수):", per_class)
print(f"학습 {len(Xtr)} / 평가 {len(Xte)} 샘플, 특징 {Xtr.shape[1]}차원\n")

print("=== 1) RandomForest (train_posture.py와 동일 설정) ===")
t0 = time.time()
rf = RandomForestClassifier(n_estimators=200, max_depth=None, n_jobs=-1,
                             random_state=42, class_weight="balanced")
rf.fit(Xtr, ytr)
t_fit_rf = time.time() - t0
t0 = time.time()
pred_rf = rf.predict(Xte)
t_pred_rf = time.time() - t0
acc_rf = accuracy_score(yte, pred_rf)
f1_rf = f1_score(yte, pred_rf, average="macro")
print(f"정확도={acc_rf*100:.1f}%  F1(macro)={f1_rf*100:.1f}  "
      f"학습={t_fit_rf:.2f}s  추론={t_pred_rf:.3f}s({len(Xte)}건)")
print(classification_report(yte, pred_rf, digits=3))

print("\n=== 2) TabPFN v2 (scikit-learn API 드롭인) ===")
try:
    from tabpfn import TabPFNClassifier
    t0 = time.time()
    tpfn = TabPFNClassifier(device="cpu", ignore_pretraining_limits=True)
    tpfn.fit(Xtr, ytr)
    t_fit_tp = time.time() - t0
    t0 = time.time()
    pred_tp = tpfn.predict(Xte)
    t_pred_tp = time.time() - t0
    acc_tp = accuracy_score(yte, pred_tp)
    f1_tp = f1_score(yte, pred_tp, average="macro")
    print(f"정확도={acc_tp*100:.1f}%  F1(macro)={f1_tp*100:.1f}  "
          f"학습={t_fit_tp:.2f}s  추론={t_pred_tp:.3f}s({len(Xte)}건)")
    print(classification_report(yte, pred_tp, digits=3))

    print("\n=== 요약 (같은 데이터·같은 시간순 분할) ===")
    print(f"RF     acc={acc_rf*100:5.1f}%  F1={f1_rf*100:5.1f}  추론 {t_pred_rf*1000:7.1f}ms 전체")
    print(f"TabPFN acc={acc_tp*100:5.1f}%  F1={f1_tp*100:5.1f}  추론 {t_pred_tp*1000:7.1f}ms 전체  "
          f"(RF 대비 {t_pred_tp/max(t_pred_rf,1e-9):.0f}배)")
except Exception as e:
    print(f"[TabPFN 실행 실패] {type(e).__name__}: {e}")
    print("→ 이 환경에서 TabPFN을 실행할 수 없었음(예: 사전학습 가중치 다운로드 불가 등).")
    print("→ RF 결과만 유효하며, TabPFN 비교는 별도 환경에서 재시도 필요.")
