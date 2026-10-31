"""낙상 사건 리포트 — 낙상이 감지되면 바로 '왜 넘어졌을 가능성이 있는지'를 정리한다(2026-10-13 사용자 요청).

주간 종합 리포트(integrated_analysis)와 다른 점: 한 사건에 집중한다.
  ① 사실 모으기(규칙, LLM 없음): 사건 값(하강 시간·세로 속도·바닥에 머문 시간·평소 높이), 낙상 전후 9초 몸 높이
     (mmWave/fall_traces), 직전 30분 사건(침상 이탈 등), 시각(야간 여부), Morse 항목·거동 범위, 투약·위험 요인·진단,
     최근 보행 속도.
  ② 원인 후보(규칙): 사실에서 바로 나오는 것만 — 예) 침상 이탈 5분 안 → 기립 직후, 진정·마약성·혈압약 → 약물,
     야간 → 어두움·각성 저하, 급격한 하강 → 미끄러짐·실신 감별 필요. 근거 문장을 함께 남긴다.
  ③ LLM(키가 있을 때): 위 사실·후보만으로 간호사용 문장 작성. 없는 사실을 만들지 않게 하고, 실패하면 ②로 대신 쓴다.
저장: ai_analysis(analysis_type='fall_incident'), report에 '리포트_종류'='낙상 사건'과 그래프 데이터(charts).
낙상 확률·진단이 아니다 — '가능성 있는 요인'을 간호사가 확인하도록 돕는 정리다.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta

import db
import report_charts

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE_DIR = os.path.join(HERE, "..", "mmWave", "fall_traces")
PROMPT_VERSION = "fall-incident-v1"
NIGHT_HOURS = (21, 6)                  # 21시~06시
BED_EXIT_WINDOW_MIN = 5                # 침상 이탈 뒤 이 안에 넘어지면 '기립·이동 직후'
MED_WORDS = {
    "진정·수면": ("수면", "진정", "zolpidem", "졸피뎀", "lorazepam", "로라제팜", "alprazolam", "benzodiazepine", "quetiapine", "쿠에티아핀"),
    "마약성 진통": ("oxycodone", "옥시코돈", "마약성", "tramadol", "트라마돌", "fentanyl"),
    "혈압·이뇨": ("amlodipine", "암로디핀", "furosemide", "푸로세미드", "이뇨", "혈압", "losartan", "tamsulosin"),
    "어지럼 유발 가능": ("어지럼", "donepezil", "도네페질", "기립성"),
}

SYSTEM_PROMPT = (
    "당신은 요양병원 간호사를 돕는 낙상 사건 리포트 작성자입니다. 아래 JSON의 사실과 '원인_후보'만 근거로 쓰세요. "
    "수치를 새로 만들거나 바꾸지 말고, JSON에 없는 증상·목격·검사 결과를 지어내지 마세요. "
    "레이더 낙상 감지는 '몸 중심 높이가 바닥 높이로 내려가 3초 넘게 머문 것'이며 확정 진단이 아닙니다 — '낙상 감지'라고 쓰세요. "
    "원인은 단정하지 말고 '가능성'으로 쓰고, 각 원인마다 근거가 된 사실을 괄호로 붙이세요. 근거가 없는 원인은 쓰지 마세요. "
    "데이터_출처가 simulation이면 요약 첫 문장에 '시뮬레이션 사건입니다.'라고 쓰세요. "
    "다음 키를 가진 JSON 객체 하나로만 답하세요(모든 값은 한국어 문자열): "
    "'요약'(2~3문장), '낙상_양상'(하강 속도·시간·바닥에 머문 시간을 수치 그대로), '추정_원인'(가능성 높은 순 번호 목록 문장), "
    "'직전_상황'(직전 30분 사건·시각), '즉시_확인할_것'(간호사가 지금 볼 것: 의식·통증·외상·활력징후 등 일반 낙상 후 확인 절차), "
    "'재발_방지_제안'(원인 후보 각각에 맞춘 구체적 간호 조치, 근거 있는 것만), "
    "'데이터_한계'(이번 사건에서 센서·기록으로 알 수 없었던 것을 구체적으로: 예) 레이더는 몸 중심 높이만 봐서 미끄러짐·어지럼 여부는 모름, "
    "직전 30분 기록이 없으면 그 사실). 항목 설명 문구를 그대로 복사하지 말고 이번 사건 내용으로 채우세요. "
    "값이 null인 수치는 쓰지 말고 생략하세요."
)
FIELDS = ("요약", "낙상_양상", "추정_원인", "직전_상황", "즉시_확인할_것", "재발_방지_제안", "데이터_한계")


def _trace_for(event: dict) -> dict | None:
    """사건 시각과 가장 가까운 fall_traces 파일의 환자 추적 높이(전후 9초)."""
    at = event.get("at")
    if not isinstance(at, (int, float)):
        return None
    best = None
    for f in sorted(glob.glob(os.path.join(TRACE_DIR, "*.json")))[-30:]:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        e = d.get("event") or {}
        if isinstance(e.get("at"), (int, float)) and abs(e["at"] - at) < 5 and (best is None or abs(e["at"] - at) < best[0]):
            best = (abs(e["at"] - at), d)
    if not best:
        return None
    d = best[1]
    tid = d["event"].get("tid")
    pts = []
    for t, tracks in (d.get("before") or []) + (d.get("after") or []):
        for tr in tracks:
            if tr[0] == tid:
                pts.append({"x": round(t - at, 2), "y": round(tr[3], 3)})
    return {"title": "낙상 전후 몸 중심 높이(레이더)", "unit": "m", "points": pts,
            "marks": [{"y": round(0.8 * d["event"]["ref"], 2), "label": "서 있음(평소 80%)"},
                      {"y": round(0.25 * d["event"]["ref"], 2), "label": "바닥 높이(평소 25%)"}],
            "x_label": "낙상 판정 시각 기준(초)"}


def _recent_events(bed_id: str, when: datetime, minutes: int = 30) -> list[dict]:
    since = (when - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    until = (when + timedelta(seconds=1)).isoformat(timespec="seconds")
    rows = db.get_activity_log(bed_id, limit=40, since=since, until=until)
    out = []
    for r in rows:
        if r["event_type"] in report_charts.EXCLUDE:
            continue
        out.append({"시각": r["ts"][11:19], "ts": r["ts"][:19], "종류": r["event_type"], "수준": r["level"], "제목": r["title"], "메모": r["note"]})
    return list(reversed(out))[-15:]


def _med_flags(meds: list[dict]) -> list[str]:
    flags = []
    for m in meds:
        text = f"{m.get('name', '')} {m.get('notes') or ''}".lower()
        for group, words in MED_WORDS.items():
            if any(w.lower() in text for w in words):
                flags.append(f"{group}: {m.get('name')}")
    return flags


def candidate_causes(facts: dict) -> list[dict]:
    """사실에서 바로 나오는 원인 후보와 근거. 근거 없는 후보는 만들지 않는다."""
    out = []
    ev = facts["사건"]
    if ev.get("양상") == "급격한 하강":
        out.append({"원인": "미끄러짐·발 걸림 또는 갑작스러운 힘 빠짐(실신·기립성 저혈압 감별 필요)",
                    "근거": f"1초 이내 하강, 세로 속도 {ev.get('세로_속도_최저_m_s')} m/s"})
    elif ev.get("양상") == "천천히 주저앉음":
        out.append({"원인": "다리 힘 빠짐·어지럼으로 서서히 주저앉음",
                    "근거": f"서 있음→바닥까지 천천히 내려감, 바닥에 {ev.get('바닥에_머문_초')}초"})
    when = datetime.fromisoformat(facts["발생_시각"])
    exits = []
    for e in facts["직전_30분_사건"]:
        if e["종류"] in ("bed_alert", "bed_exit", "sim_exit") or "이탈" in (e["제목"] or ""):
            try:
                mins = (when - datetime.fromisoformat(e["ts"])).total_seconds() / 60
            except ValueError:
                continue
            if 0 <= mins <= BED_EXIT_WINDOW_MIN:
                exits.append((mins, e))
    if exits:
        mins, e = exits[-1]
        out.append({"원인": "침상 이탈·기립 직후 균형 상실",
                    "근거": f"낙상 {mins:.1f}분 전 '{e['제목']}'({e['시각']})"})
    if facts["야간"]:
        out.append({"원인": "야간(어두움·졸림·야간뇨로 서두름)", "근거": f"발생 시각 {facts['발생_시각'][11:16]}"})
    for f in facts["투약_주의"]:
        out.append({"원인": f"약물 영향 가능({f.split(':')[0]})", "근거": f})
    morse = facts.get("Morse_항목") or {}
    if morse.get("gait") in ("weak", "impaired"):
        out.append({"원인": "평소 보행 약화", "근거": f"Morse 보행 항목 '{morse.get('gait')}'"})
    if morse.get("mental") == "forgets_limitations":
        out.append({"원인": "인지 저하로 자기 한계를 잊고 혼자 이동", "근거": "Morse 정신 상태 '자기 한계를 잊음'"})
    g = facts.get("최근_보행_속도") or []
    if len(g) >= 3 and g[-1]["y"] < 0.85 * g[0]["y"]:
        out.append({"원인": "최근 보행 속도 감소", "근거": f"{g[0]['x']} {g[0]['y']} m/s → {g[-1]['x']} {g[-1]['y']} m/s"})
    seen = " ".join(c["근거"] for c in out)
    for rf in facts["위험_요인"]:
        if len(out) >= 7:
            break
        if any(w and w in seen for w in rf.split()[:1]):   # 이미 투약 등으로 나온 요인은 다시 쓰지 않는다
            continue
        out.append({"원인": f"기존 위험 요인: {rf}", "근거": "환자 위험 요인 기록"})
    return out


def collect_facts(bed_id: str, event: dict, source: str = "live") -> dict:
    when = datetime.fromtimestamp(event["at"]) if isinstance(event.get("at"), (int, float)) else datetime.now()
    patient = db.get_patient(bed_id) or {}
    meds = db.list_medications(bed_id)
    rfs = [r.get("label") or r.get("factor") for r in db.list_risk_factors(bed_id)]
    try:
        import fall_risk
        with db.get_conn() as conn:
            prof = fall_risk.get_profile(conn, bed_id) or {}
        morse = {k: v.get("value") for k, v in (prof.get("morse") or {}).items()}
        mobility = prof.get("mobility_ko")
    except Exception:
        morse, mobility = {}, None
    kind = event.get("kind")
    gait = report_charts.gait_line(bed_id)["points"]
    return {
        "데이터_출처": "simulation" if source == "sim" else "live",
        "환자": {"이름": patient.get("name"), "나이": patient.get("age"), "진단": patient.get("diagnosis"), "병실": patient.get("room")},
        "발생_시각": when.isoformat(timespec="seconds"),
        "야간": when.hour >= NIGHT_HOURS[0] or when.hour < NIGHT_HOURS[1],
        "사건": {"양상": "급격한 하강" if kind == "fast" else "천천히 주저앉음" if kind == "slow" else (event.get("label") or "낙상 감지"),
               "하강_시간_초": event.get("drop_sec"), "세로_속도_최저_m_s": event.get("vz_min"),
               "바닥에_머문_초": event.get("low_sec"), "몸_중심_높이_m": event.get("z"), "평소_높이_m": event.get("ref"),
               "위치_m": [round(event["x"], 2), round(event["y"], 2)] if isinstance(event.get("x"), (int, float)) else None,
               "설명": event.get("note")},
        "직전_30분_사건": _recent_events(bed_id, when),
        "Morse_항목": morse, "거동_범위": mobility,
        "투약": [f"{m.get('name')} ({m.get('frequency') or ''}) {m.get('notes') or ''}".strip() for m in meds],
        "투약_주의": _med_flags(meds),
        "위험_요인": [r for r in rfs if r],
        "최근_보행_속도": gait,
    }


def _fallback_report(facts: dict, causes: list[dict]) -> dict:
    ev = facts["사건"]
    sim = "시뮬레이션 사건입니다. " if facts["데이터_출처"] == "simulation" else ""
    nums = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in ev.items() if v not in (None, "") and k not in ("양상", "설명", "위치_m"))
    return {
        "요약": f"{sim}{facts['발생_시각'][11:16]}에 낙상이 감지되었습니다({ev['양상']}). 아래 요인은 기록에서 확인된 가능성이며 확정이 아닙니다.",
        "낙상_양상": f"{ev['양상']} · {nums}" if nums else ev["양상"],
        "추정_원인": "\n".join(f"{i + 1}. {c['원인']} ({c['근거']})" for i, c in enumerate(causes)) or "기록에서 확인되는 요인이 없습니다.",
        "직전_상황": "\n".join(f"{e['시각']} {e['제목']}" for e in facts["직전_30분_사건"]) or "직전 30분 동안 저장된 사건이 없습니다(사건이 없었다는 증거는 아님).",
        "즉시_확인할_것": "의식·반응, 머리 부딪힘 여부, 통증·변형(고관절·손목), 출혈, 활력징후, 혼자 일어나려 하지 않도록 도움.",
        "재발_방지_제안": "추정 원인을 확인한 뒤 담당 의료진과 상의하세요.",
        "데이터_한계": "레이더는 몸 중심 높이만 봅니다. 미끄러짐·어지럼 같은 직접 원인은 센서로 알 수 없습니다. (AI 문장 생성 실패로 규칙 요약을 표시)",
    }


def generate(bed_id: str, event: dict, source: str = "live") -> dict:
    facts = collect_facts(bed_id, event, source)
    facts["사건"] = {k: v for k, v in facts["사건"].items() if v not in (None, "")}   # null 수치는 LLM에 주지 않는다
    causes = candidate_causes(facts)
    payload = {**facts, "원인_후보": causes}
    report, model = None, "rule-fallback"
    try:
        import ai_report
        res = ai_report.call_openai(system_prompt=SYSTEM_PROMPT, json_mode=True,
                                    user_content="낙상 사건 사실:\n" + json.dumps(payload, ensure_ascii=False, indent=1, default=str))
        if res.get("success"):
            out = json.loads(res["text"])
            if isinstance(out, dict):
                report = {k: (v if isinstance(v, str) else "\n".join(map(str, v)) if isinstance(v, list) else json.dumps(v, ensure_ascii=False))
                          for k, v in out.items() if k in FIELDS}
                for k in FIELDS:
                    report.setdefault(k, "정보 부족")
                model = res.get("model") or "llm"
    except Exception as e:  # LLM 실패해도 사건 리포트는 남긴다
        print(f"[fall_report] LLM 실패: {type(e).__name__}")
    if report is None:
        report = _fallback_report(facts, causes)
    charts = report_charts.build(bed_id)
    trace = _trace_for(event) if source == "live" else None
    if trace and trace["points"]:
        charts["fall_trace"] = trace
    report.update({"리포트_종류": "낙상 사건", "원인_후보": causes, "charts": charts,
                   "사건_수치": facts["사건"], "발생_시각": facts["발생_시각"]})
    aid = db.insert_ai_analysis(bed_id, "fall_incident", None, report["요약"][:300], None,
                                [c["원인"] for c in causes], None, model, PROMPT_VERSION, None, report,
                                input_snapshot=payload)
    db.insert_sensor_event(bed_id, "ai_analysis_completed", ts=datetime.now().isoformat(timespec="milliseconds"),
                           level="normal", title="낙상 사건 리포트 작성",
                           note=report["요약"][:200])
    return {"success": True, "analysis_id": aid, "model": model}
