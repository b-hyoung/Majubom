# MajuBom

치매환자 낙상 위험 예측 시스템 — 다중 센서(mmWave / ToF / WiFi CSI) 기반.
ESP32에서 raw 데이터를 수집하고 WiFi/MQTT로 Pi 5에 전송, Pi에서 신호처리·위험도 계산을 수행합니다.

## 저장소 구조

```
MajuBom/
├── esp32/                       # ESP32 펌웨어 및 CSI 검증 코드
│   ├── HANDOFF.md               # 작업 인수인계 / 1주차 미션 정리
│   ├── hello_world/             # 기본 동작 확인용 ESP-IDF 프로젝트
│   ├── parse_csi.py             # CSI 로그 시각화 (heatmap, timeseries)
│   ├── csi_log.csv              # 수집된 CSI 샘플 데이터
│   ├── esp-csi/    (submodule)  # Espressif CSI 예제 (pin: 8633d67)
│   └── esp-idf/    (submodule)  # ESP-IDF v5.3 (pin: e0991fa)
└── (이후 mmWave / Pi 등 하위 프로젝트 추가 예정)
```

## 처음 받을 때 (다른 PC에서)

서브모듈까지 한 번에 받기 위해 `--recurse-submodules` 옵션을 꼭 붙입니다.

```bash
git clone --recurse-submodules https://github.com/b-hyoung/Majubom.git
cd Majubom
```

이미 클론했다면 서브모듈만 별도로 받기:

```bash
git submodule update --init --recursive
```

> esp-idf는 자체 서브모듈이 많아 수 GB 다운로드 + 10분 이상 걸릴 수 있습니다.

---

## ESP32 작업 시 (필수 셋업 순서)

> 다른 하위 프로젝트가 추가되어도 **ESP32 작업은 아래 순서만 따라하면 됩니다.**

### 1) ESP-IDF 툴체인 설치 (최초 1회)

저장소에 포함된 esp-idf 서브모듈을 사용합니다. macOS / Linux 기준:

```bash
cd esp32/esp-idf
./install.sh esp32s3        # 보드가 esp32s3 인 경우. esp32 면 install.sh esp32
cd ../..
```

Windows 는 `install.bat esp32s3` 로 대체.

### 2) 환경변수 활성화 (셸 열 때마다)

```bash
source esp32/esp-idf/export.sh    # macOS / Linux
# Windows: esp32\esp-idf\export.bat
```

> `idf.py --version` 이 정상 출력되면 준비 완료.

### 3) 빌드 & 플래시 (예: hello_world)

```bash
cd esp32/hello_world
idf.py set-target esp32s3        # 보드에 맞게 esp32 / esp32s3 / esp32c3 등
idf.py build
idf.py -p /dev/cu.usbmodem* flash monitor   # macOS 포트 예시
```

`monitor` 종료: `Ctrl + ]`

### 4) CSI 시각화 (Python)

ESP32 가 출력한 `csi_log.csv` 를 heatmap / timeseries 로 변환:

```bash
cd esp32
python3 -m venv venv
source venv/bin/activate
pip install numpy pandas matplotlib scipy
python parse_csi.py
```

생성 결과: `csi_heatmap.png`, `csi_timeseries.png` (gitignore 됨 — 매번 재생성)

---

## 참고

- 1주차 미션·역할 분담은 `esp32/HANDOFF.md` 참조
- ESP-IDF 버전 변경 시: `cd esp32/esp-idf && git checkout <tag>` 후 부모 저장소에서 커밋
- 빌드 산출물(`build/`, `sdkconfig.old`, `venv/`)은 `.gitignore` 처리됨
