# 관련 연구 — 보행 패턴 기반 낙상 예측 (우리 시스템과 대조) — 2026-07-06

> 목적: 보행으로 낙상을 예측한 선행연구를 근거로 확보하고, 우리(mmWave IWR6843 점군 +
> LSTM/z-score) 접근과 정면 대조. **논문은 "방향의 근거"이지 우리 성능 근거가 아님**(정직 원칙).

## 최근접 2편
- **[논문A] Micro-Doppler + LSTM 보행분류** — 모델이 가장 가까움 (LSTM 시계열).
  PMC8197185 · https://pmc.ncbi.nlm.nih.gov/articles/PMC8197185/
- **[논문B] Micro-Doppler + SVM 낙상위험** — 목표가 가장 가까움 (레이더→faller/non-faller).
  PMC8839600 · https://pmc.ncbi.nlm.nih.gov/articles/PMC8839600/

## 정면 대조

| 항목 | 논문A (LSTM 보행) | 논문B (낙상위험) | **우리 (Majubom)** |
|---|---|---|---|
| 레이더 | 24GHz CW micro-Doppler (ILT BSS-110) | 24GHz CW micro-Doppler | **60GHz FMCW IWR6843 AOP** |
| 신호표현 | 도플러 스펙트로그램→속도포락선 | 스펙트로그램→속도파라미터 | **3D 점군(1020)+트랙(1010)** |
| 특징 | vu/vl/vm 속도포락선 | 몸·다리 평균속도+변동 4종 | speed·sway·stride·freeze·cv + 점군시퀀스 |
| 모델 | LSTM 400셀 1층 | SVM(가우시안) | LSTM(5→24) + z-score baseline |
| 데이터 | 300명(청년87/노인213) | 33명(faller14/non19)+시뮬480 | **1명·1세션**(이탈 569시퀀스) |
| 라벨 | 청년 vs 노인 | 1년내 낙상이력 faller | 이탈임박(ToF자동)+z-score |
| 과제 | 나이 보행분류 | 낙상위험 분류 | 침대이탈 예측 + 보행 위험도 |
| 성능 | 94.9% | 정확도 78.8%/민감 64%/특이 82% | 이탈 F1 ~45 |
| 설치 | 경로 앞 0.86m | 정면(높이 미기재) | **코너 3m/15° 대각선** |
| 보행 | 10m 직선 정면 | 10m 직선 정면 | **방 안 자유보행+침대** |

## 시사점

**빌려올 근거**
- 논문A의 **LSTM 접근 = 우리 이탈 LSTM의 직접 선례** (속도 시계열→LSTM 94.9%).
- 논문B: **다리속도 변동(std)이 faller 구분 핵심** → 우리 `sway/stride_cv/freeze` 지표가 옳은 방향.

**우리가 구조적으로 유리**
- 둘 다 micro-Doppler → **정면 10m 직선보행 필수**(radial 속도만). 논문B 명시 한계="비정면 보행 측정 불가".
- 우리는 **3D 점군** → 방 안 어느 방향 보행도 위치추적 + 침대맥락·이탈·자유보행. 재택/병실 실사용에 적합.
- 60GHz FMCW = 24GHz CW보다 거리분해능↑, 점군 생성 가능.

**정직한 열세**
- 데이터·라벨: 저들 33~300명 **실제 낙상이력 코호트** vs 우리 1명·1세션·프록시 라벨.
- 성능 직접비교 불가(과제·지표 상이): 저들 78~95% 정확도 vs 우리 F1 45.

**한 줄 결론:** 우리 접근(보행 시계열+LSTM, 속도변동 특징)은 이 두 논문으로 **방법·특징이 검증**됨.
차별점은 **micro-Doppler의 정면보행 제약을 3D 점군으로 푼 것**. 남은 건 **다인·낙상이력 데이터 검증**.

## 기타 참고 (보행지표 기반, 특징 설계)
- XGBoost 보행 낙상위험 (stride length·speed·stance) — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8190134/
- 시계열 보행특징+ML (Frontiers 2024, LightGBM 96%) — https://pmc.ncbi.nlm.nih.gov/articles/PMC11389313/
- 국소동적안정성 1년 전향적 낙상예측 — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5944953/
