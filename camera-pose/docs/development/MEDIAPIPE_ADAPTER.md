# MediaPipe 실험용 어댑터

[문서 목차](../README.md) · [분야 목차](README.md) · [현재 상태](../PROJECT_STATUS.md)


**HTML 작업실 보조 기능 추가 · 2026-10-28:** `MediaPipeAdapter(silhouette=True)`로 분할 출력을 활성화하고 희소 표본을 별도 `PoseOutput.silhouette_observation`에 전달합니다. 기본 생성자·오프라인 benchmark는 기존 마스크 꺼짐을 유지합니다. 원본 관절 구조와 기존 최종 자세 판정은 보존하고, 보조 읽기 실패는 별도 확인 불가로 처리합니다. [출처·설정·검증 한계](../design/SILHOUETTE_SUPPORT.md).

[프로젝트 홈](../../README.md) · [진행 현황](../PROJECT_STATUS.md) · [비교 실험 계획](../research/POSE_BENCHMARK_PLAN.md)

**2026-10-27: 코드 연결·SDK 경계 검사 완료, 실제 모델 추론 성공·등록 게이트·침대 성능 검증 미완료.** MediaPipe의 최종 채택을 의미하지 않습니다.

## 구현 범위

- 선택 의존성 `mediapipe==0.10.35`. 기본 합성 실행은 이 패키지를 요구하지 않습니다.
- `independent_frames` → Tasks IMAGE, `ordered_sequence` → Tasks VIDEO. SDK LIVE_STREAM 비동기 모드는 미구현입니다. 현재 실시간 작업실은 카메라 RGB를 VIDEO 경로에 연결합니다.
- 첫 연결은 명시적 CPU delegate만 지원합니다. GPU 요청은 거부하며 자동으로 CPU로 바꾸지 않습니다. 프로젝트 전체의 실행 장비를 확정한 것은 아닙니다.
- 로컬 `.task` 자산 하나(`role=pose_landmarker_task`)와 옵션 JSON의 SHA-256을 확인합니다. 자산은 검증한 바이트로 SDK에 전달하고 자동 다운로드하지 않습니다.
- `num_poses`, 세 confidence 설정을 명시합니다. 생성 옵션의 0.5는 SDK 기본값을 기록한 출발점이며 침대 관절 품질의 합격 기준이 아닙니다.
- 원본 33점의 x/y/z·visibility·presence·name을 image/world별로 보존합니다. 공통 17점은 image x/y를 원본 W/H로 한 번만 변환합니다. 누락·비유한 값·화면 밖 처리는 공통 매핑 정책을 사용합니다.
- 공통 순서: `[0,2,5,7,8,11,12,13,14,15,16,23,24,25,26,27,28]`. 추정 world 좌표는 센서 실측 거리로 사용하지 않습니다.
- VIDEO는 SDK 내부 추적을 사용하며 공식 graph의 단일 pose VIDEO 경로에는 평활화가 있습니다. `num_poses>1`에서는 이 평활화를 켜지 않습니다. 외부 평활화는 추가하지 않았습니다.
- reset은 task를 닫고 재생성합니다. warm-up 이후도 별도 재생성하고 설정 동일성을 확인합니다.
- 후보 ID `p0`, `p1`은 프레임 안에서만 유효합니다. SDK 결과 순서가 같은 사람을 유지한다고 가정하지 않으며 자동 대상 추적·자세/이탈 판정은 없습니다.
- 실제 SDK 호출 여부를 성공·실패 프레임에 보존합니다. 매핑 실패도 이미 수행한 SDK 호출을 지우지 않습니다.
- `execution_kind=model_inference`는 실제 어댑터를 요청한 실행 종류입니다. 성공 여부는 `run_status`로 확인합니다. 구현 검증 수준은 profile의 `implementation_validation=sdk_boundary_tests_only_model_execution_pending`에 별도로 기록합니다.

## 준비된 모델이 있을 때의 명령

아래는 인터페이스 안내입니다. 현재 환경에서는 유효한 모델 자산으로 추론까지 실행하지 못했습니다. 모델 제공처와 사용 조건을 확인한 로컬 파일을 사용하며, 모델·영상·실험 결과는 공개 GitHub에 올리지 않습니다.

```sh
python -m pip install -e ".[mediapipe]"
python -m bed_pose prepare --video videos/example.mp4 --output data/input_01
python -m bed_pose configure-mediapipe --config data/input_01/run_config.fake.json --model checkpoints/pose_landmarker_full.task --output data/mediapipe_01 --num-poses 1 --execution-mode independent_frames
python -m bed_pose run --config data/mediapipe_01/run_config.mediapipe.json
```

설정 생성 폴더는 새 폴더여야 합니다. 생성된 옵션을 변경하면 설정의 옵션 해시도 다시 계산해야 합니다. 기본 `demo`는 계속 합성 전용이며 실제 모델로 자동 전환되지 않습니다. 실제 모델 설정에 `--fake-*` 비기본 옵션을 주면 거부합니다.

## 실제 수행한 검증과 남은 제약

- 기존 29개 개발 검사를 재실행했습니다.
- SDK 경계를 모의한 10개 검사 추가: 공통 관절 순서·픽셀 변환, 원본 필드·점수 보존, 시간 순서·reset, 정상 0명, 잘못된 출력·SDK 예외, 모델/옵션 해시, 장치·버전 거부, warm-up·정식 기록 연결, 매핑 실패의 호출 여부, 설정 CLI. 전체 39개 통과. **모의 SDK 응답은 실제 추론 증거가 아닙니다.**
- Linux/Python 3.12에서 실제 0.10.35 SDK 설치, RGB Image 생성, landmark dataclass 필드, 옵션 생성, 유효하지 않은 모델 바이트의 SDK 거부를 확인했습니다.
- 최초 native 로딩에서 `libGLESv2.so.2` 누락을 발견했습니다. 이 개발 환경에서는 Debian `libgles2`를 임시 디렉터리에 추출하고 해당 프로세스의 라이브러리 경로로 공급해 확인했습니다. 저장소 코드에 특정 임시 경로를 넣지 않았습니다. Linux 최소 환경은 CPU 실행에도 SDK의 EGL/GLES 공유 라이브러리가 필요할 수 있습니다. Windows는 별도 실동작 검증 전입니다.
- 공식 Full task URL `https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task` 다운로드는 현재 실행 환경에서 HTTP 403으로 실패했습니다. 유효 모델 해시·모델 사용 조건·실제 추론 출력·속도는 미검증입니다. SDK 소스의 라이선스만으로 모델 자산의 사용 조건을 확정하지 않습니다.
- wheel 빌드·설치 후 저장소 밖에서 합성 demo/inspect와 설정 CLI를 실행했습니다. 실제 SDK에 유효하지 않은 모델을 전달한 실행이 명시적으로 실패하고, 6프레임을 모두 미처리로 보존한 완결 ledger가 inspect를 통과함을 확인했습니다. 모델 오류를 합성 결과로 대체하지 않았습니다.
- 다음 등록 게이트: 공식 자산과 사용 조건 확인 → 모델 로딩·사람 없는 영상/사람 있는 영상 추론 → 원본/공통 좌표·시간·다중 후보 실제 출력 검토. 이후에도 침대 영상·정답 기반 평가가 별도로 필요합니다.

## 직접 확인한 공식 자료

| 자료 | 확인한 내용 / 한계 |
|---|---|
| [Tasks Python v0.10.35](https://github.com/google-ai-edge/mediapipe/blob/v0.10.35/mediapipe/tasks/python/vision/pose_landmarker.py) | Options, IMAGE/VIDEO 메서드와 ms timestamp, image/world 결과 자료형. 설치된 SDK signature도 대조 |
| [Tasks graph v0.10.35](https://github.com/google-ai-edge/mediapipe/blob/v0.10.35/mediapipe/tasks/cc/vision/pose_landmarker/pose_landmarker_graph.cc) | use_stream_mode 추적 및 num_poses=1의 smooth_landmarks 설정 |
| [33점 명칭 enum v0.10.21](https://github.com/google-ai-edge/mediapipe/blob/v0.10.21/mediapipe/python/solutions/pose.py) | 기존 공식 33점 명칭·인덱스. 현재 Tasks 결과의 실제 모델 출력은 아직 대조하지 못했으므로 등록 게이트에서 재확인 |
| [SDK LICENSE v0.10.35](https://github.com/google-ai-edge/mediapipe/blob/v0.10.35/LICENSE) | SDK 소스 Apache 2.0 문구 확인. 모델 자산 라이선스와 구분 |

논문을 새로 확인하거나 논문의 성능을 재현한 작업이 아닙니다.
