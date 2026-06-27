# MajuBom (마주봄)

치매 환자의 낙상을 미리 잡으려는 비접촉 센서 시스템입니다. 침대에 카메라나 웨어러블을 붙이지 않고 ToF·mmWave·WiFi CSI 세 센서로 환자 상태를 읽습니다. 2026 글로벌 피우다프로젝트 본선 진행 중이고, 이 저장소는 센서 수집 펌웨어부터 서버의 위험도 계산까지를 담고 있습니다.

## 동작 방식

세 센서가 각자 다른 신호를 맡습니다.

- **ToF** — 침상 이탈 트리거. 환자가 일어서려는 순간을 잡습니다.
- **mmWave** (TI IWR6843AOP) — 보행 패턴 분석. 당일 위험도의 70%를 차지합니다.
- **WiFi CSI** (ESP32-S3 2개) — 정지 상태의 심박·호흡 변화로 자율신경 추세를 봅니다. 나머지 30%.

ESP32와 라즈베리파이가 raw 데이터를 모아 서버로 보내고, 서버는 환자별 30일 베이스라인 대비 z-score를 계산해 위험도를 4단계(🟢 normal / 🟡 caution / 🟠 warning / 🔴 critical)로 띄웁니다. 절대값이 아니라 "평소보다 얼마나 벗어났나"를 보기 때문에 환자마다 기준이 다릅니다. 서버가 보낸 JSON·z-score·예외처리 규칙은 [`FIX_NOTES.md`](./FIX_NOTES.md)에 정리돼 있습니다.

## 지금까지 된 것, 안 된 것

과장 없이 적습니다. CSI 파트가 가장 어려웠고 기록도 제일 많습니다.

- **심박은 잡힙니다.** 에어컨을 끄고 숨을 참은 16초 구간을 위상 자기상관으로 분석하면 애플워치 대비 ±2bpm까지 맞습니다. ESP-NOW로는 8.5Hz밖에 안 나와 버렸고, softAP + ping 구조로 111Hz까지 끌어올린 게 전환점이었습니다.
- **HRV 절대값(SDNN/RMSSD)은 아직 못 믿습니다.** 단일 안테나 ESP32로 ms 단위 정확도를 내는 건 논문상으로도 한계라, "정확한 HRV 수치" 대신 "평소 대비 자율신경이 흔들렸나"를 추세(LF/HF z-score)로 보는 쪽으로 방향을 바꿨습니다.
- 디버깅 중에 진폭 신호가 호흡 고조파에 속아 심박이 ~85bpm에 고정되던 문제를 데이터로 추적했습니다. 진폭을 버리고 위상 기반으로 갈아엎은 근거와 검증 과정은 [`esp32/README_CSI_HRV.md`](./esp32/README_CSI_HRV.md), [`esp32/CSI_HRV_진단결과_의사결정.md`](./esp32/CSI_HRV_진단결과_의사결정.md)에 있습니다.

## 저장소 구조

```
MajuBom/
├── esp32/        # WiFi CSI — 펌웨어, 측정/분석 스크립트, 진단 기록
│   ├── csi_tx_softap/        송신기 펌웨어 (softAP)
│   ├── record_csi.py         시리얼에서 CSI를 CSV로 수집
│   ├── vital_csi3.py         위상 기반 HR/HRV 분석 (현재 메인)
│   ├── diag_*.py             심박 고정 원인 진단 스크립트들
│   ├── presence_cnn.py       재실감지(2인 이상) 실험
│   └── README_CSI_HRV.md     CSI 작업 인수인계 (제일 자세함)
├── mmWave/       # TI IWR6843AOP 설정(.cfg) + 수신 스크립트
├── TOF/          # ToF 센서
├── server/       # 수집 데이터 저장 + 위험도 계산
│   ├── db.py                 baseline / z-score / alert_level
│   ├── csi_server.py, mmw_server.py, tof_server.py
│   └── run_all.py            세 서버 한 번에 실행
└── docs/         # 발표 자료, 대시보드, 논문 리뷰
```

## 처음 받을 때

서브모듈(esp-idf, esp-csi)까지 한 번에 받습니다.

```bash
git clone --recurse-submodules https://github.com/b-hyoung/Majubom.git
cd Majubom
git submodule update --init --recursive   # 이미 클론했다면 이것만
```

> esp-idf는 자체 서브모듈이 많아 다운로드에 수 GB, 10분 이상 걸립니다.

## ESP32 작업

ESP-IDF 툴체인은 저장소에 포함된 서브모듈을 씁니다 (macOS / Linux).

```bash
# 1) 툴체인 설치 (최초 1회)
cd esp32/esp-idf && ./install.sh esp32s3 && cd ../..

# 2) 환경변수 (셸 열 때마다)
source esp32/esp-idf/export.sh

# 3) 빌드 & 플래시
cd esp32/csi_tx_softap
idf.py set-target esp32s3
idf.py -p /dev/cu.usbmodem* flash monitor   # monitor 종료: Ctrl + ]
```

빌드 타겟은 보드가 ESP32-S3라 항상 `esp32s3`입니다. 포트·펌웨어 매핑과 자주 밟는 함정은 [`esp32/README_CSI_HRV.md`](./esp32/README_CSI_HRV.md) §1을 보세요.

## CSI 측정 & 분석

분석은 시스템 `python3`를 씁니다 (numpy/scipy/sklearn/pyserial 필요, `esp32/venv`는 비어 있으니 쓰지 마세요).

```bash
cd esp32
python3 record_csi.py csi_rest.csv 90    # 완전 정지 상태로 90초
python3 vital_csi3.py csi_rest.csv       # 호흡 rpm, 심박, 신뢰도 플래그 출력
```
