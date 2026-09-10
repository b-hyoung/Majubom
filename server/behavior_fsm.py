"""
behavior_fsm.py — 침상 이탈(exit) 지속시간 상태전이 판단 (서버 측)
====================================================================
2026-09-10 복구: git reset/clean으로 유실된 파일 재작성. "화장실에 오래 있다" 같은
행동패턴 판단(01번 탭에서 재검토 때 지목한 4번째 AI 지점)을 담당 — ToF가 이탈(exit)을
감지한 뒤, 그 이탈이 "이번엔 평소보다 오래 지속되고 있는가"를 개인화 baseline(과거 이탈
지속시간들의 평균·표준편차) 대비 z-score로 판정한다.

mmw_logic.py/tof_logic.py와 같은 사상(누적 μ·σ, MIN_SAMPLES, WARMUP_DAYS, SIGMA_FLOOR)을
따르되, 추적 지표가 "이탈 지속시간(초)" 하나뿐이라 저장 구조가 더 단순하다(중첩 dict 없이
{bed_id: {first_seen, n, sum, sumsq}} 평면 구조) — 실제 server/behavior_fsm_baseline.json에
남아있던 데이터(bed_02, n=1, sum=3600.0)가 정확히 이 평면 구조였다.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(_HERE, "behavior_fsm_baseline.json")

MIN_SAMPLES = 5
WARMUP_DAYS = 14
SIGMA_FLOOR_SEC = 60.0  # 1분 미만 분산은 노이즈로 간주해 하한 고정

TH_CAUTION, TH_WARNING, TH_CRITICAL = 1.5, 3.0, 4.5

LEVEL_KO = {"normal": "정상", "caution": "주의", "warning": "경고", "critical": "위험"}


def load_baseline(path: str = BASELINE_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_baseline(store: dict, path: str = BASELINE_PATH) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def update_baseline(store: dict, bed_id: str, duration_sec: float) -> dict:
    bed = store.setdefault(bed_id, {"first_seen": datetime.now(timezone.utc).isoformat(),
                                     "n": 0, "sum": 0.0, "sumsq": 0.0})
    v = float(duration_sec)
    bed["sum"] += v
    bed["sumsq"] += v * v
    bed["n"] += 1
    return bed


def baseline_stats(bed: dict | None) -> dict | None:
    if not bed or bed.get("n", 0) == 0:
        return None
    n = bed["n"]
    mu = bed["sum"] / n
    var = bed["sumsq"] / n - mu * mu
    sigma = max(math.sqrt(var) if var > 0 else 0.0, SIGMA_FLOOR_SEC)
    try:
        first = datetime.fromisoformat(bed["first_seen"])
        age_days = (datetime.now(timezone.utc) - first).total_seconds() / 86400.0
    except (KeyError, ValueError):
        age_days = 0.0
    return {"n": n, "duration_mu_sec": round(mu, 1), "duration_sigma_sec": round(sigma, 1),
            "age_days": round(age_days, 2)}


def classify_alert(z: float) -> str:
    if z >= TH_CRITICAL:
        return "critical"
    if z >= TH_WARNING:
        return "warning"
    if z >= TH_CAUTION:
        return "caution"
    return "normal"


def evaluate_completed(bed_id: str, duration_sec: float, store: dict, persist: bool = True) -> dict:
    """이탈 에피소드 하나가 "끝난" 시점(환자가 침상으로 복귀)에 호출. 이번 이탈의 지속시간을
    baseline에 반영하기 *전에* 먼저 z-score를 계산해(자기 자신이 자기 baseline을 흐리지 않게),
    그 다음에 baseline을 갱신한다."""
    prior_stats = baseline_stats(store.get(bed_id))

    z = None
    level = "normal"
    reasons: list[str] = []
    warming = (prior_stats is None) or (prior_stats["n"] < MIN_SAMPLES) or (prior_stats["age_days"] < WARMUP_DAYS)

    if prior_stats and prior_stats["n"] >= MIN_SAMPLES:
        z = round((float(duration_sec) - prior_stats["duration_mu_sec"]) / prior_stats["duration_sigma_sec"], 2)
        level = classify_alert(z)

    alarm = False
    alarm_urgent = False
    if warming:
        if prior_stats and prior_stats["n"] < MIN_SAMPLES:
            reasons.append(f"베이스라인 학습 중: 누적 {prior_stats['n']}/{MIN_SAMPLES}건 — 정식 알람 보류")
        else:
            age = prior_stats["age_days"] if prior_stats else 0
            reasons.append(f"베이스라인 학습 중: 누적 {age:.1f}/{WARMUP_DAYS}일 — 정식 알람 보류")
    else:
        if level in ("warning", "critical"):
            alarm, alarm_urgent = True, level == "critical"
            reasons.append(f"이탈 지속시간 {duration_sec:.0f}초(z={z}) → {LEVEL_KO[level]} — "
                            f"평소 평균 {prior_stats['duration_mu_sec']:.0f}초 대비 이례적으로 김")
        else:
            reasons.append(f"이탈 지속시간 {duration_sec:.0f}초(z={z}) → {LEVEL_KO[level]}")

    update_baseline(store, bed_id, duration_sec)
    if persist:
        save_baseline(store)

    return {
        "bed_id": bed_id, "sensor": "behavior_fsm_exit",
        "duration_sec": duration_sec, "baseline": baseline_stats(store.get(bed_id)),
        "z_score": z, "alert_level": level, "alert_level_ko": LEVEL_KO.get(level),
        "alarm": alarm, "alarm_urgent": alarm_urgent, "reasons": reasons,
        "received_at": datetime.now().isoformat(timespec="milliseconds"),
    }


# ── 단독 테스트 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== behavior_fsm.py 자체 테스트 (복구본) ===\n")
    store = load_baseline()
    print("[로드된 baseline]", {k: v.get("n") for k, v in store.items()})

    test_store = {k: dict(v) for k, v in store.items()}
    for d in (300, 320, 280, 310, 290, 305):
        r = evaluate_completed("bed_01_test", d, test_store, persist=False)
    print("[평상시 반복 후 41분(2460초) 이탈]",
          evaluate_completed("bed_01_test", 2460, test_store, persist=False)["alert_level"],
          evaluate_completed("bed_01_test", 2460, test_store, persist=False)["z_score"])
