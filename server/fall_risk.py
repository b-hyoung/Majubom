"""
fall_risk.py — 환자별 낙상 위험도 점수와 거동 허용 범위별 침상 알림 단계 (2026-10-12)
==========================================================================
점수 = Morse 낙상 척도(입원·재평가 때 간호사가 정하는 기본 점수)
     + 변동 점수(센서 이상 신호·간호사 낙상 기록으로 오르고, 조용한 날이 이어지면 내려감)

  위험군 : 0~24 저위험 · 25~44 중위험 · 45 이상 고위험 (Morse 척도에서 흔히 쓰는 구분, 기관마다 다를 수 있음)
  거동   : free(자유) · toilet_only(화장실 정도 단독) · assisted(보조 시에만) · bed_rest(절대 안정) — 의료진이 정한다

침상 알림 단계는 docs/질문.md 11장 결정표를 그대로 옮겼다(거동 범위 × 현재 상황, 고위험·야간이면 한 단계 위).
변동 점수 폭(+3, +10, +15, −2/일)과 시간 값은 근거가 없는 설계값이다. 실측·임상 자료가 생기면 다시 정한다.
이 점수는 낙상 확률이 아니다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

# ── Morse Fall Scale (Morse 1989) ─────────────────────────────────────────
MORSE_ITEMS = {
    "fall_history":   {"label": "낙상 이력(이번 입원 중 또는 최근 3개월)", "options": {"no": 0, "yes": 25}},
    "secondary_dx":   {"label": "2개 이상의 진단", "options": {"no": 0, "yes": 15}},
    "ambulatory_aid": {"label": "보행 보조", "options": {"none": 0, "cane_walker": 15, "furniture": 30}},
    "iv":             {"label": "정맥 주사·헤파린 락", "options": {"no": 0, "yes": 20}},
    "gait":           {"label": "보행 상태", "options": {"normal": 0, "weak": 10, "impaired": 20}},
    "mental":         {"label": "정신 상태", "options": {"oriented": 0, "forgets_limitations": 15}},
}
OPTION_KO = {"no": "아니오", "yes": "예", "none": "없음·침상 안정·간호사 부축", "cane_walker": "지팡이·목발·보행기",
             "furniture": "가구를 짚고 이동", "normal": "정상·침상 안정·휠체어", "weak": "약함", "impaired": "장애",
             "oriented": "자기 능력을 앎", "forgets_limitations": "한계를 과대평가·잊음"}
MORSE_ZERO = {k: next(iter(v["options"])) for k, v in MORSE_ITEMS.items()}
GROUP_BOUNDS = ((45, "high"), (25, "moderate"), (0, "low"))
GROUP_KO = {"low": "저위험", "moderate": "중위험", "high": "고위험"}

MOBILITY = ("free", "toilet_only", "assisted", "bed_rest")
MOBILITY_KO = {"free": "자유 거동", "toilet_only": "화장실 정도 단독", "assisted": "보조 시에만 거동", "bed_rest": "절대 안정"}

# ── 변동 점수 규칙(설계값) ──────────────────────────────────────────────────
DYNAMIC_MAX = 40
RULES = {
    "gait_warning":   {"delta": 3,  "cooldown_min": 60, "reason": "보행 이상 신호(평소 대비 경고 단계)"},
    "floor_critical": {"delta": 10, "cooldown_min": 10, "reason": "낙상 감지(mmWave: 급격한 하강 또는 바닥에서 못 일어남)"},
    "nurse_fall":     {"delta": 15, "cooldown_min": 0,  "reason": "간호사 낙상 기록"},
    # gait_trend.py 경고(7일 누적 감소가 Phillips 2017 경고 기준을 넘었을 확률 ≥80%) — 하루 한 번까지(설계값)
    "gait_decline":   {"delta": 5,  "cooldown_min": 1440, "reason": "보행 저하 예측 경고(7일 누적 감소, Phillips 2017 기준)"},
}
DECAY_PER_DAY = 2          # 센서·간호 위험 신호가 없는 하루마다 변동 점수 −2(0 아래로는 내려가지 않음)

# ── 침상 알림 결정표(docs/질문.md 11장) ────────────────────────────────────
LEVELS = ("normal", "caution", "warning", "critical")
LEVEL_KO = {"normal": "정상", "caution": "주의", "warning": "경고", "critical": "위험"}
SITUATIONS = ("sitting", "edge_sit", "exit", "not_returned")
SITUATION_KO = {"sitting": "일어나 앉음", "edge_sit": "걸터앉음", "exit": "침상 이탈", "not_returned": "이탈 후 미복귀"}
ALERT_MATRIX = {
    "bed_rest":    {"sitting": "caution", "edge_sit": "warning", "exit": "critical", "not_returned": "critical"},
    "assisted":    {"sitting": "normal",  "edge_sit": "caution", "exit": "warning",  "not_returned": "critical"},
    "toilet_only": {"sitting": "normal",  "edge_sit": "normal",  "exit": "caution",  "not_returned": "warning"},
    "free":        {"sitting": "normal",  "edge_sit": "normal",  "exit": "normal",   "not_returned": "caution"},
}
NOT_RETURNED_MIN = 10      # 이탈 후 이 시간이 지나도 돌아오지 않으면 미복귀(설계값)
AWAY_MAX_MIN = 120         # 이보다 오래 비어 있으면 '부재'로 보고 미복귀 알림을 멈춘다(빈 병상이 계속 울리지 않게)
NIGHT_HOURS = (22, 6)      # 22시~06시는 한 단계 위


def _now():
    return datetime.now()


def morse_score(items: dict) -> int:
    return sum(MORSE_ITEMS[k]["options"].get(items.get(k), 0) for k in MORSE_ITEMS)


def group_of(score: float) -> str:
    for bound, name in GROUP_BOUNDS:
        if score >= bound:
            return name
    return "low"


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS fall_risk_profile (
        bed_id TEXT PRIMARY KEY, morse_json TEXT NOT NULL, mobility TEXT NOT NULL,
        dynamic_points REAL NOT NULL DEFAULT 0, last_signal_at TEXT, last_decay_at TEXT,
        source TEXT, updated_at TEXT, updated_by TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS fall_risk_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bed_id TEXT NOT NULL, ts TEXT NOT NULL,
        kind TEXT NOT NULL, delta REAL, reason TEXT, score_after REAL, group_after TEXT,
        detail_json TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fall_risk_events_bed ON fall_risk_events(bed_id, ts)")


def prefill_from_records(patient: dict, risk_factors: list[dict]) -> tuple[dict, str]:
    """기록에서 Morse 항목을 미리 채운다(간호사 확인 전 초안). 단어 규칙일 뿐이라 source='auto'로 남긴다."""
    text = " ".join([str(patient.get("diagnosis") or "")] +
                    [f"{r.get('factor') or r.get('label') or ''} {r.get('notes') or ''}" for r in risk_factors])
    items = dict(MORSE_ZERO)
    if "낙상" in text and ("이력" in text or "과거" in text):
        items["fall_history"] = "yes"
    diagnoses = [d for d in str(patient.get("diagnosis") or "").replace("·", ",").split(",") if d.strip()]
    if len(diagnoses) >= 2 or any(w in text for w in ("편마비", "파킨슨", "골절", "수술")):
        items["secondary_dx"] = "yes"
    if any(w in text for w in ("보행기", "보행 보조기", "지팡이", "목발")):
        items["ambulatory_aid"] = "cane_walker"
    if any(w in text for w in ("편마비", "보행 장애", "불안정 보행")):
        items["gait"] = "impaired"
    elif any(w in text for w in ("근력 저하", "보행 느림")):
        items["gait"] = "weak"
    if any(w in text for w in ("치매", "알츠하이머", "섬망")):
        items["mental"] = "forgets_limitations"
    return items, "auto"


def ensure_profile(conn, bed_id: str, items: dict | None = None, mobility: str = "free",
                   source: str = "default", by: str = "system") -> None:
    """프로필이 없을 때만 만든다. 이미 있으면 그대로 두되, 간호사가 손대지 않은 자동 초안(source=auto)은
    새 자동 초안으로 갱신한다(단어 규칙이 고쳐졌을 때)."""
    row = _row(conn, bed_id)
    if row:
        new_items = {**MORSE_ZERO, **(items or {})}
        if row["source"] == "auto" and source == "auto" and json.loads(row["morse_json"]) != new_items:
            conn.execute("UPDATE fall_risk_profile SET morse_json=?, mobility=?, updated_at=? WHERE bed_id=?",
                         (json.dumps(new_items, ensure_ascii=False), mobility, _now().isoformat(timespec="seconds"), bed_id))
            _log(conn, bed_id, "auto_refresh", morse_score(new_items) - morse_score(json.loads(row["morse_json"])),
                 f"자동 초안 갱신: Morse {morse_score(new_items)}점", morse_score(new_items) + row["dynamic_points"],
                 {"morse": new_items, "mobility": mobility})
        return
    items = {**MORSE_ZERO, **(items or {})}
    now = _now().isoformat(timespec="seconds")
    conn.execute("""INSERT INTO fall_risk_profile (bed_id, morse_json, mobility, dynamic_points,
                    last_decay_at, source, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?)""",
                 (bed_id, json.dumps(items, ensure_ascii=False), mobility, 0.0, now, source, now, by))
    score = morse_score(items)
    _log(conn, bed_id, "initial", 0, f"초기 평가({source}): Morse {score}점, {MOBILITY_KO[mobility]}", score,
         {"morse": items, "mobility": mobility})


def _log(conn, bed_id, kind, delta, reason, score_after, detail=None):
    conn.execute("""INSERT INTO fall_risk_events (bed_id, ts, kind, delta, reason, score_after, group_after, detail_json)
                    VALUES (?,?,?,?,?,?,?,?)""",
                 (bed_id, _now().isoformat(timespec="seconds"), kind, delta, reason, score_after,
                  group_of(score_after), json.dumps(detail, ensure_ascii=False) if detail else None))


def _row(conn, bed_id):
    r = conn.execute("SELECT * FROM fall_risk_profile WHERE bed_id=?", (bed_id,)).fetchone()
    return dict(r) if r else None


def _apply_decay(conn, row: dict) -> dict:
    """마지막 위험 신호 이후 지난 '조용한 날'마다 변동 점수를 내린다. 조회할 때 늦게(lazy) 계산한다."""
    now = _now()
    since = max([datetime.fromisoformat(t) for t in (row.get("last_signal_at"), row.get("last_decay_at")) if t] or [now])
    days = int((now - since).total_seconds() // 86400)
    if days <= 0 or row["dynamic_points"] <= 0:
        return row
    new_points = max(0.0, row["dynamic_points"] - DECAY_PER_DAY * days)
    delta = new_points - row["dynamic_points"]
    stamp = (since + timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("UPDATE fall_risk_profile SET dynamic_points=?, last_decay_at=? WHERE bed_id=?",
                 (new_points, stamp, row["bed_id"]))
    row.update(dynamic_points=new_points, last_decay_at=stamp)
    score = morse_score(json.loads(row["morse_json"])) + new_points
    _log(conn, row["bed_id"], "decay", delta, f"위험 신호 없는 {days}일 경과로 감점", score)
    return row


def get_profile(conn, bed_id: str, events: int = 10) -> dict | None:
    row = _row(conn, bed_id)
    if not row:
        return None
    row = _apply_decay(conn, row)
    items = json.loads(row["morse_json"])
    morse = morse_score(items)
    score = morse + row["dynamic_points"]
    group = group_of(score)
    hist = [dict(r) for r in conn.execute(
        "SELECT ts, kind, delta, reason, score_after, group_after FROM fall_risk_events WHERE bed_id=? ORDER BY id DESC LIMIT ?",
        (bed_id, events))]
    return {
        "bed_id": bed_id, "score": round(score, 1), "group": group, "group_ko": GROUP_KO[group],
        "morse_score": morse, "dynamic_points": round(row["dynamic_points"], 1),
        "morse": {k: {"value": items.get(k), "value_ko": OPTION_KO.get(items.get(k)), "points": MORSE_ITEMS[k]["options"].get(items.get(k), 0),
                      "label": MORSE_ITEMS[k]["label"], "options": list(MORSE_ITEMS[k]["options"])} for k in MORSE_ITEMS},
        "mobility": row["mobility"], "mobility_ko": MOBILITY_KO.get(row["mobility"]),
        "source": row["source"], "updated_at": row["updated_at"], "updated_by": row["updated_by"],
        "recent_events": hist,
        "note": "Morse 기본 점수 + 센서·간호 기록 변동 점수. 낙상 확률이 아니며 변동 폭은 설계값이다.",
    }


def set_assessment(conn, bed_id: str, morse: dict | None, mobility: str | None, by: str = "nurse") -> dict:
    row = _row(conn, bed_id)
    if not row:
        ensure_profile(conn, bed_id)
        row = _row(conn, bed_id)
    items = json.loads(row["morse_json"])
    for k, v in (morse or {}).items():
        if k not in MORSE_ITEMS or v not in MORSE_ITEMS[k]["options"]:
            raise ValueError(f"Morse 항목 값이 올바르지 않습니다: {k}={v}")
        items[k] = v
    if mobility is not None and mobility not in MOBILITY:
        raise ValueError(f"거동 범위 값이 올바르지 않습니다: {mobility}")
    new_mob = mobility or row["mobility"]
    now = _now().isoformat(timespec="seconds")
    conn.execute("UPDATE fall_risk_profile SET morse_json=?, mobility=?, source=?, updated_at=?, updated_by=? WHERE bed_id=?",
                 (json.dumps(items, ensure_ascii=False), new_mob, "nurse", now, by, bed_id))
    score = morse_score(items) + row["dynamic_points"]
    _log(conn, bed_id, "assessment", morse_score(items) - morse_score(json.loads(row["morse_json"])),
         f"간호사 재평가: Morse {morse_score(items)}점, {MOBILITY_KO[new_mob]}", score, {"morse": items, "mobility": new_mob})
    return get_profile(conn, bed_id)


def add_signal(conn, bed_id: str, kind: str, detail: dict | None = None, now: datetime | None = None) -> bool:
    """센서·간호 신호로 변동 점수를 올린다. 같은 종류는 cooldown 안에 한 번만. 반영했으면 True."""
    rule = RULES[kind]
    row = _row(conn, bed_id)
    if not row:
        return False
    now = now or _now()
    if rule["cooldown_min"]:
        last = conn.execute("SELECT ts FROM fall_risk_events WHERE bed_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                            (bed_id, kind)).fetchone()
        if last and now - datetime.fromisoformat(last[0]) < timedelta(minutes=rule["cooldown_min"]):
            return False
    row = _apply_decay(conn, row)
    new_points = min(DYNAMIC_MAX, row["dynamic_points"] + rule["delta"])
    stamp = now.isoformat(timespec="seconds")
    conn.execute("UPDATE fall_risk_profile SET dynamic_points=?, last_signal_at=?, last_decay_at=? WHERE bed_id=?",
                 (new_points, stamp, stamp, bed_id))
    score = morse_score(json.loads(row["morse_json"])) + new_points
    _log(conn, bed_id, kind, new_points - row["dynamic_points"], rule["reason"], score, detail)
    return True


def reset_dynamic(conn, bed_id: str, by: str = "nurse") -> dict | None:
    """센서·간호 신호로 쌓인 변동 점수를 0으로 되돌린다(Morse 평가·거동 범위는 그대로). 오탐 정리·시험 후 사용."""
    ensure_profile(conn, bed_id)
    row = _row(conn, bed_id)
    if not row:
        return None
    stamp = _now().isoformat(timespec="seconds")
    conn.execute("UPDATE fall_risk_profile SET dynamic_points=0, last_decay_at=? WHERE bed_id=?", (stamp, bed_id))
    _log(conn, bed_id, "reset", -row["dynamic_points"], f"변동 점수 초기화({by})",
         morse_score(json.loads(row["morse_json"])), {"by": by, "before": row["dynamic_points"]})
    return get_profile(conn, bed_id)


def record_fall(conn, bed_id: str, note: str = "", mobility: str | None = None, worsened: bool = False,
                by: str = "nurse") -> dict:
    """간호사 낙상 기록: Morse 낙상 이력=예, 변동 +15, 필요하면 거동 범위 변경(상태 악화)."""
    ensure_profile(conn, bed_id)
    morse = {"fall_history": "yes"}
    if worsened and mobility is None:
        mobility = "bed_rest"
    set_assessment(conn, bed_id, morse, mobility, by=by)
    add_signal(conn, bed_id, "nurse_fall", {"note": note, "worsened": worsened, "mobility": mobility})
    return get_profile(conn, bed_id)


def on_mmw_result(conn, bed_id: str, result: dict) -> str | None:
    """mmWave 평가 결과에서 점수 신호를 고른다. 잠정 판정과 보류 구간은 쓰지 않는다."""
    level = result.get("alert_level")
    reasons = " ".join(result.get("reasons") or [])
    if level == "critical" and "바닥 근처" in reasons:
        return "floor_critical" if add_signal(conn, bed_id, "floor_critical", {"reasons": result.get("reasons")}) else None
    if level == "warning" and result.get("alarm") and not result.get("provisional") and not result.get("measuring_held"):
        return "gait_warning" if add_signal(conn, bed_id, "gait_warning", {"zscore": result.get("zscore")}) else None
    return None


def situation_from_tof(posture: str | None, in_bed: bool | None, since_iso: str | None, now: datetime | None = None):
    """ToF 자세·재실로 지금 상황을 고른다. 판단할 수 없으면 None."""
    now = now or _now()
    if in_bed is True:
        if posture == "edge_sit":
            return "edge_sit"
        if posture == "sitting":
            return "sitting"
        return None
    if in_bed is False and since_iso:
        try:
            away = (now - datetime.fromisoformat(since_iso)).total_seconds() / 60
        except ValueError:
            return None
        if away < NOT_RETURNED_MIN:
            return "exit"
        if away < AWAY_MAX_MIN:
            return "not_returned"
    return None


def bed_alert(profile: dict | None, situation: str | None, now: datetime | None = None) -> dict:
    """거동 범위 × 상황 → 알림 단계. 고위험군·야간이면 한 단계 위."""
    if not profile or not situation:
        return {"level": "normal", "situation": situation, "reason": None}
    now = now or _now()
    base = ALERT_MATRIX[profile["mobility"]][situation]
    idx = LEVELS.index(base)
    bumps = []
    if profile["group"] == "high":
        idx += 1; bumps.append("고위험군")
    start, end = NIGHT_HOURS
    if now.hour >= start or now.hour < end:
        idx += 1; bumps.append("야간")
    level = LEVELS[min(idx, len(LEVELS) - 1)]
    reason = f"{SITUATION_KO[situation]} · {profile['mobility_ko']} · {profile['group_ko']}({profile['score']}점)"
    if bumps and level != base:
        reason += " · " + "·".join(bumps) + " 가중"
    return {"level": level, "level_ko": LEVEL_KO[level], "situation": situation,
            "situation_ko": SITUATION_KO[situation], "base_level": base, "reason": reason}
