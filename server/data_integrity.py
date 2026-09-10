"""
data_integrity.py — raw 센서값과 서버 판정(label/quality) 사이의 일관성 진단
====================================================================
2026-09-10 복구: git reset/clean으로 유실된 파일 재작성. 12번 탭(시스템 확장) STEP 6-9
"raw/clean 데이터 일관성 검증" — 센서가 보낸 원시값과, 그 값으로부터 서버가 내린 판정이
서로 말이 안 되는 조합인지 확인해 sensor_clean_data/activity_log에 남긴다. 판정 자체를
바꾸지 않는다(진단만 하고 별도 기록) — 정직한 실패 원칙과 같은 방향: "이상하다"를 숨기지
않고 기록해서 나중에 데이터 품질을 감사할 수 있게 한다.
"""
from __future__ import annotations


def check_tof_consistency(distances: list, label: str | None,
                           valid_zone_expected: dict[str, tuple[int, int]]) -> dict:
    """ToF 8x8(또는 4x4) distances_mm와 서버가 분류한 자세 label이 서로 맞는 조합인지 확인.
    valid_zone_expected: {"supine": (20, 64), "empty": (0, 5), ...} 같은 라벨별 기대 유효존 범위
    (tof_server.py의 TOF_VALID_ZONE_EXPECTED — 아직 실측 기반으로 다듬어지지 않은 초기값임을
    그쪽 코드에도 명시해뒀다).

    반환: {"consistent": bool, "valid_zones": int, "expected_range": (lo,hi)|None, "note": str}
    라벨이 기대 테이블에 없거나 distances가 비어있으면 판단 보류(consistent=None)로 정직하게 표시."""
    valid_zones = sum(1 for d in (distances or []) if isinstance(d, (int, float)) and d and d > 0)

    if not distances:
        return {"consistent": None, "valid_zones": 0, "expected_range": None,
                "note": "distances 비어있음 — 판단 보류"}
    if not label or label not in valid_zone_expected:
        return {"consistent": None, "valid_zones": valid_zones, "expected_range": None,
                "note": f"라벨 '{label}'에 대한 기대 유효존 범위 미정의 — 판단 보류"}

    lo, hi = valid_zone_expected[label]
    consistent = lo <= valid_zones <= hi
    note = (f"유효존 {valid_zones}개, 라벨 '{label}' 기대범위 {lo}~{hi}" +
            ("" if consistent else " — 불일치(라벨과 실제 유효존 수가 안 맞음)"))
    return {"consistent": consistent, "valid_zones": valid_zones,
            "expected_range": [lo, hi], "note": note}


def check_mmw_consistency(samples_count: int, walking: bool,
                           min_samples_walking: int = 5) -> dict:
    """mmWave가 '보행 중(walking=true)'이라고 판정했는데 포인트 수(samples_count)가
    지나치게 적으면(트랙이 짧게 끊긴 노이즈일 가능성) 불일치로 표시한다. samples_count는
    실제 포인트클라우드 개수가 아니라 이 엔드포인트에서 확인 가능한 근사치(quality.samples_count)
    임을 호출부(mmw_server.py)에도 명시해뒀다 — 그 한계를 그대로 이어받는다."""
    if walking and samples_count < min_samples_walking:
        return {"consistent": False, "samples_count": samples_count, "walking": walking,
                "note": f"walking=true인데 samples_count={samples_count} < {min_samples_walking} — "
                        "짧게 끊긴 트랙을 보행으로 오판했을 가능성"}
    return {"consistent": True, "samples_count": samples_count, "walking": walking,
            "note": "이상 없음"}
