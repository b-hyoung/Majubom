# AI 작업 시작 안내

이 문서는 루트 AGENTS.md에서 필독으로 지정한다. 먼저 이 문서, PROJECT_STRUCTURE.md,
SYSTEM_ARCHITECTURE.md를 읽고, 대상 코드와 호출 경로를 확인한 다음 수정한다.
AGENTS.md를 지원하는 에이전트가 자동으로 규칙을 발견하도록 루트에 배치했다.
Claude는 루트 CLAUDE.md에서 같은 필독 문서들을 가져온다.
지원하지 않는 도구에는 최초 진입점으로 AGENTS.md를 지정해야 한다.

## 실제 장치와 데이터 경로

- ToF: VL53L5CX → ESP32-S3 펌웨어 `TOF/src/main.ino` → 공유기 2.4GHz Wi-Fi →
  유선 LAN의 Raspberry Pi 5 → `POST :5001/tof` → DB/자세·재실 판정.
  한 보드당 센서 하나, 왼쪽 `tof1`, 오른쪽 `tof2`, 8×8 거리 64개.
- mmWave: IWR6843AOP → USB CP2105 듀얼 UART → Pi의 `mmWave/send_mmw.py` →
  `POST :5002/mmw`와 `/mmw/live` → DB/보행 분석.
- 추가 로우데이터 화면은 :5012이며 mmWave/diagnostic_server.py가 센서별 최신 복사본만 메모리에 유지한다.
  기존 ToF 서버와 단일 mmWave 리더에서 비동기 분기하며, 기존 간호사 데이터 경로는 그대로 함께 동작한다.
  이 화면을 변경하기 전 docs/SENSOR_DIAGNOSTICS.md도 읽는다.
- 실측 센서값과 로우데이터 계산 관측은 실제 연결 환자 bed_01 전용이다. 가상 EMR 환자의 기존 시뮬레이션 경로와 섞지 않는다.
- 로우데이터 탭은 ToF/mmWave로 분리하고 운영 계산·DB 저장 행·융합 이벤트와 실제 AI 리포트 실행을 관측한다.
  diagnostic_observer.py/report_trace.py의 추가 기록은 제한된 RAM이며 관측 API는 재평가·리포트 생성·DB 쓰기를 하지 않는다.
- `server/run_all.py`는 HTTP 수신 서버 두 개만 켠다. mmWave 리더는 별도 실행해야 한다.
- `site/index.html`과 `site/dashboard-assets/`가 간호사 대시보드이며 ToF 서버가 제공한다.
- 실측 위험 판정, 실험 모델, 시뮬레이션, LLM 보고서 설명은 서로 다른 경로다.

2026-10-06 현장 확인: Pi 유선 IP `192.168.0.4/24`, 공유기 `192.168.0.1`.
사용자가 말한 2.4GHz SSID는 `majubom`(실제 접속 검증 전).
저장소 펌웨어는 `majubom`과 `192.168.0.4:5001/tof`로 수정했고 비밀번호는 로컬 secrets.h에 입력해야 한다. 보드 업로드는 아직 하지 않았다.
저장소 소스로 현재 보드에 업로드된 바이너리/설정을 단정할 수 없다.
Pi 주소는 DHCP로 변경될 수 있으므로 매 실행 시 확인하거나 공유기에서 DHCP 예약한다.

## 영역별 필독

| 수정 영역 | 추가로 읽을 파일 |
|---|---|
| ToF 펌웨어/배선 | TOF/src/main.ino, TOF/src/platformio.ini, TOF/tof.md, TOF/TROUBLESHOOTING.md, server/tof_server.py |
| ToF 분류/학습 | TOF/ml/README.md, TOF/dataset/README.md, predict_posture.py, roi.py, 해당 학습 스크립트 |
| mmWave 수집/보행 | mmWave/RUN.md, RISK_NOTES.md, send_mmw.py, server/mmw_logic.py |
| mmWave 자세/이탈 실험 | mmWave/POSTURE_NOTES.md, unified_reader.py, posture_probe.py, exit_seq/HANDOFF.md |
| API/DB/판정 | server/server.md, db.py, dashboard_api.py, 해당 서버/logic, integrated_analysis.py |
| 환자 EMR/개인 벡터/예시 데이터 | docs/EMR_DATA_MODEL.md, server/emr_kb.py, demo_emr.py/json, dashboard_api.py, db.py |
| AI 보고서/근거 검색 | server/ai_agent.py, ai_report.py, report_review.py, paper_kb.py, retrieval.py, gait_prior.md |
| 웹 대시보드 | site/DASHBOARD_WORK.md, index.html, dashboard-assets/, server/dashboard_api.py |
| 후보 알고리즘 연구 | research/alternatives/README.md |
| 실험용 8×8 펌웨어 | test_8x8/README.md와 해당 firmware/; 운영 원본은 TOF/src/ |

문서와 코드가 다르면 실제 코드를 확인하고 차이를 문서에 반영한다.
역사 문서의 과거 배선과 설치 조건은 현재 코드의 한 보드 한 센서 구조와 다를 수 있다.
현재 설치 조건: ToF 양쪽 1.2m, 하향 tof1(발) 50°·tof2(머리) 30°(2026-10-08), mmWave 2.5m/하향50°. 현장 방향·침대 영역은 재검증한다.

## Pi 실행

프로젝트 루트에서 실행한다. 현재 `.venv`는 시스템의 numpy/pyserial/requests를 사용하도록
`python3 -m venv --system-site-packages .venv`로 생성했고 Flask/flask-cors를 추가했다.
새 환경에서는 최소 수집 의존성 `flask flask-cors pyserial numpy requests`를 설치한다.
전체 기능 의존성은 requirements.txt와 각 모델 문서도 확인한다.

```bash
ip -brief address
ls -l /dev/serial/by-id/
.venv/bin/python server/run_all.py
```

별도 터미널에서(이미 스트림이 시작된 경우 cfg 생략):

```bash
.venv/bin/python mmWave/send_mmw.py \
  --cli /dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_011D1AD6-if00-port0 \
  --data /dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_011D1AD6-if01-port0 \
  --target test_bed
```

장치 일련번호는 이 Pi에서 확인한 값이다. 다른 장치에서는 다시 확인한다.
최초 전원 인가 후에는 적합한 `.cfg` 전송이 필요할 수 있다(mmWave/RUN.md).
장착 높이/각도/침대 범위를 모른 채 기존 cfg를 현재 환경에 맞는 것으로 단정하지 않는다.
실측 테스트는 test_bed로 보행 baseline을 운영 대상과 분리한다.

## 검증과 완료 조건

- `/tof/log`, `/tof/latest`: 두 센서 ID별 프레임과 수신 시각이 계속 갱신되는지 확인.
- mmWave: UART 바이트와 정상 magic/frame 수신을 먼저 확인. 타겟이 없어도 빈 프레임은 수신 가능.
  `/mmw/log`의 0값 전송이나 보류만으로 USB 수신 성공을 판정하지 않는다.
- 웹: `http://<Pi-IP>:5001/dashboard`; 테스트 화면에서 시뮬레이션 여부도 확인.
- 이번 현장 결과·실행 세션은 `docs/SENSOR_CHECK_2026-10-06.md` 참조.
- 구조 변경 후 `python3 tools/update_project_structure.py` 실행.
- 역할/실행/API 변경 시 문서 설명도 수정하고 `--check`, `git diff --check`로 확인.
- 선택 모델(joblib/sklearn/torch 등)이 없으면 수집 서버는 켜져도 자세/이상탐지 기능은 비활성일 수 있다.
