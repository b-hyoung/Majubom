# 간호사 화면과 로우데이터 화면을 동시에 사용하기

## 두 경로

- 간호사 대시보드: `http://<Pi-IP>:5001/dashboard`.
  기존 ToF/mmWave 수신·DB 저장·환자/자세/보행 분석은 계속 사용한다.
- 로우데이터 대시보드: `http://<Pi-IP>:5012/`.
  ToF 2개 거리 격자 + 상태 코드, mmWave 원시 압축값 + 정제 좌표 + 실제 점군을 표시한다.
  이 분기는 최신 프레임을 메모리에만 유지하고 DB/파일/브라우저 저장소/히스토리에 저장하지 않는다.
  최신 프레임을 내려주는 응답은 `Cache-Control: no-store`다.
- 시스템 구성도: `http://<Pi-IP>:5001/system-architecture`, 원본은 `docs/SYSTEM_ARCHITECTURE.html`.

## 데이터 흐름

```text
ToF → ESP32-S3 → POST :5001/tof ┬→ 기존 DB/판정 → 간호사 화면
                              └→ latest-only 비동기 복사 → :5012 → ToF 로우 격자
mmWave → USB → send_mmw.py ┬→ 기존 :5002/mmw + /mmw/live → DB/판정 → 간호사 화면
                          └→ 프레임 디코딩 + latest-only 비동기 복사 → :5012 → 원시값/점군
```

mmWave DATA UART는 send_mmw.py만 연다. 로우데이터 서버는 기본 모드에서 USB를 열지 않는다.
`server/live_diagnostics.py`가 최대 세 센서의 최신 전송만 유지한다.
진단 서버가 꺼져 있으면 오래된 복사본은 버리고 기존 수집 요청은 대기시키지 않는다.
기존 운영 DB 저장과 로우데이터 분기의 비저장은 동시에 적용되는 서로 다른 동작이다.

## Pi 실행(프로젝트 루트)

```bash
# 터미널 1: 기존 운영 수신 서버
.venv/bin/python server/run_all.py

# 터미널 2: 로우데이터 표시 서버(USB 직접 수신 안 함)
.venv/bin/python mmWave/diagnostic_server.py

# 터미널 3: 단일 USB 리더 → 운영 서버와 로우데이터 서버 둘 다 전송
.venv/bin/python mmWave/send_mmw.py \
  --cli /dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_011D1AD6-if00-port0 \
  --data /dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_011D1AD6-if01-port0 \
  --cfg mmWave/AOP_diag_2m5_d50.cfg --target room_01
```

센서에 cfg가 이미 적용되어 스트리밍 중이면 `--cfg`를 생략한다.
장치 재연결로 포트가 달라질 수 있으므로 `/dev/serial/by-id/`를 다시 확인한다.
`--diagnostics-url ""`이면 추가 로우데이터 분기를 끄고 기존 수집만 한다.
`--height/--tilt/--min-snr`는 프레임을 디코딩하는 send_mmw.py에 지정한다. 기본 릴레이 서버는 수신된 설치 조건을 표시한다.
`--no-send`는 기존 보행/라이브 서버 전송을 끈다(로우데이터는 별도 옵션으로 제어).
단독 USB 진단용 `diagnostic_server.py --data ... --cli ... --cfg ...`도 지원하지만
동시 대시보드 구성에서는 사용하지 않는다. 같은 UART를 두 리더가 열면 안 된다.

## 설치 조건과 좌표

- ToF 둘 다 바닥 기준 높이 1.20m, 수평에서 아래로 tof1(발) 50°·tof2(머리) 30°(2026-10-08).
  `TOF/ml/roi.py`와 3D 뷰어의 각도는 수직 아래를 0°로 정의하므로 70°로 반영했다.
  센서 간 거리/침대 폭/센서 롤 방향은 새 현장에서 아직 재측정하지 않았다.
- mmWave 높이 2.5m, 수평에서 하향 50°.
  `AOP_diag_2m5_d50.cfg`의 `sensorPosition 2.5 0 50`, 원시 압축 점군은
  진단 디코더에서 바닥 기준으로 회전·높이 보정한다.
  트랙 TLV는 센서 펌웨어에서 이미 좌표가 변환되어 추가로 회전하지 않는다.
  기존 .cfg와 침대 영역을 복사한 상태라 실제 감지 영역·트랙 좌표는 현장에서 확인해야 한다.
- mmWave 원시값은 TLV 1020의 압축 정수와 단위, TLV 1010의 트랙 필드다.
  정제값은 유한 숫자·0~20m 유효 거리·SNR 기준 필터 및 좌표 변환 결과다.
  기존 환자별 보행 판정값과는 별개다. 표시 점수는 최신 프레임 최대 2048개, 표는 처음 100개.
- 점군이 없다고 트랙 중심점을 거리 점군으로 대체하지 않는다.
- ToF 원시 거리/상태 코드는 새 펌웨어의 `raw_distances_mm`/`target_status`가 있어야 표시된다.
  구 펌웨어는 유효 거리 격자만 표시하며 원시값 미수신이라고 안내한다.
- ToF 상하/좌우 반전은 원시값으로 설치 방향을 확인하기 위한 화면 기능이다.
  운영 데이터와 학습 모델의 존 순서를 자동 변경하지 않는다. 실제 반전은 기준 물체를 이동해 확인한 후 결정한다.

## 펌웨어 설정

`TOF/src/secrets.example.h`를 `secrets.h`로 복사하고 실제 Wi-Fi 비밀번호를 입력한다.
기본 SSID `majubom`, 서버 `http://192.168.0.4:5001/tof`, 각 보드 ID tof1/tof2.
비밀번호는 Git에서 제외한다. Pi IP가 바뀌면 DHCP 예약 또는 URL 재설정이 필요하다.
펌웨어는 설치 높이/각도와 원시 거리·상태를 추가 전송한다. 기존 `distances_mm`는 유효값 필터를 유지한다.
컴파일은 통과했지만 새 비밀번호 입력/보드 업로드는 아직 하지 않았다.

## 현장 상태(2026-10-06)

이전 기본 cfg에서는 USB 8초간 145프레임 수신했으며 사람 트랙은 없었다.
새 2.5m/50° cfg 적용 뒤 CLI/USB가 응답하지 않아 센서 전원 재연결 후 재설정이 필요하다.
현재 로우데이터 화면은 mmWave 프레임 대기와 ToF 미수신 상태를 실제 그대로 표시한다.
사람이 없어 빈 프레임이 오는 상태와 USB 바이트/프레임 자체가 없는 상태를 구분한다.
ToF 서버는 아직 tof1/tof2 모두 수신이 없으며 새 펌웨어 업로드 후 두 화면에서 재확인해야 한다.


## 계산·저장·리포트 실시간 관측

화면 왼쪽 위의 **ToF / mmWave / 융합·이벤트 / AI 리포트** 탭으로 구분한다.
원시 프레임은 약 200ms, 운영 계산·DB 관측은 1초 간격으로 조회한다.
실측 진단의 침대는 bed_01로 고정한다. mmWave target은 해당 연결 환자의 실제 운영 배정과 일치해야 한다(기본 room_01).
예시 환자 컨텍스트는 관측 API에서 거절하며, 가상 EMR/기존 시뮬레이션과 실측 센서값을 섞지 않는다.
일시정지는 두 조회와 표시값을 함께 멈춘다.
침대의 실제 mmw_target_id와 요청 target이 다르면 센서/다른 환자 기록을 혼합하지 않고 조회를 보류한다.
Z-score가 계산되지 않은 패킷은 비교 미실행으로 표시하며 가상 posterior 비교를 생성하지 않는다.

- `GET :5012/api/operations?bed_id=bed_01&target=room_01`은 세 관측 API를 읽기만 한다.
- `GET :5001/api/diagnostics/tof?bed_id=...`는 최신 수신·보정·실행된 자세 판정,
  실제 알고리즘 입력/출력, DB 저장 결과를 제공한다. 조회 때문에 RF/위험 카운터를 진행하지 않는다.
- `GET :5002/api/diagnostics/mmwave?target=...&bed_id=...`는 실제 evaluate 전후 기준선,
  누적 통계와 정확한 Z-score 비교값·게이트·저장 행을 제공한다.
- `GET :5001/api/diagnostics/reports?bed_id=...`는 실제 AI 실행의 메모리 타임라인을 읽는다.
  조회로 리포트 생성, 유료 API 호출, 보정, DB 저장을 실행하지 않는다.

### 평균의 입력과 실제 저장

mmWave `send_mmw.py`의 최근 6초 선택 트랙 (t,x,y,z,vx,vy) 표본에서
v=√(vx²+vy²), speed=Σv/N, speed_cv=std(v)/mean(v)를 계산한다.
표본 10개 미만은 지표 계산 보류다. 진단은 동일 표본의 N/합/제곱합/평균과 최신 10개 입력을
메모리에만 전달한다. DB 인자에는 이 추가 메타데이터를 넣지 않는다.
기존 운영 `mmw_readings`에는 반올림된 구간 지표와 결과가 저장된다.
FFT 0.5~3Hz 피크로 보폭을 추정하며 실패 0은 prior 유효값 집계에서 제외한다.
현재 height_drop은 max(z)−min(z) 높이 범위이며 시간 순서의 하강만 구분하지 않는다는 한계를 표시한다.

개인 보행 기준선은 reliable/patient_locked/walking 조건을 통과한 **구간 지표**를 누적한다.
n, sum, sumsq, μ=sum/n, σ=max(√(sumsq/n−μ²),floor)를 보여준다.
기존 경로는 이번 구간 반영 후 반올림 기준선과 비교한다. EMR 문헌 prior 경로는 반영 전 기준선과
문헌·개인 posterior를 비교하며, metric별 유효값 전용 v_n/v_sum/v_sumsq도 별도 표시한다.
저장된 baseline 파일과 메모리 기준선 일치 여부, 입력이 실제 반영되었는지도 확인한다.

ToF 최신 유효 거리 평균은 화면 설명용이며 운영 DB에 저장하지 않는다.
빈 침대 baseline은 수동 calibrate의 최신 프레임 복사로, 누적 평균이 아니다.
몸 중심 Y 평균은 baseline 대비 점유존의 보정 좌표를 사용한다.
현재 수신 경로에서 호출하지 않는 ToF 행동 Z-score는 비활성으로 표시한다.
joblib/torch 등 모델 의존성이 없으면 RF/LSTM도 실행 성공으로 표시하지 않는다.

`server/diagnostic_observer.py`는 기존 DB 호출을 한 번 실행한 결과만 관찰하고,
SQLite `mode=ro`/`query_only`로 실제 행을 읽는다. 기존 SQLite 테이블
`tof_readings`, `mmw_readings`, `sensor_clean_data`, `activity_log`, `baseline_history`,
`ai_analysis`의 저장 성공/실패·행 ID·실제 라벨과 조건부 저장을 구분한다.
추가 진단 분기는 새 DB/파일에 쓰지 않는다. 이벤트는 최대 16개 scope ×80개 메타데이터로 제한한다.
두 센서 결합은 기존 저장 리포트의 max 위험 단계와 실제 실행 결과를 보여주며,
센서 부재 상태에서 낙상이나 새로운 실시간 통합 판정을 만들지 않는다.

### AI 실행의 질문·검색·작성

`server/report_trace.py`는 실제 리포트 실행을 감싸 환자별로 분리한 메모리 기록을 제공한다.
생성 검색 질문, 추가 질문, 실제 system/user 프롬프트와 구조화 응답, 질의 임베딩 차원/모델,
LanceDB 테이블/코사인 검색, BM25·RRF 순위와 점수, 선택 근거, 충분성 판단,
통합 위험, 15항목 검증, ai_analysis 저장 ID와 근거 검토를 실행 시에만 표시한다.
모델 내부 추론을 수집하지 않는다. 원시 임베딩 벡터와 키/비밀번호는 표시하지 않는다.
최대 40회 실행 ×160개 이벤트이며 서버 재시작 시 사라진다. 계측 이전 실행을 재구성하지 않는다.
기존 리포트 저장 정책은 유지된다. 현재 환경에서 AI 의존성 부족으로 실제 실행이 실패하면
실패 단계/오류가 표시된다. 진단 기능 검증은 임시 DB/모의 AI를 사용하며 실측 서버에 모의값을 넣지 않는다.
