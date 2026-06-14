# 📝 MajuBom CSI/HRV 진행 기록·결정·TODO

> 윈도우 작업 기록. macOS 진단 문서(`esp32/CSI_HRV_진단결과_의사결정.md`)와 같이 보세요.
> 작성: 2026-06-13 ~ · 담당: 박형석

---

## 🎯 한 줄 현재 상태 (2026-06-14)

**HRV 절대값(SDNN ms) 포기 확정. 신청서 표현 "HRV" 그대로 유지, 의미를 "자율신경 변화 추세 z-score"로 확장.**
중간평가 **2026-07-08까지 24일** 남음.

---

## ✅ 확정 결정 사항

### 1. 알고리즘 방향
| 항목 | 결정 |
|---|---|
| SDNN/RMSSD 절대값 ms 측정 | ❌ **포기** (단일 안테나 학술적 불가) |
| HR 추세 z-score | ✅ **메인 출력** |
| 호흡 추세 z-score | ✅ **메인 출력** |
| 자기상관 강도 z-score | ✅ **보조 출력 (자율신경 안정성)** |
| 30일 개인 베이스라인 누적 | ✅ |
| LF/HF 주파수영역 분석 | △ 시도해보고 결정 (CSI HR 시퀀스가 짧아 한계) |
| 위상(phase) 사용 | ✅ vital_csi3.py 위상 기반 검증됨 (±2bpm) |

### 2. 하드웨어 (현재 그대로)
| 항목 | 결정 |
|---|---|
| ESP32-S3 × 2 (TX + RX) | ✅ 현재 구성 유지 |
| TI IWR6843AOPEVM → Pi 5 USB | ✅ 직결 확정 (ESP32 USB 호스트 불가) |
| **Polar H10 구매** | ❌ **보류** (가슴띠 = 비접촉 차별성과 모순) |
| **DFRobot C1001 구매** | ❌ **보류** (HR만 출력, CSI와 중복) |
| 애플워치 (보유) | ✅ ground truth 검증용 |

### 3. 알고리즘 차용 보류
| 항목 | 보류 사유 |
|---|---|
| Pulse-Fi (UCSC 2025) LSTM | HR 정밀도만 향상 (MAE 0.46 vs 우리 ±2 BPM), 본질적 HRV 한계 동일 |
| 논문 A Quotient | 단일 안테나라 적용 불가 (확정) |
| 논문 A SDNN-based subcarrier 선택 | 추세 z-score만 필요해 굳이 안 함 |
| 논문 B 적응형 peak μ+1.5σ | PCA z-score 신호엔 부적합 (k=0이 적정, 실측 확인) |

### 4. 검증 방식
- 애플워치 마음챙김 1분 호흡 + CSI 90초 동시 측정
- 이미 5회 측정 완료 (csi_watch1~3.csv, csi_hold4.csv)
- 추가 5~10회로 보강 (평소 vs 가벼운 stress 비교)

---

## 📚 학술 근거 정정 (발표 인용 가능)

### 발표 시 SOTA 인용 = 논문 A만
| 논문 | 인용 가능? | 비고 |
|---|---|---|
| **논문 A** (Shirakami & Sato, **IEEE BHI 2021**, DOI 10.1109/BHI50953.2021.9508523) | ✅ SOTA 근거로 인용 OK | Intel 5300 SIMO, peer-reviewed |
| **Pulse-Fi** (Kocheta, Bhatia, Obraczka, **IoTDCoSS 2025**, UCSC) | ✅ ESP32 단일 안테나 HR 근거 | "HRV는 의도적 제외, 단일 안테나는 HR까지가 한계" 강력한 근거 |
| **논문 B** (Chaudhari et al., 2026, **Preprints.org**, 비심사) | ⚠️ **방법론 참고만** | 비심사, Fitbit GT 약점. SOTA 인용 금지 |

### 진짜 사용할 수치 (정정 완료)
- 논문 A: RMSE **130ms**, SDNN 최저 오차 **8.8%**, IBI 평균 오차 **50ms = HR 5.7 BPM**
- ~~"R-R 상대오차 2.53~4.83%, 복조 MSE 0.35%"~~ ← **본문에 없는 환각 수치. 인용 금지**
- Pulse-Fi: MAE **0.46 BPM** (30s 윈도우, ESP32 단일 안테나)
- 우리(에어컨off + 숨참기 + 위상): ±2 BPM vs 애플워치 (검증)

---

## 🧾 신청서 정합 분석 결과

### 신청서가 진짜 요구한 것 (재해석)
신청서 본문 인용:
- "**개인별 기준 대비 변화량(z-score)**을 기반으로 당일 낙상 위험도를 산출"
- "**30일 데이터 기반 베이스라인** + 평소 대비 얼마나 변화"
- "낙상의 주요 원인 중 하나인 **생리적 이상 상태**를 추가적으로 반영"

→ **신청서는 HRV 절대값 ms를 요구한 적이 없음.** "변화량 z-score"가 명시적 요구.

### 차별성 5개 중 영향 받는 항목
| 차별성 | 영향 |
|---|---|
| ① 사전 예측 중심 설계 | 추세 z-score로 그대로 가능 ✓ |
| ② 3중 센서 융합 | ToF + mmWave + CSI 구조 유지 ✓ |
| ③ **완전 비접촉 시스템** | Polar H10 안 사니까 유지 ✓ |
| ④ **자율신경 기반 낙상 분석** | "추세 z-score = 자율신경 상태 변화"로 동일 달성 ✓ |
| ⑤ 상황 인지형 알림 | 4단계 위험도 유지 ✓ |

→ **차별성 5개 모두 유지됨**. HRV 정의만 확장하면 신청서 정합.

### 팀원 R&R 정정 필요 (유현기)
- 신청서: "심박수 및 HRV(SDNN, RMSSD) 추출 알고리즘 구현"
- 변경: "심박수 및 자율신경 변화 추세(HR/호흡/안정성 z-score) 분석 알고리즘 구현"
- → 유현기에게 변경 안내 필요

---

## 🛡 발표 방어 한 줄

> **"HRV 절대값(SDNN ms) 측정은 단일 안테나 ESP32의 학술적 한계로 확정(IEEE BHI 2021, Pulse-Fi IoTDCoSS 2025). 신청서 핵심 목표인 자율신경 변화 z-score 추세는 HR/호흡/안정성 추세로 동일 달성하며, 의료기기 인증 애플워치 ground truth로 검증함."**

### 예상 질문 대응
**Q: HRV(SDNN/RMSSD) 측정 결과는?**
A: 단일 안테나 ESP32로는 학술적 불가. Shirakami(BHI 2021)는 Intel 5300 다중안테나, Pulse-Fi(2025)는 ESP32 단일이지만 의도적으로 HR만. 우리는 신청서 핵심인 자율신경 변화 z-score를 추세 기반으로 동일 달성.

**Q: 신청 단계와 결과가 다른 거 아닌가?**
A: 핵심 목표("자율신경 변화 사전 감지")는 그대로. 측정 방식이 절대값 → 추세로 정밀화된 것. 이는 정상적 연구 과정.

---

## 📅 7.8 중간평가 24일 액션 (Top-Down)

### Week 1 (오늘 ~ 6.21)
- [x] FIX_NOTES.md 큰 폭 업데이트 (지금)
- [x] esp32/CSI_HRV_진단결과_의사결정.md 통합 업데이트 (지금)
- [ ] `vital_csi3.py`에 **z-score 추세 모드** 추가
  - HR 추세 (60초 윈도우)
  - 호흡 추세 (60초 윈도우)
  - 자기상관 강도 추세
  - 베이스라인 누적 시뮬레이션
- [ ] 기존 csi_watch1~3, csi_hold4로 z-score 시각화 1차

### Week 2 (6.22 ~ 6.30)
- [ ] 추가 측정 (애플워치 동시):
  - 평소(baseline) 5회
  - 가벼운 stress 후 5회 (애플워치 마음챙김 활용)
- [ ] 30일 베이스라인 시뮬레이션 그래프
- [ ] 다중 센서 통합 시뮬 (ToF + mmWave + CSI z-score)

### Week 3 (7.1 ~ 7.7)
- [ ] 시연 영상 (낙상 위험 신호 검출 시나리오)
- [ ] 발표 슬라이드 작성:
  - "WBS 일정 달성 — HRV 알고리즘 분석 완료"
  - 학술 근거 (IEEE BHI 2021, Pulse-Fi 2025)
  - 애플워치 검증 데이터
  - 30일 베이스라인 시뮬레이션
  - 자율신경 변화 z-score 시연
- [ ] 리허설 + 예상 질문 대응 준비
- [ ] 유현기에게 R&R 변경 안내

---

## 📂 관련 파일 (이 폴더)

| 파일 | 내용 |
|---|---|
| `esp32/README_CSI_HRV.md` | 전체 인수인계 (하드웨어/측정/논문) |
| `esp32/CSI_HRV_진단결과_의사결정.md` | 진단 결과 + 최종 결정 흐름 (메인 의사결정) |
| `esp32/vital_csi3.py` | 위상 기반 분석기 (HR 검증, HRV는 z-score 모드 추가 예정) |
| `esp32/vital_csi2.py` | 진폭 기반 구버전 (참고) |
| `esp32/csi_rest.csv`, `csi_rest2.csv` | 1·2차 측정 (베이스라인) |
| `esp32/csi_watch1~3.csv` | 애플워치 동시 측정 (검증) |
| `esp32/csi_hold2~4.csv` | 숨참기 측정 (HR ±2bpm 검증 완료) |
| `esp32/diag_*.py` | 진단 스크립트 (스펙트럼/방법비교/숨참기) |
| `FIX_NOTES.md` | 이 파일 (윈도우 작업 기록) |

## 📂 외부 참고 자료

| 파일 | 내용 |
|---|---|
| `c:\Users\ACE\Desktop\개개비\참고문헌\Heart Rate Variability Extraction using Commodity Wi-Fi...pdf` | 논문 A (Shirakami BHI 2021) PDF |
| `c:\Users\ACE\Desktop\Extracting_Heart_Rate_Variability_Indica.pdf` | 논문 B (Chaudhari 2026 preprint) PDF |
| `c:\Users\ACE\Desktop\개개비\paper_review\index.html` | 논문 A 한글 정리 뷰어 |
| `c:\Users\ACE\Desktop\개개비\paper_review_B\index.html` | 논문 B 한글 정리 뷰어 |
| `c:\Users\ACE\Desktop\개개비\terms\index.html` | 용어사전 (CSI/HRV/신호처리) |
| `c:\Users\ACE\Desktop\개개비\algo_test\` | k sweep 등 알고리즘 진단 결과 |

---

## 📜 이력 (시간순)

| 날짜 | 항목 |
|---|---|
| 2026-06-13 | FIX_NOTES 생성. 논문 A PDF 풀텍스트 파싱, 수치 정정 (130ms RMSE, 8.8% SDNN). 논문 B PDF 파싱 → 비심사 preprint + Fitbit GT 확인. 논문 A/B 한글 뷰어 제작. |
| 2026-06-13 | vital_csi3.py 윈도우 버전 작성 (적응형 peak + IQR). 실측 적용 결과 단순 이식 실패. k sweep 진단 → PCA z-score 신호엔 k=0이 적정. |
| 2026-06-14 | macOS git push로 진단 결과 수신. 위상 기반 vital_csi3.py + 애플워치 5회 비교 + 숨참기 검증 데이터 확보. **HRV 절대값 불가 확정**. |
| 2026-06-14 | Pulse-Fi (UCSC IoTDCoSS 2025) 발견. **ESP32 단일 안테나 HR 학술 근거 + HRV 의도적 제외**. |
| 2026-06-14 | DFRobot C1001 사양 검토. **getHeartRate() 만 출력, HRV 직접 출력 X** → 구매 보류. |
| 2026-06-14 | TI IWR6843AOPEVM ↔ ESP32 USB 호스트 불가 확정 (CP2105 듀얼). Pi 5 USB 직결 결정. |
| 2026-06-14 | 신청서 본문 분석. "z-score 변화량"이 진짜 요구사항. HRV 절대값 요구 없음. 차별성 5개 모두 추세 z-score로 달성 가능. |
| 2026-06-14 | **Polar H10 구매 보류** (가슴띠 = 비접촉 차별성 모순). 애플워치로 충분. |
| 2026-06-14 | **최종 방향 확정**: HRV 추세 z-score (HR + 호흡 + 자기상관 강도) + 30일 베이스라인 + 애플워치 검증. |
| 2026-06-14 | FIX_NOTES 큰 폭 재작성 (이 버전). |
