# MajuBom (마주봄)

치매 환자의 낙상을 미리 잡으려는 비접촉 센서 시스템입니다. 침대에 카메라나 웨어러블을 붙이지 않고 ToF·mmWave 두 센서로 환자 상태를 읽습니다. 2026 글로벌 피우다프로젝트 본선 진행 중이고, 이 저장소는 센서 수집 펌웨어부터 서버의 위험도 계산까지를 담고 있습니다.

> 원래 WiFi CSI(ESP32 기반 심박·호흡 센싱)도 세 번째 센서로 개발했으나, 단일 안테나로는 HRV 절대값 신뢰도를 확보하기 어려워 2026-09-10에 기능 자체를 완전히 제거했습니다. 관련 코드·문서는 저장소 히스토리에 남아있습니다.

## 동작 방식

두 센서가 각자 다른 신호를 맡습니다.

- **ToF** — 침상 이탈 트리거. 환자가 일어서려는 순간을 잡습니다.
- **mmWave** (TI IWR6843AOP) — 보행 패턴 분석.

ESP32와 라즈베리파이가 raw 데이터를 모아 서버로 보내고, 서버는 환자별 baseline 대비 z-score를 계산해 위험도를 4단계(🟢 normal / 🟡 caution / 🟠 warning / 🔴 critical)로 띄웁니다. 절대값이 아니라 "평소보다 얼마나 벗어났나"를 보기 때문에 환자마다 기준이 다르고, 여러 신호 중 가장 높은 위험 단계를 대표값으로 씁니다.

## 저장소 구조

```
MajuBom/
├── mmWave/       # TI IWR6843AOP 설정(.cfg) + 수신 스크립트
├── TOF/          # ToF 센서
├── server/       # 수집 데이터 저장 + 위험도 계산
│   ├── db.py                 baseline / z-score / alert_level
│   ├── mmw_server.py, tof_server.py
│   └── run_all.py            서버 한 번에 실행
└── docs/         # 발표 자료, 대시보드, 논문 리뷰
```

## 처음 받을 때

```bash
git clone https://github.com/b-hyoung/Majubom.git
cd Majubom
pip install -r requirements.txt
```
