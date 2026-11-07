# 두 카메라 보정과 실시간 3D 공간 모니터

**현재 기본 방식:** [자동 정적 배경 보정](AUTOMATIC_CALIBRATION.md). 마커 없이 사람/움직임을 제외하며 사용자 시작 지시 후 10초 대기합니다. 아래 초기 패턴 도구는 선택 경로입니다.

**최신 추가:** 인쇄는 필수가 아닙니다. 화면 ChArUco/체커보드/ArUco, 실측 기준점 PnP, 알려진 내부값의 자연 특징 상대 자세, 수동 시각화, GPU/고주사율 데모는 [화면 보정·GPU 안내](SCREEN_CALIBRATION.md)를 우선 참고하세요. 아래는 초기 체커보드 CLI와 기본 좌표/호환성 계약입니다.

[프로젝트 홈](../../README.md) · [현재 상태](../PROJECT_STATUS.md) · [기존 오프라인 깊이 근거](STEREO_DEPTH.md)

2026-11-01 요청의 **2단계(보정·미터 단위 3D 복원), 3단계(Three.js 표시)** 구현입니다. 첨부 요청서의 삼각측량 보류보다 사용자가 이번에 정의한 단계를 우선했습니다. 6자세 분류·ToF 저장·학습은 범위에 포함하지 않습니다. 이후 사용자가 외장 카메라 두 대를 연결해 실제 영상 수신까지 확인했습니다. 실제 보정 촬영·물리 치수·정답 좌표 검증은 체커보드 준비가 필요해 아직 수행하지 않았습니다.

## 설치와 실행

프로젝트 루트에서 기존 실행 방식을 유지합니다. Python 3.11 이상이 필요합니다.

```powershell
python -m pip install -e ".[live,stereo]"
python -m bed_pose live
```

표시된 작업실 주소로 접속한 뒤 상단 **3D 공간 모니터 ↗**를 누릅니다. 서버는 기존 `127.0.0.1` 작업실 하나를 사용합니다. 주소의 `#` 뒤 접근 토큰이 필요합니다. 다른 노트북의 USB 카메라를 자동 연결하는 기능은 추가하지 않았습니다. 실제 촬영 시 프로그램을 실행하는 PC에 두 카메라를 연결하거나 기존 Pi 입력을 사용합니다.

빌드된 프론트엔드를 Python 패키지에 포함했으므로 일반 실행에 Node가 필요하지 않습니다. 프론트엔드를 수정할 때만 Node와 npm을 사용합니다. 이번 빌드 환경은 Node 24입니다.

```powershell
cd tools/room-monitor
npm ci
npm run build
```

Vite 결과는 `src/bed_pose/static/monitor/`입니다. 기존 Python 서버가 파일을 직접 제공하며 별도 FastAPI 서버나 React 개발 서버가 필요하지 않습니다. 수정 후 새로고침하세요.

## 이미지 수집

보정판이 없다면 3D 화면의 **출력용 보정판 열기**를 사용하세요. A4 가로, 배율 100%/실제 크기로 인쇄하는 10×7칸(내부 코너 9×6), 설계 한 칸 25mm의 판입니다. 인쇄 후 자로 칸 크기와 100mm 확인선을 실측하고 실제 크기를 입력하세요. 화면에 띄운 판을 실측 없이 실제 25mm 판으로 취급하면 안 됩니다. 소스는 `tools/room-monitor/public/checkerboard.html`입니다.

1. 기존 측정 작업실에서 카메라 두 대를 추가합니다. 해상도/FPS는 기존 입력 추가 UI를 사용하며 변경하려면 입력을 제거하고 다시 추가합니다.
2. 3D 화면에서 카메라 A/B를 선택하고 설정을 저장합니다. 실제 장치에 A/B를 표시해 둡니다. 미보정 상태에서는 영상·동기화 확인과 촬영만 가능하며 3D 관절은 나타나지 않습니다.
3. 평평한 체커보드를 양쪽에 보이게 놓습니다. **내부 코너 수**와 **실측 한 칸 크기**를 준비합니다. A/B에서 동일한 물리 코너 순서로 검출되어야 합니다. 대칭 체커보드는 반대 방향/뒷면 관찰 때 코너 순서가 뒤집힐 수 있습니다. 시작 코너와 방향을 확인하고 뒷면을 사용하지 마세요. 자동 코너 순서 인증은 제공하지 않습니다.
4. 보드를 정지시킨 뒤 **현재 동기화 쌍 촬영**으로 10쌍 이상 수집합니다. 중앙/가장자리, 다양한 거리와 기울기를 포함합니다. 같은 프레임 쌍의 중복 저장은 거부합니다. 로그에 저장 폴더 이름이 나옵니다. 10쌍은 최소 개수 조건이며 품질 보장이 아닙니다.
5. 한 쌍은 체커보드를 **방 바닥**에 평평하게 놓고 촬영합니다. 첫 내부 코너 위치와 코너 배열의 방향을 실측합니다. 이 촬영 폴더 이름을 `--anchor`로 사용합니다. 침대 위에서 기준 촬영한다면 그 높이를 `--origin`의 Y에 반영해야 합니다.

기본 저장 위치는 `recordings/live/.monitor/captures/<pair-id>/camera_a.png`, `camera_b.png`, `pair.json`입니다. 원본 RGB를 PNG로 저장하며 관절 오버레이를 넣지 않습니다. 메타데이터에 입력 ID·프레임 ID·공통 수신 단조 시각·해상도를 남깁니다. 노출 시각을 측정한 것은 아니므로 보드는 촬영 동안 정지시키세요.

입력을 재연결하면 ID가 바뀝니다. 한 집합에 다른 연결·설치의 촬영을 섞지 마세요. 기존 촬영을 다른 로컬 폴더로 옮기거나 새 `--output-root`를 사용하세요. CLI는 서로 다른 입력 ID 혼합을 거부합니다. 촬영/보정 파일은 Git에 올리지 않습니다.

## 보정 계산과 적용

숫자는 **형식 예시**입니다. 실제 내부 코너 수·실측 칸 크기·기준 촬영 ID로 교체하세요.

```powershell
python -m bed_pose.calibration --images recordings/live/.monitor/captures --columns 9 --rows 6 --square-m 0.025 --anchor 촬영폴더이름 --origin 0 0 0 --yaw-degrees 0 --output outputs/room-calibration.json
```

`--square-m`는 미터입니다(25mm이면 0.025). `--origin X Y Z`는 기준 체커보드 **첫 내부 코너**의 방 좌표이며 보드 바깥 모서리가 아닙니다. yaw=0에서 코너 열 증가 방향은 방 +X, 행 증가 방향은 +Z입니다. `--yaw-degrees`는 Y축 오른손 회전으로 Three.js와 같습니다. 실측 기준 보드의 3D-2D 대응으로 `solvePnP`를 수행하고 스테레오 상대 변환으로 B의 방 변환을 얻습니다.

카메라별 `calibrateCamera`, 고정 내부값 `stereoCalibrate`, `stereoRectify`를 수행합니다. 코너 미검출 쌍은 제외하고 목록을 기록합니다. 유효 쌍 10개 미만/기준 촬영 미검출이면 중단합니다. **양쪽 및 모든 촬영 해상도가 같은 경우**를 지원합니다. 해상도가 다르면 동일하게 맞춰 재촬영하세요. 기존 출력 파일은 덮어쓰지 않습니다.

내부 행렬·왜곡 계수·해상도·월드→카메라 R/t·카메라별 RMS·스테레오 RMS·양쪽 기준 촬영 RMS·E/F·정렬 행렬·Q·시각·촬영 출처를 저장합니다. 공통 시야 부족/작고 흐린 보드에서는 검출이 실패합니다. 큰 보드나 촬영 거리/방향 변경으로 양쪽 코너를 확보하세요. 공통 타깃이 전혀 보이지 않는 배치의 외부 측량/멀티마커 보정은 후속입니다.

3D 화면의 **보정 JSON 불러오기**로 결과를 적용합니다. 버전·행렬·유한수·해상도·회전 직교성/행렬식·기준선·오차를 검사합니다. `calibration_validity=measured`는 출처 선언이며 프로그램의 실측 인증을 뜻하지 않습니다.

같은 실제 카메라를 A/B에 연결하고 고정 위치·렌즈·해상도·영상 변환이 같은지 확인합니다. 같은 사람 한 명이 양쪽에 있는지 확인한 뒤 두 체크박스를 선택하고 설정을 저장합니다. 서버 재시작/보정 교체 시 확인은 해제됩니다. 해상도 불일치는 차단하지만 물리 이동·초점 변화·동일 해상도의 자르기/회전은 자동 감지하지 못하므로 변경 후 재보정해야 합니다.

## 3D와 방 좌표계

원본 픽셀을 `undistortPoints(P=K)`로 왜곡 제거한 뒤 기존 `stereo_depth.triangulate`의 DLT를 사용합니다. MediaPipe 상대 z나 두 단안 3D 평균은 사용하지 않습니다. COCO17 관절의 양쪽 visibility, 제공되는 경우 presence도 확인합니다. visibility 누락은 채우지 않습니다. 한 화면에 사람이 둘 이상이거나 관절이 화면 밖/저신뢰이면 보류합니다. 새 3D 경로는 다인 추적을 지원하지 않습니다.

카메라 뒤쪽·낮은 시차·큰 재투영 오차를 관절별로 보류합니다. 기본 가시성 0.5·재투영 3px·시차 2°는 **미검증 초기값**이며 보정 JSON의 quality로 조정합니다. 카메라별/스테레오/기준 촬영 RMS가 재투영 한계를 초과해도 복원을 중단합니다. 낮은 RMS가 잘못된 사람/대칭 코너 대응·체계적 관절 오차·틀린 칸 크기를 배제하지는 못합니다.

`Skeleton3D`는 `coordinateSystem=room_world`, `unit=m`, `jointDefinition=coco17`, 관절별 valid·confidence·결측 사유·재투영 오차를 제공합니다. confidence는 양쪽 검출 점수 최솟값이며 **3D 오차 확률이 아닙니다**. 기존 자세 판정/기록 라벨을 바꾸지 않습니다. 실시간 3D 영구 기록과 기존 오프라인 `stereo-pinhole-0.1` 파일과의 자동 변환은 후속입니다. 기존 오프라인 내보내기는 유지합니다.

- 오른손 좌표계 X=가로, Y=위쪽, Z=세로, 거리=m입니다. 보정 원점을 방 바닥 모서리로 지정하면 Python과 Three.js가 같은 좌표를 사용합니다.
- 처음 방 4×2.7×5m, 침대 1×0.55×2m는 **DEMO 치수**입니다. 실측값과 치수 출처를 입력하세요. 침대 위치는 부피 중심, 회전은 Y축 각도입니다. 윗면 높이는 중심 Y + 크기 Y/2입니다.
- 바닥·벽·침대·격자·좌표축·선택적 천장, 회전/이동/확대, 초기/위/정면/측면/침대 중심 시점을 지원합니다.
- 카메라 위치는 `-Rᵀt`, 방향과 핀홀 시야는 K와 R/t로 계산합니다. 미보정 카메라의 임의 위치/시야는 표시하지 않습니다. 왜곡된 원본 영상 외곽과 핀홀 프러스텀은 다를 수 있습니다.
- 설정은 `<output-root>/.monitor/room.json`, 보정은 `calibration.json`입니다. 설정 적용 시 버퍼/통계를 초기화합니다.

## API와 시간 계약

기존 HTTP 서버와 토큰/동일 출처 검사를 재사용합니다. 별도 서버와 WebSocket은 추가하지 않았습니다. 화면은 요청 완료 후 약 350ms 간격으로 상태/영상을 읽고 실패 시 관절/영상을 비우며 재연결합니다. 이는 카메라 처리 속도와 다릅니다. 더 빠른 화면 갱신이나 전송 최적화가 필요하면 WebSocket과 영상 스트림을 후속으로 연결합니다.

| 경로 | 역할 |
|---|---|
| GET `/monitor` | React/Three.js 화면 |
| GET `/api/room/status` | 설정·보정·동기화·3D·차단 사유 |
| POST `/api/room/config` | 설정 검증/저장/적용 |
| POST `/api/room/calibration` | 보정 JSON 검증/저장/적용 |
| POST `/api/room/capture` | 동기화 RGB 쌍 저장 |
| 기존 `/api/state`, `/api/view/{id}` | 입력 상태/분석 프레임 영상/관절 |

엔진별 상대 `received_ns`에 `engine.origin`을 더해 **같은 PC의 perf_counter_ns**로 변환합니다. 상대 시각을 직접 비교하지 않습니다. 입력별 최대 8프레임의 현재 버퍼에서 가장 가까운 조합을 온라인 선택합니다. 미래 프레임까지 고려하는 전역 최적 매칭은 아닙니다. 허용 차이 초과는 대기하고 오래된 프레임/버퍼 초과/선택 쌍 이전 프레임을 제외합니다. 사용한 프레임은 재사용하지 않습니다.

수신 기준이며 노출 시각/하드웨어 누락을 측정하지 않습니다. 네트워크 입력에는 전송 지연 편차도 있습니다. paired FPS는 설정 이후 누적 평균, 매칭 비율은 `2×쌍 수/수신 분석 프레임 수`입니다. 버퍼 제외 수는 하드웨어 드롭 수가 아닙니다. 최신 쌍이 max_age_ms(초기 1500ms)를 넘거나 입력 종료/오류이면 3D를 숨깁니다. 영상 파일/합성 입력은 상태/영상 확인에 사용할 수 있지만 실시간 3D/보정 촬영은 차단합니다. 녹화 두 영상의 PTS 기반 3D 재생은 후속입니다.

## 실제 코드 분석과 호환성

| 항목 | 기존 코드와 이번 연결 |
|---|---|
| 실행 | `__main__` → `cli.main` → `live_web.launch_browser`, 기존 명령 유지 |
| 카메라·동시 수집 | `live_sources`, `latest_camera`, 입력별 `LiveWorker` 유지 |
| 추론·좌표 | `live_engine`, `pose/mediapipe`, `mapping`의 픽셀 COCO17/SDK 점수 재사용 |
| 시간·그룹 | `group_session`, `time_alignment`, `fusion_inputs` 유지. 화면용 기하 버퍼 추가 |
| CLIP | `clip_posture`, `clip_pool`, `body_state` 유지. 새 3D 계산에는 미사용 |
| 자세 | automatic/multiview/evidence 기존 규칙 유지. 새 6자세 미추가 |
| 대상 | `target_tracking`, `bed_association` 유지. 새 3D는 한 명 제한/운영자 확인 |
| 침대 | 기존 영상 네 점 영역 유지. 미터 단위 침대는 별도 실측 설정 |
| 서버·UI | 기존 `live_web` 루프백 서버/인증/HTML 유지, `/monitor` 추가 |
| 기하 | 기존 `stereo_depth.triangulate` 재사용, 보정/왜곡 제거/방 좌표 추가 |

추가 모듈은 `calibration.py`, `room_monitor.py`, 프론트 소스 `tools/room-monitor`, 정적 결과 `src/bed_pose/static/monitor`입니다. `pyproject.toml`에 OpenCV 4 선택 설치와 정적 파일 패키징을 추가했습니다. Windows/Linux 공통 API를 사용하지만 이번 실행 검사는 Windows에서 했습니다. 참조: [OpenCV 공식 보정·3D API](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html), [Three.js OrbitControls](https://threejs.org/docs/pages/OrbitControls.html).

## 검증과 다음 작업

새 테스트는 합성 체커보드 검출/실패, 합성 내부·스테레오·바닥 기준 보정, 왜곡 픽셀의 미터 복원, 결측/재투영 실패, 파일 검증, 시간 매칭/재사용 방지/버퍼/오래된 쌍, 해상도 불일치, 설정 복원, 캡처 중복, visibility/다인 보류, API 접근 제어와 합성 두 엔진 연결을 다룹니다. 실제 장치 성공을 의미하지 않습니다.

TypeScript/Vite 빌드와 브라우저의 미연결 3D 방/침대, 미보정 표시, 침대 위치 저장 후 새로고침 복원, 위 시점, 좁은/데스크톱 배치를 확인했습니다. 실제 두 카메라/연결 해제/보정 촬영/방 실측/3D 정확도/장시간 성능은 사용자 장비로 수행해야 합니다.

추가 실장비 검사에서 Windows의 HD Pro Webcam C920(DirectShow 701), USB2.0 PC CAMERA(702)를 식별하고 각각 640×480을 수신했습니다. 두 장치를 동시에 열어 독립 스레드로 각 25프레임 수신도 확인했습니다. 이는 노출 동기화/MediaPipe 추론/보정/3D 정확도 검사가 아닙니다. 현재 양쪽 영상은 같은 침대를 보지만 체커보드는 없었습니다.

다음은 출력한 보정판을 준비하고 카메라를 최종 위치에 고정해 촬영한 뒤 알려진 좌표 타깃으로 거리/재투영 오차를 검증하는 일입니다. 결과를 바탕으로 6자세 분류, 신뢰도 정책/3D 기록, ToF 저장, CNN/분류기 비교를 별도 진행합니다.

### 이번 실행의 검사 결과

- 새 보정/실시간/API 검사 9개 통과. 기존 스테레오 검사 7개 통과.
- TypeScript 검사·Vite production build·Python wheel 빌드 통과. wheel에 모니터 HTML/JS/CSS와 출력용 보정판 포함 확인.
- 전체 Python 검사 464개 중 458개 통과, 5개 선택 의존성 검사 건너뜀, 기존 그룹 마감 비동기 검사 1개 대기시간 초과. 해당 검사 `test_group_auto_predictions_recompute_and_tampering_is_rejected`는 독립 재실행에서 통과했습니다. 전체 일괄 통과로 보고하지 않습니다.
- 기존 Windows 테스트 2곳의 이식성 문제(UTF-8 명시, 원본 바이트 보존)를 수정했습니다. 기록 검증 기준은 변경하지 않았습니다.
- 실제 외장 두 카메라의 단일/동시 수신 검사 후 MediaPipe 0.10.35와 공식 Full 모델을 준비하고 실제 작업실의 두 입력과 화면에 연결했습니다. 두 입력 모두 프레임 처리와 수신 기준 시간 매칭이 진행됨을 확인했습니다. 관찰 시점에는 사람이 검출되지 않았으며 실제 사람의 관절 정확도 검사는 아닙니다.
- 새 기능이 기존 CLIP을 제거하거나 대체하지 않습니다. 이번 PC 연결 점검에서는 CLIP을 선택하지 않았습니다.
- 보정판이 없어 실제 보정값은 생성/적용하지 않았고, 방/침대 치수도 DEMO 상태입니다.

### 변경 파일 목록

- Python: `src/bed_pose/calibration.py`, `room_monitor.py`, 기존 `live_web.py`, `templates/live_workspace.html`, `pyproject.toml`.
- 프론트엔드: `tools/room-monitor/package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.js`, `index.html`, `src/main.tsx`, `src/scene.ts`, `src/types.ts`, `src/monitor.css`, `public/checkerboard.html`.
- 배포용 정적 파일: `src/bed_pose/static/monitor/index.html`, `monitor.js`, `monitor.css`, `checkerboard.html`.
- 검사: 새 `tests/test_room_monitor.py`, 기존 `tests/test_camera_decoupling.py`, `tests/test_multiview_posture.py`.
- 안내: `README.md`, `ROADMAP.md`, `docs/PROJECT_STATUS.md`, `docs/DECISIONS.md`, `docs/guides/ROOM_MONITOR.md`, `docs/guides/STEREO_DEPTH.md`.

현재 작업 폴더에는 Git 메타데이터가 없어 커밋/업로드는 수행하지 않았습니다. 카메라 점검 프레임·스크린샷·빌드 wheel은 무시되는 로컬 `outputs/`에, 모델은 무시되는 `checkpoints/`에만 보관했습니다.
