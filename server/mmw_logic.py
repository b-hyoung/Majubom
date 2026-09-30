"""
mmWave 보행 분석 로직 — baseline 누적 / z-score / alert_level 계산 (서버 측)
====================================================================
mmWave 파트(송신부)는 보행 지표(gait features) + quality + presence 만 보냄.
baseline(평소 μ·σ) · z-score(평소 대비 변화) · alert_level(단계) 은 **서버가** 계산한다.

tof_logic.py와 동일한 인터페이스(load_baseline / save_baseline / evaluate)를 따른다.
→ 통합 단계에서 두 센서 결과를 같은 방식으로 합칠 수 있다.

baseline은 "과거 측정의 집계"이므로 JSON 파일(server/mmw_baseline.json)을
대상(target_id, 기본 'room_01')별 누적 통계로 사용한다.

이 모듈은 Flask 비의존(순수 함수) → 단독 테스트 가능.
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

# ── 문헌 기반 사전분포(gait_prior.py)가 있을 때만 쓰는 상수 ─────────────────
#  개인 기록이 MIN_SAMPLES건 미만인 동안은 문헌 기대 범위와 비교한 '잠정' 판정만 한다.
#  KIM2025가 10·90 백분위를 정상 범위 경계로 제시하므로 하위 10%를 주의, 하위 2.5%를 경고로 둔다.
#  잠정 판정은 '위험' 단계와 정식 알람을 만들지 않는다(높이 급강하 절대 규칙은 그대로 적용).
Z_P10 = 1.2816               # 정규분포 10·90 백분위 z (기대 범위 표시용)
PROV_CAUTION_Z = -Z_P10
PROV_WARNING_Z = -1.9600
IDENTITY_Z = 2.3263          # 기대 범위 상위 1%보다 빠르면 '환자가 아닐 가능성' 안내(판정에는 반영하지 않음)
MIN_METRICS_FOR_S = 4        # 합계 S는 원래 지표 4개 기준으로 설계됐다(아래 임계 ×1.5 주석). 그보다 적으면 잠정 유지


# ── 영속 저장 (tof_logic.py와 동일 방식) ─────────────────────────────────
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


# ── 사전분포 경로용 '유효값 전용' 누적 ─────────────────────────────────────
# 기존 합계(sum·sumsq, 건수 n)는 이전 버전과 똑같이 쌓는다(보폭 추정 실패 0.0 포함).
# → 이전 버전 코드로 되돌려도 파일을 그대로 읽어 같은 결과를 낸다.
# 사전분포 경로는 결측(보폭 0.0·NaN·무한대·비정상 크기)을 뺀 v_sum·v_sumsq·v_n을 옆 칸에 따로 쌓는다.
# valid_at_n == n일 때만 이 칸을 믿고, 어긋나면(이전 버전 코드가 파일을 갱신) 기존 합계에서 다시 시작한다.
VALID_KEY = "valid_at_n"
MAX_ABS_METRIC = 1e3   # 물리적으로 불가능한 크기 — 제곱이 넘치지 않도록 누적 전에 결측 처리


def _valid_ready(bed: dict) -> bool:
    return bed.get(VALID_KEY) == bed.get("n") and all("v_n" in bed.get(m, {}) for m in METRICS)


def _reset_valid_from_legacy(bed: dict) -> None:
    n = bed.get("n", 0)
    for m in METRICS:
        st = bed[m]
        st["v_sum"], st["v_sumsq"], st["v_n"] = st["sum"], st["sumsq"], n
    bed[VALID_KEY] = n


def _legacy_value(raw: dict, m: str) -> float:
    """기존 합계에 넣을 값: 이전 버전과 같게 raw 값(없으면 0.0). 단 NaN·무한대·비정상 크기는 0.0."""
    try:
        f = float(raw.get(m, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) and abs(f) <= MAX_ABS_METRIC else 0.0


def update_baseline(store: dict, target_id: str, raw: dict) -> dict:
    """신뢰 가능한 측정 1건을 대상 누적 통계에 반영. (store를 제자리 수정)"""
    bed = store.setdefault(target_id, _new_target())
    track_valid = _valid_ready(bed)     # 사전분포 경로가 만든 유효값 누적이 있으면 함께 갱신
    for m in METRICS:
        v = float(raw.get(m, 0.0))
        bed[m]["sum"] += v
        bed[m]["sumsq"] += v * v
        if track_valid:
            x = _metric_value(raw, m)
            if x is not None:
                bed[m]["v_sum"] += x
                bed[m]["v_sumsq"] += x * x
                bed[m]["v_n"] += 1
    bed["n"] += 1
    if track_valid:
        bed[VALID_KEY] = bed["n"]
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


# ── 메인: 측정 1건 평가 (tof_logic.evaluate 와 동일 시그니처) ───────────
def evaluate(payload: dict, store: dict, persist: bool = True, prior: dict | None = None) -> dict:
    """mmWave payload 1건을 받아 baseline 갱신 + z-score/alert 계산 후 보강 dict 반환.

    prior: gait_prior.py가 만든 문헌 기반 사전분포(없으면 기존 동작 그대로).
      - 개인 기록 5건 미만: 문헌 기대 범위와 비교한 '잠정' 판정(주의·경고까지, 정식 알람 없음)
      - 5건 이상: 개인 평균에 사전분포를 섞은 값(베이즈 갱신)으로 기존 S 판정
      - 현재 구간은 비교가 끝난 뒤 기준선에 넣는다(자기 자신과 비교하지 않음)
      - 보폭 추정 실패(stride_length 0.0)는 0으로 쌓지 않고 결측으로 처리

    예외규칙(tof_logic.py와 동일 사상):
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
        invalid_fields = quality.get("invalid_fields")
        if invalid_fields:
            why = f"지표 값 오류({', '.join(map(str, invalid_fields))}) — 0.0으로 바꾸고 보행 기준선에서 제외"
        elif moving:
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

    extra: dict = {}
    if prior and (prior.get("metrics") or {}).get("speed"):
        return _evaluate_with_prior(payload, raw, quality, presence, store, target_id, prior,
                                    abs_critical, height_drop, reasons, persist)
    if prior and prior.get("not_applicable") and "보행 불가" in str(prior.get("not_applicable")):
        note = "보행 불가(와상 등) 기록 환자인데 보행이 감지됨 — 환자 본인이 아닐 가능성 또는 기록 갱신 필요"
        reasons.append(note)
        extra = {"identity_note": note, "prior_id": prior.get("id")}

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
                   reasons=reasons, measuring_held=False, extra=extra)


# ── 문헌 기반 사전분포 경로 ─────────────────────────────────────────────────
def _finite_number(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) and abs(f) <= MAX_ABS_METRIC else None


def _metric_value(raw: dict, m: str) -> float | None:
    """유효값 전용 누적·비교에 쓸 값. NaN·무한대·비정상 크기는 누적하면 기준선을 망가뜨리므로 결측."""
    v = _finite_number(raw.get(m))
    if v is None:
        return None
    if m in ("stride_length", "stride_cv"):
        s = _finite_number(raw.get("stride_length"))
        if s is None or s <= 0.0:
            return None   # send_mmw가 보폭 추정에 실패하면 0.0을 보낸다 → 0으로 쌓지 않고 결측
        if m == "stride_cv" and v <= 0.0:
            return None   # 걸음 구간이 2개 미만이라 변동계수를 못 구해도 0.0을 보낸다 → 결측
    return v


def _personal(bed: dict | None, m: str) -> tuple[int, float | None, float | None]:
    """현재 구간을 넣기 전의 개인 기록(유효값 전용 누적): (건수, 평균, 표준편차)."""
    if not bed or not _valid_ready(bed):
        return 0, None, None
    stat = bed[m]
    n = int(stat.get("v_n", 0))
    if n <= 0:
        return 0, None, None
    mean = stat["v_sum"] / n
    var = stat["v_sumsq"] / n - mean * mean
    return n, mean, math.sqrt(var) if var > 0 else 0.0


def _posterior(mu0: float, tau0: float, n: int, mean: float | None, s_w: float) -> tuple[float, float]:
    """정규-정규 베이즈 갱신: 사전 N(mu0, tau0²) + 개인 구간 n개(평균 mean, 구간 흔들림 s_w)."""
    if n <= 0 or mean is None:
        return mu0, tau0 ** 2
    prec = 1.0 / tau0 ** 2 + n / s_w ** 2
    return (mu0 / tau0 ** 2 + n * mean / s_w ** 2) / prec, 1.0 / prec


def _classify_scaled(total_abs: float, scale: float) -> str:
    if total_abs >= TH_CRITICAL * scale:
        return "critical"
    if total_abs >= TH_WARNING * scale:
        return "warning"
    if total_abs >= TH_CAUTION * scale:
        return "caution"
    return "normal"


def _within_sd(n: int, sd: float | None, prior_sd: float, floor: float) -> float:
    """구간 사이 흔들림(σ_w). 개인 기록이 MIN_SAMPLES건 미만이면 사전값과 합친 추정을 쓴다.
    사전값의 무게는 자유도 MIN_SAMPLES−1 — 기존 설계가 개인 σ를 믿기 시작하는 5건과 같은 크기의 정보로 취급."""
    nu0 = MIN_SAMPLES - 1
    if n >= MIN_SAMPLES and sd is not None:
        pooled = sd
    elif n >= 2 and sd is not None:
        # sd는 n으로 나눈 분산의 제곱근이므로 편차 제곱합 = n·sd² (자유도 n−1)
        pooled = math.sqrt((nu0 * prior_sd ** 2 + n * sd ** 2) / (nu0 + n - 1))
    else:
        pooled = prior_sd
    return max(pooled, floor)


def _evaluate_with_prior(payload, raw, quality, presence, store, target_id, prior,
                         abs_critical, height_drop, reasons, persist) -> dict:
    metrics_prior = prior.get("metrics") or {}
    bed = store.get(target_id)
    if bed and bed.get("n") and not _valid_ready(bed):
        stale = any("v_n" in bed.get(m, {}) for m in METRICS)
        _reset_valid_from_legacy(bed)
        reasons.append("기준선 파일이 이전 버전 코드로 갱신돼 유효값 누적을 기존 합계에서 다시 시작함" if stale
                       else "기존 기준선을 이어받음(보폭 추정 실패 0.0이 섞인 과거 기록 포함)")
    values = {m: _metric_value(raw, m) for m in METRICS}
    z, prov_z, id_z, expected = {}, {}, {}, {}
    for m in METRICS:
        n_m, mean_m, sd_m = _personal(bed, m)
        pr = metrics_prior.get(m)
        post_mean = mean_m
        x = values[m]
        if pr:
            mu0, tau0 = float(pr["mean"]), float(pr["tau"])
            s_w = _within_sd(n_m, sd_m, float(pr["sigma_within_prior"]), SIGMA_FLOOR[m])
            post_mean, post_var = _posterior(mu0, tau0, n_m, mean_m, s_w)
            pred_sd = math.sqrt(s_w ** 2 + post_var)
            prior_weight = (1.0 / tau0 ** 2) / (1.0 / tau0 ** 2 + n_m / s_w ** 2)
            expected[m] = {"mean": round(post_mean, 3),
                           "p10": round(max(0.0, post_mean - Z_P10 * pred_sd), 3),
                           "p90": round(post_mean + Z_P10 * pred_sd, 3),
                           "n_personal": n_m, "prior_weight": round(prior_weight, 3)}
            if x is not None:
                # 잠정 판정: 지금까지 k개 구간 평균이 문헌 기대 분포(평균 mu0, 개인차 tau0 + 구간 흔들림/k)의 어디인지
                k = n_m + 1
                mean_k = ((mean_m or 0.0) * n_m + x) / k
                prov_z[m] = (mean_k - mu0) / math.sqrt(tau0 ** 2 + s_w ** 2 / k)
                # 신원 확인 안내: 이번 한 구간이 기대 분포에서 얼마나 벗어났는지
                id_z[m] = (x - mu0) / math.sqrt(tau0 ** 2 + s_w ** 2)
        if x is None:
            continue
        if n_m >= MIN_SAMPLES and post_mean is not None:
            z[m] = (x - post_mean) / max(sd_m or 0.0, SIGMA_FLOOR[m])

    n_before = int((bed or {}).get("n", 0))
    identity_note = None
    if len(z) >= MIN_METRICS_FOR_S:
        provisional = False
        scale = len(z) / len(METRICS)
        total = sum(abs(v) for v in z.values())
        level = _classify_scaled(total, scale)
        zscore = {m: round(v, 2) for m, v in z.items()}
        zscore.update(total_abs=round(total, 2), metrics_used=len(z), threshold_scale=round(scale, 3))
        source = "blended"
    else:
        provisional = True
        source = "population_prior"
        use_speed = metrics_prior.get("speed", {}).get("use_for_provisional")
        zs = prov_z.get("speed") if use_speed else None
        level = "normal"
        if zs is None:
            reasons.append(f"개인 기록 {n_before}건 — 이번 구간에 문헌 기준값이 있는 지표가 없어 비교하지 않음")
        else:
            if zs <= PROV_WARNING_Z:
                level = "warning"
            elif zs <= PROV_CAUTION_Z:
                level = "caution"
            reasons.append(f"개인 기록 {n_before}건 — 지금까지 {n_before + 1}개 구간 평균을 문헌 기대 범위와 비교한 "
                           f"잠정 판정: 속도 z={zs:.2f} → {LEVEL_KO[level]}")
            if use_speed and id_z.get("speed", 0.0) >= IDENTITY_Z:
                identity_note = ("이 환자의 문헌 기대 범위보다 훨씬 빠른 보행 — 직원·보호자 보행이거나 "
                                 "기록 정보(진단·보행 상태)가 실제와 다를 가능성")
                reasons.append(identity_note)
        zscore = {"speed": round(zs, 2) if zs is not None else None, "total_abs": None, "provisional": True}

    stats_before = baseline_stats(bed) if bed and bed.get("n") else None
    age_days = stats_before["age_days"] if stats_before else 0.0
    alarm = alarm_urgent = False
    if provisional:
        reasons.append("잠정 판정 — 개인 기준이 생기기 전이라 정식 알람을 보내지 않음")
    elif n_before < MIN_SAMPLES or age_days < WARMUP_DAYS:
        reasons.append(f"베이스라인 학습 중: 누적 {age_days:.1f}/{WARMUP_DAYS}일 — 정식 알람 보류")
    elif level in ("warning", "critical"):
        alarm = True
        alarm_urgent = (level == "critical")
        reasons.append(f"z-score 종합 {zscore['total_abs']} (지표 {zscore['metrics_used']}개) → {LEVEL_KO[level]} 알람")
    else:
        reasons.append(f"z-score 종합 {zscore['total_abs']} (지표 {zscore['metrics_used']}개) → {LEVEL_KO[level]}")

    if abs_critical:
        level = "critical"
        alarm = alarm_urgent = True
        reasons.append(f"높이 급강하(drop={height_drop}m) → 즉시 위험(베이스라인 무관)")

    # 비교가 끝난 뒤 현재 구간을 기준선에 넣는다.
    #  기존 합계: 이전 버전과 같은 방식(보폭 실패 0.0 포함)  ·  유효값 전용 합계: 결측은 건너뜀
    bed = store.setdefault(target_id, _new_target())
    if not _valid_ready(bed):
        _reset_valid_from_legacy(bed)
    for m in METRICS:
        stat = bed[m]
        lv = _legacy_value(raw, m)
        stat["sum"] += lv
        stat["sumsq"] += lv * lv
        v = values[m]
        if v is not None:
            stat["v_sum"] += v
            stat["v_sumsq"] += v * v
            stat["v_n"] += 1
    bed["n"] += 1
    bed[VALID_KEY] = bed["n"]
    if persist:
        save_baseline(store)
    stats = baseline_stats(bed)

    extra = {"provisional": provisional, "baseline_source": source, "expected": expected,
             "prior_id": prior.get("id")}
    if identity_note:
        extra["identity_note"] = identity_note
    return _result(payload, raw, quality, presence, stats, zscore,
                   alert_level=level, alarm=alarm, alarm_urgent=alarm_urgent,
                   reasons=reasons, measuring_held=False, extra=extra)


def _result(payload, raw, quality, presence, stats, zscore, *,
            alert_level, alarm, alarm_urgent, reasons, measuring_held, extra=None) -> dict:
    out = {
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
    if extra:
        out.update(extra)
    return out
