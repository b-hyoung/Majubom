# 단일 자세 계획 측정

[문서 목차](../README.md) · [분야 목차](README.md) · [현재 상태](../PROJECT_STATUS.md)


[작업실](LIVE_MEASUREMENT.md) · [통합 입력](../design/TWO_VIEW_INPUTS.md) · [진행 현황](../PROJECT_STATUS.md)

2026-10-28. 현재는 **두 입력 그룹의 단일 목표 자세 측정**입니다. 자유 측정은 유지하며 여러 자세 순차 진행은 아직 구현하지 않았습니다.

## 화면 사용 순서

1. 두 입력의 침대 영역·대상을 지정하고 동일인 지정과 허용 수신 시각 차이를 설정합니다.
2. 그룹 설정의 ‘측정 방식’에서 **단일 자세 계획 측정**을 선택합니다.
3. 목표 자세를 고릅니다: 바로 누움(supine), 옆으로 누움(side), 걸터앉음(edge_sitting).
4. 준비 방식을 선택합니다. ‘준비 완료 버튼’은 사용자 입력을 기다리고, ‘준비 시간 후 시작’은 설정한 초가 지나면 시작합니다. 타이머 중에도 준비 완료를 눌러 먼저 시작할 수 있습니다.
5. 측정 시간은 선택 사항입니다. 초를 입력하면 자동 종료하고 빈칸이면 그룹 종료 버튼으로 마칩니다.
6. **계획 준비 시작 → 준비 완료/타이머 → 그룹 측정 → 종료/자동 종료** 순서입니다.

준비 시간은 타이머에서 0~86400초, 자동 측정 시간은 0초 초과~86400초를 허용하는 UI/운영 범위입니다. 검증된 실험 프로토콜이나 권장 유지 시간이 아닙니다. 자유 측정의 기존 그룹 시작/종료 동작은 그대로입니다.

## 단계와 실패 처리

그룹은 preparing → starting → recording → stopping → finalizing → completed/failed로 진행합니다. 준비 중에는 입력을 예약해 다른 개별/그룹 측정과 중복되지 않도록 합니다. ‘준비 취소’는 원본 측정 폴더를 만들지 않고 예약을 해제합니다. 그룹 폴더에는 취소된 계획 메타데이터만 남고 ledger_complete=false입니다. 이는 측정 결과가 생성되지 않았다는 뜻입니다.

준비 중 SDK 미리보기·잠정 추적은 계속하지만 **원본 측정 프레임은 기록하지 않습니다.** 준비 시각/조작 메타데이터와 실제 기록 구간을 구분합니다. 이 초기 단일 자세 방식은 자세 전환/이탈/복귀를 포함하는 전체 순차 측정 프로토콜의 완료가 아닙니다.

준비 중/측정 중 대상을 다시 지정할 수 있으며 필요하면 양쪽 동일인 지정을 재확인합니다. 자세를 바꾸다 추적이 보류돼도 목표 자세가 실제로 수행됐다고 추정하지 않습니다. 측정이 시작되면 원본 관절/보류 상태를 기록하고 통합 입력 생성에서 기존 품질/추적 게이트를 적용합니다.

입력 종료/오류, 부분 시작 실패, 저장 실패는 기존 그룹의 정리 정책을 따릅니다. 준비 중 입력을 제거하면 계획을 취소하고 양쪽 연결을 정리합니다. 앱 종료도 준비 예약/진행 기록을 정리합니다. 측정 시작 요청 하나가 두 카메라의 동일 노출 시각을 보증하지 않습니다.

## 시간과 기록

그룹 manifest의 measurement_mode는 planned_single 또는 free입니다. measurement_plan에는 목표/준비 방식/설정 시간과 다음 메타데이터가 들어갑니다. 입력별 원본 manifest의 mode=free는 원본 획득 기록 형식이며 그룹의 계획 모드/라벨을 대신하지 않습니다.

| 필드 | 의미 |
|---|---|
| preparation_started_perf_counter_ns | 계획 준비 시작의 단조시계 시각 |
| ready_perf_counter_ns / ready_trigger | 사용자 준비 완료 또는 타이머 만료 적용 시각/출처 |
| requested_start_perf_counter_ns | 준비 후 양쪽 Worker에 시작을 요청한 시각 |
| members.measurement_started_perf_counter_ns | 각 Worker의 실제 측정 시작 시각 |
| measurement_started_perf_counter_ns | 두 실제 시작 시각 중 늦은 시각. 자동 종료 경과 시간 기준 |
| requested_stop_perf_counter_ns / measurement_stopped_perf_counter_ns | 종료 요청 시각. 실제 카메라 노출 종료 시각이 아님 |
| stop_trigger | operator_stop, duration_elapsed, input_ended 또는 group_error |
| stop_before_both_started | 시작 중 빨리 종료를 요청한 경우의 표시 |

실제 입력별 종료/프레임 구간은 양쪽 원본 manifest와 프레임 기록을 참조합니다. 시작/종료 요청은 프레임 사이에서 처리되므로 설정 시간과 각 입력의 마지막 기록 시각이 정확히 일치한다고 가정하지 않습니다. 매우 짧은 측정은 프레임이 없을 수도 있습니다. 화면의 경과 시간은 운영용이며 참가자의 실제 자세 유지 시간 정답이 아닙니다.

## 목표·예측·정답 분리

measurement_plan.goal_posture는 **운영자가 수행하도록 지시한 목표**입니다. label_source는 operator_instruction_not_ground_truth, verified_gt는 null입니다.

통합 입력 행의 target_posture에는 목표 코드를 넣고 target_label_source로 출처를 명시합니다. 좌표 특징/마스크를 계산할 때 목표를 사용하지 않으며 posture_prediction은 not_implemented/null, verified_gt와 sensor_results는 null을 유지합니다. 원본 카메라 행의 기존 라벨 필드도 변경하지 않습니다. 자유 측정 통합 입력의 target_posture는 계속 null입니다.

`group-inspect`는 계획 메타데이터와 파생 입력의 일관성까지 검사합니다. 실제 수행을 확인하는 절차나 GT 작성 도구는 아니며 목표를 학습 정답으로 자동 사용해서는 안 됩니다.

## 검증

합성 입력으로 수동 준비/예약, 타이머 준비, 준비 완료로 타이머 생략, 자동 종료, 준비 취소/입력 제거, 잘못된 설정 거부와 목표·정답 분리를 검사했습니다. Chromium에서 계획 선택→목표/준비 조작→그룹 기록/재지정/통합 입력 검사와 좁은 화면을 확인했습니다. 실제 사람이 목표를 수행했는지 또는 자세 정확도를 검증한 것은 아닙니다.
