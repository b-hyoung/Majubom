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
# z 합계 판정에 쓰는 지표(2026-11-05 C안). 보폭 두 지표는 실측(걸음 수를 센 6초 창 69개)에서 46%가 계산 실패,
# 정답 ±10% 안은 7%라 판정에서 뺀다. 기준선에는 METRICS 전부를 계속 쌓는다(나중에 다시 넣을 수 있게).
JUDGED_METRICS = ("speed", "speed_cv", "sway", "freeze_ratio")

# σ 하한 (측정 초기 분산이 0에 가까워 z가 폭발하는 것을 방지)
SIGMA_FLOOR = {
    "speed": 0.05, "speed_cv": 0.03, "sway": 0.02,
    "freeze_ratio": 0.03, "stride_length": 0.03, "stride_cv": 0.02,
}

# alert_level 임계 (total_abs 기준) — 2026-11-05부터 보행 단계는 아래 '보행 점수'로 정한다.
# total_abs(양방향 |z| 합)와 이 문턱은 기록(DB total_abs 열)과 코드 안내 문서(tools/build_code_guide.py)용으로만 남긴다.
TH_CAUTION, TH_WARNING, TH_CRITICAL = 3.0, 6.0, 9.0

# ── 보행 점수: A+S 큰 쪽 (2026-11-05) ──────────────────────────────────
# 비교 근거: Downloads/majubom-reports/gait_algo_compare_20261105.html. 양방향 |z| 합은 서서히 생기는 저하를 찍기 수준
# (AUC 0.46~0.48)으로만 가려냈고, 좋아진 걸음에 경보 쪽으로 반응했다.
#  A = 위험한 방향으로 벗어난 z만 더한 값,  S = 속도가 느려진 정도(−z_speed)
#  둘을 본인 평소 점수 분포로 표준화한 뒤 큰 쪽을 점수로 쓴다(한쪽이 나빠지면 다른 쪽에 상쇄되지 않고, 경보 이유를 말할 수 있다).
GAIT_DIR = {"speed": -1, "speed_cv": 1, "sway": 1, "freeze_ratio": 1}   # 속도는 낮아지면, 나머지는 높아지면 위험
TH_GAIT_CAUTION, TH_GAIT_WARNING, TH_GAIT_CRITICAL = 2.0, 3.0, 4.0     # 본인 점수 표준편차 단위(사용자 결정)
SCORE_MIN = 20              # 본인 A·S 분포를 믿기 시작하는 기록 수
SCORE_SD_FLOOR = 0.3        # 본인 점수 표준편차 하한(기록이 거의 같을 때 점수가 폭발하지 않게)
A_DEFAULT = (1.6, 1.17)     # 그 전에는 이론값: 지표 4개가 독립 표준정규일 때 A의 평균 4·φ(0), 표준편차 √(4·0.341)
S_DEFAULT = (0.0, 1.0)      # S는 z 자체라 평균 0, 표준편차 1
GAIT_DRIVER_KO = {"speed": "속도가 평소보다 느림", "speed_cv": "속도 변동 증가", "sway": "흔들림 증가",
                  "freeze_ratio": "멈칫거림 증가"}

# 절대 임계 (baseline 무관 즉시 위험) — 2026-10-12 변경
#  low_z: 추적 중인 사람의 최근 1초 몸 높이(z, 바닥 기준 m) 중앙값(send_mmw가 계산).
#         FLOOR_FALL_Z보다 낮으면 바닥 근처에 있다고 보고 즉시 critical. 침대 매트리스 윗면은 37cm(현장 실측).
#  이전 규칙(6초 창의 max(z)-min(z) > 1.2m)은 시간 순서를 보지 않고, 몸통 높이 기준으로는 실제 낙상에서도
#  넘기 어려워 폐기했다. height_drop 값은 기록용으로 계속 받는다.
#  주의: 침대 위에 누운 사람의 몸 중심이 0.4m 아래로 잡히면 오탐이 된다(실측 확인 필요, docs/나중에_할일.md 3장).
FLOOR_FALL_Z = 0.4
# 2026-10-12 실측: 레이더는 바닥을 못 보고 거리·설치각 오차로 절대 높이가 0.3~0.5m씩 틀어져 고정 0.4m 규칙을 끈다.
# 낙상은 mmWave/fall_detect.py(본인 평소 높이 대비 1초 안에 25% 이하 = 급격한 하강, 25% 이하 3초 넘게 = 못 일어남)가 프레임 단위로 판정해 /mmw/fall로 보낸다.
FLOOR_FALL_ENABLED = False

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
    for m in JUDGED_METRICS:
        zi = (float(raw.get(m, 0.0)) - stats[f"{m}_mu"]) / stats[f"{m}_sigma"]
        z[m] = round(zi, 2)
        total += abs(zi)
    z["total_abs"] = round(total, 2)
    return z


def classify_alert(total_abs: float) -> str:
    """예전 판정(total_abs 기준). 2026-11-05부터 보행 단계에는 쓰지 않는다(classify_gait)."""
    if total_abs >= TH_CRITICAL:
        return "critical"
    if total_abs >= TH_WARNING:
        return "warning"
    if total_abs >= TH_CAUTION:
        return "caution"
    return "normal"


def _score_mean_sd(bed: dict | None, key: str, default: tuple[float, float]) -> tuple[float, float]:
    st = ((bed or {}).get("gait_score") or {}).get(key) or {}
    n = st.get("n", 0)
    if n < SCORE_MIN:
        return default
    mean = st["sum"] / n
    var = st["sumsq"] / n - mean * mean
    return mean, max(math.sqrt(var) if var > 0 else 0.0, SCORE_SD_FLOOR)


def gait_score(z: dict, bed: dict | None) -> dict:
    """지표별 z(JUDGED_METRICS) → A, S, 본인 분포로 표준화한 n_a·n_s, 큰 쪽 점수(combined), 경보 이유(driver)."""
    parts = {m: max(0.0, GAIT_DIR[m] * z[m]) for m in JUDGED_METRICS if z.get(m) is not None}
    a = sum(parts.values())
    s = -z["speed"] if z.get("speed") is not None else None
    a_m, a_s = _score_mean_sd(bed, "A", A_DEFAULT)
    n_a = (a - a_m) / a_s
    n_s = None
    if s is not None:
        s_m, s_s = _score_mean_sd(bed, "S", S_DEFAULT)
        n_s = (s - s_m) / s_s
    combined = n_a if n_s is None else max(n_a, n_s)
    if n_s is not None and n_s >= n_a:
        driver = "speed"
    else:
        driver = max(parts, key=parts.get) if parts else "speed"
    return {"a": a, "s": s, "n_a": n_a, "n_s": n_s, "combined": combined, "driver": driver}


def update_gait_score(bed: dict, sc: dict) -> None:
    """비교가 끝난 구간의 A·S를 본인 점수 분포에 쌓는다(자기 자신과 비교하지 않게, 비교 뒤에 부른다)."""
    acc = bed.setdefault("gait_score", {})
    for key, v in (("A", sc["a"]), ("S", sc["s"])):
        if v is None:
            continue
        st = acc.setdefault(key, {"sum": 0.0, "sumsq": 0.0, "n": 0})
        st["sum"] += v
        st["sumsq"] += v * v
        st["n"] += 1


def classify_gait(combined: float) -> str:
    if combined >= TH_GAIT_CRITICAL:
        return "critical"
    if combined >= TH_GAIT_WARNING:
        return "warning"
    if combined >= TH_GAIT_CAUTION:
        return "caution"
    return "normal"


def _gait_fields(sc: dict) -> dict:
    return {"gait_score": round(sc["combined"], 2), "gait_a": round(sc["n_a"], 2),
            "gait_s": None if sc["n_s"] is None else round(sc["n_s"], 2), "gait_driver": sc["driver"]}


def _gait_reason(zscore: dict, level: str, alarm: bool) -> str:
    return (f"보행 점수 {zscore['gait_score']}({GAIT_DRIVER_KO.get(zscore['gait_driver'], zscore['gait_driver'])}) "
            f"→ {LEVEL_KO[level]}{' 알람' if alarm else ''} · 참고 |z| 합 {zscore['total_abs']}")


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
      - low_z < FLOOR_FALL_Z     → baseline 무관 즉시 critical (바닥 근처 = 낙상 의심)
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
    low_z = raw.get("low_z")

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
    abs_critical = (FLOOR_FALL_ENABLED and isinstance(low_z, (int, float)) and not isinstance(low_z, bool)
                    and math.isfinite(low_z) and low_z < FLOOR_FALL_Z)
    # ToF가 지금 침상 위에 사람이 있다고 보면 바닥 판정을 하지 않는다(2026-10-12 실측: 침대 걸터앉음 z≈0.32m가
    # 0.4m 미만으로 잡혀 낙상 오탐). 레이더 track 높이는 반사점 평균이라 앉으면 허벅지·무릎 높이로 낮아진다.
    if abs_critical and presence.get("tof_in_bed") is True:
        abs_critical = False
        reasons.append(f"몸 높이 z={low_z}m < {FLOOR_FALL_Z}m지만 ToF 침상 재실 중 → 바닥 낙상 판정 제외")

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
            reasons.append(f"단, 바닥 근처 몸 높이(z={low_z}m < {FLOOR_FALL_Z}m) → 즉시 위험")
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
            reasons.append(f"바닥 근처 몸 높이(z={low_z}m < {FLOOR_FALL_Z}m) → 즉시 위험")
        return _result(payload, raw, quality, presence, stats, None,
                       alert_level=level, alarm=alarm, alarm_urgent=alarm_urgent,
                       reasons=reasons, measuring_held=not abs_critical)

    # 5) 정상 경로: 환자 특정 + 보행 → baseline 갱신 후 평가
    if n_targets >= 2:
        reasons.append(f"환자 특정: {n_targets}명 중 소속도 {bed_affinity} track 채택")

    extra: dict = {}
    if prior and (prior.get("metrics") or {}).get("speed"):
        return _evaluate_with_prior(payload, raw, quality, presence, store, target_id, prior,
                                    abs_critical, low_z, reasons, persist)
    if prior and prior.get("not_applicable") and "보행 불가" in str(prior.get("not_applicable")):
        note = "보행 불가(와상 등) 기록 환자인데 보행이 감지됨 — 환자 본인이 아닐 가능성 또는 기록 갱신 필요"
        reasons.append(note)
        extra = {"identity_note": note, "prior_id": prior.get("id")}

    # 현재 구간은 비교가 끝난 뒤 기준선에 넣는다(자기 자신과 비교하지 않음). 사전분포 경로와 같은 순서.
    stats_before = baseline_stats(store.get(target_id))

    zscore = None
    level = "normal"
    warming = (stats_before is None) or (stats_before["n"] < MIN_SAMPLES) or (stats_before["age_days"] < WARMUP_DAYS)

    score = None
    if stats_before and stats_before["n"] >= MIN_SAMPLES:
        zscore = compute_zscore(raw, stats_before)
        score = gait_score(zscore, store.get(target_id))
        zscore.update(_gait_fields(score))
        level = classify_gait(score["combined"])

    if warming:
        if stats_before is None or stats_before["n"] < MIN_SAMPLES:
            n_before = stats_before["n"] if stats_before else 0
            reasons.append(f"베이스라인 학습 중: 누적 {n_before}/{MIN_SAMPLES}건 — 정식 알람 보류")
        else:
            reasons.append(f"베이스라인 학습 중: 누적 {stats_before['age_days']:.1f}/{WARMUP_DAYS}일 — 정식 알람 보류")
        extra["warming"] = True   # 학습 기간 판정은 통합 위험 점수에 넣지 않는다(integrated_analysis)
    else:
        if level in ("warning", "critical"):
            alarm = True
            alarm_urgent = (level == "critical")
        reasons.append(_gait_reason(zscore, level, alarm))

    # 절대 임계(낙상)는 학습 여부와 무관하게 즉시 위험
    if abs_critical:
        level = "critical"
        alarm, alarm_urgent = True, True
        extra["fall_rule"] = True   # 학습 기간이어도 통합 위험 점수에 넣는 critical(integrated_analysis)
        reasons.append(f"바닥 근처 몸 높이(z={low_z}m < {FLOOR_FALL_Z}m) → 즉시 위험(베이스라인 무관)")

    if score is not None:
        update_gait_score(store[target_id], score)
    update_baseline(store, target_id, raw)
    update_done = True
    stats = baseline_stats(store.get(target_id))
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
                         abs_critical, low_z, reasons, persist) -> dict:
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
        if x is None or m not in JUDGED_METRICS:
            continue
        if n_m >= MIN_SAMPLES and post_mean is not None:
            z[m] = (x - post_mean) / max(sd_m or 0.0, SIGMA_FLOOR[m])

    n_before = int((bed or {}).get("n", 0))
    identity_note = None
    score = None
    if len(z) >= MIN_METRICS_FOR_S:
        provisional = False
        total = sum(abs(v) for v in z.values())
        score = gait_score(z, bed)
        level = classify_gait(score["combined"])
        zscore = {m: round(v, 2) for m, v in z.items()}
        zscore.update(total_abs=round(total, 2), metrics_used=len(z), **_gait_fields(score))
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
    else:
        if level in ("warning", "critical"):
            alarm = True
            alarm_urgent = (level == "critical")
        reasons.append(_gait_reason(zscore, level, alarm))

    if abs_critical:
        level = "critical"
        alarm = alarm_urgent = True
        reasons.append(f"바닥 근처 몸 높이(z={low_z}m < {FLOOR_FALL_Z}m) → 즉시 위험(베이스라인 무관)")

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
    if score is not None:
        update_gait_score(bed, score)
    if persist:
        save_baseline(store)
    stats = baseline_stats(bed)

    extra = {"provisional": provisional, "baseline_source": source, "expected": expected,
             "prior_id": prior.get("id")}
    if not provisional and (n_before < MIN_SAMPLES or age_days < WARMUP_DAYS):
        extra["warming"] = True   # 학습 기간 판정은 통합 위험 점수에 넣지 않는다(integrated_analysis)
    if abs_critical:
        extra["fall_rule"] = True   # 학습 기간이어도 통합 위험 점수에 넣는 critical(integrated_analysis)
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
