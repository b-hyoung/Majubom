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
#  speed         : 평균 보행 속도 (m/s)        — 느려지면 위험
#  speed_cv      : 속도 변동계수               — 불규칙할수록 위험
#  sway          : 좌우 흔들림 (m)             — 비틀거림
#  freeze_ratio  : 거의 멈춰 있던 시간 비율    — 멈칫거림
#  stride_length : 추정 보폭 (m)              — 짧아지면 위험
#  stride_cv     : 보폭 변동계수               — 불규칙할수록 위험
METRICS = ("speed", "speed_cv", "sway", "freeze_ratio", "stride_length", "stride_cv")

# σ 하한 (측정 초기 분산이 0에 가까워 z가 폭발하는 것을 방지)
SIGMA_FLOOR = {
    "speed": 0.05, "speed_cv": 0.03, "sway": 0.02,
    "freeze_ratio": 0.03, "stride_length": 0.03, "stride_cv": 0.02,
}

# alert_level 임계 (total_abs 기준)
# 지표 4개→6개로 늘어나 total_abs가 자연히 커지므로 비례 조정 (×1.5)
TH_CAUTION, TH_WARNING, TH_CRITICAL = 3.0, 6.0, 9.0

# 절대 임계 (baseline 무관 즉시 위험)
#  height_drop: track 높이(머리/몸통)가 한 윈도우에서 이만큼(m) 이상 급강하 →
#               주저앉음/낙상으로 간주하고 즉시 critical.
HEIGHT_DROP_FALL = 1.2

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
      - reliable=false          → 측정 무시 (baseline 갱신 X, 알람 X)
      - patient_locked=false    → 환자 특정 실패(2명+인데 소속도로 못 가림/0명) → 보류
      - walking=false           → 환자 정지·휴식(누움 등) → gait baseline 미반영 (낙상만 감시)
      - height_drop > 임계       → baseline 무관 즉시 critical (낙상 순간)
      - age_days<14 / n<MIN     → 학습 중, z는 보여주되 정식 알람 보류

    환자 특정은 송신부(send_mmw.select_patient)가 '침대 소속도'로 수행하고,
    presence.patient_locked / quality.walking 로 전달한다. 서버는 그 플래그로 게이트만 건다.
    """
    target_id = payload.get("target_id", "room_01")
    raw = payload.get("raw", {}) or {}
    quality = payload.get("quality", {}) or {}
    presence = payload.get("presence", {}) or {}

    reliable = bool(quality.get("reliable", True))
    n_targets = presence.get("n_targets", 1)
    # 구 payload 호환: patient_locked 없으면 "1명이면 환자"로 간주, walking 없으면 보행으로 간주
    patient_locked = bool(presence.get("patient_locked", n_targets == 1))
    walking = bool(quality.get("walking", True))
    straightness = quality.get("straightness")
    bed_affinity = presence.get("bed_affinity")
    bed_candidates = presence.get("bed_candidates")
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

    # 3) 환자 특정 실패 → 측정 보류 (누구의 보행인지 귀속 불가). 낙상 절대임계만 감시.
    #    (0명, 또는 2명+인데 소속도로 환자를 못 가린 경우)
    if not patient_locked:
        if n_targets == 0:
            who = "아무도 없음"
        elif bed_candidates is not None and bed_candidates >= 2:
            who = f"{n_targets}명 중 침대 소속 {bed_candidates}명 — 환자 구분 불가(애매→보류)"
        else:
            aff_txt = f", 소속도 {bed_affinity}" if bed_affinity is not None else ""
            who = f"{n_targets}명 — 환자 특정 실패(침대 소속 없음{aff_txt})"
        reasons.append(f"측정 보류: {who}")
        stats = baseline_stats(store.get(target_id))
        level = "critical" if abs_critical else "normal"
        if abs_critical:
            reasons.append(f"단, 높이 급강하(drop={height_drop}m) → 즉시 위험")
            alarm, alarm_urgent = True, True
        return _result(payload, raw, quality, presence, stats, None,
                       alert_level=level, alarm=alarm, alarm_urgent=alarm_urgent,
                       reasons=reasons, measuring_held=not abs_critical)

    # 4) 환자는 특정됐으나 '보행'이 아님(정지/휴식/누움) → gait baseline 미반영. 낙상만 감시.
    #    (보행 baseline 은 걷는 값으로만 쌓아야 오염되지 않음. 정지값을 넣으면 baseline 붕괴)
    if not walking:
        moving = isinstance(raw.get("speed"), (int, float)) and raw["speed"] >= 0.2
        if moving:
            st_txt = f" (직진성 {straightness})" if straightness is not None else ""
            why = f"보행이나 경로 굽음/왕복 — gait 왜곡 배제{st_txt}"
        else:
            why = "환자 정지/휴식(보행 아님)"
        aff_txt = f" (소속도 {bed_affinity})" if bed_affinity is not None else ""
        reasons.append(f"측정 보류: {why}, gait baseline 미반영{aff_txt}")
        stats = baseline_stats(store.get(target_id))
        level = "normal"
        if abs_critical:
            level = "critical"
            alarm, alarm_urgent = True, True
            reasons.append(f"높이 급강하(drop={height_drop}m) → 즉시 위험")
        return _result(payload, raw, quality, presence, stats, None,
                       alert_level=level, alarm=alarm, alarm_urgent=alarm_urgent,
                       reasons=reasons, measuring_held=not abs_critical)

    # 5) 정상 경로: 환자 특정 + 보행 → baseline 갱신 후 평가
    if n_targets >= 2:
        reasons.append(f"환자 특정: {n_targets}명 중 소속도 {bed_affinity} track 채택")
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
