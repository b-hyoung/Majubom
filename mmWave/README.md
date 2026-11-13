# mmWave 사용설명서

천장에 단 TI IWR6843AOP 레이더 1대로 침상 자세, 보행, 돌아눕기, 침상 이탈 전조를 보는 작업 폴더입니다.
여기서는 장치 연결부터 녹화, 데이터 구조, 분석 스크립트 실행까지 순서대로 정리합니다.

- 설치: 천장, 높이 약 2.7m, 하향 90°(바닥을 봄), 뒤집어 설치
- 레이더 펌웨어: TI 3D People Tracking (설정 파일 `AOP_ceiling_2m5.cfg`)
- 프레임: 약 55ms 간격 (초당 약 18~19프레임)

---

## 1. 폴더 구조

| 경로 | 내용 |
|---|---|
| `send_mmw.py` | 리더. 레이더 USB를 읽어 트랙·보행 지표를 계산하고 서버(5002)로 보냄. 원본 바이트를 `127.0.0.1:5003`으로 복사 |
| `fall_detect.py` | 프레임 단위 낙상 판정 (리더가 호출) |
| `AOP_ceiling_2m5.cfg` | 레이더 설정 (천장 설치용) |
| `viewer/` | 노트북에서 돌리는 실시간 테스트 뷰어 + 녹화 (`viewer_server.py`, `viewer.html`) |
| `data/viewer_sessions/` | 뷰어로 녹화한 원본 43개 (2026-11-01 ~ 11-13) |
| `data/unlabeled_20261031.jsonl`, `data/compare_result.json` | 예전 뷰어 실험 데이터 |
| `analysis/20261113_raw_left_right_arm/` | 원본 프레임 해석, 좌우 옆누움, 돌아눕는 방향, 팔-안전바 실험 스크립트와 결과 |
| `docs/algorithm_report.html` | 자세·보행 알고리즘 비교 보고서 (https://majubom-mmwave.pages.dev 와 같은 내용) |
| `../tools/Record-CamMmw.ps1` | 카메라+mmWave 동시 자세 녹화 / 보행 녹화 (음성 안내) |
| `../tools/Record-LeftRight.ps1` | 좌우 옆누움 녹화 (음성 안내, 같은 자세 반복 가능) |
| `../tools/Record-ArmRail.ps1` | 오른쪽 옆누움에서 팔을 안전바에 올렸다 내리기 녹화 |
| `../tools/match_cam_mmw.py` | 카메라 프레임과 mmWave 프레임을 시각으로 짝짓고 음성 안내로 정답을 붙임 |
| `../camera-pose/recordings/cam-mmw/` | 자세 녹화 9개의 매칭 결과 (`matched.csv`), 참가자 목록 (`participants.json`) |
| `../camera-pose/recordings/cam-mmw-models/` | 자세·보행 알고리즘 비교 스크립트와 결과 (`compare_scripts_20261111/`) |

---

## 2. 장치 켜기

### 2-1. 파이 (192.168.0.4)
리더와 수신 서버는 systemd 서비스로 등록돼 있어 부팅하면 자동으로 켜집니다.

```bash
systemctl is-active majubom-mmw-reader majubom-mmw-server   # 상태 확인
sudo systemctl restart majubom-mmw-reader                   # 리더 다시 시작 (cfg 다시 보냄)
journalctl -u majubom-mmw-reader -f                         # 리더 로그
```

- 리더 실행 인자: `--cfg mmWave/AOP_ceiling_2m5.cfg --target room_01 --height 2.7 --tilt 90 --raw-tap 5003`
- 레이더 설정은 레이더 전원이 새로 켜질 때만 적용됩니다. 설정을 바꿨으면 USB를 뺐다 꽂습니다.
- 뷰어로 녹화할 때 5002 서버는 꺼도 됩니다(`sudo systemctl stop majubom-mmw-server`). 리더는 켜 둬야 합니다. 뷰어가 리더의 원본 복사본(5003)을 읽기 때문입니다.
- ToF 서버(5001)는 서비스로 등록돼 있지 않아 재부팅하면 꺼집니다. ToF와 같이 녹화할 때는 수동으로 켭니다.

### 2-2. 노트북 뷰어
```powershell
$env:PYTHONPATH = 'C:\Majubom\mmWave'
cd C:\Majubom\mmWave\viewer          # 또는 Downloads\mmw_viewer
C:\Majubom\.venv\Scripts\python.exe viewer_server.py --reader C:\Majubom\mmWave\send_mmw.py
```
- 브라우저에서 http://127.0.0.1:8765 를 엽니다.
- 뷰어는 SSH로 파이에 붙어 레이더 원본을 받습니다(`--host pi@192.168.0.4`). 리더가 켜져 있으면 5003 복사본을, 꺼져 있으면 USB를 직접 읽습니다.
- 상태 확인: `curl http://127.0.0.1:8765/api/state` → `connected: true`, `frames` 숫자가 계속 늘어야 정상입니다.
- 뷰어는 녹화를 자기 폴더의 `sessions/`에 저장합니다.

---

## 3. 녹화하기 (음성 안내)

Windows PowerShell에서 실행합니다. 스크립트는 뷰어 녹화를 시작하고, 안내 문장을 읽고, 안내 시각을 `marks.json`에 저장합니다.

```powershell
# 자세 5종(섬, 옆누움, 침대 위 앉음, 누움, 걸터앉음) 각 60초, 순서는 무작위
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-CamMmw.ps1 -Participant lee

# 보행 (원 모양 길: 평소 / 천천히 / 종종걸음)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-CamMmw.ps1 -Gait -Participant lee
# 평소 걷기만 길게 (기준선 수집)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-CamMmw.ps1 -Gait -NormalOnly -HoldSeconds 300 -Participant lee

# 좌우 옆누움 (바로 누움 사이에 왼쪽·오른쪽을 무작위로 각 4번)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-LeftRight.ps1 -Participant lee

# 오른쪽 옆누움에서 오른팔을 안전바에 올렸다 내리기 5번
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-ArmRail.ps1 -Participant lee
```

- 자세 녹화에서 카메라도 쓰려면 먼저 `camera-pose/Start-PiUsb.ps1`로 카메라 작업실을 켜고 "그룹 측정 시작" 직전까지 준비합니다.
- 안내 시각은 음성을 읽기 **직전**에 찍힙니다. 실제 동작은 2~3초 뒤에 시작되므로, 분석할 때는 안내 직후 몇 초를 뺍니다.
- 안내와 다르게 움직였으면 `marks.json`을 고칩니다. 예: `TEST_20261113_194649`는 13번째 안내(오른쪽)에서 왼쪽으로 돌아서 라벨을 고쳤고, 원래 파일은 `marks.original.json`에 있습니다.
- `.ps1`에 한글이 들어 있으면 **UTF-8 BOM**으로 저장해야 Windows PowerShell 5.1이 제대로 읽습니다.

---

## 4. 녹화 데이터 구조 (`data/viewer_sessions/<세션>/`)

| 파일 | 내용 |
|---|---|
| `raw.bin` | 레이더가 USB로 보낸 바이트 그대로 (가장 원본) |
| `frames.jsonl` | 프레임마다 한 줄: 받은 시각 `t`, `raw.bin` 안 위치(`offset`, `length`), 뷰어가 계산한 트랙 값 |
| `session.json` | 설치 높이·각도, 참가자, 시작·종료 시각, 프레임 수 |
| `marks.json` | 음성 안내 시각과 라벨 (자세 정답) |
| `labels.jsonl` | 뷰어 화면에서 누른 라벨 기록 |

- 세션 이름: `TEST_날짜_시각` (안내 녹화), `S0`~`S6`은 예전 시나리오 녹화
- **용량이 커서 gzip으로 올린 파일 3개**: `S4_20261107_154857/raw.bin.gz`, `S4_20261107_154857/frames.jsonl.gz`, `S4_20261107_151507/raw.bin.gz`. 쓰기 전에 `gzip -dk <파일>`로 풉니다. GitHub 파일 크기 제한(100MB) 때문입니다.
- 참가자: `camera-pose/recordings/cam-mmw/participants.json`, 각 세션의 `session.json`·`marks.json`의 `participant`
- 시각은 노트북 시계 기준입니다. 파이 DB(ToF 등)와 맞출 때는 시계 차이를 보정합니다(2026-11-13 측정: 파이가 약 0.29초 빠름).

### 4-1. 원본 프레임(`raw.bin`) 읽는 법
프레임 하나 = 헤더 40바이트 + TLV 블록 여러 개 (리틀 엔디언, TI 3D People Tracking 출력 형식)

| 부분 | 구조 | 내용 |
|---|---|---|
| 헤더 | magic `02 01 04 03 06 05 08 07` + `<8I` | 버전, 전체 길이, 칩 종류, 프레임 번호, CPU 사이클, 반사점 수, TLV 수, 서브프레임 |
| TLV 머리 | `<2I` | 종류 번호, 길이 |
| 1020 반사점 | 단위 `<5f` + 점마다 `<bbhHH` | 고도, 방위, 도플러, 거리, SNR (정수 × 단위 = 실제 값) |
| 1010 트랙 | 트랙마다 `<I27f` (112B) | tid, 위치 3, 속도 3, 가속도 3, 오차 공분산 16, 게이팅, 신뢰도 (레이더 기준 좌표) |
| 1011 점 소속 | 점마다 1바이트 | **직전 프레임** 각 점의 트랙 번호 (253·254·255는 소속 없음) |
| 1012 트랙 높이 | 트랙마다 `<I2f` | tid, 최고·최저 높이 |
| 1021 재실 | `<I` | 1 = 있음 |

- 바닥 기준 좌표 (하향 90°, 뒤집어 설치): `x = −posX`, `y = −posZ`, `z = 높이 − posY`, 수평 속도 `√(velX² + velZ²)`, 세로 속도 `−velY`
- 레이더 바로 앞 약 0.25m 반사점은 레이더 몸체에서 새는 신호라 사람이 아닙니다.
- 해석 예시: `analysis/20261113_raw_left_right_arm/raw_examples/` (프레임 1개 전체, 자세 입력 8개의 원본, 실시간 41프레임)
- 직접 풀어 보기
  - 실시간: `live_to_json.py` (파이 5003에서 받은 바이트를 프레임별 JSON으로)
  - 녹화: `raw_frame.py` (보고서 예시 프레임, `TEST_20261112_155312`의 누움 프레임을 찾아 JSON으로), `raw8.py` (자세 입력 8개의 원본만 모으고 다시 계산해 검증)

### 4-2. 자세 입력 8개와 원본의 관계
| 입력 | 원본 |
|---|---|
| z, x, y | 1010 트랙 위치 + 설치 높이·각도 |
| speed, vz | 1010 트랙 속도 |
| npts | 1011 점 소속 중 환자 트랙 개수 |
| head_z | 1020 반사점 + 1011 소속, 최근 1초 상위 90% 높이의 중앙값 (`viewer_server.py`의 `head_height`) |
| bed_dist | 1010 트랙 위치 + 침대 꼭짓점 (`viewer/bed_corners.json`) |

---

## 5. 분석 스크립트 실행

### 5-1. 자세·보행 알고리즘 비교 (`camera-pose/recordings/cam-mmw-models/compare_scripts_20261111/`)
- 자세: 전처리(EMA, 칼만, Savitzky-Golay, 이동 평균) × 분류(XGBoost 등) 비교, 녹화 하나씩 빼고 시험 → `emaxgb.py`, `grid16.json` 등
- 보행: z-score 등 전처리와 A·S 판정 비교 → `gait_all.py`, `sway_new.py`, `gate_check.py`
- 스크립트 안의 경로가 노트북 기준 절대경로입니다(`C:\Users\tmdal\Downloads\mmw_viewer\sessions`, `C:\Majubom\camera-pose\recordings\cam-mmw`). 다른 PC에서는 `SES`, `ROOT`를 `mmWave/data/viewer_sessions` 등으로 바꿔 쓰세요.
- 실행 위치: 스크립트 폴더 안에서 `C:\Majubom\.venv\Scripts\python.exe <스크립트>`

### 5-2. 2026-11-13 실험 (`analysis/20261113_raw_left_right_arm/`)
```powershell
$S = 'C:\Majubom\mmWave\data\viewer_sessions'
$B = 'C:\Majubom\mmWave\viewer\bed_corners.json'
python lr_analyze.py  "$S\TEST_20261113_193637" $B lr_park.json    # 좌우 옆누움 (park)
python lr_analyze.py  "$S\TEST_20261113_194649" $B lr_lee.json     # 좌우 옆누움 (lee)
python lr_cross.py    .                                            # 사람끼리 바꿔 시험 (lr_*_feats.npz 필요)
python arm_analyze.py "$S\TEST_20261113_201751" $B arm_lee.json    # 팔-안전바 (mmWave)
```
- `tof_arm.py`는 같은 시간대 ToF 원본(파이 DB `tof_readings`에서 꺼낸 JSON)이 필요합니다. ToF 원본 파일은 이 저장소에 넣지 않았습니다.

| 실험 | 결과 (2026-11-13, 사람 1~2명) |
|---|---|
| 좌우 옆누움 (반사점 특징) | 같은 사람 73~80%, 처음 보는 사람 72~82% (찍기 50%) |
| 돌아눕는 방향 (도플러와 좌우 위치의 상관 부호) | 32번 중 29번 |
| 팔을 안전바에 올린 상태 | ToF 91.4%, mmWave 60.7% |
| 팔을 안전바로 뻗는 순간 (가장자리 도플러 상관 부호) | mmWave 10번 중 10번 |

- 결과 JSON: `analysis/20261113_raw_left_right_arm/results/`
- 인원과 반복 수가 적어 일반화하려면 더 많은 사람의 녹화가 필요합니다.

---

## 6. 주의할 점
- 침대 좌표(`viewer/bed_corners.json`, `send_mmw.py`의 `BED_POLYGON`)와 방 범위는 레이더나 가구를 옮기면 다시 재야 합니다.
- 뷰어는 테스트 전용입니다. 운영 경로는 파이의 리더 → 5002 서버 → DB입니다.
- 파이 DB의 원본(`tof_readings`, `mmw_readings`)은 3일 지나면 매일 03:30에 `server/raw_archive/`로 옮겨집니다.
- ToF 원본은 침대에 사람이 있을 때(+이탈 후 10초)만 저장됩니다.
