# 문서 안내

[프로젝트 홈](../README.md) · [진행 현황](PROJECT_STATUS.md) · [로드맵](../ROADMAP.md)

처음에는 아래 세 문서만 읽으면 됩니다. 상세 문서는 작업할 때 찾아보세요.

1. **[진행 현황](PROJECT_STATUS.md):** 지금 가능한 기능과 검증되지 않은 부분
2. **[측정 작업실 사용법](guides/LIVE_MEASUREMENT.md):** 설치·실행·입력·측정·기록 확인
3. **[로드맵](../ROADMAP.md):** 전체 단계·남은 체크리스트·연구 과제·다음 순서·완료 기준

## 목적별 바로가기

| 하고 싶은 일 | 먼저 읽을 문서 |
|---|---|
| 관절 미검출·부분 가림·GPU 연결 오류 개선 | [논문 조사와 실제 적용](research/POSE_RECOVERY_REVIEW_2026-11-01.md) |
| ToF 동기 저장·내일 학습 준비 | [5·6단계 수집·GPU 학습](guides/TOF_TRAINING.md) |
| 확정된 범위·미정 사항 확인 | [결정 사항](DECISIONS.md) · [요구사항](REQUIREMENTS.md) |
| 카메라 연결·화질·영역 설정 | [카메라 설정](guides/CAMERA_CAPTURE.md) · [표시 화질](guides/PREVIEW_QUALITY.md) · [침대 영역](guides/BED_REGION.md) |
| 기록의 잘못된 판정 확인 | [기록 조회](guides/RECORD_REVIEW.md#browse) → [시간축](guides/RECORD_REVIEW.md#timeline) → [원본 프레임 대조](guides/RECORD_REVIEW.md#image) |
| 자세 점수가 계산되는 방식 이해 | [자동 판정 0.6](design/AUTOMATIC_POSTURE.md) · [이전 가중 적합도 0.5](design/WEIGHTED_POSTURE.md) |
| 다중 카메라의 논문 근거 확인 | [다중 시점·연속 영상 논문 모음](research/MULTI_CAMERA_PAPERS.md) |
| 두 카메라의 현재 결합 방식 확인 | [그룹 설계](design/MULTI_CAMERA_DESIGN.md) · [통합 관측 계약](design/TWO_VIEW_INPUTS.md) |
| 코드를 수정하거나 검수 | [현재 코드 구성](design/CODE_DESIGN.md) · [시스템 구조](design/ARCHITECTURE.md) · [검수 기록](reviews/README.md) |
| 실제 영상 평가 준비 | [측정 절차 초안](design/MEASUREMENT_PROTOCOL.md) · [데이터·평가 설계](design/DATA_AND_VALIDATION.md) · [비교 실험 계획](research/POSE_BENCHMARK_PLAN.md) |
| 논문·모델 후보 비교 | [문헌 안내](research/LITERATURE_REVIEW.md) · [기술 후보](research/TECHNOLOGY_CANDIDATES.md) |
| 오프라인 CLI·개발 검사 | [후처리 도구](development/POSTPROCESSING.md) · [어댑터](development/MEDIAPIPE_ADAPTER.md) |

## 폴더별 전체 목록

| 위치 | 역할 |
|---|---|
| [guides/](guides/README.md) | 현재 사용법과 화면 조작 |
| [design/](design/README.md) | 현재 구현 계약·최종 구조·설계 제안 |
| [research/](research/README.md) | 논문·기술 후보·실험 계획·확인 범위 |
| [development/](development/README.md) | 개발 도구·어댑터·benchmark 필드 계약·AI 작업 양식 |
| [reviews/](reviews/README.md) | 작성 당시의 코드·판정 검수 결과 |
| [archive/](archive/README.md) | 과거 구현 과정·이전 계획·호환 규칙 |
| [schemas/](schemas/benchmark-v0.1.schema.json) · [examples/](examples/benchmark-v0.1/run_config.json) | 개발용 benchmark 스키마와 가상 예시. 실제 측정 데이터 아님 |

## 상태와 근거를 읽는 기준

- **현재 지원 범위:** PROJECT_STATUS가 기준입니다. 오래된 문서의 ‘현재’는 작성 시점 기준입니다.
- **합의·제안·미정·보류:** DECISIONS에서 구분합니다. 최종 요구와 구현 완료는 다릅니다.
- **검증:** 개발 검사·기록 무결성·실제 장치 동작·정답 기반 정확도를 구분합니다.
- **연구:** 본문 미확인 P11·P12·P19·P34와 P35의 미확인 최종판은 해당 본문을 전제로 판단하지 않습니다.

## 문서 갱신 규칙

README는 소개와 링크, 진행 현황은 현재 상태, 로드맵은 다음 작업, 결정 사항은 합의를 관리합니다. 사용법·수식·필드·검사 근거는 해당 상세 문서에 한 번 작성하고 연결합니다. 개발 이력을 이 네 문서에 반복해서 쌓지 않습니다.

2026-10-29 문서 정리에서 기존 상세 문서를 목적별 폴더로 이동했습니다. 기존 링크는 갱신했고, 정리 전 주요 문서 원문은 archive에 보존했습니다. 새 모델·판정 기준·기능 범위의 결정 변경은 없습니다.

미완료 계획은 archive에만 보관하지 않습니다. 전체 단계별 계획과 영상 없이 준비할 수 있는 병행 작업도 현재 ROADMAP에서 관리합니다.
