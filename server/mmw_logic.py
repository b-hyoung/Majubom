"""
mmWave 보행 분석 로직 — baseline 누적 / z-score / alert_level 계산 (서버 측)
====================================================================
mmWave 파트(송신부)는 보행 지표(gait features) + quality + presence 만 보냄.
baseline(평소 μ·σ) · z-score(평소 대비 변화) · alert_level(단계) 은 **서버가** 계산한다.

csi_logic.py 와 동일한 인터페이스(load_baseline / save_baseline / evaluate)를 따른다.
→ 통합 단계에서 두 센서 결과를 같은 방식으로 합칠 수 있다.

baseline은 "과거 측정의 집계"이므로 JSON 파일(server/mmw_baseline.json)을
대상(target_id, 기본 'room_01')별 누적 통계로 사용한다.

이 모듈은 Flask 비의존(순수 함수) → 단독 테스트 가능.

CSI 와의 대응 관계
  raw.hr_bpm/resp_rpm/autocorr_strength  →  raw.speed/speed_cv/sway/freeze_ratio
  절대임계 HR>140 (즉시 critical)        →  height_drop > 임계 (낙상 순간, 즉시 critical)
  quality.reliable=false (무시)          →  track 신뢰 불가(짧음/포인트 적음) → 무시
  presence.gate_active=false (보류)      →  n_targets != 1 (여러 사람/없음) → 보류
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

# ── 경로 ──────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(_HERE, "mmw_baseline.json")

# ── 튜닝 상수 ─────────────────────────────────────────────────────────
MIN_SAMPLES = 5          # z-score를 신뢰할 최소 누적 측정 수 (σ 안정화)
WARMUP_DAYS = 14         # 누적 일수 < 14 → "학습 중", 정식 알람 보류

# 추적하는 보행 지표 4종. 각각 baseline μ·σ 를 누적한다.
#  speed       : 평균 보행 속도 (m/s)        — 느려지면 위험
#  speed_cv    : 속도 변동계수               — 불규칙할수록 위험
#  sway        : 좌우 흔들림 (m)             — 비틀거림
#  freeze_ratio: 거의 멈춰 있던 시간 비율    — 멈칫거림
METRICS = ("speed", "speed_cv", "sway", "freeze_ratio")

# σ 하한 (측정 초기 분산이 0에 가까워 z가 폭발하는 것을 방지)
SIGMA_FLOOR = {"speed": 0.05, "speed_cv": 0.03, "sway": 0.02, "freeze_ratio": 0.03}

# alert_level 임계 (total_abs 기준)
TH_CAUTION, TH_WARNING, TH_CRITICAL = 2.0, 4.0, 6.0

# 절대 임계 (baseline 무관 즉시 위험)
#  height_drop: track 높이(머리/몸통)가 한 윈도우에서 이만큼(m) 이상 급강하 →
#               주저앉음/낙상으로 간주하고 즉시 critical.
HEIGHT_DROP_FALL = 0.5

LEVELS = ("normal", "caution", "warning", "critical")
LEVEL_KO = {"normal": "정상", "caution": "주의", "warning": "경고", "critical": "위험"}


# ── 영속 저장 (csi_logic 과 동일 방식) ─────────────────────────────────
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
    os.replace(tmp, path)  # 원자적 교체


# ── 누적 통계 (running sum / sumsq) ────────────────────────────────────
def _new_target() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    bed = {"first_seen": now, "n": 0}
    for m in METRICS:
        bed[m] = {"sum": 0.0, "sumsq": 0.0}
    return bed


def update_baseline(store: dict, target_id: str, raw: dict) -> dict:
    """신뢰 가능한 측정 1건을 대상 누적 통계에 반영. (store를 제자리 수정)"""
    bed = store.setdefault(target_id, _new_target())
    for m in METRICS:
        v = float(raw.get(m, 0.0))
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
    """누적 통계 → μ·σ·age_days. 데이터 없으면 None."""
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


# ── z-score / alert ────────────────────────────────────────────────────
def compute_zscore(raw: dict, stats: dict) -> dict:
    z = {}
    total = 0.0
    for m in METRICS:
        zi = (float(raw.get(m, 0.0)) - stats[f"{m}_mu"]) / stats[f"{m}_sigma"]
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


# ── 메인: 측정 1건 평가 (csi_logic.evaluate 와 동일 시그니처) ───────────
def evaluate(payload: dict, store: dict, persist: bool = True) -> dict:
    """mmWave payload 1건을 받아 baseline 갱신 + z-score/alert 계산 후 보강 dict 반환.

    예외규칙(csi_logic 과 동일 사상):
      - reliable=false        → 측정 무시 (baseline 갱신 X, 알람 X)
      - n_targets != 1        → 0명(아무도 없음) 또는 2명 이상(보행 분리 불가) → 보류
      - height_drop > 임계     → baseline 무관 즉시 critical (낙상 순간)
      - age_days<14 / n<MIN   → 학습 중, z는 보여주되 정식 알람 보류
    """
    target_id = payload.get("target_id", "room_01")
    raw = payload.get("raw", {}) or {}
    quality = payload.get("quality", {}) or {}
    presence = payload.get("presence", {}) or {}

    reliable = bool(quality.get("reliable", True))
    n_targets = presence.get("n_targets", 1)
    height_drop = raw.get("height_drop", 0.0)

    reasons: list[str] = []
    alarm = False
    alarm_urgent = False
    update_done = False

    # 1) 신뢰 불가 측정 → 무시 (가짜 track/거울상 등)
    if not reliable:
        reasons.append("측정 무시: track 신뢰 불가(reliable=false)")
        stats = baseline_stats(store.get(target_id))
        return _result(payload, raw, quality, presence, stats, None,
                       alert_level=None, alarm=False, alarm_urgent=False,
                       reasons=reasons, measuring_held=True)

    # 2) 절대 임계: 높이 급강하 = 낙상 순간 (baseline 무관)
    abs_critical = isinstance(height_drop, (int, float)) and height_drop > HEIGHT_DROP_FALL

    # 3) 사람이 1명이 아니면 보행 분리 불가 → 측정 보류 (baseline 갱신 안 함)
    if n_targets != 1:
        who = "아무도 없음" if n_targets == 0 else f"{n_targets}명 감지(보행 분리 불가)"
        reasons.append(f"측정 보류: {who}")
        stats = baseline_stats(store.get(target_id))
        level = "critical" if abs_critical else "normal"
        if abs_critical:
            reasons.append(f"단, 높이 급강하(drop={height_drop}m) → 즉시 위험")
            alarm, alarm_urgent = True, True
        return _result(payload, raw, quality, presence, stats, None,
                       alert_level=level, alarm=alarm, alarm_urgent=alarm_urgent,
                       reasons=reasons, measuring_held=not abs_critical)

    # 4) 정상 경로: baseline 갱신 후 평가
    update_baseline(store, target_id, raw)
    update_done = True
    stats = baseline_stats(store.get(target_id))

    zscore = None
    level = "normal"
    warming = (stats is None) or (stats["n"] < MIN_SAMPLES) or (stats["age_days"] < WARMUP_DAYS)

    if stats and stats["n"] >= MIN_SAMPLES:
        zscore = compute_zscore(raw, stats)
        level = classify_alert(zscore["total_abs"])

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

    # 절대 임계(낙상)는 학습 여부와 무관하게 즉시 위험
    if abs_critical:
        level = "critical"
        alarm, alarm_urgent = True, True
        reasons.append(f"높이 급강하(drop={height_drop}m) → 즉시 위험(베이스라인 무관)")

    if persist and update_done:
        save_baseline(store)

    return _result(payload, raw, quality, presence, stats, zscore,
                   alert_level=level, alarm=alarm, alarm_urgent=alarm_urgent,
                   reasons=reasons, measuring_held=False)


def _result(payload, raw, quality, presence, stats, zscore, *,
            alert_level, alarm, alarm_urgent, reasons, measuring_held) -> dict:
    return {
        "timestamp": payload.get("timestamp"),
        "target_id": payload.get("target_id", "room_01"),
        "sensor": "mmwave",
        "raw": raw,
        "quality": quality,
        "presence": presence,
        "baseline": stats,
        "zscore": zscore,
        "alert_level": alert_level,
        "alert_level_ko": LEVEL_KO.get(alert_level) if alert_level else None,
        "alarm": alarm,
        "alarm_urgent": alarm_urgent,
        "measuring_held": measuring_held,
        "reasons": reasons,
        "received_at": datetime.now().isoformat(timespec="milliseconds"),
    }
