"""보행 저하 추세 예측 — 매일의 보행 속도·보폭으로 '앞으로 7·14일 뒤'를 예측하고 저하 신호를 낸다(2026-10-13).

쉽게 말하면:
  레이더가 6초마다 잰 걸음 기록 중 '환자가 곧게 걸은 구간'만 골라 하루 대표값(중앙값)을 만든다.
  그 하루 값들에 칼만 필터(국소 선형 추세 모형)를 적용해 '지금 실제 수준'과 '하루에 얼마씩 변하는지(기울기)'를
  불확실성(±)과 함께 추정하고, 그 기울기를 앞으로 늘려 7·14일 뒤를 예측한다.

근거(docs/CODE_GUIDE_LIVE.html '보행 저하 예측' 장에 원문 수치와 함께 정리):
  * Phillips LJ et al., West J Nurs Res 2017;39(1):78-94 (PMC5272917) — 노인 거주지 집 안 상시 보행 측정(Kinect).
    7일 동안 보행 속도가 누적 5.1 cm/s 줄면 3주 안 낙상 확률 86.3%(변화 없음 19.5%), AUC 0.86.
    7일 동안 보폭이 7.6 cm 줄면 50.6%(변화 없음 11.4%), AUC 0.88. 2.54 cm/s 감소 시 낙상 오즈 4.22배.
    → 경고 기준 = 7일 누적 감소 5.1 cm/s 또는 7.6 cm, 주의 기준 = 2.54 cm/s 또는 2.54 cm.
    → 하루 추정값을 14일 창에서 만든 방식도 이 논문을 따라 '최근 14일 이상 기록'을 요구한다.
  * Kalman RE, J Basic Eng 1960;82(1):35-45 / Harvey AC, Forecasting, Structural Time Series Models and the
    Kalman Filter, Cambridge Univ Press 1989 — 국소 선형 추세(level+slope) 모형과 칼만 필터.
  * Kuys SS et al., JAMDA 2014 (gait_evidence.json KUYS2014) — 요양시설 노인 평소 보행 속도 0.475 m/s.
    → 0.8 m/s 같은 고정 기준은 대부분을 위험으로 만들므로 쓰지 않고 '본인 대비 감소'로 판단한다.
한계: Phillips는 23명·69회 낙상의 예비 연구(Kinect)다. 우리 레이더의 측정 오차·인구에서의 정확도는 아직 검증 전이며
backtest()로 우리 데이터의 예측 오차를 따로 계산해 보고한다. 이 결과는 '낙상 확률'이 아니라 '보행 저하 신호'다.
"""
from __future__ import annotations

import math
import statistics as st
from datetime import date, datetime, timedelta

# 지표별 설정. 내부 계산은 m/s·m, 사람에게 보이는 변화량은 cm/s·cm.
METRICS = {
    "speed": {"col": "speed", "ko": "보행 속도", "unit": "m/s", "show_unit": "cm/s", "valid": (0.1, 2.0),
              # Phillips 2017: 7일 누적 감소 2.54 cm/s → 낙상 오즈 4.22배, 5.1 cm/s → 3주 내 낙상 86.3%
              "caution_cm": 2.54, "warn_cm": 5.1,
              # 칼만 필터 잡음(설계값): 하루 사이 실제 수준이 흔들리는 정도, 기울기가 바뀌는 정도, 하루 대표값 최소 오차
              "q_level": 0.005, "q_slope": 0.001, "r_floor": 0.02, "slope_prior_sd": 0.005},
    "stride": {"col": "stride_length", "ko": "보폭", "unit": "m", "show_unit": "cm", "valid": (0.1, 1.5),
               # Phillips 2017: 7일 누적 감소 2.54 cm → 오즈 6.78배, 7.6 cm → 3주 내 낙상 50.6%
               "caution_cm": 2.54, "warn_cm": 7.6,
               "q_level": 0.005, "q_slope": 0.001, "r_floor": 0.03, "slope_prior_sd": 0.005},
}
WINDOW_SD_FLOOR = 0.10     # 6초 구간 하나의 측정 흔들림 최소값(m/s·m). 하루 구간이 적을 때 오차를 과소평가하지 않게
                           # (합성 시험: 하루 5구간·잡음 0.15에서 오탐 6/40 → 이 하한으로 줄임)
MIN_WINDOWS_PER_DAY = 5     # 하루에 곧게 걸은 6초 구간이 5개 미만이면 그날 값은 쓰지 않는다(Phillips: 하루 4~35회 보행)
MIN_DAYS = 14               # 예측을 내려면 최근 기록일이 14일 이상(Phillips의 14일 보행 추정 창)
LOOKBACK_DAYS = 60
CONFIDENT = 0.8             # 감소가 기준을 넘었을 확률이 80% 이상일 때만 신호(측정 잡음으로 울리지 않게)
Z80 = 1.2816                # 80% 예측 구간의 표준정규 분위수


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _robust_sd(values: list[float]) -> float:
    """중앙값 절대편차(MAD)×1.4826 — 튀는 값 몇 개에 흔들리지 않는 표준편차."""
    if len(values) < 2:
        return 0.0
    med = st.median(values)
    return 1.4826 * st.median(abs(v - med) for v in values)


def _usable(row: dict) -> bool:
    """보행 추세에 쓸 6초 구간인지. 새 열(walking·patient_locked)이 없는 예전 행은 속도·보폭으로 대신 거른다."""
    if not row.get("reliable"):
        return False
    if row.get("walking") == 0 or row.get("patient_locked") == 0:
        return False
    if row.get("walking") is None and not ((row.get("speed") or 0) >= 0.2 and (row.get("stride_length") or 0) > 0):
        return False
    return True


def daily_series(conn, target_id: str, days: int = LOOKBACK_DAYS, now: datetime | None = None) -> list[dict]:
    """mmw_readings → 날짜별 대표값. [{'date','n','speed','speed_sd','stride','stride_sd'}] (기록 있는 날만)."""
    now = now or datetime.now()
    start = (now - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute("""SELECT timestamp, speed, stride_length, reliable, walking, patient_locked
                           FROM mmw_readings WHERE target_id = ? AND timestamp >= ? ORDER BY timestamp""",
                        (target_id, start)).fetchall()
    by_day: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        r = dict(r)
        if not _usable(r):
            continue
        day = by_day.setdefault(str(r["timestamp"])[:10], {"speed": [], "stride": []})
        for key, cfg in METRICS.items():
            v = r.get(cfg["col"])
            lo, hi = cfg["valid"]
            if isinstance(v, (int, float)) and lo <= v <= hi:
                day[key].append(float(v))
    out = []
    for d in sorted(by_day):
        item = {"date": d, "n": len(by_day[d]["speed"])}
        for key in METRICS:
            vals = by_day[d][key]
            ok = len(vals) >= MIN_WINDOWS_PER_DAY
            item[key] = round(st.median(vals), 4) if ok else None
            item[key + "_sd"] = round(_robust_sd(vals), 4) if ok else None
            item[key + "_n"] = len(vals)
        if item["speed"] is not None or item["stride"] is not None:
            out.append(item)
    return out


def kalman_trend(points: list[tuple[int, float, float]], cfg: dict, prior: tuple[float, float] | None = None) -> dict | None:
    """국소 선형 추세 칼만 필터.

    상태 x = [수준 L, 하루 기울기 b].  하루가 지나면  L ← L + b,  b ← b  (+ 작은 무작위 변화 Q).
    관측 y_t = L_t + 측정 오차(분산 R_t = max(그날 표준오차², 최소 오차²)).
    points: [(기준일로부터 날짜 번호, 그날 대표값, 그날 표준오차)] — 기록 없는 날은 예측 단계만 거친다.
    prior: (기대 수준, 표준편차) — 입원 기록 기반 기대 범위가 있으면 출발점으로 쓴다.
    """
    if not points:
        return None
    q_l, q_s, r_floor = cfg["q_level"] ** 2, cfg["q_slope"] ** 2, cfg["r_floor"] ** 2
    first_day, first_val, _ = points[0]
    level0, level_sd0 = prior if prior else (first_val, 0.2)
    x = [level0, 0.0]
    P = [[level_sd0 ** 2, 0.0], [0.0, cfg["slope_prior_sd"] ** 2]]
    day = first_day
    history = []
    for t, y, se in points:
        while day < t:   # 하루씩 앞으로(예측 단계): x = F x, P = F P Fᵀ + Q,  F = [[1,1],[0,1]]
            x = [x[0] + x[1], x[1]]
            P = [[P[0][0] + 2 * P[0][1] + P[1][1] + q_l, P[0][1] + P[1][1]],
                 [P[1][0] + P[1][1], P[1][1] + q_s]]
            day += 1
        # 갱신 단계: 칼만 이득 K = P Hᵀ / (H P Hᵀ + R),  H = [1, 0]
        R = max(se ** 2, r_floor)
        S = P[0][0] + R
        K = [P[0][0] / S, P[1][0] / S]
        innov = y - x[0]
        x = [x[0] + K[0] * innov, x[1] + K[1] * innov]
        P = [[(1 - K[0]) * P[0][0], (1 - K[0]) * P[0][1]],
             [P[1][0] - K[1] * P[0][0], P[1][1] - K[1] * P[0][1]]]
        history.append({"day": t, "obs": y, "level": x[0], "level_sd": math.sqrt(max(P[0][0], 0)),
                        "slope": x[1], "slope_sd": math.sqrt(max(P[1][1], 0))})

    def forecast(h: int) -> dict:
        """h일 뒤 수준: 예측 단계를 h번 반복(x = F x, P = F P Fᵀ + Q). 평균 = L + h·b, 분산 = 반복 후 P00."""
        fx, fP = list(x), [row[:] for row in P]
        for _ in range(h):
            fx = [fx[0] + fx[1], fx[1]]
            fP = [[fP[0][0] + 2 * fP[0][1] + fP[1][1] + q_l, fP[0][1] + fP[1][1]],
                  [fP[1][0] + fP[1][1], fP[1][1] + q_s]]
        sd = math.sqrt(max(fP[0][0], 0))
        return {"mean": fx[0], "sd": sd, "lo80": fx[0] - Z80 * sd, "hi80": fx[0] + Z80 * sd}

    return {"level": x[0], "level_sd": math.sqrt(max(P[0][0], 0)), "slope": x[1],
            "slope_sd": math.sqrt(max(P[1][1], 0)), "last_day": day, "history": history,
            "forecast7": forecast(7), "forecast14": forecast(14)}


def _metric_assessment(series: list[dict], key: str, prior: tuple[float, float] | None, today: date) -> dict:
    cfg = METRICS[key]
    pts = []
    base = None
    for d in series:
        if d.get(key) is None:
            continue
        dd = date.fromisoformat(d["date"])
        base = base or dd
        se = max(d.get(key + "_sd") or 0.0, WINDOW_SD_FLOOR) / math.sqrt(max(d.get(key + "_n") or 1, 1))
        pts.append(((dd - base).days, d[key], se))
    out = {"metric": key, "ko": cfg["ko"], "days_used": len(pts)}
    if len(pts) < MIN_DAYS:
        out.update(status="insufficient", message=f"{cfg['ko']}: 하루 대표값이 {len(pts)}일치(필요 {MIN_DAYS}일)")
        return out
    k = kalman_trend(pts, cfg, prior)
    # 7일 누적 변화(Phillips 2017의 정의) = 오늘 추정 수준 − 7일 전 추정 수준(그날 기록이 없으면 그 이전 가장 가까운 날).
    # 표준편차 = √(오늘 수준 분산 + 7일 전 수준 분산). 처음엔 '7×기울기'를 썼으나 기울기는 늦게 반응해
    # 합성 시험(하루 1 cm/s 감소)에서 경고를 거의 못 냈다(2026-10-13 조정 기록은 문서 참조).
    past = [h for h in k["history"] if h["day"] <= k["history"][-1]["day"] - 7]
    ref = past[-1] if past else k["history"][0]
    change7 = k["level"] - ref["level"]
    change7_sd = math.sqrt(k["level_sd"] ** 2 + ref["level_sd"] ** 2)
    p_caution = _norm_cdf((-cfg["caution_cm"] / 100 - change7) / change7_sd) if change7_sd > 0 else float(change7 <= -cfg["caution_cm"] / 100)
    p_warn = _norm_cdf((-cfg["warn_cm"] / 100 - change7) / change7_sd) if change7_sd > 0 else float(change7 <= -cfg["warn_cm"] / 100)
    level = "warning" if p_warn >= CONFIDENT else "caution" if p_caution >= CONFIDENT else "normal"
    days_since_last = (today - (base + timedelta(days=k["last_day"]))).days
    out.update(
        status="ok", level=level,
        current=round(k["level"], 3), current_sd=round(k["level_sd"], 3),
        slope_per_day_cm=round(k["slope"] * 100, 2), slope_sd_cm=round(k["slope_sd"] * 100, 2),
        change7_cm=round(change7 * 100, 2), change7_sd_cm=round(change7_sd * 100, 2),
        p_decline_caution=round(p_caution, 3), p_decline_warning=round(p_warn, 3),
        caution_cm=cfg["caution_cm"], warn_cm=cfg["warn_cm"],
        forecast7={k2: round(v, 3) for k2, v in k["forecast7"].items()},
        forecast14={k2: round(v, 3) for k2, v in k["forecast14"].items()},
        days_since_last=days_since_last,
        history=[{"date": (base + timedelta(days=h["day"])).isoformat(), "obs": round(h["obs"], 3),
                  "level": round(h["level"], 3)} for h in k["history"]],
    )
    return out


def assess(conn, target_id: str, prior: dict | None = None, now: datetime | None = None) -> dict:
    """대상(target_id)의 보행 저하 예측 결과. 리포트·대시보드·위험도 점수가 이 결과만 읽는다."""
    now = now or datetime.now()
    series = daily_series(conn, target_id, now=now)
    metrics_prior = (prior or {}).get("metrics") or {}
    priors = {}
    if metrics_prior.get("speed", {}).get("mean"):
        priors["speed"] = (metrics_prior["speed"]["mean"], metrics_prior["speed"].get("tau") or 0.2)
    if metrics_prior.get("stride_length", {}).get("mean"):
        priors["stride"] = (metrics_prior["stride_length"]["mean"], metrics_prior["stride_length"].get("tau") or 0.2)
    per = {key: _metric_assessment(series, key, priors.get(key), now.date()) for key in METRICS}
    ok = [m for m in per.values() if m["status"] == "ok"]
    order = ["normal", "caution", "warning"]
    level = max((m["level"] for m in ok), key=order.index) if ok else None
    reasons = []
    for m in ok:
        unit = METRICS[m["metric"]]["show_unit"]
        reasons.append(f"{m['ko']} 7일 변화 {m['change7_cm']:+.1f}±{m['change7_sd_cm']:.1f} {unit}"
                       f" (주의 기준 −{m['caution_cm']} 이상 감소 확률 {m['p_decline_caution']:.0%},"
                       f" 경고 기준 −{m['warn_cm']} 이상 감소 확률 {m['p_decline_warning']:.0%})")
    return {
        "target_id": target_id, "computed_at": now.isoformat(timespec="seconds"),
        "status": "ok" if ok else "insufficient", "level": level,
        "level_ko": {"normal": "정상", "caution": "주의", "warning": "경고"}.get(level),
        "days_with_data": len(series), "min_days": MIN_DAYS, "metrics": per, "reasons": reasons,
        "prior_used": sorted(priors),
        "method": "하루 대표값(곧게 걸은 6초 구간 중앙값) → 국소 선형 추세 칼만 필터 → 7·14일 예측과 7일 누적 감소 확률",
        "evidence": "Phillips 2017 WJNR 39(1):78-94: 7일 보행 속도 5.1 cm/s 감소 → 3주 내 낙상 86.3%(AUC 0.86), "
                    "보폭 7.6 cm 감소 → 50.6%(AUC 0.88). 우리 레이더 데이터에서의 정확도는 검증 전.",
        "caveat": "낙상 확률이 아니라 보행 저하 신호다. 기준값은 Kinect 연구에서 왔고 레이더 측정 오차는 별도 검증이 필요하다.",
    }


def backtest(series: list[dict], key: str = "speed", horizon: int = 7, min_train: int = MIN_DAYS) -> dict:
    """과거 시점 재현 검증: 매일 '그날까지의 기록'으로 horizon일 뒤를 예측하고 실제 하루 값과 비교한다.
    MAE(평균 절대 오차)와 80% 예측 구간 적중률(이상적이면 0.8)을 돌려준다."""
    cfg = METRICS[key]
    pts = []
    base = None
    for d in series:
        if d.get(key) is None:
            continue
        dd = date.fromisoformat(d["date"])
        base = base or dd
        se = max(d.get(key + "_sd") or 0.0, WINDOW_SD_FLOOR) / math.sqrt(max(d.get(key + "_n") or 1, 1))
        pts.append(((dd - base).days, d[key], se))
    by_day = {p[0]: p[1] for p in pts}
    errors, hits = [], 0
    for i in range(min_train, len(pts)):
        train = pts[:i]
        target_day = train[-1][0] + horizon
        if target_day not in by_day:
            continue
        k = kalman_trend(train, cfg)
        f = k["forecast7"] if horizon == 7 else k["forecast14"]
        actual = by_day[target_day]
        errors.append(abs(actual - f["mean"]))
        hits += f["lo80"] <= actual <= f["hi80"]
    if not errors:
        return {"n": 0}
    return {"n": len(errors), "mae": round(st.mean(errors), 4), "coverage80": round(hits / len(errors), 3),
            "unit": cfg["unit"]}


def summary_for_bed(bed_id: str, include_history: bool = False) -> dict:
    """환자(bed_id) → 연결된 mmWave 대상의 예측. 리포트에는 이력 없이 핵심 수치만 넘긴다."""
    import db
    with db.get_conn() as conn:
        row = conn.execute("SELECT mmw_target_id FROM patients WHERE bed_id = ?", (bed_id,)).fetchone()
        target = row[0] if row else None
        if not target:
            return {"status": "no_sensor", "message": "이 환자에게 연결된 mmWave 대상이 없습니다."}
        prior = None
        try:
            import gait_prior
            prior = gait_prior.get_active_prior_for_target(conn, target)
        except Exception:
            prior = None
        result = assess(conn, target, prior=prior)
    if not include_history:
        for m in result["metrics"].values():
            m.pop("history", None)
    return result
