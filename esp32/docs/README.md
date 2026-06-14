# 📚 MajuBom CSI/HRV 자료 모음 (HTML 뷰어)

> 윈도우에서 작업한 HTML 뷰어들을 macOS에서도 볼 수 있게 복사.
> 모두 단일 HTML 파일로 작동 (Tailwind CDN 외 외부 의존 X).
> 작성: 2026-06-15

---

## 🚀 실행 방법

**모든 HTML은 그냥 더블클릭 또는 `open` 명령으로 열림.**

### macOS
```bash
cd esp32/docs

open paper_review/index.html        # 논문 A 한글 뷰어
open paper_review_B/index.html      # 논문 B 한글 뷰어
open paper_review_C/index.html      # 논문 C 한글 뷰어 (작업 2)
open terms/index.html               # 용어사전
open dday_demo/index.html           # 발표 슬라이드
```

### Linux
```bash
xdg-open paper_review/index.html
```

### 윈도우
```powershell
start paper_review/index.html
# 또는 파일 더블클릭
```

**조건**: 인터넷 연결 (Tailwind CSS CDN 로드용). 오프라인이면 스타일 깨짐.

---

## 📁 폴더 구성 + 각 HTML 안내

### 1. `paper_review/` — 논문 A 한글 뷰어 (작업 1 · HRV)
- **논문**: Shirakami & Sato, "Heart Rate Variability Extraction using Commodity Wi-Fi Devices via Time Domain Signal Processing", **IEEE BHI 2021** (DOI 10.1109/BHI50953.2021.9508523)
- **PDF**: `c:/Users/ACE/Desktop/개개비/참고문헌/Heart Rate Variability Extraction...pdf`
- **내용**: Intel 5300 다중 안테나 + Quotient 모델 + SDNN-based subcarrier 선택 → RMSE 130ms
- **우리한테 의미**: SOTA, 발표 시 인용 가능 (peer-reviewed)
- **한계 정정**: README의 "R-R 오차 2.53~4.83%, 복조 MSE 0.35%" 수치는 본문에 **없음** (환각). 실제는 RMSE 130ms, SDNN 8.8% 오차

### 2. `paper_review_B/` — 논문 B 한글 뷰어 (작업 1 · HRV, ⚠️ 비심사)
- **논문**: Chaudhari et al., "Extracting Heart-Rate Variability Indicators from Wi-Fi CSI: A Pilot Correlation Study", **Preprints.org 2026** (DOI 10.20944/preprints202601.0518.v1)
- **PDF**: `c:/Users/ACE/Desktop/Extracting_Heart_Rate_Variability_Indica.pdf`
- **내용**: ESP32-WROOM-32 × 2 + 적응형 peak(μ+1.5σ) + IQR outlier → SDNN MAE 5.8ms, LF/HF r=0.84
- **⚠️ 주의**: 비심사 preprint + Fitbit GT 약점. **발표 SOTA 인용 금지**, 방법론 참고만
- **우리한테 의미**: 우리 환경(ESP32 2개)과 거의 동일. 방법론 참고용

### 3. `paper_review_C/` — 논문 C 한글 뷰어 (작업 2 · 재실감지) ⭐
- **논문**: Yakub et al., "Presence Detection with Wi-Fi using ESP32", **IEEE ICETSIS 2024** (DOI 10.1109/ICETSIS61505.2024.10459433)
- **PDF**: `c:/Users/ACE/Desktop/개개비/참고문헌/Presence_Detection_with_Wi-Fi_Using_ESP32.pdf`
- **내용**: ESP32 + CNN(1D Conv × 2 + Dense × 2) → 재실 98%, 0~2명 96.65%
- **우리한테 의미**: **작업 2 직접 베이스**. CNN 구조 100% 이식 (`esp32/presence_cnn.py`)
- **교수님이 주신 논문**

### 4. `terms/` — 용어사전
- CSI · HRV · 신호처리 핵심 용어 한글 설명
- 카테고리:
  - 심박 기본 (HR, R-R, IBI)
  - HRV 지표 (시간영역 SDNN/RMSSD, 주파수영역 LF/HF)
  - CSI 기본 (subcarrier, quotient, 진폭/위상)
  - 신호처리 (BPF, Butterworth, PCA, autocorrelation, z-score)
  - Peak 찾기 (find_peaks, μ+σ 적응형 임계, 슬라이딩 윈도우)
  - Outlier (median, IQR, percentile)
  - 검증·통계 (Ground truth, ECG/PPG, RMSE/MAE, p-value)
  - FAQ (자주 헷갈리는 6가지)
- **언제 보나**: 모르는 용어 나올 때 검색해서

### 5. `dday_demo/` — 발표 슬라이드 (17장)
- **2026-07-08 중간평가용 발표 자료**
- **조작**:
  - `→ / Space / PageDown`: 다음
  - `← / PageUp`: 이전
  - `Home / End`: 처음/끝
  - `F`: 풀스크린
  - 하단 점 클릭 = 직접 이동
- **슬라이드 구성**:
  1. 표지 (MajuBom)
  2. 동기 (할머니 낙상)
  3. 기존 시스템 한계
  4. 우리 해법 (3중 센서)
  5. CSI 파트 초점
  6. 학술 근거 (논문 A + Pulse-Fi)
  7. 우리 결정 (HRV → z-score)
  8. z-score 개념 (시험 점수 비유)
  9. 시연 STEP1 (베이스라인)
  10. 시연 STEP2 (라이브 측정)
  11. 시연 STEP3 (z-score 결과)
  12. 솔직 인정 (false positive 의미)
  13. Q&A "30일 학습?"
  14. Q&A "비정상 환자?"
  15. WBS 일정
  16. 전체 시스템
  17. 마무리

---

## 🔗 다른 자료와 연결

### esp32 폴더 안 (이미 git 포함)
- `README_CSI_HRV.md` — 전체 인수인계 (메인)
- `CSI_HRV_진단결과_의사결정.md` — 의사결정 흐름 (핵심 결정)
- `CSI_HRV_진행정리.md` — 오늘 스냅샷 + TODO + Q&A
- `작업2_측정계획.md` — 작업 2 측정 절차 + CNN 학습 가이드
- `presence_cnn.py` — 작업 2 CNN 코드 (PyTorch, 논문 C 기반)

### 윈도우에만 있음 (git 밖)
- `c:/Users/ACE/Desktop/개개비/algo_test/` — 알고리즘 실험 스크립트 (k sweep, 강도 분포, z-score 시뮬)

---

## 📖 자료 사용 순서 (다음 작업자/AI용)

```
1. 현재 상태 파악
   → CSI_HRV_진행정리.md  (스냅샷 한 페이지)

2. 의사결정 흐름 이해
   → CSI_HRV_진단결과_의사결정.md  (왜 이렇게 결정했는지)

3. 전체 인수인계 (긴 본문)
   → README_CSI_HRV.md

4. 모르는 용어 나오면
   → docs/terms/index.html  (용어사전)

5. 논문 참고 시
   → docs/paper_review_X/index.html  (A/B/C 한글 뷰어)

6. 발표 준비 시
   → docs/dday_demo/index.html  (슬라이드)

7. 작업 2 (재실감지) 진행 시
   → 작업2_측정계획.md  +  presence_cnn.py
```

---

## ⚠️ 주의 사항

### 인터넷 연결 필수
모든 HTML은 Tailwind CSS CDN 사용 → 오프라인 시 스타일 깨짐 (글자만 보임)

### 한글 폰트
- macOS: Apple SD Gothic Neo 자동 적용
- Windows: Malgun Gothic 자동 적용
- 깨지면 시스템 한글 폰트 설치 확인

### 이미지 크기
- `paper_review_B/img/`: 12페이지 PNG (논문이 12장이라 큼)
- `paper_review/img/`, `paper_review_C/img/`: 각 4~5장
- 총 docs 폴더 약 9MB

---

## 📜 이력

| 날짜 | 변경 |
|---|---|
| 2026-06-13 | 논문 A/B 뷰어, 용어사전 작성 (윈도우 측) |
| 2026-06-14 | 발표 슬라이드 17장 작성 |
| 2026-06-14 | 논문 C 뷰어 작성 |
| 2026-06-15 | esp32/docs/로 복사 + git push (이번) |
