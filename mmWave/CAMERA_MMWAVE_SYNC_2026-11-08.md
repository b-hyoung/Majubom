# 카메라 + mmWave 동시 녹화 시험 (2026-11-08)

카메라 작업실(`camera-pose`, Pi C920 + Windows USB2.0)과 mmWave 테스트 뷰어를 **같은 Windows 노트북**에서 함께 녹화하고,
두 기록을 PC 시각으로 맞출 수 있는지 확인했다. 결론: **시각 맞추기는 된다(각 카메라와 가장 가까운 mmWave 프레임 차이 중앙 17~24ms).**
첫 시험은 Pi C920 한 대만 저장됐고, 두 번째 시험(그룹 측정)에서 두 카메라를 함께 기록했다.
카메라 판정은 "섬"만 대체로 맞았고, 걸터앉음과 침대 위 앉음은 단독·통합 판정 모두 실제 자세와 달랐다.

## 장치와 실행

| 항목 | 내용 |
|---|---|
| 카메라 | A: Pi C920 (Pi `bed-pose-camera.service`, 사용자 서비스, MJPEG 8091), B: 노트북 USB2.0 PC CAMERA |
| 카메라 실행 | `camera-pose/Start-PiUsb.ps1` (Windows, Python 3.12, CPU, CLIP ViT-B/16 + MediaPipe, GPU 없음) |
| mmWave | Pi 리더(`--target test_bed`, `--raw-tap 5003`) → 노트북 테스트 뷰어가 복사본을 읽어 녹화 |
| 시계 | 두 프로그램 모두 같은 노트북의 PC 시각 |

## Windows에서 고친 것

`camera-pose/tools/start_mac_monitor.py:133`의 `os.fchmod`는 Windows용 Python 3.12에 없어서(3.13부터 있음)
작업실이 시작 직후 `AttributeError`로 멈췄다. `hasattr(os, 'fchmod')`일 때만 호출하도록 고쳤다.
이 때문에 Windows에서는 `outputs/mac-pi-connection.json`(작업실 접근 토큰 포함)에 "나만 읽기" 권한이 걸리지 않는다.
노트북을 혼자 쓴다는 전제에서만 안전하다.

## 시각 맞추기 방법 (`tools/cam_mmw_join.py`)

- 카메라: `live_frames.jsonl`의 `posture_assessment.received_ns`(측정 기준 perf_counter). `control_events.jsonl`의 측정 시작·종료 요청에
  `requested_time_ns`(perf_counter)와 `requested_utc_ns`(UTC)가 함께 있어서, 그 차이로 실제 시각으로 바꾼다.
- mmWave: 뷰어 `frames.jsonl`의 `t`(PC 시각, 초).
- 그룹 파일(`alignment.jsonl`, `posture_predictions.jsonl`)의 시각은 `perf_counter` **절댓값**이다. 실제 시각으로 바꿀 때는
  카메라 `manifest.json`의 `clock_origin_perf_counter_ns`를 빼고 위의 차이를 더한다.

```powershell
python tools/cam_mmw_join.py <카메라 측정 폴더> <mmWave 녹화 폴더> --marks 걸터앉음=12:47:34.446 "침대 위 앉음=12:48:12.850" 섬=12:48:56.694 끝=12:49:52.369
```

## 첫 시험: 한 화면 측정 (12:46~12:50)

| 항목 | 값 |
|---|---|
| 카메라 기록 | 350프레임, 12:47:17 ~ 12:49:34 (**Pi C920 한 대만 저장됨**) |
| mmWave 기록 | 4,666프레임, 12:45:36 ~ 12:49:54 (`TEST_20261108_124536`) |
| perf→UTC 차이 | 시작 요청과 종료 요청에서 0.4ms 차이(시계 흔들림이 작음) |
| **카메라 프레임 → 가장 가까운 mmWave 프레임** | **중앙 17ms, 95% 45ms, 150ms 안 100%** |

| 실제 자세 (측정자 알림 시각) | A 안정 판정 (바꾸는 동작 3초 제외) |
|---|---|
| 걸터앉음 (12:47:34) | 앉음 48, 기타 16, 없음 5 |
| 침대 위 앉음 (12:48:12) | 옆누움 38, 없음 17, 서 있음 13, 기타 12 |
| 섬 (12:48:56) | **서 있음 75**, 없음 73 |

- **B가 빠진 원인:** 화면 하나의 "측정 시작"을 눌렀기 때문이다(`manifest.json`의 입력이 `raspberry_pi` 하나, `mode=free`).
- 측정 시작 요청과 첫 기록 사이가 약 1분이었다(`requested_time_ns` 723.3s → `time_ns` 785.1s). 기록 프레임은 처리 속도(약 10fps)보다 훨씬 적은 약 2.6fps였다.

## 두 번째 시험: 두 카메라 그룹 측정 (13:02~13:06)

두 카메라를 함께 기록하려면 **두 카메라 통합 판정 설정 → 두 입력 선택 → 양쪽 침대 영역 확인 → 허용 수신 시각 차이 입력 → 그룹 측정 시작** 순서로 한다
(`camera-pose/docs/guides/LIVE_MEASUREMENT.md:53`, `:125`). 이번 설정: 허용 수신 시각 차이 150ms, 블랙박스 끔, 두 카메라 영상 녹화 켬.

| 항목 | A: Pi C920 | B: 노트북 USB |
|---|---|---|
| 기록 프레임 | 808 (약 4.2fps) | 1,601 (약 8.4fps) |
| 기록 구간 | 13:02:46 ~ 13:05:56 | 13:02:46 ~ 13:05:56 |
| mmWave와 가장 가까운 프레임 차이 | 중앙 20ms, 95% 48ms, 150ms 안 100% | 중앙 24ms, 95% 47ms, 150ms 안 99% |

- 두 카메라가 같은 순간에 기록을 시작했고, 첫 시험의 1분 지연은 없었다.
- **그룹 짝짓기(A↔B, `alignment.jsonl`):** 664쌍 짝지음. 짝지어진 쌍의 차이 중앙 129ms, 최대 150ms로 **허용 한계에 몰렸고**,
  A 808프레임 중 144개(18%)가 짝을 못 찾았다. 두 카메라 사이의 일정한 밀림은 거의 없었으므로(가장 가까운 B까지 부호 있는 차이 중앙 −13ms)
  네트워크 지연 때문이 아니다. 측정 중 처리 속도가 측정 전(A 약 10fps, B 약 29fps)보다 크게 떨어져 프레임이 띄엄띄엄 저장된 것이 원인이다.
  이 노트북은 GPU가 없어 CPU로 두 카메라의 MediaPipe·CLIP과 영상 녹화를 함께 했다.

| 실제 자세 (바꾸는 동작 3초 제외, 안정 판정) | A: Pi C920 | B: 노트북 USB | 두 카메라 통합 (`posture_predictions.jsonl`) |
|---|---|---|---|
| 걸터앉음 (13:03:14) | 앉음 154, 기타 23 | 앉음 55, 옆누움 46, 없음 35, **걸터앉음 25**, 기타 24 | 없음 170, 앉음 56, 걸터앉음 4 |
| 침대 위 앉음 (13:04:13) | 기타 95, 없음 41, 서 있음 27, **앉음 19** | 기타 147, 서 있음 26 | 없음 184, 기타 52 |
| 섬 (13:05:14) | **서 있음 124**, 없음 61 | 없음 448, **서 있음 78** | 없음 551 |

- 통합 판정은 대부분 보류(없음)였다. 한쪽 카메라가 침대 위 사람을 못 찾으면(`bed_person_unavailable_or_ambiguous`) 보류하는 방식이라,
  B가 침대 밖에 선 사람을 못 보는 "섬" 구간은 전부 보류였다. 그룹 설정에는 `posture_prediction.status=not_implemented`로 표시되어 있어 통합 판정은 실험 단계다.

## 정리와 제안

1. 카메라 두 대와 mmWave를 PC 시각으로 맞추는 것은 된다(각 카메라↔mmWave 대부분 50ms 안).
2. 허용 수신 시각 차이는 **250ms**를 권한다. 150ms에서는 짝이 한계값에 몰리고 A 프레임 18%를 잃었다.
   ToF 학습의 카메라 짝짓기도 250ms를 쓴다(`camera-pose/src/bed_pose/tof_learning/session_train.py:219`).
3. 학습 자료를 모을 때는 영상 녹화를 끄거나 GPU가 있는 노트북에서 측정해 프레임 수를 늘린다.
4. 카메라 판정(단독·통합 모두)은 정답으로 쓰기 어렵다. 학습 정답은 측정자가 알린 자세 시각(검토 라벨)으로 하고,
   카메라는 자세가 바뀐 순간을 프레임 단위로 잡는 보조로 쓴다(`camera-pose/docs/guides/TOF_TRAINING.md`의 검토 라벨 원칙과 같음).
5. 침대 한가운데의 삼각대가 두 카메라 모두에서 몸통과 다리를 가린다.

녹화 원본(영상·관절·레이더)은 개인 정보와 용량 때문에 저장소에 올리지 않았다. 위치(측정 노트북, 카메라는 `camera-pose/recordings/mac-pi-20261107/` 아래):
- 첫 시험: 카메라 `8bed0a32fbbb4bc3996b0ca95fced751/`, mmWave `mmw_viewer/sessions/TEST_20261108_124536/`
- 두 번째 시험: 카메라 A `e7d2c2302c334cafb6c17f0f3a87dfe5/`, B `342b12cd2c5b42ada2ea089b4c4e0670/`, 그룹 `groups/77f7de54a2fd46eb845c59d26c017644/`,
  mmWave `mmw_viewer/sessions/TEST_20261108_130233/`
