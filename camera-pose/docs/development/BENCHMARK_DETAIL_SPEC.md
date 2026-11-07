# 첫 비교 프로그램 세부 설계

[문서 목차](../README.md) · [분야 목차](README.md) · [현재 상태](../PROJECT_STATUS.md)


[프로젝트 홈](../../README.md) · [코드 구성](../design/CODE_DESIGN.md) · [실험 계획](../research/POSE_BENCHMARK_PLAN.md) · [결정 사항](../DECISIONS.md)

**작성: 2026-10-27 · 규약 식별자: `benchmark-0.1-draft` · 저장 영상·정식 기록·합성 어댑터 연결, 실제 모델/침대 실측 전**

모듈 구성은 [코드 설계](../design/CODE_DESIGN.md)를 따르고, 첫 저장 영상 러너의 자료형·함수·실행·실패·계산 규약은 이 문서로 구체화합니다. 아래 기본값은 재현 가능한 첫 구현을 위한 제안이며 성능 임계값이나 사용자 요구의 추가 확정이 아닙니다. 모델·가중치·최종 실행 플랫폼은 미선정입니다.

## 1. 범위와 준비 조건

- 한 실행(run)은 영상 한 개, 모델·모드·설정 한 개입니다. 전체 프레임을 표시 순서대로 동기 처리합니다. 모델별 실행은 순차 수행합니다.
- 첫 실행은 모든 반환 인물 후보를 보존합니다. 자동 대상 추적·재식별은 아직 구현하지 않습니다. 추론 대상 정책은 `none`이고 모든 프레임에서 `not_configured`를 기록합니다. 대상별 평가는 별도 확인한 평가 매칭이 있을 때만 가능합니다.
- 자세 분류·침대 영역 자동 인식·이탈/복귀 판정·실시간 대기열은 후속 단계입니다. 같은 관절 출력·품질 계약을 연결할 지점은 유지합니다.
- 설정·입력 파일·SDK 기능은 실행 전에 검사합니다. 모델 옵션의 실제 지원 여부·라이선스·가중치 접근·설치 버전은 첫 어댑터 구현 시 확인합니다.
- 아래 규약 중 입력·합성 어댑터·정식 기록 경로를 구현했습니다. `prepare/run/demo/inspect` 실행법은 [README](../../README.md)를 따릅니다. 실제 모델·평가·렌더링 API와 docs/examples의 가상 데이터는 실행 구현·실험 결과와 구분합니다.

## 2. 공통 자료형·직렬화

### 공통 규칙

| 항목 | 규약 |
|---|---|
| 스키마 버전 | 모든 외부 JSON/JSONL 레코드에 `schema_version="benchmark-0.1-draft"` |
| 식별자 | `run_id`, `video_id`, `candidate_id`, `record_id`는 비어 있지 않은 문자열. 경로와 별개이며 사용자 자세·센서 결과를 포함하지 않음 |
| run 식별 | 새 실행마다 UUID 등 충돌 없는 ID 생성. 기존 실행 폴더에 덮어쓰기·추적 상태 임의 재개 금지 |
| frame 식별 | 원본 표시 순서의 0부터 시작하는 정수. 첫 버전은 `0..N-1` 전부 처리; PTS를 프레임의 유일 키로 쓰지 않음 |
| 레코드 키 | `(run_id, video_id, frame_id)`; 후보는 여기에 프레임 내 `candidate_id` 추가. 후보 ID 예: `p0`, `p1`; 다음 프레임 동일성을 의미하지 않음 |
| 시간 | 내부 PTS·monotonic ns는 Python 정수, 파일에는 정밀도 보존용 십진 문자열. time base 분자·분모는 양의 JSON 정수 |
| 좌표 | 내부 유한 float, 파일은 JSON number. 픽셀·원본 점수의 단위/종류를 명시. 결측은 명시적 `null` |
| 비유한 수 | 공통 좌표는 `null` + 사유, 원본은 `{"nonfinite":"nan"}`, `pos_inf`, `neg_inf`로 종류 보존. JSON NaN/Infinity 금지 |
| 선택 필드 | 규약에 지정된 필드는 필수 키이며 값이 없으면 `null`; 누락과 구별. 임의 필드는 거부하고 확장은 버전 변경으로 관리 |
| 파일 인코딩 | UTF-8, JSONL 한 레코드 한 줄, 각 줄 끝 개행. 소수 자릿수를 임의 반올림하지 않음 |

원본은 사용한 SDK 경로가 공개한 수치·배열 순서·차원·dtype·좌표계·점수를 보존하는 의미입니다. 비공개 내부 텐서까지 확보했다고 주장하지 않습니다. 요청하지 않은 마스크·이미지를 원본 결과라고 임의 생성하지 않습니다.

설정 안 상대경로는 해당 설정 파일의 디렉터리를, 입력 매니페스트 안 상대경로는 그 매니페스트의 디렉터리를 기준으로 해석합니다. 평가 설정도 같은 규칙입니다. 레코드 참조는 run 디렉터리 안 고정 파일명입니다. 실행에는 원래 경로 문자열과 해석된 절대경로·파일 해시를 기록하고 현재 작업 디렉터리에 따라 다른 파일을 읽지 않습니다. 파일 해시는 실제 UTF-8/바이너리 바이트의 SHA-256이고, 확정 설정 객체의 해시는 키 정렬·공백 없는 JSON·UTF-8·비유한 수 금지 직렬화 바이트의 SHA-256입니다.

### 입력 매니페스트와 프레임 목록

`prepare`가 다음 두 파일을 생성하고, 모든 모델 실행은 동일 파일 해시를 참조합니다.

| 파일 | 필드·자료형 |
|---|---|
| `input_manifest.json` | `schema_version`, `video_id:str`, `video_sha256:hex64`, `video_path:str`, `frame_manifest_path:str`, `frame_manifest_sha256:hex64`, `frame_count:int`, `complete:bool`, `decoder_profile:object`, `canonical_width:int`, `canonical_height:int`, `timing_status:valid_pts/missing_pts/nonmonotonic_pts`, `errors:list` |
| `frames.jsonl` | `video_id:str`, `frame_id:int`, `source_pts:decimal_str|null`, `time_base_num:int|null`, `time_base_den:int|null`, `source_duration_pts:decimal_str|null`, `timestamp_source:pts/unavailable`, `pixel_sha256:hex64|null`, `schema_version` |

`decoder_profile`은 라이브러리·버전, 선택한 영상 스트림, 회전 메타데이터 처리, RGB 변환 규칙을 포함합니다. 프레임 크기가 도중에 바뀌거나 프레임 목록을 신뢰할 수 없는 소스는 `complete=false`로 두고 첫 공통 비교 입력으로 거부합니다. 디코더가 식별하지 못한 손상 구간에 가상의 프레임 번호나 PTS를 만들지 않습니다.

첫 run 입력은 `complete=true`이고 frame_count≥1이어야 합니다. 실행 중 N개 전에 EndOfVideo가 나오면 정상 종료가 아니라 입력 불일치입니다. 다음 기대 프레임의 decode 실패·나머지 미처리를 기록하고 중단합니다. N개 뒤 추가 프레임이 나오거나 목록과 PTS·크기가 다르면 공통 입력 무결성 실패입니다.

프레임 이미지 계약은 **회전 메타데이터를 적용한 canonical 방향, H×W×3, uint8, RGB, C-contiguous 배열**입니다. 미러링·왜곡 보정·침대 크롭은 첫 공통 입력에 자동 적용하지 않습니다. 이미지 바이트는 JSON에 넣지 않습니다. 디코드와 모델 설정이 환경마다 달라지면 그 차이를 보고하며 동일 입력 비교로 합치지 않습니다.

### 내부 프레임·모델 결과

| 자료형 | 정확한 구성 |
|---|---|
| `FramePacket` | 프레임 목록 필드 + `rgb:ndarray[H,W,3]`, `width:int`, `height:int`. 디코드 배열은 어댑터가 원본을 수정하지 않는 읽기 입력 |
| `ModelInput` | `frame:FramePacket`, `model_timestamp_ms:int|null`. 계획·정답·센서 필드 없음 |
| `AdapterMetadata` | `adapter_id`, SDK 버전, 모델·가중치·검출기 자산 해시, 실행 모드, 실제 장치/런타임, 관절 정의, 최대 반환 인원, 내부 추적/평활화 설정, 상태 초기화 방법, 좌표 변환 프로필 |
| `PoseOutput` | `candidates:tuple[NativePerson,...]`, `model_timestamp_ms:int|null`. 정상 후보 0개는 빈 tuple |
| `NativePerson` | `candidate_id:str`, `sdk_track_id:str|null`, `bbox:NativeBox|null`, `pose_blocks:tuple[PoseBlock,...]`, `score_blocks:tuple[ScoreBlock,...]` |
| `PoseBlock` | `block_id`, `joint_definition_id`, `source_joint_names:list[str]`, `coordinate_space`, `units`, `dtype`, `shape`, `values`, `transform_to_canonical:Transform2D|null`. world/3D 블록은 별도이며 2D 좌표로 임의 변환하지 않음 |
| `ScoreBlock` | `name`, `semantics`, `dtype`, `shape`, `values`, `documented_range:[min,max]|null`. 제공되지 않는 visibility/presence는 만들어 채우지 않음 |
| `NativeBox` | `xyxy:[float,float,float,float]`, `coordinate_space`, `source:sdk/derived`, `transform_to_canonical`. 박스가 없으면 `bbox=null`이며 관절로 만든 박스는 `derived` |
| `Transform2D` | `kind:identity/normalized_to_canonical/affine`, `matrix:3x3 유한수`, `source_space`, `destination_space=canonical_pixels`. kind와 행렬의 일치 검사 |

첫 버전의 `candidate_id`는 SDK 반환 순서에 따라 `p0..pK-1`로 붙입니다. 프레임별 ID를 추적 ID처럼 연결하지 않습니다. 첫 어댑터 구현 시 SDK별 블록·점수 필드 매핑을 명시하고 profile 해시를 저장합니다. SDK 옵션은 어댑터별 허용 목록으로 검사하며 문자열을 `eval`하거나 알 수 없는 옵션을 묵인하지 않습니다.

### 원본·공통 관절 레코드

원본 파일은 **프레임당 한 레코드 안에 후보 목록 전체**를 저장합니다. 공통 파일도 같은 단위로 저장해 후보 수가 달라도 참조 규칙을 유지합니다.

- `raw_results.jsonl`: 공통 키 + `record_type="raw_result"`, `record_id="frame:<frame_id>"`, `adapter_profile_sha256`, `model_timestamp_ms`, `candidates:list[NativePerson]`.
- `keypoints.jsonl`: 공통 키 + `record_type="mapped_result"`, 동일 규칙의 `record_id`, `mapping_profile_sha256`, `people:list[MappedPerson]`.
- `MappedPerson`: `candidate_id`, `raw_candidate_id`, `joints:list[MappedJoint]`(정확히 17개), `numeric_summary`.
- `MappedJoint`: `source_index:int|null`, `x_px/y_px/x_norm/y_norm:float|null`, `availability:predicted/missing/not_applicable`, `reason:null/not_output/non_finite/mapping_undefined`, `flags:list[outside_image]`.
- `numeric_summary`: `finite_joint_count:int`, `missing_joint_count:int`, `not_applicable_count:int`, `policy_status="not_assessed"`. 세 수의 합은 17.

COCO17 순서는 `nose, left_eye, right_eye, left_ear, right_ear, left_shoulder, right_shoulder, left_elbow, right_elbow, left_wrist, right_wrist, left_hip, right_hip, left_knee, right_knee, left_ankle, right_ankle`로 고정합니다. 각 이름은 사람의 해부학적 좌우입니다.

`MappingProfile`은 `profile_id`, `source_joint_definition_id`, `expected_source_count:int`, `target_definition_id="coco17"`, `source_indices:list[int|null]`(위 순서 17개), `image_block_id`, 검증한 SDK/관절 정의 출처·버전을 가집니다. 알려진 비대응점은 null/not_applicable로, 실제 반환에 없는 점은 missing/not_output으로 구분합니다. 예상 관절 수·순서·좌표 블록이 다른 SDK 결과를 임의 슬라이스하지 않고 MappingError로 중단합니다. affine은 3×3 유한값·역변환 가능 여부를 검사하며 normalized_to_canonical은 선언한 image 좌표의 W/H 배율을 한 번 적용합니다.

`predicted`는 2D 유한값이 있어 변환할 수 있음을 뜻하며 정확하거나 실제 보였다는 뜻이 아닙니다. 이 경우 네 좌표는 모두 유한값이고 `reason=null`입니다. 나머지 상태는 네 좌표가 전부 `null`입니다. `(0,0)`은 모델의 결측 표현임이 확인되지 않는 한 유효 좌표로 보존합니다. `x_norm=x_px/W`, `y_norm=y_px/H`; 범위 밖 유한값은 잘라내지 않고 `outside_image`로 표시합니다. 가림 추정값도 원본을 보존합니다. 점수는 원본 후보의 score block을 이름·관절 index로 참조합니다.

## 3. 실행 설정 규약

[JSON Schema](../schemas/benchmark-v0.1.schema.json)의 `RunConfig`와 [가짜 어댑터 설정 예시](../examples/benchmark-v0.1/run_config.json)를 참고합니다. 예시는 실제 모델 실행 결과가 아닙니다.

| 필드 | 형식·허용값 | 기본·검사 |
|---|---|---|
| `record_type`, `schema_version` | `run_config`, 공통 버전 | 정확히 일치 |
| `input_manifest_path`, `input_manifest_sha256` | 경로, hex64 | 필수. 파일·프레임 목록·영상 해시를 모두 대조 |
| `output_root` | 디렉터리 경로 | 필수. 저장 위치가 Git 추적 대상인지 확인; 기존 run 덮어쓰기 거부 |
| `adapter_id` | `fake/mediapipe_pose/yolo_pose/rtmpose` | 필수. fake는 실행 결과에도 synthetic 표시 |
| `execution_mode` | `independent_frames/ordered_sequence` | 필수. 어댑터 지원 여부 확인; SDK 모드명은 profile에서 매핑 |
| `requested_device` | 비어 있지 않은 문자열 | 필수. fake는 `none`; 실제 어댑터는 요청·관측 장치를 모두 기록 |
| `assets` | `{role,path,sha256}` 목록 | 실제 어댑터는 필요한 모델·검출기·설정 모두 명시. 자동 latest 다운로드 금지 |
| `adapter_options_path`, `adapter_options_sha256` | 경로·hex64 또는 둘 다 null | SDK별 옵션·매핑 profile 파일. 실제 어댑터에 필수; 두 필드의 존재 여부 일치 |
| `warmup_frames` | 정수 ≥ 0 | 기본안 0. 양수면 동일 영상 첫 지정 프레임을 임시 인스턴스로 실행하고 본 실행 인스턴스 재생성 |
| `target_policy` | `none` | 첫 버전 고정. 첫/유일 후보를 자동으로 원래 대상으로 간주하지 않음 |
| `error_policy` | `abort` | 첫 버전 고정. 실패 후 상태가 손상된 모델을 계속 실행하지 않음 |

스키마의 기본값은 프로그램이 실제로 채워 넣어야 합니다. 검증 라이브러리가 값을 자동 삽입한다고 가정하지 않습니다. 숨은 default가 생기지 않도록 파싱 뒤 확정된 설정 전체와 해시를 매니페스트에 기록합니다. 품질 점수·뼈 길이·PCK α 등에 임의 합격값을 기본 제공하지 않습니다.

스키마는 기본값을 보완한 확정 설정을 검사합니다. `warmup_frames <= frame_count`와 자산 파일·권한·해시는 별도 의미 검사입니다. 실제 어댑터의 필수 자산은 해당 profile에서 추가 검사하고 비어 있는 자산 목록으로 실제 모델 지원을 가장하지 않습니다.

## 4. 모듈 함수와 호출 계약

다음은 프로젝트 내부 인터페이스이며 공식 SDK 함수명이 아닙니다. 파일명은 [코드 구성](../design/CODE_DESIGN.md)의 안을 따릅니다.

```python
prepare_input(video_path: Path, output_dir: Path, decoder: DecoderProfile) -> InputManifest
load_run_config(path: Path) -> RunConfig
validate_run(config: RunConfig, input: InputManifest) -> ValidatedRun
run_benchmark(run: ValidatedRun, adapter: PoseAdapter, recorder: Recorder) -> RunSummary

class VideoSource:
    open(manifest: InputManifest) -> None
    read_next() -> FramePacket | EndOfVideo  # 읽기 실패는 SourceError
    close() -> None

class PoseAdapter:
    open(settings: AdapterSettings) -> AdapterMetadata
    reset_for_video() -> ResetReport
    estimate(frame: ModelInput) -> PoseOutput
    close() -> None

map_keypoints(raw: PoseOutput, geometry: FrameGeometry, profile: MappingProfile) -> MappedFrame
resolve_target(candidates: MappedFrame, policy: NoTargetPolicy) -> TargetResolution
inspect_numeric_quality(mapped: MappedFrame) -> MappedFrameQuality

class Recorder:
    begin(manifest: RunManifest) -> None
    append(bundle: FrameBundle) -> WriteReceipt
    finalize(outcome: RunOutcome) -> RunSummary
    close() -> None

evaluate_run(run_dir: Path, settings: EvaluationSettings, gt: GTBundle | None) -> EvaluationReport
compare_runs(requested_runs: list[Path], report_dir: Path) -> ComparisonReport
render_overlay(video_path: Path, run_dir: Path, output: Path) -> RenderReport
```

`EndOfVideo`는 정상 종료를 나타내는 별도 객체이며 프레임이나 예외와 혼동하지 않습니다. 프레임 누락·추론 예외를 빈 후보 목록으로 바꾸지 않습니다. `MappedFrameQuality`는 수치 점검 결과를 제공하며 원본이나 매핑 좌표를 수정하지 않습니다.

처리 순서는 map → numeric quality → target(none)로 두고 둘 중 어느 단계도 GT를 입력받지 않습니다. 단계별 함수는 입력 데이터형을 수정하지 않고 새 결과를 반환하며 출력 계약 위반은 해당 단계의 예외로 보고합니다.

`reset_for_video`는 성공 시 **시간 상태가 초기화된 새 상태**를 보장하고 `method=recreate/documented_reset`, `elapsed_ns`, 유지/초기화된 캐시 정보를 반환합니다. 보장할 수 없으면 `AdapterInitializationError`로 중단합니다. 본 실행에서는 영상 첫 프레임부터 다시 시작하며 과거 실행·warm-up 상태를 이어받지 않습니다.

어댑터의 실행 예외는 `AdapterCallError`에 `sdk_call_started:bool`과 진단 코드를 포함합니다. 입력 변환 단계에서 실패하면 false, SDK 함수를 실제 호출하기 시작한 뒤 실패하면 true입니다. SDK용 시각은 어댑터 호출 직전에 프레임 기록에 보존하므로 원본 결과가 없는 실패에서도 추적 가능합니다. 가짜 어댑터는 실제 SDK가 없으므로 false이며 synthetic 실행으로 표시합니다.

## 5. 프레임·실행 상태와 실패

### FrameResult

[FrameResult 스키마](../schemas/benchmark-v0.1.schema.json)와 [정상 미검출](../examples/benchmark-v0.1/frame_no_detection.json)·[추론 실패](../examples/benchmark-v0.1/frame_inference_error.json)·[미처리](../examples/benchmark-v0.1/frame_not_processed.json) 예시를 제공합니다.

| 필드 | 자료형·조건 |
|---|---|
| `record_type`, `schema_version`, `run_id`, `video_id`, `frame_id` | `frame_result`, 공통 버전·키 |
| `segment_id` | 0 이상 정수. 첫 정상 실행은 0; 첫 버전은 중간 reset 후 재개 없음 |
| `source_pts`, `time_base_num`, `time_base_den`, `source_duration_pts`, `timestamp_source` | 프레임 목록과 정확히 일치. PTS 없으면 시각 관련 값 모두 null |
| `model_timestamp_ms`, `sdk_call_started` | SDK용 시각은 decimal_str 또는 null, 호출을 시작했는지는 bool. 시각은 어댑터에 전달한 값이며 SDK 실제 호출 여부와 함께 해석. 독립 프레임 모드·어댑터 미호출 시 시각은 null |
| `processing_status` | `ok/failed/not_processed` |
| `failure_stage`, `failure_code`, `error_message` | 정상은 모두 null; 실패·미처리는 단계·정해진 코드 필수, 메시지는 경로·비밀값 없는 진단 문자열 또는 null |
| `candidate_count` | 추론이 정상 반환한 경우 0 이상 정수; 호출 전·호출 실패면 null. 매핑 실패 시 원본 후보 수 보존 |
| `target` | `{status:not_configured/resolved/unresolved/ambiguous, candidate_id:str|null, reason:str|null}`. 첫 정책은 not_configured/null/null |
| `raw_ref`, `keypoints_ref` | null 또는 `{file,record_id}`. file은 고정된 파일명만 허용; 외부 경로/상위 경로 금지 |
| `timing` | `clock_id:str`, `decode_start_ns/end_ns`, `adapter_start_ns/end_ns`, `mapping_start_ns/end_ns`(decimal_str 또는 null) |

정상 프레임은 두 참조가 모두 존재하며 후보가 0명이어도 후보 목록이 빈 원본·공통 레코드를 만듭니다. 추론 실패는 두 참조와 `candidate_count`가 null입니다. 매핑 실패는 원본만 보존합니다. 대상·품질 점검의 구현 예외도 명시적 실패로 처리하고 필요한 중간 결과를 보존합니다.

타이밍 각 쌍은 둘 다 값이 있거나 둘 다 null입니다. 값이 있으면 같은 시계에서 `end>=start`이고 호출 순서와 일치해야 합니다. 예외를 잡고 종료 시각을 얻을 수 있으면 실패 호출 시간도 기록합니다. 아직 시작하지 않은 단계는 0 대신 null입니다. 필드 간 관계·참조 일치·시간 순서는 스키마 외 의미 검사로도 확인합니다.

| 코드 | 실패 단계 | 처리 |
|---|---|---|
| `DECODE_ERROR` | decode | 해당 프레임 식별 가능 시 실패 행; 입력 연속성 검증 불가하면 중단 |
| `INFERENCE_ERROR` | inference | 실패 행 기록 후 중단 |
| `MAPPING_ERROR` | mapping | 원본 보존·실패 행 후 중단; 틀린 매핑으로 다음 프레임 진행 금지 |
| `TARGET_ERROR`, `QUALITY_ERROR` | target, quality | 후보 모호 같은 정상 상태와 구분한 프로그램 예외; 실패 행 후 중단 |
| `ABORTED_AFTER_ERROR`, `USER_CANCELLED` | runner | 남은 알려진 프레임에 `not_processed` 기록 |
| `RECORDING_ERROR` | recording | 가능하면 별도 오류 상태 기록, 중단. 저장 불가능하면 행 기록 성공을 보장하지 않음 |
| `INTEGRITY_ERROR` | recording | 중복 키·깨진 참조·잘못된 카운터 발견. 정상 평가 거부 |

### RunManifest와 생명주기

필수 항목은 공통 버전·run/video ID, 입력 매니페스트 해시·설정 전체 및 해시, `execution_kind=real/synthetic`, adapter metadata, 코드 버전·작업 트리 변경 여부, `run_status`, `ledger_complete`, 카운터·오류, 실제 생성 산출물 해시입니다. 설치 라이브러리·모델·검출기·관절 매핑·측정 장치도 저장합니다.

`run_status=initializing → running → succeeded/failed/cancelled`를 사용하고, `ledger_complete`는 독립적인 bool입니다. 비정상 종료로 running이 남으면 독자가 interrupted로 분류하되 자동 성공 처리하지 않습니다. failed이고 ledger_complete=true인 실행은 가능한 정상 사례입니다.

첫 행을 쓰기 전 미완료 매니페스트를 원자적으로 생성합니다. source·adapter는 최종 상태 확정 전에 종료하고 `close` 예외는 매니페스트의 `CLEANUP_ERROR`로 기록해 run_status=failed로 처리합니다. 프레임 결과를 소급해서 실패로 바꾸지는 않습니다. `Recorder.finalize`는 데이터 스트림 종료·무결성 검증 뒤 최종 매니페스트를 교체합니다. 모든 종료 경로에서 finally로 남은 자원을 닫으며 close는 중복 호출해도 안전해야 합니다. 실행 폴더 생성 후 초기화가 실패하면 매니페스트에 failed·원인·전체 미처리 수를 남깁니다. 프레임 목록이 유효하고 저장이 가능하면 전체 미처리 행도 생성합니다. 실행 폴더 자체를 만들 수 없으면 오류 반환과 비정상 종료 코드만 보장합니다.

## 6. 실행 순서·시각 변환·계측

```text
설정/파일/입력 프레임 목록 검사
  → 실행 폴더·미완료 manifest 생성
  → adapter.open → 선택적 warm-up → reset_for_video
  → source.open → 반복: read / estimate / map / numeric quality / target / append
  → source/adapter 종료 결과 수집
  → 전체 행·참조 검증 → 데이터 파일 동기화·종료 → 최종 manifest 교체
  → recorder 잔여 자원 종료
```

순차 시각은 첫 유효 PTS의 시간 `t0`를 기준으로 유리수 계산합니다. `t_i=pts_i*num_i/den_i`, `sdk_ms_i=floor(1000*(t_i-t0))`. 계산은 float가 아니라 Fraction 등 정수·유리수로 수행합니다. 전달값은 별도 기록하고 원본 PTS를 수정하지 않습니다.

- `ordered_sequence`: 모든 원본 시각이 있고, 시각과 변환한 ms가 엄격히 증가하는지 실행 전에 확인합니다. 중복·역행·반올림 충돌은 부적합으로 거부합니다. 몰래 +1ms, 프레임 드롭, FPS 대체를 하지 않습니다.
- `independent_frames`: SDK 시각은 null입니다. 원본 PTS가 없거나 비단조이면 해당 시간 기반 지표는 미계산하고, 프레임 단위 지표는 입력이 완전하면 계산 가능합니다.
- 지원 모드 변경은 새 설정·새 run입니다. 첫 버전은 동기 반환 방식만 사용합니다. 불명확한 손상 구간의 프레임 수·지속시간은 추정해 채우지 않습니다.

warm-up 기본안은 0입니다. 사용하는 경우 별도 임시 인스턴스의 프레임 수·시간과 본 실행 재생성 시간을 기록합니다. 재생성하면 캐시도 사라질 수 있어 steady-state 측정이라고 단정하지 않습니다. 본 실행의 첫 프레임은 전체 분포에 포함하고 첫 호출 시간을 별도로 보고합니다.

모든 단계 시간은 같은 `clock_id`의 `perf_counter_ns()` 등에서 측정합니다. 어댑터 시간은 색상/입력 변환·실제 검출/추론·공개 결과 수집까지 포함하며, SDK 내부 단계를 관측할 수 없으면 분리 수치를 만들지 않습니다. GPU 실제 완료 대기 방식이 검증되지 않은 SDK는 시간 비교 부적합 사유를 보고합니다.

파일의 monotonic ns는 실행 시작에 잡은 같은 기준점을 빼 0부터의 경과 ns로 저장하고 clock_id를 run·프로세스에 귀속시킵니다. UTC나 다른 프로세스 시계와 혼합하지 않습니다. 기준점·계측 함수와 실제 측정 범위를 매니페스트에 남깁니다.

본 실행 경과시간 `T`는 첫 프레임 디코드 시작부터 마지막 프레임 기록 및 source/adapter 종료 후 데이터 파일 flush/fsync·종료 완료까지입니다. 이 오프라인 실행 처리량에는 종료 비용도 포함하며 별도 종료 시간도 기록합니다. 초기화·warm-up·최종 manifest 교체·평가·렌더링은 별도 시간입니다. 실패 실행의 T는 성공 실행과 분리한 진단값입니다.

기록 종료 시간을 자기 자신의 프레임 행 안에 써 넣을 수는 없습니다. `Recorder.append`는 원본·공통·프레임 행을 쓴 뒤 `WriteReceipt(frame_id, clock_id, started_ns, finished_ns)`를 반환하고, 별도 `write_timings.jsonl`에 저장합니다. receipt 파일 자신의 쓰기 비용은 frame write 지표에서 제외하고 전체 T에는 포함합니다. receipt가 없으면 그 기록 시간은 미계산입니다.

## 7. 파일 쓰기·정합성

원본·공통·프레임 행·write receipt는 같은 키를 사용합니다. 참조의 record_id는 각 파일 안에서 유일해야 합니다. 원본/공통의 후보 ID 집합·수가 일치하고 각 공통 후보가 원본 후보를 참조해야 합니다.

1. 해당 프레임의 이용 가능한 원본·공통 레코드를 쓰고 flush합니다.
2. 성공 또는 실패 사유와 실제로 존재하는 참조를 담은 프레임 행을 마지막에 쓰고 flush합니다.
3. write receipt를 쓰고 flush합니다. 첫 버전은 프레임마다 OS 버퍼까지 flush하고 최종 확정 전에 모든 결과 파일을 fsync합니다. 프레임별 전원 장애 내구성을 보장한다고 표현하지 않습니다.
4. 모든 지정 프레임의 행이 하나씩 있는지, 중복·범위 밖 ID·깨진 참조·카운터 불일치가 없는지 확인합니다.
5. `N=ok+failed+not_processed`, 참조 무결성과 파일 해시를 확인하고 manifest 임시 파일을 flush/fsync한 뒤 같은 디렉터리에서 replace합니다. 가능하면 디렉터리도 동기화하고 지원/보장 범위를 기록합니다.

오류 후 남은 프레임은 디코드·모델 호출 없이 입력 목록에서 `not_processed`로 기록합니다. 실제 실패한 최초 행은 failed로 유지합니다. 입력 목록·저장 공간 문제가 있어 완료할 수 없으면 ledger_complete=false입니다. 고립 원본 행·마지막 잘린 JSONL·중복 프레임은 정상 성공 프레임이 아닙니다. 복구 모드는 첫 버전에 추가하지 않고 새 run으로 전체 재실행합니다.

평가 파일은 `evaluation_id`별 새 하위 디렉터리에 저장해 다른 GT·정책의 결과를 덮어쓰지 않습니다. 코드 설계의 `aggregate_metrics.json`·`report.md`는 각 평가 디렉터리 안에 둡니다. 입력·실행 기록은 읽기 전용으로 취급하며 오버레이 경로도 기존 파일 덮어쓰기를 거부합니다.

## 8. 평가 입력·대상 연결

`EvaluationSettings`는 `run_dir`, `input_manifest_sha256`, `scope=full/diagnostic_partial`, `association_mode=none/gt_association`, `gt_path/gt_sha256`, `matches_path/matches_sha256`, `pck_alpha:float|null`, `visibility_policy=visible_only/verified_visible_and_occluded`를 가집니다. GT·matches 경로는 해당 해시와 함께 있거나 함께 null입니다. GT 없는 처음 평가에서 alpha는 null입니다.

`GTFrame`은 `video_id, frame_id, subject_id, subject_present:bool, verified:bool, joints[17], scale_px:float|null, stationary_segment_id:str|null`입니다. 각 GT 관절은 `x_px/y_px`, `state=visible/occluded_verified/uncertain/outside`를 갖고, 좌표가 확인된 첫 두 상태만 평가 후보가 됩니다. 양수 scale_px는 별도 주석 지침에서 정의·검증해야 하며 예측 좌표로 계산하지 않습니다.

첫 버전의 `gt_association`은 사람이 대상 동일성을 검토한 `{run_id,video_id,frame_id,subject_id,candidate_id|null,status=matched/missing/ambiguous,reviewer_id,verified}` 목록을 평가 입력으로 사용합니다. 후보 배열 순서로 자동 매칭하지 않습니다. GT subject_present=true이고 평가를 요청한 프레임에 연결 기록이 빠지면 미검출로 추정하지 않고 평가 자료 불완전으로 처리합니다. 검증된 missing/ambiguous는 출력 누락으로 분모에 남깁니다.

이것은 **정답 보조 인물 연결을 사용한 관절 진단**으로 이름 붙이며 실제 추론 대상 추적 성능으로 보고하지 않습니다. 추론 경로에는 GT·matches를 전달하지 않습니다. 자동 IoU 매칭이나 실제 대상 추적은 별도 정책과 검증을 추가할 때만 지원합니다. 첫 버전에서 이 기능의 구현이 늦어지면 대상별 지표를 미계산하고 후보 출력·성능 기록부터 제공합니다.

## 9. 지표 공식·분모·미계산 규칙

`F`는 사전에 지정한 입력 프레임 집합, `N=|F|`입니다. 실제 대상이 없거나 GT가 미확인인 경우의 제외는 GT 정책에서 정하고 수를 보고합니다. 예측 결과·미검출·모호·결측·실패 때문에 분모를 줄이지 않습니다.

| 지표명 | 식·조건 |
|---|---|
| `candidate_output_rate` | 후보 수가 알려져 있고 >0인 프레임 수 / N. 후보가 하나라도 출력된 비율이지 사람·대상 검출 정확도가 아님 |
| `processing_ok_rate` | ok 프레임 수 / N. 후보 0개 정상 호출도 ok이며 실패·미처리는 분모 유지 |
| `target_joint_output_rate:<joint>` | 검증된 연결 대상에서 해당 관절의 유한 공통 좌표가 있는 프레임 수 / 대상 존재·verified GT 평가 프레임 수. 임계값 기반 수용률과 구분 |
| `pck_end_to_end` | 아래 U에서 거리 ≤ alpha×scale_px인 예측 수 / |U|. 누락·모호·실패는 분자 0, 분모 유지 |
| `pck_conditional` | U 중 유한 대상 예측이 있는 관절만의 PCK. 포함 관절수/|U|를 반드시 병기 |
| `successful_pipeline_fps` | ok 프레임 수 / T초. 첫 호출·입력·변환·파일 flush/fsync 포함, 초기화·평가·렌더 제외 |

PCK 집합 U는 검증된 대상 존재 GT, 선택한 가시성 정책, 유한 GT 좌표, 양수 GT scale_px를 갖는 `(frame,joint)` 쌍입니다. alpha는 0보다 큰 검증용 설정이며 아직 수치를 정하지 않았습니다. GT·alpha·scale 정의가 없거나 U가 비면 `unavailable`입니다. 제외 관절·스케일 미확인 수·예측 유한값 비율을 함께 보고합니다. ‘좌표가 없어 제외’는 **GT 좌표 부재**를 뜻하며 예측 누락을 뜻하지 않습니다.

연속 누락은 동일 영상·대상 평가 구간·상태 구간 안에서 해당 관절 출력이 없는 연속 프레임입니다. `[a,b]`의 `frame_count=b-a+1`, 첫/마지막 PTS, `observed_span_s=t_b-t_a`를 보고합니다. 다음 프레임 시각이나 엄격히 양수이며 단위가 검증된 마지막 프레임 duration으로 끝 경계가 있을 때만 `[t_a,t_end)` 표본 구간 길이를 계산합니다. 원본 duration=0은 보존하되 종료 경계 근거로 사용하지 않습니다. 이는 표본 기준 길이이며 실제 동작 시작/종료의 정답이 아닙니다. 마지막 경계가 없으면 `sample_interval_s=null`, `right_censored=true`, 관측 범위를 하한으로 남기고 임의 FPS를 더하지 않습니다. 시간이 유효하지 않으면 프레임 수만 계산합니다.

정지 구간 흔들림은 별도로 확인한 정지 구간·동일 대상·고정 카메라가 있을 때만 계산합니다. 구간별 해당 관절의 관측 좌표 p_i와 평균 p_bar에 대해 `rms_px=sqrt(sum(||p_i-p_bar||²)/m)`를 사용합니다. m<2는 미계산, 누락은 보간하지 않으며 m/평가 프레임 수·최대 공백도 보고합니다. 낮은 RMS 자체는 좌표 정확도를 보장하지 않습니다.

단계 시간 `duration_ms=(end_ns-start_ns)/1e6`는 정상·실패 표본을 따로 집계합니다. 정렬한 n개 값에서 분위수 위치 `h=(n-1)*p`로 인접 두 값의 선형 보간을 사용합니다(p=0.5/0.95). n=1은 그 값, n=0은 unavailable입니다. 표본 수·측정 누락 수·첫 호출 값을 함께 기록합니다. 시계가 다르거나 시간이 역행하면 invalid입니다.

`MetricResult`의 필수 필드는 [스키마](../schemas/benchmark-v0.1.schema.json)의 `MetricResult`를 따릅니다. `status=computed`이면 유한 value가 있고 reason=null입니다. `unavailable/invalid`이면 value=null이고 reason 코드가 있습니다. 분모 0은 값 0으로 채우지 않습니다. GT 없는 정답 지표의 사유 예는 `NO_VERIFIED_GT`, `NO_TARGET_ASSOCIATION`, `NO_SCALE`, `NO_ALPHA`입니다.

`numerator/denominator`는 개수 비율용 정수이며 computed ratio에서는 `0<=numerator<=denominator`, denominator>0입니다. FPS는 numerator=ok 수, denominator=null, `elapsed_seconds=T>0`으로 기록해 실수 시간 분모를 보존합니다. 시간 분위수·RMS는 두 count 필드와 elapsed_seconds가 null입니다. n_samples는 비율의 분모 표본수, PCK의 U 수(조건부는 예측 있는 U 수), 시간/RMS의 관측수, FPS의 ok 프레임수입니다. 각 지표 값은 이 구성값과 수작업 계산이 일치하는지 의미 검사합니다.

failed/cancelled/interrupted 실행은 정상 성능 집계에 섞지 않습니다. `compare_runs`는 사용자가 요청한 실행 목록을 기준으로 누락 폴더까지 실패/불명 항목으로 표시합니다. 부분 기록의 진단 계산은 scope를 diagnostic_partial로 명시하고 원래 N·처리 접두 범위·미처리 비율을 유지합니다. 깨진 참조나 중복 키는 진단 성공값으로 바꾸지 않고 integrity error로 보고합니다.

`full` 지표는 run_status=succeeded, ledger_complete=true, 입력·참조 검사를 통과한 경우에 허용합니다. failed/cancelled/interrupted에서 full을 요청하면 성능 지표는 unavailable/INCOMPLETE_RUN이며 실행 상태·오류·프레임 카운터는 계속 보고합니다. ledger_complete=true인 failed 실행은 diagnostic_partial로 원래 F와 U 전체의 출력률·PCK를 계산할 수 있고, 미처리를 누락으로 포함합니다. 처리 접두 구간만으로 분모를 줄이지 않습니다. 이때 시간 분위수는 실제 측정된 접두 표본만을 사용하고 해당 범위·표본수를 표시하며 successful_pipeline_fps는 미계산합니다. ledger_complete=false 또는 참조 불일치이면 원본 진단·읽힌 행 수·알려진 N을 보고하되 성능값은 invalid/INPUT_INTEGRITY_ERROR입니다. 비교표는 scope·association_mode를 필수 열로 표시합니다.

## 10. 모델 어댑터별 연결 규칙과 검증 게이트

다음 표는 연결 설계입니다. MediaPipe 패키지 설치·SDK 경계 검사는 수행했지만 유효 모델 추론 검증은 미완료이며, 다른 후보의 SDK 연결은 아직 수행하지 않았습니다. 최신 근거는 [어댑터 기록](MEDIAPIPE_ADAPTER.md)을 따릅니다.

| 어댑터 | 계약 연결·확인할 점 |
|---|---|
| MediaPipe Pose Landmarker | 첫 러너는 IMAGE/VIDEO의 동기 결과 경로. 프로젝트 ordered_sequence는 VIDEO에 매핑하고 ms 전달값 기록. 반환 image/world landmarks·점수는 분리. 공개 reset을 가정하지 않고 close/재생성으로 상태 분리하는 기본안. 실제 검출 박스가 공개되지 않으면 null |
| Ultralytics YOLO Pose | 공통 RGB 배열을 NumPy 예측 경로에 넣을 때 공식 입력 규약에 맞게 BGR 변환. `keypoints.xy`처럼 이미 원본 입력 픽셀 좌표인 출력은 identity 변환이며 letterbox를 다시 역변환하지 않음. 다른 출력 경로는 별도 profile |
| RTMPose/MMPose | top-down의 실제 검출기·관절 모델 설정/가중치를 함께 자산으로 고정. 전체 이미지 ROI 제공·검출기 생략 경로는 전체 경로와 다른 실행. 선택 API의 색상·결과 좌표계·부재 표시·추적 상태를 설치 버전에서 확인 |

각 어댑터는 `coordinate_space`와 `transform_to_canonical`을 선언해 좌표 복원을 정확히 한 번만 적용합니다. 단순 RGB/BGR 테스트 이미지, 알려진 리사이즈/패딩 좌표, SDK 원본 오버레이와의 대응으로 검사합니다. MediaPipe 33→17 인덱스는 기존 실험 계획의 안을 공식 관절 정의·설치 메타데이터와 대조한 뒤 profile을 활성화합니다.

등록 전 게이트: SDK 옵션/모드·실제 반환 필드·최대 인원·점수 의미·결측 표현, 좌표 원점/단위·색상 변환, 상태 초기화 보장, 순차 시각 조건, GPU 계측 방법, 자산 해시·라이선스·환경 재현 정보. 미확인 항목이 있으면 unsupported 상태로 남기며 가짜 어댑터 통과를 실제 SDK 지원 완료로 보고하지 않습니다.

공식 자료: [MediaPipe Python](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker/python), [PoseLandmarker API](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/PoseLandmarker), [결과 자료형](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/PoseLandmarkerResult), [Ultralytics 입력](https://docs.ultralytics.com/modes/predict/), [Keypoints API](https://docs.ultralytics.com/reference/engine/results/#ultralytics.engine.results.Keypoints), [MMPose 추론](https://mmpose.readthedocs.io/en/latest/user_guides/inference.html), [PyAV 시간](https://pyav.org/docs/stable/api/time.html). 문서 내용은 버전 변경 시 재확인합니다.

## 11. 명령·종료 코드·완료 기준

현재 `python -m bed_pose prepare --video PATH --output DIR`, `run --config PATH`, `demo --output DIR`, `inspect --run PATH`를 구현·실행 확인했습니다. run은 합성 어댑터와 실험용 MediaPipe 어댑터 코드를 지원합니다. MediaPipe는 유효 자산 추론 등록 게이트 전이며 다른 실제 모델 이름은 거부합니다. configure-mediapipe는 해시가 있는 설정을 생성합니다. evaluate/compare는 정답 없는 진단·비교를, render는 SVG 프레임·HTML 선택 화면을 구현했습니다. GT 기반 평가는 미구현입니다. demo/inspect는 합성 실행 확인·무결성 점검을 위한 추가 명령이며 정답 평가 지표를 제공하지 않습니다.

종료 코드는 `0=작업 성공`, `2=설정·입력 부적합`, `3=SDK 초기화/실행/변환 실패`, `4=저장/무결성 실패`, `130=사용자 중단`으로 제안합니다. 후보 0명·GT 없는 지표의 unavailable은 프로그램 실패가 아닙니다. 단, GT가 없는 지표를 computed로 만드는 오류는 검사 실패입니다. compare는 비교 불가능한 실행을 표에 남기고 무결성 실패가 있으면 정상 성공 코드로 끝내지 않습니다.

### 설계 예시·검사

[예시 디렉터리](../examples/benchmark-v0.1/run_config.json)의 자료는 가상의 식별자·해시·시각을 사용한 계약 예시이며 완전한 실행 폴더나 실행 가능한 입력 세트가 아닙니다. [미계산 지표](../examples/benchmark-v0.1/metric_unavailable.json)를 포함하며 실험 수치·원본 영상·실제 SDK 결과가 아닙니다. JSON Schema는 주요 교환 계약인 설정·프레임 결과·지표를 검사합니다. 나머지 내부·원본/공통 자료형은 위 표를 규범으로 구현하고 예시 추가와 함께 검사 범위를 넓힙니다.

| 수작업 확인 사례 | 기대 결과 |
|---|---|
| N=4, 후보 출력 2프레임, 실패 1, 정상 0명 1 | candidate_output_rate=0.5, processing_ok_rate=0.75 |
| U=4관절, 거리 0·5·11px 및 예측 누락, alpha×scale=10px | 전체경로 PCK=0.5; 조건부=2/3, 포함 비율=3/4 |
| 정상 호출 시간 [10,20,30,40]ms | p50=25ms, p95=38.5ms |
| PTS [0,40,100]ms, 마지막 2프레임 누락, 마지막 duration 없음 | frame_count=2, observed_span=0.06s, 끝 경계 미확인 |
| 모델 실패 뒤 나머지 프레임 미처리 기록 완료 | run_status=failed, ledger_complete=true 가능 |
| target 미지정·정답 연결 없음 | 대상별 지표 unavailable, 후보 출력 지표만 계산 |

첫 구현 검사는 위 사례와 좌표/좌우·0/결측·중복 PTS·영상 간 reset·실패 참조·잘린 JSONL·저장 실패를 다룹니다. `tests/`는 개발용이며 실제 분석 중에는 수치 점검·기록 무결성 등 필요한 검사가 각 실행 모듈에서 동작합니다. 테스트 파일을 모두 미리 만들지 않습니다.

## 12. 구현 착수 순서와 남은 조건

1. contracts/config + 스키마 의미 검사 + 가짜 어댑터·Recorder로 상태·시간·실패 행을 확인합니다.
2. VideoSource·prepare를 연결하고 실제 영상 프레임/PTS·오류·입력 해시를 확인합니다.
3. 모델 하나의 공식 API·자산·좌표 profile을 고정하고 실제 실행으로 등록 게이트를 통과시킵니다.
4. evaluate/compare/render를 연결하고 GT 없는 보고·기본 지표·실패 실행 표시를 확인합니다.
5. 추가 어댑터를 동일 계약에 연결하고 침대 영상·정답 자료로 후보를 압축합니다.

데이터형·구현 기본값은 본 초안으로 진행할 수 있습니다. 실제 모델/버전/자산, 어노테이션 지침·scale·PCK alpha, 대상 매칭 자료, 배치별 시험 영상, 성능 합격 기준은 해당 단계 전까지 확보·검증해야 합니다. 초기 자세·이탈 정의나 실행 플랫폼을 이 문서로 추가 확정하지 않습니다. 이 설계 작성과 예시 검사는 실제 프로그램 구현·모델 실행·인식 성능 검증과 별도로 보고합니다.

## 현재 구현의 명시적 범위

프로젝트 라이브러리의 객체는 설계의 역할을 유지하되 일부 생성자·함수 인자를 실제 코드에 맞춰 구체화했습니다. `VideoSource(manifest, rows, video_path).open()`, `ResultRecorder(directory, manifest, source_manifest, rows, clock)`를 사용하고, 실행 기록의 수치·참조 검사는 `validation.py`, 합성 관절 매핑과 수치 요약은 `mapping.py`에서 수행합니다. 별도 기하학/신뢰도 임계값·자동 대상 추적은 아직 적용하지 않습니다.

`--fake-no-detection-at`, `--fake-fail-at`, `--fake-people`, `--fake-missing-joint`는 CLI의 합성 실행 전용 옵션입니다. 값은 testing_options 및 어댑터 profile에 보존하고 profile 해시를 원본 레코드와 연결합니다. 품질·인식 성능 기준이 아닙니다. PyAV 18.1.0의 요구 조건에 맞춰 설치 메타데이터의 Python 최소 버전은 3.11입니다.

디코더 profile에 PyAV/FFmpeg 버전·RGB·회전 정책을 기록합니다. 정사각 픽셀 또는 미지정 입력과 순수 90도 단위 display matrix를 지원하고, 그 외 회전·반사·스케일·레거시 rotate 메타데이터·크기 변경은 거부합니다. 가변 간격 생성 영상·90도 회전 영상으로 현재 경로를 확인했으며 모든 코덱/촬영 조건 지원을 보장하지 않습니다.

디렉터리 fsync에 의한 전원 장애 내구성은 아직 검증하지 않았습니다. JSONL flush·파일 fsync와 최종 매니페스트 교체, 중단 기록의 실패 처리·해시/참조 검증을 구현했습니다. FrameLedger는 이전 호환용 프로토타입이며 새 CLI 경로에서는 사용하지 않습니다.

MediaPipe 어댑터의 추가 메타데이터·원본 SDK landmark 필드와 등록 게이트의 현재 상태는 [상세 구현 기록](MEDIAPIPE_ADAPTER.md)을 따릅니다. `PoseOutput.sdk_call_started`로 성공한 SDK 호출도 기록하며 매핑 실패가 이를 지우지 않습니다.

현재 no-GT 평가와 비교는 [후처리 범위](POSTPROCESSING.md)를 따릅니다. GT/연결 입력은 아직 받지 않으며 해당 지표는 미계산합니다. 오버레이는 원본 PTS와 RGB를 보존하는 SVG 프레임·수동 HTML 선택으로 구현했습니다. 설계의 동영상 출력·자동 재생은 후속입니다.
