"""AI 리포트에 붙이는 그래프 데이터(2026-10-13 사용자 요청: 원그래프·선그래프로 보기 편하게).

그리는 것은 대시보드(dashboard.js의 SVG 함수)가 하고, 여기서는 DB에서 숫자만 모은다. 계산값만 넣고 꾸며내지 않는다.
  · activity_pie  — 최근 24시간 사건 종류 비율(낙상·침상 알림·주의 등). 데이터 품질 경고(data_integrity_flag)는 뺀다.
  · gait_line     — 최근 7일 하루 보행 속도 중앙값(환자 확정·걷는 6초 구간). 시뮬레이션 환자는 기록이 없으면 빈 목록.
  · risk_line     — 낙상 위험도 점수 변동 기록(Morse + 변동 점수) 최근 30건.
"""
from __future__ import annotations

import statistics as st
from datetime import datetime, timedelta

import db

# 사건 종류 → 원그래프 묶음(사람이 읽는 이름). 시뮬레이션 사건(sim_*)도 같은 이름으로 묶어 환자마다 비율이 달라 보이게 한다.
PIE_LABELS = {
    "fall_detected": "낙상", "fall_suspected": "낙상", "nurse_fall_report": "낙상", "sim_fall": "낙상",
    "fall_recovered": "낙상 후 일어남", "fall_dip": "쪼그림·주의",
    "bed_alert": "침상 알림", "bed_exit": "침상 이탈", "sim_exit": "침상 이탈",
    "sim_edge": "침상 가장자리", "sim_risk": "위험 자세", "sim_checkingNormal": "이탈 후 확인(정상)",
    "sim_checkingAbnormal": "이탈 후 확인(이상)", "sim_toss": "뒤척임", "sim_sleep": "수면", "sim_walk": "보행",
    "gait_alert": "보행 변화", "gait_decline": "보행 변화", "mmw_alert": "보행 변화",
}
EXCLUDE = ("data_integrity_flag", "ai_analysis_completed", "tof_baseline_auto")


def _group(event_type: str) -> str:
    return PIE_LABELS.get(event_type, "기타")


def activity_pie(bed_id: str, hours: int = 24) -> dict:
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with db.get_conn() as conn:
        rows = conn.execute("SELECT event_type, COUNT(*) n FROM activity_log WHERE bed_id=? AND ts>=? GROUP BY event_type",
                            (bed_id, since)).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        if r["event_type"] in EXCLUDE:
            continue
        g = _group(r["event_type"] or "")
        counts[g] = counts.get(g, 0) + r["n"]
    items = sorted(({"label": k, "value": v} for k, v in counts.items()), key=lambda a: -a["value"])
    return {"title": f"최근 {hours}시간 사건 종류", "unit": "건", "items": items}


def gait_line(bed_id: str, days: int = 7) -> dict:
    with db.get_conn() as conn:
        p = conn.execute("SELECT mmw_target_id FROM patients WHERE bed_id=?", (bed_id,)).fetchone()
        target = p["mmw_target_id"] if p and p["mmw_target_id"] else None
        rows = []
        if target:
            since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
            rows = conn.execute("""SELECT substr(timestamp,1,10) d, speed FROM mmw_readings
                                   WHERE target_id=? AND timestamp>=? AND walking=1 AND patient_locked=1 AND speed>0""",
                                (target, since)).fetchall()
    by_day: dict[str, list[float]] = {}
    for r in rows:
        by_day.setdefault(r["d"], []).append(r["speed"])
    points = [{"x": d[5:], "y": round(st.median(v), 3), "n": len(v)} for d, v in sorted(by_day.items())]
    return {"title": f"최근 {days}일 하루 보행 속도(중앙값)", "unit": "m/s", "points": points}


def risk_line(bed_id: str, limit: int = 30) -> dict:
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT ts, score_after FROM fall_risk_events WHERE bed_id=? ORDER BY ts DESC LIMIT ?",
                                (bed_id, limit)).fetchall()
    except Exception:
        rows = []
    points = [{"x": r["ts"][5:16].replace("T", " "), "y": round(r["score_after"], 1)} for r in reversed(rows)]
    return {"title": "낙상 위험도 점수 변동", "unit": "점", "points": points}


def build(bed_id: str) -> dict:
    return {"activity_pie": activity_pie(bed_id), "gait_line": gait_line(bed_id), "risk_line": risk_line(bed_id)}
