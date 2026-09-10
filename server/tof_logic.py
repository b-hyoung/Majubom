"""
tof_logic.py — ToF 행동패턴 baseline 누적 / z-score / alert_level 계산 (서버 측)
====================================================================
2026-09-10 복구: git reset/clean으로 유실된 파일을 mmw_logic.py와 완전히 동일한
인터페이스(load_baseline/save_baseline/evaluate, μ·σ 누적, MIN_SAMPLES, WARMUP_DAYS,
SIGMA_FLOOR, 4단계 alert_level)로 재작성 — 세 센서(csi/mmw/tof)가 서버에서 같은 방식으로
다뤄지게 하는 것이 04번 탭 융합 AI의 전제조건이라는 설계 원칙 그대로 유지.
server/tof_behavior_baseline.json에 실제 남아있던 데이터(bed_01, n=12, 세 지표 sum/sumsq)를
그대로 읽어쓸 수 있도록 필드명을 정확히 맞췄다.

추적 지표 3종(자세분류 6클래스 시퀀스에서 윈도우 단위로 계산):
  posture_change_rate : 자세 전환 빈도(회/시간) — 평소보다 뒤척임이 급증/급감하면 이상 신호
  edge_sit_ratio       : 걸터앉음(edge_sit) 체류 비율 — 낙상 직전 전조 자세로 문헌에서 자주 언급됨
  out_of_bed_ratio     : 침상 이탈 비율 — 시간대별(특히 야간) 이탈 빈도가 평소보다 늘면 위험 신호
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(_HERE, "tof_behavior_baseline.json")

MIN_SAMPLES = 5
WARMUP_DAYS = 14

METRICS = ("posture_change_rate", "edge_sit_ratio", "out_of_bed_ratio")

SIGMA_FLOOR = {
    "posture_change_rate": 1.0,   # 회/시간
    "edge_sit_ratio": 0.03,
    "out_of_bed_ratio": 0.03,
}

TH_CAUTION, TH_WARNING, TH_CRITICAL = 1.5, 3.0, 4.5

LEVELS = ("normal", "caution", "warning", "critical")
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


def _new_bed() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    bed = {"first_seen": now, "n": 0}
    for m in METRICS:
        bed[m] = {"sum": 0.0, "sumsq": 0.0}
    return bed


def update_baseline(store: dict, bed_id: str, window: dict) -> dict:
    bed = store.setdefault(bed_id, _new_bed())
    for m in METRICS:
        v = float(window.get(m, 0.0))
        bed[m]["sum"] += v
        bed[m]["sumsq"] += v * v
    bed["n"] += 1
    return bed


def _mu_sigma(stat: dict, n: int, floor: float) -> tuple[float, float]:
    mu = stat["sum"] / n
    var = stat["sumsq"] / n - mu * mu
    sigma = math.sqrt(var) if var > 0 else 0.0
    return mu, max(sigma, floor)


def baseline_stats(bed: dict | None) -> dict | None:
    if not bed or bed.get("n", 0) == 0:
        return None
    n = bed["n"]
    out = {"n": n}
    for m in METRICS:
        mu, sig = _mu_sigma(bed[m], n, SIGMA_FLOOR[m])
        out[f"{m}_mu"] = round(mu, 3)
        out[f"{m}_sigma"] = round(sig, 3)
    try:
        first = datetime.fromisoformat(bed["first_seen"])
        age_days = (datetime.now(timezone.utc) - first).total_seconds() / 86400.0
    except (KeyError, ValueError):
        age_days = 0.0
    out["age_days"] = round(age_days, 2)
    return out


def compute_zscore(window: dict, stats: dict) -> dict:
    z = {}
    total = 0.0
    for m in METRICS:
        zi = (float(window.get(m, 0.0)) - stats[f"{m}_mu"]) / stats[f"{m}_sigma"]
        z[m] = round(zi, 2)
        total += abs(zi)
    z["total_abs"] = round(total, 2)
    return z


def classify_alert(total_abs: float) -> str:
    if total_abs >= TH_CRITICAL:
        return "critical"
    if total_abs >= TH_WARNING:
        return "warning"
    if total_abs >= TH_CAUTION:
        return "caution"
    return "normal"


def evaluate(bed_id: str, window: dict, store: dict, persist: bool = True) -> dict:
    """자세 시퀀스에서 이미 계산된 윈도우 지표 1건(posture_change_rate/edge_sit_ratio/
    out_of_bed_ratio)을 받아 baseline 갱신 + z-score/alert 계산. mmw_logic.evaluate()와
    동일한 반환 shape을 쓰되, ToF는 '환자 특정 실패' 같은 게이팅이 없어 더 단순하다."""
    update_baseline(store, bed_id, window)
    stats = baseline_stats(store.get(bed_id))

    zscore = None
    level = "normal"
    reasons: list[str] = []
    warming = (stats is None) or (stats["n"] < MIN_SAMPLES) or (stats["age_days"] < WARMUP_DAYS)

    if stats and stats["n"] >= MIN_SAMPLES:
        zscore = compute_zscore(window, stats)
        level = classify_alert(zscore["total_abs"])

    alarm = False
    alarm_urgent = False
    if warming:
        if stats and stats["n"] < MIN_SAMPLES:
            reasons.append(f"베이스라인 학습 중: 누적 {stats['n']}/{MIN_SAMPLES}건 — 정식 알람 보류")
        else:
            age = stats["age_days"] if stats else 0
            reasons.append(f"베이스라인 학습 중: 누적 {age:.1f}/{WARMUP_DAYS}일 — 정식 알람 보류")
    else:
        if level in ("warning", "critical"):
            alarm = True
            alarm_urgent = (level == "critical")
            reasons.append(f"z-score 종합 {zscore['total_abs']} → {LEVEL_KO[level]} 알람")
        else:
            reasons.append(f"z-score 종합 {zscore['total_abs']} → {LEVEL_KO[level]}")

    if persist:
        save_baseline(store)

    return {
        "bed_id": bed_id,
        "sensor": "tof_behavior",
        "window": window,
        "baseline": stats,
        "zscore": zscore,
        "alert_level": level,
        "alert_level_ko": LEVEL_KO.get(level),
        "alarm": alarm,
        "alarm_urgent": alarm_urgent,
        "reasons": reasons,
        "received_at": datetime.now().isoformat(timespec="milliseconds"),
    }


# ── 단독 테스트 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== tof_logic.py 자체 테스트 (복구본) ===\n")
    store = load_baseline()
    print("[로드된 baseline]", {k: v.get("n") for k, v in store.items()})

    test_store = {k: dict(v) for k, v in store.items()}  # 실 baseline 오염 방지용 사본
    normal_window = {"posture_change_rate": 2.0, "edge_sit_ratio": 0.05, "out_of_bed_ratio": 0.02}
    r = evaluate("bed_01_test", normal_window, test_store, persist=False)
    print("[평상시 윈도우]", r["alert_level"], r["reasons"])

    for _ in range(6):
        update_baseline(test_store, "bed_01_test", normal_window)
    abnormal_window = {"posture_change_rate": 25.0, "edge_sit_ratio": 0.6, "out_of_bed_ratio": 0.4}
    r2 = evaluate("bed_01_test", abnormal_window, test_store, persist=False)
    print("[이상 윈도우]", r2["alert_level"], r2["zscore"], r2["reasons"])
