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

실측 결과(2026-09-18, 이 저장소 환경 — priorlabs.ai 계정 인증 완료 후 재실측):
  - RF: 정확도 97.9%(기존 기록과 소수점까지 동일 — 재현 확인), 학습 0.31s, 추론 999건에 37.2ms.
  - TabPFN: `TABPFN_TOKEN` 환경변수로 인증 게이트를 통과해 실제로 실행 성공.
    정확도 99.8%(RF가 놓치던 edge_sit recall 0.910→1.000, empty precision 0.882→1.000까지
    거의 모든 오분류를 해소), 학습 1.93s. 그러나 **추론이 999건에 309.5초(1건당 약 310ms) —
    RF 대비 8,310배 느림**(CPU 실행, device="cpu"). 정확도는 TabPFN이 뚜렷하게 우세하지만,
    이 정도 지연은 라즈베리파이에서 매 ToF 프레임(수백 ms~초 단위 주기)마다 실시간으로
    자세를 분류해야 하는 이 프로젝트의 용도에는 결정적으로 부적합하다 — 한 프레임 판정에
    0.3초씩 걸리면 실시간 파이프라인이 밀린다. 결론: **정확도만 보면 TabPFN이 이기지만,
    실시간·엣지 배포 제약 때문에 RF를 유지한다.** (GPU를 쓰면 훨씬 빨라질 수 있으나, 이
    프로젝트의 게이트웨이는 GPU 없는 Raspberry Pi다 — CPU 실측이 실제 배포 조건과 일치.)

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
