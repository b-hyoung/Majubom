"""
CSI 분석 로직 — baseline 누적 / z-score / alert_level 계산 (서버 측)
====================================================================
CSI 파트는 raw + quality + presence 만 보냄 (esp32/서버연동_변경사항.md).
baseline(평소 μ·σ) · z-score(평소 대비 변화) · alert_level(단계) 은 **서버가** 계산한다.

baseline은 "과거 측정의 집계"이므로 DB가 단일 출처 — 여기서는 가벼운 JSON 파일
(server/csi_baseline.json)을 침대(bed_id)별 누적 통계로 사용한다.

이 모듈은 Flask 비의존(순수 함수) → 단독 테스트 가능.
공식 출처: esp32/서버연동_변경사항.md
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

# ── 경로 ──────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(_HERE, "csi_baseline.json")

# ── 튜닝 상수 ─────────────────────────────────────────────────────────
MIN_SAMPLES = 5          # z-score를 신뢰할 최소 누적 측정 수 (σ 안정화)
WARMUP_DAYS = 14         # 누적 일수 < 14 → "학습 중", 정식 알람 보류
# σ 하한 (측정 초기 분산이 0에 가까워 z가 폭발하는 것을 방지)
SIGMA_FLOOR = {"hr": 2.0, "resp": 1.0, "strength": 0.03}
# alert_level 임계 (total_abs 기준) — 서버연동_변경사항.md
TH_CAUTION, TH_WARNING, TH_CRITICAL = 2.0, 4.0, 6.0
# 절대 임계 (baseline 무관 즉시 위험)
HR_ABS_HIGH, HR_ABS_LOW = 140, 40

LEVELS = ("normal", "caution", "warning", "critical")
LEVEL_KO = {"normal": "정상", "caution": "주의", "warning": "경고", "critical": "위험"}


# ── 영속 저장 ──────────────────────────────────────────────────────────
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
    os.replace(tmp, path)  # 원자적 교체 (쓰기 도중 손상 방지)


# ── 누적 통계 (running sum / sumsq) ────────────────────────────────────
def _new_bed() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "first_seen": now,
        "n": 0,
        "hr": {"sum": 0.0, "sumsq": 0.0},
        "resp": {"sum": 0.0, "sumsq": 0.0},
        "strength": {"sum": 0.0, "sumsq": 0.0},
    }


def update_baseline(store: dict, bed_id: str, raw: dict) -> dict:
    """신뢰 가능한 측정 1건을 침대 누적 통계에 반영. (store를 제자리 수정)"""
    bed = store.setdefault(bed_id, _new_bed())
    pairs = (("hr", raw["hr_bpm"]), ("resp", raw["resp_rpm"]),
             ("strength", raw["autocorr_strength"]))
    for key, val in pairs:
        v = float(val)
        bed[key]["sum"] += v
        bed[key]["sumsq"] += v * v
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
    hr_mu, hr_sig = _mu_sigma(bed["hr"], n, SIGMA_FLOOR["hr"])
    resp_mu, resp_sig = _mu_sigma(bed["resp"], n, SIGMA_FLOOR["resp"])
    str_mu, str_sig = _mu_sigma(bed["strength"], n, SIGMA_FLOOR["strength"])
    try:
        first = datetime.fromisoformat(bed["first_seen"])
        age_days = (datetime.now(timezone.utc) - first).total_seconds() / 86400.0
    except (KeyError, ValueError):
        age_days = 0.0
    return {
        "n": n,
        "age_days": round(age_days, 2),
        "hr_mu": round(hr_mu, 1), "hr_sigma": round(hr_sig, 2),
        "resp_mu": round(resp_mu, 1), "resp_sigma": round(resp_sig, 2),
        "strength_mu": round(str_mu, 3), "strength_sigma": round(str_sig, 3),
    }


# ── z-score / alert ────────────────────────────────────────────────────
def compute_zscore(raw: dict, stats: dict) -> dict:
    z_hr = (raw["hr_bpm"] - stats["hr_mu"]) / stats["hr_sigma"]
    z_resp = (raw["resp_rpm"] - stats["resp_mu"]) / stats["resp_sigma"]
    z_str = (raw["autocorr_strength"] - stats["strength_mu"]) / stats["strength_sigma"]
    # total_abs = 생체 신호(심박·호흡)만. 신호강도(z_str)는 품질 지표라 위험점수 제외 → 신뢰 게이트로만.
    total_abs = abs(z_hr) + abs(z_resp)
    return {"hr": round(z_hr, 2), "resp": round(z_resp, 2),
            "strength": round(z_str, 2),   # 참고용 표시(위험점수 미합산)
            "total_abs": round(total_abs, 2)}


def classify_alert(total_abs: float) -> str:
    if total_abs >= TH_CRITICAL:
        return "critical"
    if total_abs >= TH_WARNING:
        return "warning"
    if total_abs >= TH_CAUTION:
        return "caution"
    return "normal"


# ── 메인: 측정 1건 평가 ─────────────────────────────────────────────────
def evaluate(payload: dict, store: dict, persist: bool = True) -> dict:
    """CSI payload 1건을 받아 baseline 갱신 + z-score/alert 계산 후 보강 dict 반환.

    반환에는 송신 원본(raw/quality/presence)에 더해 서버 계산값
    (baseline/zscore/alert_level/alarm/reasons)이 포함된다.
    예외규칙(서버연동_변경사항.md):
      - reliable=false       → 측정 무시 (baseline 갱신 X, 알람 X)
      - gate_active=false    → 보호자 동석, 신호 혼입 → alert 강제 normal, 알람 보류
      - hr>140 또는 <40      → baseline 무관 즉시 critical (절대 임계)
      - age_days<14 / n<MIN  → 학습 중, z는 보여주되 정식 알람 보류
    """
    bed_id = payload.get("bed_id", "unknown")
    raw = payload.get("raw", {}) or {}
    quality = payload.get("quality", {}) or {}
    presence = payload.get("presence", {}) or {}

    reliable = bool(quality.get("reliable", True))
    gate_active = bool(presence.get("gate_active", True))
    hr = raw.get("hr_bpm")

    reasons: list[str] = []
    alarm = False
    alarm_urgent = False
    update_done = False

    # 1) 신뢰 불가 측정 → 무시
    if not reliable:
        reasons.append("측정 무시: 신호 품질 낮음(reliable=false)")
        stats = baseline_stats(store.get(bed_id))
        return _result(payload, raw, quality, presence, stats, None,
                       alert_level=None, alarm=False, alarm_urgent=False,
                       reasons=reasons, measuring_held=True)

    # 2) 절대 임계 (baseline 무관)
    abs_critical = isinstance(hr, (int, float)) and (hr > HR_ABS_HIGH or hr < HR_ABS_LOW)

    # 3) 보호자 동석(게이트 차단) → 측정 보류 (baseline 갱신도 안 함: 신호 혼입)
    if not gate_active:
        reasons.append("보호자/간호사 동석 — 측정 보류(gate_active=false)")
        stats = baseline_stats(store.get(bed_id))
        level = "critical" if abs_critical else "normal"
        if abs_critical:
            reasons.append(f"단, 절대 임계(HR={hr}) → 즉시 위험")
            alarm, alarm_urgent = True, True
        return _result(payload, raw, quality, presence, stats, None,
                       alert_level=level, alarm=alarm, alarm_urgent=alarm_urgent,
                       reasons=reasons, measuring_held=not abs_critical)

    # 4) 정상 경로: baseline 갱신 후 평가
    update_baseline(store, bed_id, raw)
    update_done = True
    stats = baseline_stats(store.get(bed_id))

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

    # 절대 임계는 학습 여부와 무관하게 즉시 위험
    if abs_critical:
        level = "critical"
        alarm, alarm_urgent = True, True
        reasons.append(f"절대 임계 초과(HR={hr}) → 즉시 위험(베이스라인 무관)")

    if persist and update_done:
        save_baseline(store)

    return _result(payload, raw, quality, presence, stats, zscore,
                   alert_level=level, alarm=alarm, alarm_urgent=alarm_urgent,
                   reasons=reasons, measuring_held=False)


def _result(payload, raw, quality, presence, stats, zscore, *,
            alert_level, alarm, alarm_urgent, reasons, measuring_held) -> dict:
    return {
        "timestamp": payload.get("timestamp"),
        "bed_id": payload.get("bed_id"),
        "sensor": "csi",
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
