# 카메라 + mmWave 동시 녹화 시험 (2026-11-08)

카메라 작업실(`camera-pose`, Pi C920 + Windows USB2.0)과 mmWave 테스트 뷰어를 **같은 Windows 노트북**에서 함께 녹화하고,
두 기록을 PC 시각으로 맞출 수 있는지 확인했다. 결론: **시각 맞추기는 된다(가장 가까운 프레임 차이 중앙 17ms).**
다만 이번 카메라 기록에는 Pi C920 한 대만 저장됐고, 걸터앉음과 침대 위 앉음의 카메라 판정이 실제 자세와 달랐다.

## 장치와 실행

| 항목 | 내용 |
|---|---|
| 카메라 | A: Pi C920 (Pi `bed-pose-camera.service`, 사용자 서비스, MJPEG 8091), B: 노트북 USB2.0 PC CAMERA |
| 카메라 실행 | `camera-pose/Start-PiUsb.ps1` (Windows, Python 3.12, CPU, CLIP ViT-B/16 + MediaPipe) |
| mmWave | Pi 리더(`--target test_bed`, `--raw-tap 5003`) → 노트북 테스트 뷰어가 복사본을 읽어 녹화 |
| 시계 | 두 프로그램 모두 같은 노트북의 PC 시각 |

## Windows에서 고친 것

`camera-pose/tools/start_mac_monitor.py:133`의 `os.fchmod`는 Windows용 Python 3.12에 없어서(3.13부터 있음)
작업실이 시작 직후 `AttributeError`로 멈췄다. `hasattr(os, 'fchmod')`일 때만 호출하도록 고쳤다.
이 때문에 Windows에서는 `outputs/mac-pi-connection.json`(작업실 접근 토큰 포함)에 "나만 읽기" 권한이 걸리지 않는다.
노트북을 혼자 쓴다는 전제에서만 안전하다.

## 시각 맞추기 방법 (`tools/cam_mmw_join.py`)

- 카메라: `live_frames.jsonl`의 `posture_assessment.received_ns`(perf_counter). `control_events.jsonl`의 측정 시작·종료 요청에
  `requested_time_ns`(perf_counter)와 `requested_utc_ns`(UTC)가 함께 있어서, 그 차이로 실제 시각으로 바꾼다.
- mmWave: 뷰어 `frames.jsonl`의 `t`(PC 시각, 초).

```powershell
python tools/cam_mmw_join.py <카메라 측정 폴더> <mmWave 녹화 폴더> --marks 걸터앉음=12:47:34.446 "침대 위 앉음=12:48:12.850" 섬=12:48:56.694 끝=12:49:52.369
```

## 결과

| 항목 | 값 |
|---|---|
| 카메라 기록 | 350프레임, 12:47:17 ~ 12:49:34 (**Pi C920 한 대만 저장됨**, 시계 `53885ef4`) |
| mmWave 기록 | 4,666프레임, 12:45:36 ~ 12:49:54 (`TEST_20261108_124536`) |
| perf→UTC 차이 | 시작 요청과 종료 요청에서 0.4ms 차이(시계 흔들림이 작음) |
| **카메라 프레임 → 가장 가까운 mmWave 프레임** | **중앙 17ms, 95% 45ms, 150ms 안 100%** |

자세 구간별 카메라 안정 판정(Pi C920, 바꾸는 동작 3초 제외):

| 실제 자세 (측정자 알림 시각) | 카메라 판정 | 맞음? |
|---|---|---|
| 걸터앉음 (12:47:34) | 앉음 48, 기타 16, 판정 없음 5 | 아님 (같은 날 녹화 전 시험에서는 A가 걸터앉음 70%) |
| 침대 위 앉음 (12:48:12) | 옆누움 38, 판정 없음 17, 서 있음 13, 기타 12 | 아님 |
| 섬 (12:48:56) | 서 있음 75, 판정 없음 73 | 맞음 |

## 확인된 문제와 다음 할 일

1. **카메라 B(USB)가 기록되지 않았다.** 작업실의 "측정 시작"이 한 입력에만 적용된 것으로 보인다. 두 입력을 한 그룹으로 측정하는 방법을 확인해야 한다.
2. **측정 시작이 늦게 반영된다.** 시작 요청(UTC 기준)과 실제 첫 기록 사이가 약 1분이었다(`requested_time_ns` 723.3s → `time_ns` 785.1s).
   측정자는 화면에 기록이 시작된 것을 확인한 뒤 자세를 시작해야 한다.
3. **기록된 카메라 프레임 수가 처리 속도보다 적다.** 처리 약 10fps, 기록 약 2.6fps.
4. 침대 한가운데의 삼각대가 두 카메라 모두에서 몸통과 다리를 가린다.
5. 이 방식으로 카메라 자세 라벨과 mmWave 점 구름을 짝지을 수 있다. 학습 정답은 `camera-pose/docs/guides/TOF_TRAINING.md`처럼
   사람이 검토한 라벨만 쓴다(카메라 판정은 의사 라벨).

녹화 원본(영상·관절·레이더)은 개인 정보와 용량 때문에 저장소에 올리지 않았다. 위치: 카메라
`camera-pose/recordings/mac-pi-20261107/8bed0a32fbbb4bc3996b0ca95fced751/`, mmWave `mmw_viewer/sessions/TEST_20261108_124536/`(측정 노트북).
