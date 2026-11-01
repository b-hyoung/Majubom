# 두 시점 통합 입력 생성 · 학습/분류 전 단계

[문서 목차](../README.md) · [분야 목차](README.md) · [현재 상태](../PROJECT_STATUS.md)


[진행 현황](../PROJECT_STATUS.md) · [작업실 사용법](../guides/LIVE_MEASUREMENT.md) · [두 카메라 설계](MULTI_CAMERA_DESIGN.md)

2026-10-28. **구현:** 성공 그룹의 기록 검사 → 시간 정렬 → 두 시점 입력 후보/보류 기록. **미구현:** 학습, 자세 분류, 3D/관절 좌표 융합, 검증된 동일인 식별·촬영 시각 동기화.

## 네 점 침대 영역 · 2026-10-28

침대 모서리를 순서대로 네 번 클릭해 지정하고 점 이동·마지막 점 취소·지정 취소를 지원합니다. 볼록 영역 검사, 실제 다각형 내부 여부, 단위 사각형 대응 상대 좌표와 자동 자세 판정/원본·그룹 기록 검사를 연결했습니다. 기존 사각형 기록은 유지하며 네 점을 포함한 통합 입력은 0.3 형식입니다. [사용법과 좌표 의미](../guides/BED_REGION.md).


## 사용 흐름

기존처럼 두 입력의 영역·대상 지정과 그룹 설정을 완료한 뒤 측정합니다. 그룹 종료 시 백그라운드에서 기록 검사·정렬·통합 입력 생성을 수행합니다. UI에는 정리 중 상태를 표시하며 다른 입력의 미리보기/API는 계속 동작합니다. 앱 정상 종료는 이 작업까지 기다립니다. 강제 종료는 미완료 기록을 남길 수 있습니다.

완료 후 그룹 설정의 **‘통합 입력 준비 결과 · 자세 판정 아님’**을 펼치면 입력 후보 수, 보류 수와 사유를 볼 수 있습니다. 사유가 여러 개인 행은 사유별로 중복 집계하므로 사유 수 합계가 보류 행 수보다 클 수 있습니다.

```text
recordings/live/groups/<group_id>/
  manifest.json
  alignment.jsonl
  fusion_inputs.jsonl
```

원본 카메라 기록은 기존 measurement_id 폴더에 그대로 있습니다. 새 영상·이미지·학습 가중치는 저장/생성하지 않습니다. 참조 검사에는 원본 측정 폴더가 필요하며, 경로 재배치/독립 배포 묶음은 아직 지원하지 않습니다.

프로젝트 PowerShell에서 실제 그룹 경로로 바꿔 확인할 수 있습니다.

```powershell
& .\.venv\Scripts\python.exe -m bed_pose group-inspect --run "recordings/live/groups/<group_id>"
```

이 명령은 생성된 통합 입력이 있는 완료 그룹의 원본 파일/manifest 해시·정렬 참조·정규화/마스크 값·행/보류 카운터를 재검사합니다. 기존 그룹이나 정렬이 보류돼 입력 파일이 없는 그룹에는 생성 결과가 없다고 보고합니다. integrity=verified는 파일/계산의 일관성이며 실제 관절 정확도·동일인·자세 성능 검증이 아닙니다.

## 두 시점 데이터

새로 생성하는 형식은 `two-view-input-0.2-draft`입니다. `group-inspect`는 기존 `0.1-draft`도 당시 정책으로 검사하며 파일을 다시 쓰지 않습니다. 최종 학습/센서 교환 형식을 확정한 것이 아닙니다. `views[0]`, `views[1]`은 그룹 입력 순서이며 사람의 왼쪽/오른쪽이 아닙니다. 서로 다른 투영 좌표를 평균하거나 하나의 공통 3D 위치로 해석하지 않습니다.

| 항목 | 의미 |
|---|---|
| group_id / alignment_id | 그룹과 정렬 행의 원본 참조 |
| status / hold_reasons | candidate_input 또는 held와 보류 사유 목록 |
| views | 입력별 데이터 2개. 미매칭 쪽은 null |
| input_id / measurement_id / frame_id / stream_frame_id | 입력·원본 기록과 정확한 프레임 연결 |
| clock_id / received_ns / time_ns / delta_ns | 원래 시계/수신 시각·공통 단조시계 값·두 시점의 차이. ns 문자열 |
| width / height / mapping_profile_id / score_source_block | 좌표 기준 영상 크기·공통 매핑·점수 원본 블록 |
| tracking | 실제 프레임의 잠정 추적 상태·구간 ID·후보 ID·실험 설정 |
| bed_region_revision / bed_region | 해당 프레임에서 적용된 수동 영역 버전/좌표 |
| joints | 추적 후보의 COCO17 관절. 대상 대응이 없으면 null |
| posture_prediction | status=not_implemented, label=null |
| target_posture / verified_gt / sensor_results | 계획 측정의 target_posture는 지시 목표, 자유 측정은 null. 정답/센서 결과는 null |

식별자·해시·파일명은 추적 메타데이터이며 분류 특징으로 사용하도록 정의한 값이 아닙니다.

각 관절에는 이름/원본 인덱스·픽셀 좌표·가용성·누락 사유·범위 플래그와 원본 image visibility/presence의 값/상태를 남깁니다. 점수 0과 미출력·비정상 값을 구분하고 점수 임계값이나 평균 점수를 만들지 않습니다.

- `image_xy = (x_px/width, y_px/height)`
- `bed_xy = ((x_px-x1)/(x2-x1), (y_px-y1)/(y2-y1))`
- 좌표가 없으면 null입니다. 0 좌표를 누락으로 바꾸거나 누락을 0으로 채우지 않습니다.
- 이미지 밖 좌표와 침대 밖 상대 좌표는 잘라내지 않습니다. 원본 좌표/플래그를 보존합니다. `image_mask`는 화면 내 좌표, `bed_mask`는 화면 내 좌표와 기준 영역이 있어 침대 상대 좌표를 사용할 수 있음을 뜻합니다. `bed_mask`는 침대 내부 판정이 아닙니다.
- `inside_bed`는 관절이 지정 사각형 내부/경계에 있으면 true, 외부면 false이며, 좌표나 영역이 없으면 null입니다. 화면 밖 관절도 좌표가 있으면 사각형 내부 여부를 계산하지만 마스크는 false입니다. 사각형 경계는 포함합니다. 실제 침대 점유·물리 경계 정답은 아닙니다. 침대 밖 좌표는 이탈 분석을 위해 유지합니다. 기존 0.1 기록에는 이 필드가 없습니다.
- 침대 상대 좌표는 수동 영상 사각형 기준의 무차원 값이며 실제 길이/거리, 원근 보정 좌표, 침대의 실제 물리 경계가 아닙니다.
- 신체 중심/크기 정규화는 아직 미선정이며 policy에 not_configured로 남깁니다. 저장한 픽셀/상대 좌표를 나중에 다른 파생 특징으로 바꿀 수 있습니다.

## 입력 후보와 보류

candidate_input은 다음 소프트웨어 조건을 만족했다는 뜻입니다.

1. 허용 수신 시각 차이 안에서 일대일 매칭된 프레임 쌍.
2. 양쪽 처리 성공·잠정 추적 후보가 실제 해당 프레임 결과에 존재.
3. 양쪽 침대 영역이 지정되고 화면 내 관절 좌표가 존재.
4. 해당 시각에 유효한 **운영자 동일인 지정/재확인**의 양쪽 추적 구간 ID와 일치.

이 조건은 자세 분류에 충분한 관절 품질·실제 동일인·같은 노출 순간을 보증하지 않습니다. `identity_verified=false`, `exposure_synchronized=false`, `input_quality_status=not_assessed`를 유지합니다. 미가시 관절을 복원하거나 한쪽의 점수로 다른 쪽 관절을 정답 처리하지 않습니다.

재지정 후의 새 추적 구간은 재확인 전까지 held입니다. 운영자 재확인 시각보다 두 프레임 수신 시각이 모두 이후이고, 해당 구간 ID 조합이 맞아야 후보로 돌아갑니다. 마지막 확인 상태를 과거 전체 기록에 적용하지 않습니다. 재확인 전과 후는 다른 구간입니다.

다음은 대표 보류 사유입니다.

- unmatched_time: 시간 정렬 상대 프레임 없음.
- left/right:target_held 또는 target_not_configured: 입력별 추적 보류/미지정.
- operator_pair_confirmation_missing: 현재 추적 구간 조합의 초기 지정/재확인 없음.
- bed_region_missing, tracked_candidate_missing, processing_failed, no_in_image_joints: 해당 입력 정보 부족/실패.

left/right는 코드상 입력 슬롯이며 해부학적 좌우가 아닙니다. 보류해도 원본과 사용 가능한 한쪽 데이터는 유지합니다. 프레임별 행을 생략하거나 보류를 정상 자세로 바꾸지 않습니다.

## 완결성과 오류 처리

`fusion_inputs.py`는 전체 영상을 적재하지 않고 JSONL을 순차 처리합니다. 정렬 행마다 하나의 입력 행을 만들고, 양쪽 원본 프레임이 각각 순서대로 한 번씩 참조됐는지 확인합니다. 후보 입력과 보류를 합한 수는 정렬 행 수입니다. 원본 manifest/파일·정렬 파일과 결과 파일에 SHA-256을 사용하고 파생 policy와 그 해시를 그룹 manifest에 기록합니다.

생성은 임시 파일에 쓰고 flush/fsync 뒤 교체합니다. 오류면 임시 파일을 정리하고 원본과 기존 파생 파일은 유지하며 그룹을 실패/미완료로 보고합니다. 이 해시는 로컬 기록/계산 검사이지 악의적으로 모든 파일을 다시 작성한 경우의 진위 인증이 아닙니다.

저장 영상은 공통 PTS 오프셋이 검증되지 않아 기존처럼 정렬 보류이며, 통합 입력 생성도 blocked_alignment_unavailable입니다. 실패한 원본 그룹도 억지로 입력 후보를 생성하지 않습니다.

## 검증과 남은 작업

생성 기록으로 입력 분리·누락/0/범위 밖 좌표·영역 없음·정렬 참조 완결성·재지정 전후 시간별 보류·파생 값 변조·백그라운드 정리 중 미리보기 지속과 CLI/브라우저를 검사합니다. 실제 카메라 정확도 실험이나 모델 학습이 아닙니다.

향후 실제 두 카메라의 수신/촬영 시점 오차·추적 오류·촬영 배치 차이를 확인해야 합니다. 학습 전에는 확인된 자세 정답, 참가자/세션 단위 분할, 시점 순서/일반화 조건과 실제 입력 품질 기준이 필요합니다. 현재 입력 후보를 정답 라벨이 붙은 학습 데이터라고 취급하지 않습니다.

계획 측정은 target_label_source=operator_instruction_not_ground_truth로 목표 출처를 보존합니다. 목표는 특징 계산/게이트/예측에 넣지 않으며 계획 메타데이터와 파생 행의 일관성을 검사합니다. [단일 자세 계획 측정](../guides/SINGLE_POSTURE_PLAN.md).


## 실험 자세 판정 확장 · 2026-10-28

기준 비교 분류기가 설정된 입력의 view에는 원본 posture_assessment를 추가 보존한다. 이 특징 파일의 기존 posture_prediction은 유지하고, 통합 예측은 별도 posture_predictions.jsonl과 그룹 manifest의 posture_predictions에 기록한다. group-inspect는 파일 해시와 원본 기반 재계산을 확인한다. [사용법·현재 한계](../archive/REFERENCE_POSTURE.md).
