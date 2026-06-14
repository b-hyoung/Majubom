# 📋 CSI/HRV 진행정리 — 2026-06-14 스냅샷

> MajuBom CSI/HRV 파트 · 박형석
> 이 문서 = "**오늘까지 한 것 + 앞으로 할 것**" 한 페이지 요약
> 상세 의사결정: [`CSI_HRV_진단결과_의사결정.md`](./CSI_HRV_진단결과_의사결정.md)
> 전체 인수인계: [`README_CSI_HRV.md`](./README_CSI_HRV.md)

---

## 🎯 한 줄 현재 상태

**HRV 절대값(SDNN ms) 측정 학술적 불가 확정.** 신청서 표현 "HRV" 유지하되 의미를 "**자율신경 변화 추세 z-score**"로 확장. 알고리즘·발표 자료 1차 완성. 중간평가(2026-07-08)까지 **24일** 남음.

---

## ✅ 지금까지 한 것 (시간순)

### 1. 측정 인프라 (~6월 초)
- ESP32-S3 × 2 (TX softAP + RX ping) 셋업 → **CSI 100~111Hz 안정 수신**
- `record_csi.py`로 시리얼 → CSV 저장
- 90초 단발 측정 데이터 9개 축적
  - `csi_rest`, `csi_rest2` (마음챙김 호흡, 재현성 OK)
  - `csi_watch1~3` (애플워치 동시 측정)
  - `csi_hold`, `csi_hold2~4` (숨참기, hold4가 ±2bpm 검증 케이스)

### 2. 알고리즘 검증 (6월 중)
- `vital_csi2.py` (진폭 기반) — HR 86bpm 재현성 OK, SDNN 91.5ms (부풀림)
- `vital_csi3.py` (위상 기반) — 환경 통제 시 HR ±2bpm 검증 (csi_hold4)
- `diag_*.py` 진단 스크립트 — 호흡 고조파 락, 에어컨 노이즈 영향 측정

### 3. 학술 검증 (6월 13~14)
- **논문 A** (Shirakami & Sato, IEEE BHI 2021) PDF 풀텍스트 분석
  - Intel 5300 다중 안테나 + Quotient 모델 → RMSE 130ms
  - 우리 ESP32 단일 안테나에 그대로 적용 불가 확인
- **논문 B** (Chaudhari et al., 2026) PDF 풀텍스트 분석
  - 비심사 preprint + Fitbit GT → SOTA 인용 부적합
  - 방법론(적응형 peak, IQR outlier)은 참고 가능
- **Pulse-Fi** (UCSC, IoTDCoSS 2025) 발견 + 분석
  - ESP32 × 2 단일 안테나로 HR MAE 0.46 BPM 달성
  - **HRV 의도적 제외** → 단일 안테나 한계의 peer-reviewed 근거

### 4. 하드웨어 옵션 검토 (6월 14)
- DFRobot C1001 (60GHz mmWave) 검토 → `getHeartRate()`만 출력, HRV 직접 출력 X → **보류**
- Polar H10 가슴띠 → 비접촉 차별성 모순 → **보류**
- TI IWR6843AOPEVM ↔ ESP32 USB 호스트 불가 확정 (CP2105 듀얼) → **Pi 5 직결로 결정**

### 5. 최종 방향 확정 (6월 14)
- HRV 절대값 추구 → 폐기
- **호흡 + HR + 신호 안정성 z-score 추세 → 채택**
- 검증 = 애플워치 (이미 확보된 csi_watch1~3 + csi_hold4)
- 30일 베이스라인 학습 컨셉 시뮬레이션 (`baseline_zscore.py`, `demo_dday.py`)
- 발표 슬라이드 17장 (`dday_demo/index.html`)

### 6. 추가 발견 (분석 중)
- **자기상관 강도 게이트 한계**: 강도 ≥ 0.30 통과해도 HR 정확도 보장 X (4개 중 1개만 ±5 일치)
- **호흡 z-score는 신뢰 가능** (6rpm 정확 측정 검증)
- **신호 안정성 z-score는 메타 지표로 valid** (환경 노이즈 자동 감지)
- **HR z-score는 환경 조건부**: 통제 환경 ±2bpm, 일상 환경 5~18bpm 차이
- **베이스라인 4개로는 σ 너무 좁아 false positive 폭주** → 30일 학습 필수성 직접 입증

---

## ⚠️ 핵심 결정 (확정)

| 항목 | 결정 |
|---|---|
| HRV 절대값 (SDNN/RMSSD ms) | ❌ 포기 (단일 안테나 학술적 불가) |
| 호흡 z-score | ✅ 메인 |
| 신호 안정성 z-score | ✅ 메인 (자율신경 안정성 + 환경 게이트) |
| HR z-score | △ 조건부 (mmWave cross-check 통과 시만) |
| 30일 베이스라인 누적 | ✅ 신청서 §개인 맞춤형 정합 |
| Polar H10 구매 | ❌ 비접촉 차별성 모순 |
| DFRobot C1001 구매 | ❌ HR만 출력, CSI와 중복 |
| Pulse-Fi LSTM 차용 | ❌ HR 정밀도 효과 작음, 본질 무관 |
| 신청서 "HRV" 표현 | ✅ 유지, 의미 확장 (발표 시 솔직 명시) |

### 학술 인용 (정정 완료)
- 발표 SOTA: **논문 A (Shirakami BHI 2021)** ✅
- ESP32 단일 안테나 HR 근거: **Pulse-Fi (UCSC IoTDCoSS 2025)** ✅
- 논문 B (Chaudhari 2026): 방법론 참고만, **SOTA 인용 금지** ⚠️

### 폐기된 옛 결정
- ~~"적응형 peak μ+1.5σ 단순 도입"~~ → PCA z-score 신호엔 부적합 (실측 확인, k=0이 적정)
- ~~"R-R IQR outlier 제거 추가"~~ → 어차피 HRV 절대값 포기로 무의미
- ~~"논문 A SDNN-based subcarrier 선택"~~ → 추세만 필요해 굳이 안 함

---

## 📋 앞으로 해야 할 것 — 7.8 중간평가까지 24일

### 🟢 Week 1 (~6.21) — 알고리즘 정리
- [x] FIX_NOTES.md 큰 폭 업데이트 (루트)
- [x] CSI_HRV_진단결과_의사결정.md v2 추가 정리
- [x] 발표 슬라이드 1차 (`dday_demo/index.html` 17장)
- [x] z-score 추세 시뮬 (`baseline_zscore.py`, `demo_dday.py`)
- [ ] `vital_csi3.py`에 z-score 추세 모드 정식 통합 (단발 모드 → 추세 모드 추가)
- [ ] **유현기 R&R 변경 공유** ("HRV(SDNN, RMSSD) 추출" → "자율신경 추세 z-score 분석")

### 🟡 Week 2 (6.22~6.30) — 추가 측정 + 통합
- [ ] **베이스라인 추가 측정 10회+** (같은 자세, 같은 시간대 → σ 안정화)
- [ ] **변화 시뮬 측정** (현재 부족)
  - 빠른 호흡 후 측정 × 3회
  - 인지 부담 후 측정 × 3회
  - 누운 자세 vs 약간 자세 변화 × 3회
- [ ] **이불 영향 검증 측정** (선택)
  - 이불 X / 얇은 / 두꺼운 비교 × 각 3회
- [ ] 다중 센서 통합 시뮬 (ToF + mmWave + CSI z-score 융합)
- [ ] 30일 베이스라인 시뮬레이션 그래프 (가상 데이터로 시연용)

### 🔴 Week 3 (7.1~7.7) — 발표 준비
- [ ] **시연 영상 촬영** (라이브 실패 대비 백업)
- [ ] 발표 슬라이드 최종본
  - 측정값 실제 데이터로 교체 (Week 2 측정 후)
  - WBS 일정 달성 슬라이드 갱신
  - false positive → "30일 필요" 입증 슬라이드 강조
- [ ] **모의 발표 리허설** 2회 이상
  - 예상 질문 5개 답변 준비 (`dday_demo/index.html` Q&A 참조)
  - 시간 측정 (10~15분)
- [ ] 발표장 환경 사전 확인 (WiFi 채널, 에어컨, 공간 크기)
- [ ] 백업 장비 준비 (ESP32 예비, 노트북 충전 등)

### 🟣 발표 후 (7.8 이후, 선택)
- [ ] **작업 2: 재실감지 (1명 vs 2명+) 구현** — 논문 C 베이스 (`paper_review_C/index.html`)
  - 데이터 수집: 1명/2명 각 30회 (30초씩) → 60 샘플
  - 1D CNN 모델 (논문 C 구조 그대로, Keras 30줄)
  - 목표: 90%+ 정확도 (논문 96.65% 기준)
  - 작업 1과 통합 → HRV 측정 자동 게이트 (방에 2명+ 시 무효화)
- [ ] DFRobot C1001 또는 다른 mmWave 모듈 재검토 (시간 여유 있을 시)
- [ ] 의료 협력 기관 컨택 (실측 검증용)

---

## 🚦 발표 방어 핵심 요약 (예상 Q&A 5개)

| 질문 | 한 줄 답 |
|---|---|
| HRV(SDNN/RMSSD) 측정 결과는? | 단일 안테나 학술적 불가 (IEEE 9508523, Pulse-Fi 2025). 추세 z-score로 동일 목표 달성. |
| 신청서와 다른 거 아닌가? | 핵심 목표(자율신경 z-score 변화) 동일. 측정 방식이 절대값 → 추세로 정밀화된 정상 연구 과정. |
| 30일 학습 = 도입 즉시 효과 X? | 첫날부터 ToF + mmWave + 절대 위험 임계 작동. CSI도 학습 중 점진 정밀화. Apple Watch 등 동일 trade-off. |
| 평소 비정상 환자는 못 잡나? | 각자 평소 기준이라 자기 변화는 잡음. 평소도 위험인 경우 절대 임계(HR>140 등) 병행. |
| CSI HR 정확도는? | 환경 통제 시 ±2bpm (csi_hold4 검증). 일상 환경 노이즈에 민감 → 메인 X, 보조로만. |

---

## 📁 핵심 파일 위치

### 코드 (이 폴더)
| 파일 | 용도 |
|---|---|
| `vital_csi3.py` | 위상 기반 분석기 (단발 측정용, HR 검증된 알고리즘) |
| `vital_csi2.py` | 진폭 기반 구버전 (참고용) |
| `record_csi.py` | 시리얼 → CSV 저장 |
| `diag_*.py` | 진단 스크립트 (스펙트럼, 방법 비교, 숨참기 검출 등) |
| `csi_rest.csv`, `csi_rest2.csv` | 1·2차 베이스라인 측정 (90초) |
| `csi_watch1~3.csv` | 애플워치 동시 측정 (90초) |
| `csi_hold.csv` ~ `csi_hold4.csv` | 숨참기 측정 (45초). hold4가 ±2bpm 검증 케이스 |

### 문서 (이 폴더)
| 파일 | 내용 |
|---|---|
| `README_CSI_HRV.md` | 전체 인수인계 (하드웨어, 측정, 논문) — 메인 |
| `CSI_HRV_진단결과_의사결정.md` | 진단 + 의사결정 흐름 (v1 + v2) — 핵심 결정 |
| `CSI_HRV_진행정리.md` | **이 파일** — 오늘 스냅샷 + TODO |
| `HANDOFF.md` | 구버전 인수인계 (참고) |

### 윈도우 작업물 (`c:\Users\ACE\Desktop\개개비\`)
| 폴더/파일 | 내용 |
|---|---|
| `참고문헌/Heart Rate Variability Extraction...pdf` | 논문 A PDF (HRV, Shirakami BHI 2021) |
| `Extracting_Heart_Rate_Variability_Indica.pdf` (Desktop) | 논문 B PDF (HRV, Chaudhari 2026 preprint) |
| `참고문헌/Presence_Detection_with_Wi-Fi_Using_ESP32.pdf` | 논문 C PDF (재실감지, ICETSIS 2024) |
| `paper_review/index.html` | 논문 A 한글 뷰어 (작업 1) |
| `paper_review_B/index.html` | 논문 B 한글 뷰어 (작업 1) |
| `paper_review_C/index.html` | **논문 C 한글 뷰어 (작업 2)** ⭐ |
| `terms/index.html` | 용어사전 (CSI/HRV/신호처리) |
| `algo_test/` | 알고리즘 실험 (k sweep, 강도 분포, z-score 시뮬) |
| `dday_demo/index.html` | **발표 슬라이드 17장** ⭐ |

### 윈도우 작업 기록
- 루트 `FIX_NOTES.md` — 진행 기록, 결정 사항, 액션 플랜

---

## 🛡 발표 시 가져갈 자료 체크리스트

```
[필수]
  □ 발표 슬라이드 (dday_demo/index.html) — 풀스크린 모드
  □ 시연 영상 (백업)
  □ 노트북 (충전 완료)
  □ 인터넷 (테더링 백업)

[시연용]
  □ ESP32-S3 × 2 (TX softAP + RX, 펌웨어 확인)
  □ USB 케이블 ×2
  □ 측정용 침대/책상 셋업 도구
  □ 베이스라인 측정 30분 전 진행

[참고용]
  □ baseline.json (사전 측정 결과)
  □ 논문 A, B PDF (질의응답 대비)
  □ FIX_NOTES.md (예상 질문 답변)
```

---

## 📜 이력

| 날짜 | 변경 |
|---|---|
| 2026-06-13 | 윈도우 작업 시작. 논문 A/B PDF 풀텍스트 파싱 + 한글 뷰어. FIX_NOTES 초안. |
| 2026-06-13 | vital_csi3 (윈도우 버전) 시도 + k sweep 진단 → PCA z-score엔 k=0 적정 발견. |
| 2026-06-14 | macOS 진단 결과 pull. HRV 절대값 불가 확정. |
| 2026-06-14 | Pulse-Fi 발견. DFRobot 검토 후 보류. Polar H10 보류. |
| 2026-06-14 | 신청서 정합 분석 → "z-score 변화량" 명세 발견. |
| 2026-06-14 | z-score 추세 시뮬 (baseline_zscore.py, demo_dday.py). |
| 2026-06-14 | 발표 슬라이드 17장 (dday_demo/index.html). |
| **2026-06-14** | **이 진행정리 문서 작성. 1차 연구 마무리.** |
