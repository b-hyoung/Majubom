# CSI 작업 인수인계 (다른 컴퓨터/세션의 Claude용)

> 이 문서만 읽으면 지금까지 한 것 + 다음 할 것을 그대로 이어받을 수 있게 작성됨.
> 작성일 2026-06-13. 프로젝트: 글로벌 피우다프로젝트 팀 **마주봄(MajuBom)** — 치매환자 낙상 사전예측 다중센서(ToF+mmWave+CSI). 담당자=박형석. **이 폴더는 그중 WiFi CSI 파트.**

> ⚠️ **2026-06-14 결정 사항 업데이트** — 본 문서 §4 TODO 중 "절대 HRV 정확도(SDNN ms)" 추구는 **폐기**됨.
> 단일 안테나 ESP32 환경의 학술적 한계 확정 (IEEE BHI 2021 / Pulse-Fi IoTDCoSS 2025).
> **최종 방향**: "HRV" 표현은 유지, 의미를 "**자율신경 변화 추세 z-score** (HR + 호흡 + 자기상관 강도)"로 확장.
> **상세 의사결정 흐름**: [`CSI_HRV_진단결과_의사결정.md`](./CSI_HRV_진단결과_의사결정.md) §10~§13 (v2)
> **윈도우 작업 기록 + 24일 액션 플랜**: 루트 `FIX_NOTES.md`
> **논문 B 정정**: Chaudhari 2026은 비심사 preprint + Fitbit GT. 발표 SOTA 인용 금지, 방법론 참고만.
> **DFRobot C1001 / Polar H10 / Pulse-Fi LSTM 모두 보류** (사유 → 의사결정 문서 §10).

## ⭐ CSI 작업은 두 갈래다

| # | 작업 | 내용 | 현재 상태 |
|---|---|---|---|
| **작업1** | **HRV(심박변이도)** | 정지 상태에서 비접촉 심박→HRV 추출 | 심박 OK, HRV 절대값 미흡 (§3) |
| **작업2** | **재실감지 (2인 이상)** | CSI로 사람 수(특히 2명 이상) 감지 | 착수 전, 참고논문 확보 (§7-B) |

**같은 ESP32 2개 하드웨어를 공유**하되, 펌웨어/분석이 다름. 본 문서 §1~§4·§7-A는 주로 작업1(HRV), §7-B는 작업2(재실감지) 참고자료.

---

## 0. 한 줄 요약

**작업1(HRV)**: ESP32-S3 **2개**(송신 softAP + 수신 ping)로 CSI를 **111Hz**로 받아, 비접촉 **심박 82~86bpm은 재현성 있게 측정 성공**. **HRV(SDNN/RMSSD) 절대값은 아직 부정확**(노이즈로 부풀려짐). 다음 단계는 논문 기법(적응형 peak + 2채널 quotient + LF/HF 추세) 적용.
**작업2(재실감지 2인+)**: 아직 착수 전. CSI로 인원수 분류(CNN/ML)하는 방향, 교수님이 주신 논문(10459433)이 직접적 베이스. §7-B 참고.

---

## 1. 하드웨어 구성 (실제 작동하는 셋업)

| 역할 | 보드 | 포트(macOS) | 펌웨어 | 전원/연결 |
|---|---|---|---|---|
| **송신기 TX** | ESP32-S3 | `5B5E0513301` | `csi_tx_softap/` | 전원만 (USB는 전원용) |
| **수신기 RX** | ESP32-S3 | `5B5E0761861` | `esp-csi/examples/get-started/csi_recv_router/` | 노트북 USB (콘솔 921600) |

- **방식**: TX가 softAP(미니 와이파이 AP, SSID `esp_csi_tx` / PW `1234567890` / 채널1)를 띄우고, RX가 거기에 붙어 ping → ping 응답마다 CSI 1패킷. → **111Hz** 달성.
- **❌ 안 되는 방식**: ESP-NOW(`csi_send`/`csi_recv`)는 8.5Hz밖에 안 나와서 **버림**. 심박(1~1.7Hz)엔 Nyquist상 20Hz 이상 필요해서 ESP-NOW는 부적합.
- **물리 배치**: 침대 양쪽, **가슴 높이**로 책 쌓아 올려 두 보드를 **마주보게 세움**. 사람은 그 사이에 완전 정지로 누움(측정 중 미동 금지).
- **보드가 ESP32-S3임은 확정**(esptool chip_id로 확인). 빌드 타겟은 반드시 `esp32s3`.

### 펌웨어 다시 굽기 (필요 시)
```bash
cd /Users/bobs/Desktop/bobs_project/MajuBom/esp32/esp-idf
. ./export.sh                 # esp-idf 환경 로드 (이 경로가 맞음! bobs_project/esp32 아님)

# 송신기
cd ../csi_tx_softap
idf.py set-target esp32s3 && idf.py -p /dev/cu.usbmodem5B5E0513301 flash

# 수신기
cd ../esp-csi/examples/get-started/csi_recv_router
idf.py set-target esp32s3 && idf.py -p /dev/cu.usbmodem5B5E0761861 flash
```
**주의(겪은 함정들):**
- `set-target` 시 submodule(micro-ecc) 깨지면: `git submodule deinit -f <path>` 후 `git submodule update --init --recursive`.
- 콘솔을 USB_SERIAL_JTAG 단독으로 바꾸면 **부팅 안 됨(빨강 LED만)**. 기본값 유지: `CONFIG_ESP_CONSOLE_UART_DEFAULT=y` + `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y`.
- LED: 정상 보드는 빨강+초록 깜빡, 빨강만이면 부팅 실패 의심.
- macOS엔 `timeout` 명령 없음 → 시리얼 읽기는 pyserial 시간루프로.

---

## 2. 측정 & 분석 (실행 방법)

> **파이썬은 시스템 `python3` 사용** (numpy/scipy/sklearn/pyserial 설치돼 있음). `venv/`는 비어있으니 쓰지 말 것.

```bash
cd /Users/bobs/Desktop/bobs_project/MajuBom/esp32

# 1) 측정 (90초, 완전 정지 상태로 누워서)
python3 record_csi.py csi_rest3.csv 90
#  - PORT는 record_csi.py 안에 하드코딩됨: /dev/cu.usbmodem5B5E0761861
#  - 5초마다 Hz 출력. 100Hz 이상 나오면 정상.

# 2) 분석
python3 vital_csi2.py csi_rest3.csv
#  - 출력: 호흡 rpm, 자기상관 HR(가장 신뢰), HR, SDNN, RMSSD
```

### 파일 설명
| 파일 | 내용 |
|---|---|
| `record_csi.py` | 시리얼에서 CSI를 N초간 CSV로 저장. PORT 하드코딩. |
| `vital_csi2.py` | **현재 메인 분석기.** 파싱→진폭→PCA→자기상관 주기추정→그 주기 ±35%에서 peak→R-R median 이상치제거→SDNN/RMSSD. |
| `vital_csi.py` | 구버전(prominence 고정). SDNN 207ms로 비현실적이라 폐기. 참고만. |
| `parse_csi.py` | 초기 CSI 시각화용(heatmap/timeseries). |
| `csi_rest.csv` | 1차 측정(111Hz, 9950패킷). |
| `csi_rest2.csv` | 2차 측정(105Hz, 9451패킷). |

### CSI 데이터 포맷 (vital_csi2.py 파싱 기준)
```
CSI_DATA,seq,mac,rssi,rate,...,timestamp(18번필드 µs),...,len,first_word,"[I,Q,I,Q,...]"
```
- 끝의 `"[...]"` 배열 = 128값 = 64 subcarrier × (I,Q). 진폭 = √(I²+Q²).
- 균일 리샘플 기준 `FS=100.0Hz` (vital_csi2.py 상단).

---

## 3. 현재까지의 결과 (검증된 사실)

| 지표 | 1차(csi_rest) | 2차(csi_rest2) | 판정 |
|---|---|---|---|
| 패킷레이트 | 111Hz | 105Hz | ✅ 충분 |
| **HR(심박)** | 86bpm | 82bpm | ✅ **재현성 OK** (자기상관·peak 일치) |
| SDNN | 91.5ms | 95.4ms | ⚠️ 정상범위 진입했으나 높음(정상~50) |
| RMSSD | 101.6ms | 117.9ms | ❌ 불안정, RMSSD>SDNN = 잔여노이즈 신호 |
| 호흡 | 10rpm | 6rpm | ⚠️ 측정간 편차 큼 |

**결론**: 심박은 믿을 만함. **HRV 절대값은 아직 못 믿음.** RMSSD>SDNN은 peak 타이밍이 떨린다는 증거.

**미검증(다음에 꼭)**: ground truth(스마트워치/맥박계/ECG) 없이 측정해서 82~86bpm이 진짜인지 **외부 기준 대조 안 됨**. 정확도 주장하려면 이게 1순위.

---

## 3-B. 2026-06-14 핵심 진단 (애플워치 ground truth) ⭐⭐ 반드시 읽을 것

하루 종일 디버깅해서 **vital_csi2.py가 왜 틀렸는지 근본원인을 데이터로 확정**했다. 다음 사람은 이걸 전제로 시작할 것.

### 애플워치 비교 결과 (마음챙김 호흡앱)
| 회차 | 워치 HR | CSI HR | 워치 SDNN | CSI SDNN |
|---|---|---|---|---|
| watch1 | 79 | 79(운) | 35 | 66 |
| watch2 | 73 | 85❌ | 35 | 88 |
| watch3 | 71 | 85❌ | 33 | 96 |
→ **절대 부정확 확정.** 워치 HR은 내려가는데 CSI는 ~85 고정, SDNN은 2~3배 부풀음.

### 근본원인 4개 (전부 증명됨)
1. **진폭(amplitude)은 틀린 신호** — vital_csi2.py가 진폭 써서 실패. 진폭은 매번 ~85 엉뚱.
2. **위상(phase)이 맞는 신호** — 논문(PhaseBeat, 9508523 quotient)대로. ⬅ vital_csi3.py는 위상으로.
3. **호흡 고조파가 심박대역 도배** — 6rpm=0.1Hz의 13~16고조파 = 78/84/90/96bpm. 자기상관이 이 comb 중심(~85)에 락됨. 6rpm이면 고조파 간격 6bpm이라 심박과 분리 불가.
4. **에어컨 바람 = 심박 묻는 노이즈** — 끄니 위상 자기상관강도 0.29→0.47 개선. 공기흐름은 CSI 대표 노이즈원.

### ✅ 검증 성공
**에어컨off + 숨참기 + 위상 자기상관 → HR 67bpm vs 동시 워치 68~70bpm = ±2bpm 일치.**
- 숨참기 구간 자동검출: 호흡 envelope(hilbert(bandpass 0.1~0.5Hz))가 최소인 16초창.
- 진단 스크립트: `diag_hr.py`(스펙트럼), `diag_fix.py`(방법비교), `diag_hold2~4.py`(숨참기).

### 현재 위치
- HR: 깨끗한 조건에서 됨(±2bpm). 단 sub방법 ±8bpm 퍼짐(정밀도 거침).
- **HRV(박동간 ms): 아직 미해결 = 진짜 산.**

---

## 4. 다음 작업 (우선순위 순) ⭐

### 왜 HRV가 어려운가 (배경)
심박=평균주기(자기상관, 노이즈에 강함). HRV=박동 간 ms 단위 차이(peak 하나하나, 노이즈에 직격). 100Hz=샘플간격 10ms인데 RMSSD 정상값(20~50ms)과 peak오차(±10~20ms)가 비슷한 크기라 부풀려짐. → 해결책 = peak 정밀화 + 2채널 노이즈제거, 또는 절대값 포기하고 LF/HF 추세.

### 검증된 참고논문 2편 (환각 2회 교차확인 완료)

**논문 A — 정밀도 기준점**
[Shirakami & Sato, "Heart Rate Variability Extraction using Commodity Wi-Fi Devices via Time Domain Signal Processing", IEEE BHI 2021](https://ieeexplore.ieee.org/document/9508523/) (DOI 10.1109/BHI50953.2021.9508523)
- 핵심기법: **CSI quotient model**(두 신호 나눠 위상노이즈 제거, Intel 5300 다중안테나) + SDNN기반 subcarrier 선택
- 성능: **RMSE 130ms** (vs ECG), **SDNN 최저오차 8.8%**(좋은 위치만)
- ⚠️ 정정: 옛 메모의 "R-R 상대오차 2.53~4.83%·복조 MSE 0.35%"는 **본문에 없는 환각 수치**. 실제는 위 RMSE 130ms·SDNN 8.8% (PDF 풀텍스트 분석 기준)

**논문 B — 우리와 동일 구성(ESP32 2개) ⭐⭐**
[Extracting Heart-Rate Variability Indicators from Wi-Fi CSI: A Pilot Correlation Study (academia.edu)](https://www.academia.edu/145844177/Extracting_Heart_Rate_Variability_Indicators_from_Wi_Fi_CSI_A_Pilot_Correlation_Study)
- 하드웨어: **ESP32-WROOM-32 2개**(TX beacon ~10fps, RX monitor 모드 CSI) — 우리와 사실상 동일
- 방법: **적응형 임계 peak 검출** — `peak = μ + 1.5σ` 초과 & 기울기≈0, **30초 슬라이딩 윈도우**, **RRI≥400ms 제약**
- 성능: **SDNN 오차 5.8ms, RMSSD 4.1ms, LF/HF 추세 r=0.84 (p<0.001)**

### TODO (이 순서대로)

**[1] ground truth 확보 후 재측정** (정확도 검증의 전제)
- 스마트워치/손가락 맥박계/폰 앱 중 하나로 **CSI 측정과 동시에** 심박 기록.
- 같은 90초 측정. CSI HR(82~86) vs 기준 비교. 이게 없으면 "정확하다"를 증명 불가.

**[2] vital_csi2.py에 논문B의 적응형 peak 적용** (코드 작업, 효과 큼)
- 현재 `find_peaks(distance, prominence=0.3)` → **μ+1.5σ 임계 + 30초 슬라이딩 윈도우 로컬통계 + RRI≥400ms** 로 교체.
- 기대효과: RMSSD 부풀림 감소(현재 101~117ms → 정상 40~60ms 방향).
- 검증: 재측정 데이터로 RMSSD<SDNN 되는지 확인.

**[3] 2채널 quotient 디노이징 적용** (논문A 핵심)
- 현재 진폭 1채널만 사용 중. ESP32 한 대가 받는 **여러 subcarrier 간 나누기(quotient)** 로 공통 위상노이즈 제거.
- 또는 진폭 대신 **위상 차분** 시도(위상이 심박에 더 민감).
- 검증: peak 타이밍 jitter(R-R 표준편차) 감소 확인.

**[4] LF/HF 추세로 리포트 전환** (현실적 최종안)
- 절대 SDNN/RMSSD ms는 CSI로 한계. **LF/HF 비율 추세**가 robust(논문 둘 다 권장, r=0.84).
- 신청서 목표(평소 대비 자율신경 변화 → z-score, 30일 개인 베이스라인)와 정확히 맞음.
- 즉 "정확한 HRV값" 대신 **"평소보다 자율신경이 흔들렸나"** 를 출력하는 방향.

**낙상예측 프로젝트 관점**: 절대 HRV 정확도에 매달리지 말고 [4] LF/HF 추세를 메인으로, [2][3]은 그 추세 신뢰도를 올리는 보강으로 보는 게 맞음. 발표에서도 방어 쉬움.

---

## 5. 하지 말 것 / 주의

- ESP-NOW 방식(`csi_send`/`csi_recv`)으로 되돌아가지 말 것 → 8.5Hz로 실패 확정.
- `venv/` 쓰지 말 것 → 패키지 없음. 시스템 `python3` 사용.
- mmWave/ToF는 이 폴더 범위 밖(별도 담당). 단 mmWave는 라즈베리파이 USB로 연결하기로 결정됨(ESP32 직결 아님).
- 절대 HRV 정확도를 ECG 없이 "정확하다"고 단정하지 말 것. 한계 명시.
- 라우터/핫스팟 방식 제안 금지 → 사용자가 **ESP32 2개 구성**을 명확히 고수함.

---

## 7. 참고 논문 — 작업별 정리 (발표 인용처, 전부 링크)

> 환각 체크: ⭐ = 수치·방법을 2회 이상 독립 교차확인 완료. 나머지는 초록/검색 1회 확인.
> **§9에 발표 슬라이드에 그대로 복붙할 인용 링크 목록 정리해둠.**

### 7-A. 작업1 — HRV 참고논문

#### 🥇 1순위 (이대로 구현)
| 논문 | 우리한테 주는 것 | 핵심 수치 |
|---|---|---|
| **[Extracting HRV Indicators from Wi-Fi CSI: A Pilot Correlation Study](https://www.academia.edu/145844177/Extracting_Heart_Rate_Variability_Indicators_from_Wi_Fi_CSI_A_Pilot_Correlation_Study)** (Preprints.org 2026, **비심사**) | ESP32 2개 구성. 적응형 peak(μ+kσ)·IQR → **시도했으나 PCA z-score엔 부적합(k=0 적정)으로 기각**. SOTA 인용 금지 | SDNN 5.8ms, RMSSD 4.1ms (preprint 주장값) |
| ⭐ **[Heart Rate Variability Extraction using Commodity Wi-Fi Devices via Time Domain Signal Processing](https://ieeexplore.ieee.org/document/9508523/)** Shirakami & Sato, IEEE BHI 2021 | **CSI quotient model**(2채널 나눠 위상노이즈 제거, 다중안테나) = 정밀도 핵심 기법 (단일안테나 적용 불가) | **RMSE 130ms, SDNN 8.8%** (옛 "2.53~4.83%·MSE 0.35%"는 환각, 정정) |

#### 🥈 2순위 (보조 기법)
| 논문 | 볼 이유 |
|---|---|
| **[PhaseBeat: Exploiting CSI Phase Data for Vital Sign Monitoring with Commodity WiFi](https://www.eng.auburn.edu/~szm0001/papers/PhaseBeat_ACMHealth20.pdf)** | **위상 차분 + DWT**로 심박/호흡 추출. 진폭→위상 전환 시 참고. PDF 공개 |
| **[Non-Contact Heart Rate Monitoring Method Based on Wi-Fi CSI Signal](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11013971/)** (PMC, 오픈액세스) | 진폭+위상 rotational projection → 심박 96.8%, 중앙오차 0.8bpm |
| **[Heart Rate Variability and Body-Movement Extractions using Wi-Fi CSI](https://www.researchgate.net/publication/368597440)** | 체동 분리. 측정 중 미동 노이즈 제거 참고 |

#### ⚠️ 주의 인용
| 논문 | 주의점 |
|---|---|
| **Chaudhari et al. 2026, preprints.org** | **비심사 preprint**, ground truth가 ECG 아닌 **Fitbit**이라 근거 약함. 방법만 참고, **발표 인용은 9508523으로**. |

### 7-B. 작업2 — 재실감지(2인 이상) 참고논문

#### 🥇 1순위 (직접 베이스)
| 논문 | 우리한테 주는 것 | 핵심 수치 |
|---|---|---|
| **[Presence Detection with Wi-Fi Using ESP32](https://ieeexplore.ieee.org/document/10459433/)** (ICETSIS 2024, 바레인) | **교수님이 주신 논문.** ESP32 CSI + **CNN으로 인원수 분류** = 우리 작업2의 직접 베이스 | 재실유무 98%, 0~2명 96%, **최대 3명까지 ~86%** |
| **[Room-Level Occupancy Estimation via Wi-Fi CSI on ESP32 Nodes (Multi-Zone)](https://www.researchgate.net/publication/400250338)** | ESP32 다중노드 + **CSI–점유 정식 모델링**, 다중존 실험. 우리 침실 셋업에 가까움 | 멀티존 점유추정 |

#### 🥈 2순위 (인원 카운팅 기법)
| 논문 | 볼 이유 | 수치 |
|---|---|---|
| **[Wi-CaL: WiFi Sensing & ML Device-Free Crowd Counting and Localization](https://www.researchgate.net/publication/359132376)** | **ESP32 모듈** 사용, 인원 카운팅+위치추정 동시. ML/DL 특징비교 | — |
| **[A Novel Device-Free Counting Method Based on CSI](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6263397/)** (PMC, 오픈액세스) | 디바이스프리 인원 카운팅 기법 원리 | — |
| WiCount / DeepCount (딥러닝 카운팅) | CNN+LSTM 구조 참고 | WiCount 10명 92.8%, DeepCount 5명 89.4% |

> **작업2 주의점(논문들 공통)**: CSI 인원감지는 **방(환경)마다 multipath 지문이 달라 새 환경에서 정확도 급락**. → 시연 환경에서 **반드시 재학습/캘리브레이션** 필요. 발표 때 이 한계 미리 언급할 것.

---

## 6. 관련 경로 모음

- 작업 폴더: `/Users/bobs/Desktop/bobs_project/MajuBom/esp32/`
- esp-idf: `…/esp32/esp-idf` (export.sh 여기 것 사용)
- 신청서 PDF: `~/Downloads/2026 글로벌 피우다프로젝트 신청서(마주봄) 최종일걸.pdf`
- 메모리 파일: `~/.claude/projects/-Users-bobs-Desktop/memory/project_piuda_majubom.md`
- 구버전 인수인계(6/4): `HANDOFF.md` (참고용, 본 문서가 최신)

---

## 9. 발표 인용처 (슬라이드에 그대로 복붙)

### 작업1 — HRV
- [1] I. Shirakami, T. Sato, "Heart Rate Variability Extraction using Commodity Wi-Fi Devices via Time Domain Signal Processing," IEEE BHI 2021. https://ieeexplore.ieee.org/document/9508523/
- [2] "Extracting Heart-Rate Variability Indicators from Wi-Fi CSI: A Pilot Correlation Study" (ESP32 2개 구성). https://www.academia.edu/145844177/
- [3] "PhaseBeat: Exploiting CSI Phase Data for Vital Sign Monitoring with Commodity WiFi Devices." https://www.eng.auburn.edu/~szm0001/papers/PhaseBeat_ACMHealth20.pdf
- [4] "Non-Contact Heart Rate Monitoring Method Based on Wi-Fi CSI Signal," PMC. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11013971/

### 작업2 — 재실감지(2인 이상)
- [5] "Presence Detection with Wi-Fi Using ESP32," ICETSIS 2024 (교수님 제공, CNN 인원분류). https://ieeexplore.ieee.org/document/10459433/
- [6] "Room-Level Occupancy Estimation via Wi-Fi CSI on ESP32 Nodes: A Multi-Zone Experimental Study." https://www.researchgate.net/publication/400250338
- [7] "Wi-CaL: WiFi Sensing and ML Based Device-Free Crowd Counting and Localization" (ESP32). https://www.researchgate.net/publication/359132376
- [8] "A Novel Device-Free Counting Method Based on Channel Status Information," PMC. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6263397/

> ⚠️ 발표 인용 금지/주의: Chaudhari et al. 2026 (preprints.org) — 비심사 preprint라 인용처로 쓰지 말 것. HRV 근거는 [1]을 메인으로.
> ⚠️ 10459433은 **재실감지**용. HRV 근거로 인용하면 안 됨([5]는 작업2 전용).
