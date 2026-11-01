# 현재 코드 구성

[문서 목차](../README.md) · [분야 목차](README.md) · [현재 상태](../PROJECT_STATUS.md)


[문서 목차](../README.md) · [시스템 구조](ARCHITECTURE.md) · [진행 현황](../PROJECT_STATUS.md)

**기준일: 2026-10-29.** 실제 파일 기준의 모듈 안내입니다. 첫 비교 러너의 상세 설계와 이전 UI 경로는 [정리 전 코드 설계](https://github.com/zntks1234/bed-pose-skeleton-ai/blob/98b1529bfea26f4679f9b19d3744d5c4f122d0da/docs/archive/CODE_DESIGN_BEFORE_REORGANIZATION.md)에 보존했습니다. Tk 경로는 기본 실행에 사용하지 않습니다.

## 실행 흐름

`python -m bed_pose live` → CLI/launcher → 로컬 HTTP 작업실 → 입력별 엔진/작업 스레드 → 관절·자세·보조 근거 → 원본 기록 → 비동기 미리보기 → HTML 표시.

## 모듈별 책임

| 역할 | 주요 파일 (`src/bed_pose/` 기준) |
|---|---|
| 명령·구성 | `cli.py`, `config.py`, `contracts.py` |
| 입력 | `live_sources.py`(카메라/파일), `video.py`(정규 RGB·PTS·해시) |
| 추론 어댑터 | `pose/mediapipe.py`, 테스트 전용 `pose/fake.py`, `live_factory.py` |
| 입력별 수명주기 | `live_engine.py`: Worker가 수신·SDK·기록 소유 |
| 관절·영역·대상 | `mapping.py`, `bed_geometry.py`, `target_tracking.py` |
| 자세 | `posture.py` 인터페이스, `automatic_posture.py` 버전별 규칙, `weighted_posture.py` 적합도 |
| 신체·침대 관계 분리 | `separated_posture.py`: 영역 없는 `BodyInput`, 독립 형태 후보, 영상상 영역 관계와 해석 후보 |
| 독립 영상 근거 연결 | `body_state.py`: 대상 crop·근거 제공자 계약·현재 프레임 검증·0.8 조합. CLIP은 별도 제공자이며 사전학습 자산/정확도 검증은 별도 |
| CLIP 자산·실행 | `clip_assets.py`: 명시적 준비·해시 검사, `clip_posture.py`: 로컬 로딩·RGB 여백·문장 캐시·상태/방향 점수 및 저장 정책 재계산 |
| 정답 기반 자세 평가 | `posture_accuracy.py`: 별도 검토 주석과 저장 예측의 confusion·coverage·F1. 정답 없으면 미평가 |
| 2D/추정 3D·실루엣 | `lying_orientation.py`, `posture_evidence.py`, `silhouette.py`, `silhouette_overlay.py` |
| 그룹·시간·결합 | `group_session.py`, `time_alignment.py`, `fusion_inputs.py`, `group_posture.py`, `reference_posture.py` |
| 저장·무결성 | `live_recording.py`, benchmark용 `recording.py` |
| 조회·시간축·영상 대조 | `record_library.py`, `record_timeline.py` |
| 웹·표시 | `live_web.py`, `preview_worker.py`, `preview_quality.py`, `templates/live_workspace.html` |
| 오프라인 비교 | `runner.py`, `evaluation.py`, `rendering.py`, `evidence_report.py` |

## 유지할 경계

- 입력·원본 관측·공통 좌표·판정·시간 정보를 구분합니다. 0 좌표와 결측을 혼동하지 않습니다.
- 판정 입력에 목표·정답·외부 센서 결과를 넣지 않습니다.
- SDK 후보 번호와 지속 인물 ID, 추정 3D와 침대 기준 실측 높이를 구분합니다.
- 기록은 표시보다 먼저 수행합니다. 화면용 결과 교체는 원본 분석 기록의 누락이 아닙니다.
- 입력별 인코더는 최신 대기 작업만 보관합니다. 세대/화질 버전으로 늦은 결과를 거부합니다.
- 기록 열기/조회/원본 대조는 새 추론이나 저장 결과 수정 없이 수행합니다.
- 판정 버전과 프로필을 기록하고 이전 버전의 검사는 당시 규칙을 사용합니다.

## 상세 계약과 검사

[기록/시간 설계](DATA_AND_VALIDATION.md) · [첫 benchmark 필드 계약](../development/BENCHMARK_DETAIL_SPEC.md) · [미리보기 동시 처리](PREVIEW_CONCURRENCY.md) · [원본 대조](../guides/RECORD_REVIEW.md#image).

`tests/`는 제품 로직과 별도로 실행하는 개발 검사입니다. 정상 앱 실행 중 전체 테스트가 함께 실행되는 구조가 아닙니다. SDK 대역·생성 영상 검사는 실제 모델/장치/정답 정확도 검사와 구분합니다.

```sh
python -m unittest discover -s tests -v
```

현재 개선 과제는 실제 자료 평가, 대상·관절 품질, 장시간 자원 비용과 외부 연동입니다. 이 문서 정리는 런타임 구조나 판정 기준 변경이 아닙니다.

현재 기본 0.7은 `separated_posture.py`에서 형태 후보와 침대 관계를 독립 계산합니다. 비교용 0.6의 `viewpoint_posture.py`는 유지합니다. 원본 입력과 추정 모델을 재사용하며 버전별 기록 재계산 경로를 구분합니다. [현재 계약](AUTOMATIC_POSTURE.md).
