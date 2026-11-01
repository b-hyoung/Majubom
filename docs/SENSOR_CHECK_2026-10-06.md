# 2026-10-06 Raspberry Pi 5 현장 점검

## 확인한 환경

- Raspberry Pi 5 Model B Rev 1.1, Linux ARM64, VS Code/Codex 확장.
- 프로젝트 `/home/pi/Documents/Github/Majubom`.
- eth0 유선 연결 `192.168.0.4/24`, 기본 게이트웨이 `192.168.0.1`; wlan0 비연결.
- USB Silicon Labs CP2105 듀얼 UART: `/dev/ttyUSB0`(if00 CLI), `/dev/ttyUSB1`(if01 DATA).
- 사용자는 ToF가 ESP32에 연결되고 공유기 2.4GHz SSID가 `majubom`일 것이라고 설명함.
  이 SSID와 ESP32 실제 연결은 아직 검증하지 못함. Pi에서 ESP32 USB 장치는 확인되지 않음.

## 수행한 작업

- 프로젝트 `.venv` 생성(system-site-packages 사용).
- Flask 3.1.3, flask-cors 6.0.2, Werkzeug 3.1.8 설치.
  numpy/pyserial/requests는 기존 시스템 패키지 사용.
- `server/run_all.py` 실행, 0.0.0.0:5001/5002 리슨 확인.
- 유선 IP로 `/dashboard`, `/tof/latest` 요청 HTTP 200 확인.
- 로그: `/tmp/majubom-servers.log`, `/tmp/majubom-mmwave.log`.
- 서버와 USB 리더는 현재 작업 세션에서 실행하며 부팅 자동 실행 서비스는 생성하지 않음.
- 자세 모델(joblib/sklearn), LSTM-AE(torch) 미설치로 관련 선택 기능은 비활성.
  이번 검증은 센서 수집 경로에 한정하며 전체 AI 기능 실행 성공을 뜻하지 않음.

## ToF 결과

서버 실행 후 `/tof/log`는 빈 목록, `/tof/latest`는 tof1/tof2 모두 received_at=null.
실측 ToF 수신 확인 실패. 저장소 `TOF/src/main.ino`에는 이전 Wi-Fi 설정과
`http://192.168.6.10:5001/tof`가 남아 있어 현재 Pi 유선 주소와 다름.
단, 저장소 소스와 실제 보드 플래시 내용을 동일한 것으로 단정하지 않음.

다음 단계: 새 2.4GHz SSID/비밀번호 확인, SERVER_URL을 현재 Pi 주소로 설정,
ESP32 두 보드 각각 tof1/tof2 구분해 업로드, 공유기 LAN 격리 확인 후 두 ID 실측 수신 재검증.
현재 비밀번호를 몰라 추측값으로 펌웨어를 수정하거나 업로드하지 않음.

## mmWave 결과

최초 DATA UART 8초 관찰: 0바이트, 프레임 magic 0개.
CLI `sensorStart` 응답:

```text
Error: Full configuration must be provided before sensor can be started the first time
```

실행 가이드에 지정된 `mmWave/AOP_bed_2m7_d15.cfg`를 CLI로 전송하고
`send_mmw.py --target test_bed`를 실행했다. 이 cfg는 센서 높이 3m/하향 15°를 가정하며,
실제 설치 높이·각도·침대 영역에 맞춘 보정은 아직 수행하지 않았다.
HTTP POST만으로 실측 수신을 단정하지 않고 추가 UART 프레임 점검으로 구분한다.

설정 전송 후 별도로 DATA UART를 8초 동안 관찰해 다음을 확인했다.

- 실제 UART 수신: 9,408바이트.
- 정상 파싱 프레임: 145개, frameNum 497 → 641.
- 타겟 관측: 0개(사람 트랙 없음).
- mmWave 서버: test_bed 대상 POST HTTP 200, measuring_held=true, reliable=false.
- /mmw/live는 targets=[], ts=0: 타겟이 없어서 라이브 좌표 전송이 이루어지지 않음.

따라서 USB 센서 프레임 수신과 서버 전송 경로는 확인했다.
사람 위치·보행 값은 아직 검증하지 못했고 현재는 데이터 부족/판정 보류다.
UART 확인 중 리더를 잠시 종료했고 확인 후 cfg 재전송 없이 다시 실행해 두었다.
ToF /tof/log는 이 시점에도 비어 있었다.


## 후속 변경: 두 화면 동시 경로

기존 운영 수집기 send_mmw.py와 간호사 서버 경로를 유지하고,
ToF와 mmWave 프레임을 :5012 로우데이터 서버로도 비동기 복사하는 구조로 변경했다.
현재 기본 진단 서버는 USB를 열지 않고 HTTP 복사본만 메모리에 유지한다.
기존 본문에 있는 단독 USB 진단/3m15도는 초기 점검 기록이다.
최신 실행/배치와 현장 제한은 SENSOR_DIAGNOSTICS.md를 기준으로 한다.
ToF 새 펌웨어 컴파일은 통과했으며 보드 업로드와 실제 수신은 대기 중이다.
mmWave는 2.5m/50도 설정 후 응답이 없어 전원 재연결 후 실제 프레임 재확인이 필요하다.
