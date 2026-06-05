# ESP32 CSI 작업 인수인계 (박형석)

> 다른 PC에서 이어서 작업하기 위한 정리 문서.
> 작성 시점: 1주차 Day 3 (Python 시각화까지 완료)

---

## 1. 프로젝트 컨텍스트

- **PDF 약속:** 치매환자 낙상 위험 사전 예측 / mmWave 보행 + ToF 침대이탈 + WiFi CSI 자율신경(HRV) 3중 센서
- **본인 역할:** ESP32 + Pi 5 통신 골격 담당
- **사이드 라인:** CSI로 호흡/심박/HRV 검증 (4주일정 1차 통합 외)

### 1주차 핵심 미션

| 우선 | 미션 |
|---|---|
| 1 (팀 메인) | mmWave가 보행 데이터 뽑는지 검증 (유현기 주도, 본인 협조) |
| 2 (본인 메인) | ESP32 통신 골격 (가짜 데이터 흐름) |
| 3 (사이드) | CSI 호흡 + 보행리듬(cadence) + HRV(트라이) 검증 |

### CSI 사이드 검증 범위 (확정)

| 지표 | 신호처리 | 라이브러리 | 1주차 안 결과 |
|---|---|---|---|
| 호흡 (RR) | PCA + Bandpass [0.1-0.5Hz] + FFT peak | scipy + sklearn | 확정 라인 |
| 보행 cadence | PCA + Bandpass [1-3Hz] + FFT peak | scipy + sklearn | 확정 라인 |
| HRV | PCA + Bandpass [0.8-2Hz] + R-peak + R-R | scipy + neurokit2 | 가능성 판가름 |

> HR(평균 심박수)는 HRV 시도하면 자동으로 같이 잡힘.

---

## 2. 시스템 구조

### 전체 그림

```
[ToF + ESP32 박스]    ──WiFi/MQTT──┐
                                     │
[mmWave + ESP32 박스] ──WiFi/MQTT──┼──→ [Pi 5 + AI HAT+]
                                     │       │
[CSI ESP32 박스]      ──WiFi/MQTT──┘       ├ MQTT 구독
                                             ├ 신호처리 (scipy/neurokit2)
                                             ├ 위험도 계산
                                             └ 대시보드
```

### 역할 분담

| 기기 | 역할 |
|---|---|
| ESP32 | CSI raw 수집 + WiFi로 전송 (계산 X) |
| Pi 5 | 받은 raw → 신호처리 → 위험도 → 대시보드 (모든 계산) |

> ESP32 = 눈/귀, Pi = 뇌.

### 박스 단위 패키징

| 박스 | 내용 | 외부 케이블 |
|---|---|---|
| mmWave 박스 | IWR6843 + ESP32 + 5V | USB-C 어댑터 1개 |
| ToF 박스 | VL53L8CX × 2 + ESP32 + 5V | USB-C 어댑터 1개 |
| CSI 박스 | ESP32 단독 + 5V | USB-C 어댑터 1개 |
| Pi 5 | 자체 | USB-C 어댑터 1개 |

> 모든 통신은 WiFi. 콘센트 4개, 데이터 케이블 0개.

### 박스 안 ESP32-ToF 연결 (점퍼 와이어 4선)

```
USB-C 어댑터 (5V) → ESP32-S3 USB-C 포트
                       │
                       ├─ ESP32 동작 (내부 LDO로 3.3V 생성)
                       │
                       └─ 3V3 핀 출력 → VL53L8CX VDD
                       
                       GND → VL53L8CX GND
                       GPIO (SDA) → VL53L8CX SDA
                       GPIO (SCL) → VL53L8CX SCL
                       GPIO (LPn) → VL53L8CX LPn (옵션, ToF 2개일 때 필수)
```

### mmWave 연결 옵션 (1주차 vs 4주차)

| 시점 | 구조 | 거리 |
|---|---|---|
| 검증 (현재) | IWR6843 ──USB(~3m)──→ Pi | USB 한계 |
| 실배치 (4주차) | IWR6843 ──UART──→ ESP32 ──WiFi──→ Pi | 무선, 자유 |

---

## 3. 현재 진행 상황

### 완료 (Day 1-3)
- [x] ESP-IDF v5.3 설치 (CLI)
- [x] hello_world flash 성공
- [x] esp-csi 클론
- [x] csi_recv_router 빌드 + flash
- [x] CSI raw 데이터 시리얼 수신 확인 (약 139 Hz)
- [x] CSV 로깅 (`csi_log.csv`)
- [x] Python 시각화 (`parse_csi.py` — 히트맵 + 시계열)

### 남음 (Day 4-7)
- [ ] 호흡 추출 (Bandpass + FFT)
- [ ] 보행 cadence 추출
- [ ] R-peak 검출 + HRV (neurokit2)
- [ ] Pi 5 Mosquitto 설치
- [ ] bridge.py (시리얼 → MQTT)
- [ ] 발표 데모 (envelope + MQTT 흐름)

---

## 4. 환경 재현 (다른 PC에서 셋업)

### macOS 기준

```bash
# 1. 사전 의존성
brew install cmake ninja dfu-util ccache
xcode-select --install

# 2. ESP-IDF 설치
mkdir -p ~/Desktop/bobs_project/esp32 && cd ~/Desktop/bobs_project/esp32
git clone -b v5.3 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32s3

# 3. 환경변수 alias
echo 'alias get_idf=". $HOME/Desktop/bobs_project/esp32/esp-idf/export.sh"' >> ~/.zshrc
source ~/.zshrc

# 4. 확인
get_idf
idf.py --version   # ESP-IDF v5.3 떠야 함

# 5. esp-csi 클론
cd ~/Desktop/bobs_project/esp32
git clone https://github.com/espressif/esp-csi.git
```

### Python 환경 (시각화/분석용)

```bash
cd ~/Desktop/bobs_project/esp32
python3 -m venv venv
source venv/bin/activate
pip install numpy pandas matplotlib scipy scikit-learn neurokit2
```

---

## 5. csi_recv_router 빌드 + Flash

```bash
get_idf
cd ~/Desktop/bobs_project/esp32/esp-csi/examples/get-started/csi_recv_router

idf.py set-target esp32s3
idf.py menuconfig
# Example Configuration → WiFi SSID/Password 입력 → S 저장 → Q 종료
# 주의: 2.4GHz SSID만 (ESP32-S3는 5GHz 못 잡음)

idf.py build
idf.py -p /dev/cu.usbmodem<번호> flash monitor
# 종료: Ctrl + ]
```

### 보드 인식 확인

```bash
ls /dev/cu.*
# /dev/cu.usbmodem<번호> 가 ESP32 포트
```

### CSI 데이터 로깅 (CSV 저장)

```bash
idf.py -p /dev/cu.usbmodem<번호> monitor | tee csi_log.csv
# 30초~1분 받고 Ctrl + ]
```

---

## 6. CSI 데이터 포맷

### 한 줄 구조

```
CSI_DATA, seq, mac, rssi, rate, sig_mode, mcs, cwb, ..., len, first_word, "[CSI 배열]"
```

| 위치 | 필드 | 의미 |
|---|---|---|
| 0 | CSI_DATA | 헤더 |
| 1 | seq | 패킷 시퀀스 |
| 2 | mac | 송신자 MAC (공유기) |
| 3 | rssi | 신호 세기 dBm |
| 14 | noise_floor | 채널 잡음 dBm |
| 16 | channel | WiFi 채널 |
| 18 | timestamp | 부팅 후 μs |
| 22 | len | CSI 배열 길이 (128 = 64 subcarrier × 2) |
| 24 | CSI | I/Q 정수 배열 |

### CSI 배열 해석

```
[0, 0, -9, 24, -7, 19, ...]
 │  │   │   │   │   │
 │  │   │   │   └─ subcarrier 1 Q
 │  │   │   └─ subcarrier 1 I
 │  │   └─ subcarrier 0 Q
 │  └─ subcarrier 0 I
 └─ DC (안 씀)
```

진폭 = `sqrt(I² + Q²)`, 위상 = `atan2(Q, I)`

> 진폭이 주력 데이터. 위상은 노이즈 큼.

---

## 7. parse_csi.py 사용법

스크립트 위치: `~/Desktop/bobs_project/esp32/parse_csi.py`

```bash
source venv/bin/activate
python parse_csi.py csi_log.csv
```

출력:
- 화면: 패킷 수, RSSI, 샘플레이트(Hz), 캡처 시간
- `csi_heatmap.png` — 시간 × subcarrier 히트맵
- `csi_timeseries.png` — 평균 amplitude 시계열

### 해석법

| 본 모습 | 의미 |
|---|---|
| 히트맵 가로줄에 주기적 출렁임 | 호흡/움직임 신호 |
| 히트맵 세로로 색 확 바뀜 | 큰 움직임 (사람 지나감) |
| 시계열에 일정 출렁임 | 호흡 후보 |
| 시계열 평탄 | 정적 환경 |

### 측정한 데이터 진단 (참고)

- 샘플레이트 약 139 Hz (호흡/심박/HRV에 충분)
- RSSI -33 dBm (좋음)
- 큰 출렁임은 손 움직임. 정지 구간 잔잔한 출렁임이 호흡 후보.
- 눈으로 호흡/움직임 분리 불가 → Bandpass + FFT로 검증 필요

---

## 8. 다음 단계 — 신호처리 (Day 4-7)

### 주파수 대역별 신호

| 신호 | 대역 |
|---|---|
| 호흡 | 0.2 ~ 0.4 Hz (분당 12~24회) |
| 심박 | 0.8 ~ 2 Hz (분당 50~120회) |
| 보행 cadence | 1.5 ~ 3 Hz |
| 손 움직임 | 0.5 ~ 10 Hz |

### Bandpass 필터로 분리

```python
import numpy as np
from scipy.signal import butter, filtfilt, find_peaks
from sklearn.decomposition import PCA
import neurokit2 as nk

# Raw → amplitude → PCA
amp = np.abs(csi)   # csi = (N, 64)
pc1 = PCA(n_components=1).fit_transform(amp)[:, 0]

# 호흡
fs = 139  # 실측 샘플레이트
b, a = butter(4, [0.1, 0.5], btype='band', fs=fs)
breath_sig = filtfilt(b, a, pc1)
# FFT → peak frequency = 호흡수 / 60

# 심박
b, a = butter(4, [0.8, 2.0], btype='band', fs=fs)
hr_sig = filtfilt(b, a, pc1)
# find_peaks → R-R interval

# HRV
peaks, _ = find_peaks(hr_sig, distance=int(fs*0.3), prominence=0.5)
rr_ms = np.diff(peaks) * (1000/fs)
hrv = nk.hrv_time(peaks, sampling_rate=fs)
print(hrv[['HRV_SDNN', 'HRV_RMSSD']])

# 보행 cadence
b, a = butter(4, [1.0, 3.0], btype='band', fs=fs)
cad_sig = filtfilt(b, a, pc1)
# FFT → peak * 60 = steps/min
```

> 알고리즘 자체는 라이브러리 호출. 우리가 할 일 = 글루 코드 + R-peak 튜닝 + 검증.

---

## 9. 데이터 envelope (MQTT 발행 표준)

### 공통 envelope

```json
{
  "node": "csi",
  "ts":   1735286400123,
  "seq":  848,
  "payload": { ... }
}
```

### 노드별 payload

```json
// fall/csi/processed
"payload": {
  "rssi": -33,
  "ch": 6,
  "noise": -94,
  "amp_mean": 12.5,
  "amp_std": 3.1,
  "presence": true,
  "breath_rate": 16,
  "hr": 72,
  "hrv_sdnn": null
}

// fall/tof
"payload": {
  "zones": [120, 130, 118, ...],
  "state": "lying"
}

// fall/mmwave
"payload": {
  "n_targets": 1,
  "targets": [{"x": 0.5, "y": 1.2, "vel": 0.8}],
  "gait_speed": 1.1
}
```

### 토픽 구조

```
fall/csi/raw         ← 디버깅용 raw CSI (가끔만)
fall/csi/processed   ← 1Hz 가공값 (운용 라인)
fall/tof
fall/mmwave
fall/status/<node>   ← heartbeat 10초
```

---

## 10. 발표 데모 (내일)

### 시나리오 — Pi 브리지 (안전)

```
ESP32 ──USB──→ Pi 5 (Python bridge: 시리얼→JSON→MQTT) ──→ Mosquitto ──→ 구독 데모
```

ESP32 펌웨어 안 건드림. Pi 쪽 Python 스크립트만.

### 체크리스트

| 순서 | 작업 |
|---|---|
| 1 | Pi 5 부팅, WiFi 연결 |
| 2 | `sudo apt install mosquitto mosquitto-clients` |
| 3 | `pip install paho-mqtt pyserial` |
| 4 | bridge.py 작성 (시리얼 → envelope → MQTT) |
| 5 | ESP32 USB로 Pi에 연결 |
| 6 | 터미널 3개 — bridge / mosquitto_sub -t 'fall/#' -v / 데모 |
| 7 | 리허설 1~2회 |

### 발표 멘트

- "ESP32에서 실제 CSI 추출"
- "Pi가 받아서 정해진 envelope으로 가공"
- "MQTT로 누구든 구독 가능"
- "ToF/mmWave도 같은 envelope만 따르면 끼울 수 있음"
- "오늘은 데모 편의로 USB지만 실배치는 ESP32 펌웨어에 MQTT 직박 → WiFi 완전 무선화"

---

## 11. 자주 막히는 곳

| 증상 | 해결 |
|---|---|
| `idf.py: command not found` | `get_idf` 다시 |
| `Failed to connect` (flash) | BOOT 누른 채 flash, 진행되면 손 떼기 |
| WiFi 접속 실패 | 5GHz SSID 의심. 폰 핫스팟 (2.4GHz) 사용 |
| `wifi: SYSTEM_EVENT_STA_DISCONNECTED` 반복 | 비번 오타 |
| 시리얼 한글 깨짐 | 정상, 영문 로그만 의미 |
| Python venv pip 안 됨 | `python3 -m venv venv` 재생성 |

---

## 12. 결정 트리거 (1주차 끝)

| 결과 | 다음 분기 |
|---|---|
| 호흡/심박/HRV 다 잡힘 | PDF narrative 그대로, 2주차에 Pi 이식 + MQTT 완전화 |
| 호흡 + cadence만 OK, HRV 약함 | 팀 회의 → HRV를 mmWave에 양도 (분담 재조정) |
| 호흡만 OK | CSI 라인 축소, vital sign 통째로 mmWave 이관 |
| 다 안 잡힘 | CSI 라인 폐기, mmWave + ToF만 |

---

## 13. 참고 링크

| 자료 | URL |
|---|---|
| esp-csi (Espressif 공식) | https://github.com/espressif/esp-csi |
| esp-radar 예제 (호흡/움직임 내장) | https://github.com/espressif/esp-csi/tree/master/examples/esp-radar |
| ESP-IDF 설치 가이드 | https://docs.espressif.com/projects/esp-idf/en/v5.3/esp32s3/get-started/ |
| ESP-IDF CSI API | https://docs.espressif.com/projects/esp-idf/en/v5.3/esp32s3/api-guides/wifi.html#channel-state-information |
| ESP32-CSI-Tool (S. Hernandez) | https://github.com/StevenMHernandez/ESP32-CSI-Tool |
| Wi-ESP 논문 (PMC9375645) | https://pmc.ncbi.nlm.nih.gov/articles/PMC9375645/ |
| neurokit2 (HRV) | https://github.com/neuropsychology/NeuroKit |
| scipy.signal | https://docs.scipy.org/doc/scipy/reference/signal.html |

---

## 14. 파일/디렉토리 위치

```
~/Desktop/bobs_project/
├── esp32/
│   ├── esp-idf/              ← ESP-IDF v5.3
│   ├── esp-csi/              ← Espressif esp-csi
│   ├── hello_world/          ← 테스트 프로젝트
│   ├── csi_log.csv           ← CSI raw 로그
│   ├── parse_csi.py          ← 시각화 스크립트
│   ├── csi_heatmap.png       ← 히트맵 결과
│   ├── csi_timeseries.png    ← 시계열 결과
│   ├── venv/                 ← Python venv
│   └── HANDOFF.md            ← 이 문서
└── fall-research-review/     ← GitHub Pages 레포
    └── paper-review/
        ├── 4주일정.html
        └── 예측모델.html
```
