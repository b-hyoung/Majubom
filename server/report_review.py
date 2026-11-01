"""Traceable report checks. A successful check is not clinical approval.

No model calls, no current patient records substituted for missing historical inputs.
Saved reviews are bound to a hash of the report AND its input/score/evidence metadata.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone

import db

VERSION = "report_review_v2"
REQUIRED_FIELDS = (
    "현재_상태", "최근_변화", "ToF_변화", "mmWave_변화", "보행_변화", "활동_변화",
    "침상_관련_행동", "낙상_위험도_설명", "주요_위험_요인", "과거_데이터와의_비교",
    "향후_위험_가능성", "관련_연구_근거", "투약_조정_제안", "데이터_신뢰도_설명",
    "추가_관찰이_필요한_항목",
)
SCORES = {"normal": 0.1, "caution": 0.4, "warning": 0.7, "critical": 0.95}
LABELS = {"needs_review": "수정·확인 필요", "limited": "자료 부족으로 확인 제한",
          "checks_passed": "자동 점검 범위 내 일치", "demo_example": "미리 작성한 시연 예시"}
SCOPE = "필수 항목·점수 계산·출처 표시·일부 과잉 해석 표현을 점검합니다. 모든 문장의 사실성이나 의학적 타당성을 보증하지 않습니다."


def decode(value, default=None):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def content_hash(row):
    keys = ("patient_id", "report_json", "input_snapshot_json", "risk_json", "risk_score",
            "confidence", "evidence_ids_json", "model_version", "prompt_version")
    text = json.dumps({k: row.get(k) for k in keys}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def saved_review(row):
    with db.get_conn() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_report_reviews'").fetchone():
            return None
        record = conn.execute("SELECT * FROM ai_report_reviews WHERE analysis_id=?", (row["id"],)).fetchone()
    if not record or record["content_hash"] != content_hash(row):
        return None
    review = decode(record["review_json"])
    return review if isinstance(review, dict) and review.get("version") == VERSION else None


def save_review(row, review):
    if review.get("content_hash") != content_hash(row):
        raise ValueError("Review does not match the report content")
    with db.get_conn() as conn:
        current = conn.execute("SELECT * FROM ai_analysis WHERE id=?", (row["id"],)).fetchone()
        if not current or content_hash(dict(current)) != review["content_hash"]:
            raise ValueError("Report changed during review")
        conn.execute("""INSERT INTO ai_report_reviews VALUES (?,?,?,?)
            ON CONFLICT(analysis_id) DO UPDATE SET content_hash=excluded.content_hash,
            review_json=excluded.review_json, reviewed_at=excluded.reviewed_at""",
            (row["id"], review["content_hash"], json.dumps(review, ensure_ascii=False), review["reviewed_at"]))


def summary(review):
    return {k: review.get(k) for k in ("status", "label", "reviewed_at", "scope")} | {
        "finding_count": len(review.get("findings", [])),
        "direct_reviewed": bool(review.get("direct_review")),
    }


def _same(a, b):
    if a is None or b is None:
        return a is b
    return (isinstance(a, (float, int)) and not isinstance(a, bool)
            and isinstance(b, (float, int)) and not isinstance(b, bool)
            and math.isfinite(a) and math.isfinite(b) and abs(a - b) < 1e-8)


def _resolve(ids):
    if not ids:
        return {"success": True, "found": [], "missing_chunk_ids": []}
    try:
        import paper_kb
        return paper_kb.resolve_chunks(ids)
    except Exception:
        return {"success": False, "found": [], "missing_chunk_ids": [], "message": "저장된 문헌을 조회하지 못했습니다."}


def build_review(row, resolved=None):
    report = decode(row.get("report_json"), {})
    payload = decode(row.get("input_snapshot_json"))
    payload = payload if isinstance(payload, dict) and payload else None
    risk = decode(row.get("risk_json"), {}) or {}
    ids = decode(row.get("evidence_ids_json"), [])
    ids = ids if isinstance(ids, list) else []
    resolved = _resolve(ids) if resolved is None else resolved
    papers = [{k: v for k, v in p.items() if k != "vector"} for p in resolved.get("found", [])]
    demo = row.get("model_version") == "demo-history-seed"
    simulated = str(row.get("patient_id", "")).startswith("bed_sim_")
    checks, findings = [], []

    def check(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    def finding(code, field, quote, explanation, correction, severity="warning"):
        if not any(f["code"] == code and f["field"] == field for f in findings):
            findings.append(dict(code=code, field=field, quote=quote, explanation=explanation,
                                 correction=correction, severity=severity))

    missing = [k for k in REQUIRED_FIELDS if not isinstance(report, dict) or not isinstance(report.get(k), str)]
    check("15개 필수 항목", "fail" if missing else "pass", "누락·형식 오류: " + ", ".join(missing) if missing else "15개 항목이 문자열로 저장되어 있습니다.")
    check("작성 당시 입력", "pass" if payload else "limited",
          "작성 요청에 사용한 입력이 보존되어 있습니다." if payload else "당시 입력이 없어 현재 환자 정보로 소급 검증하지 않습니다.")
    if payload:
        owner = (payload.get("환자_프로필") or {}).get("bed_id")
        check("환자 일치", "pass" if owner == row.get("patient_id") else "fail", "리포트 환자 ID와 작성 입력의 환자 ID를 비교했습니다.")

    levels = risk.get("component_levels")
    if isinstance(levels, dict):
        expected = {k: SCORES[v] for k, v in levels.items() if v in SCORES}
        score = max(expected.values()) if expected else None
        conf = round(len(expected) / 3, 2) if expected else None
        ok = (_same(row.get("risk_score"), score) and _same(risk.get("combined_risk_score"), score)
              and risk.get("component_scores") == expected)
        check("위험점수 계산", "pass" if ok else "fail", f"저장된 신호별 단계의 최댓값: {score}; 저장 점수: {row.get('risk_score')}. 낙상 확률이 아닙니다.")
        check("신호 확보 비율", "pass" if _same(row.get("confidence"), conf) else "fail",
              f"확보 신호 {len(expected)}/3개 → {conf}; 저장값: {row.get('confidence')}. AI 정확도가 아닙니다.")
        if payload:
            # Recalculate from saved sensor facts, never from today's live readings.
            from integrated_analysis import compute_risk_score
            recalculated = compute_risk_score({"tof_behavior": payload.get("ToF_현재_판정"), "mmw_latest": payload.get("mmWave_현재_판정")})
            check("센서 입력과 판정 일치", "pass" if recalculated == risk and payload.get("계산된_위험점수") == risk else "fail",
                  "보존된 ToF·mmWave 입력으로 현재 계산 규칙을 다시 적용해 저장된 판정과 비교했습니다.")
    else:
        check("점수 계산 재현", "limited", "신호별 판정 근거가 저장되어 있지 않습니다.")

    missing_ids = resolved.get("missing_chunk_ids", [])
    if not resolved.get("success"):
        check("문헌 ID 연결", "limited", "문헌 저장소 조회 실패. 인용 내용은 확인하지 못했습니다.")
    elif missing_ids:
        check("문헌 ID 연결", "fail", "찾을 수 없는 근거: " + ", ".join(map(str, missing_ids)))
    else:
        check("문헌 ID 연결", "pass" if ids else "limited", f"저장된 근거 {len(ids)}개를 찾았습니다. ID가 있다는 것만으로 주장이 증명되지는 않습니다." if ids else "연결된 문헌 ID가 없습니다.")

    supplied = (payload or {}).get("검색된_논문_근거", [])
    identity_only = (supplied and all(p.get("구간") == "identity" for p in supplied)) if payload else (papers and all(p.get("section") == "identity" for p in papers))
    if identity_only:
        check("문헌 내용의 충분성", "limited", "연결된 자료가 제목·저자 등 서지정보뿐입니다. 연구 결과나 투약 판단의 직접 근거로 충분하지 않습니다.")
    if payload and not ((payload.get("논문_근거_충분성_판단") or {}).get("sufficient")):
        check("검색 근거 충분성", "limited", "문헌 조사 단계에서 근거가 충분하다고 확인하지 못했습니다.")

    if not isinstance(report, dict):
        report = {}
    if payload:
        cited = re.findall(r"\[근거:\s*([^\]]+)\]", report.get("관련_연구_근거", ""))
        supplied_ids = {p.get("문헌_ID") for p in supplied if p.get("문헌_ID")}
        unknown = [c.strip() for c in cited if c.strip() not in supplied_ids]
        if unknown:
            finding("unknown_citation", "관련_연구_근거", report.get("관련_연구_근거", ""), "실제 작성 입력에 없는 문헌 ID를 인용했습니다: " + ", ".join(unknown), "실제 전달된 문헌 ID와 해당 문단의 내용만 인용해야 합니다.", "error")
        if supplied_ids and not cited:
            check("문장별 근거 표시", "limited", "실제 문헌 ID를 붙인 인용이 없어 설명과 근거의 대응을 직접 확인해야 합니다.")
        previous = payload.get("직전_분석_기록")
        previous_text = report.get("과거_데이터와의_비교", "")
        if previous and re.search(r"직전 분석 기록[이은 는]*없", previous_text):
            finding("previous_report_denied", "과거_데이터와의_비교", previous_text, "작성 입력에 직전 리포트가 있는데 없다고 서술했습니다.", "직전 기록의 시각과 점수를 비교하되 원시 관측 이력의 한계는 별도로 설명해야 합니다.", "error")
        raw_speed = ((payload.get("mmWave_현재_판정") or {}).get("raw") or {}).get("speed")
        for field in ("보행_변화", "mmWave_변화"):
            text = report.get(field, "")
            if not re.search(r"직전|이전|과거", text) and isinstance(raw_speed, (int, float)):
                claimed = re.findall(r"보행\s*속도(?:는|가|:)?\s*(\d+(?:\.\d+)?)", text)
                if any(not _same(float(speed), raw_speed) for speed in claimed):
                    finding("speed_mismatch", field, text, f"작성 입력의 보행 속도는 {raw_speed}m/s입니다. 보행 감지 여부를 이유로 숫자를 바꾸면 안 됩니다.", f"센서 속도값은 {raw_speed}m/s입니다. 걷는 신호가 있는지는 별도 판정입니다.", "error")
        tof_text = report.get("ToF_변화", "")
        if isinstance(levels, dict) and levels.get("tof_posture") in ("caution", "warning", "critical") and re.search(r"위험\s*(?:상태|단계|수준)[는이가 ]*['\"]?정상", tof_text):
            finding("tof_level_mismatch", "ToF_변화", tof_text, "최종 ToF 단계는 정상이 아닙니다. 이진 이상감지 상태와 위험 단계를 혼동한 표현입니다.", "계산된 위험점수의 ToF 단계에 맞게 설명해야 합니다.", "error")
        for field in ("최근_변화", "활동_변화"):
            text = report.get(field, "")
            if re.search(r"(?:기록|이벤트).{0,12}없", text) and re.search(r"변화[는가 ]*없", text):
                finding("no_records_as_no_change", field, text, "기록이 없다는 사실은 변화가 없다는 증거가 아닙니다.", "변화 여부를 판단할 관측 자료가 부족합니다.")
        if not supplied and not re.search(r"정보 부족|근거.{0,8}(없|부족)|제시.{0,8}(없|않)", report.get("관련_연구_근거", "")):
            finding("no_supplied_evidence", "관련_연구_근거", report.get("관련_연구_근거", ""), "검색된 문헌이 입력에 없는데 연구 결과를 서술했습니다.", "이번 작성에 전달된 문헌 근거가 없다고 표시해야 합니다.", "error")
        observed_count = (payload.get("최근_24시간_활동_요약") or {}).get("n_events")
        for field in ("최근_변화", "활동_변화"):
            text = report.get(field, "")
            if "24시간" not in text:
                continue
            counts = [int(n) for n in re.findall(r"(\d+)\s*건", text)]
            if isinstance(observed_count, int) and any(n > observed_count for n in counts):
                finding("event_count_mismatch", field, text, f"입력의 최근 24시간 이벤트는 {observed_count}건인데 더 많은 건수를 서술했습니다.", "보존된 집계와 시간 범위를 기준으로 다시 작성해야 합니다.", "error")
            try:
                at = datetime.fromisoformat(payload["분석_시점"].replace("Z", "+00:00"))
                dates = re.findall(r"(20\d{2})[-년]\s*(\d{1,2})[-월]\s*(\d{1,2})", text)
                if any((at.date() - datetime(int(y), int(m), int(d)).date()).days > 2 for y, m, d in dates) and not re.search(r"24시간 이전|과거 기록|오래된", text):
                    finding("old_event_as_current", field, text, "분석일보다 여러 날 이전의 사건을 최근 24시간 사건처럼 서술했습니다.", "과거 사건과 이번 관측 기간을 분리해야 합니다.", "error")
            except (KeyError, ValueError, TypeError):
                pass
    medication_text = report.get("투약_조정_제안", "")
    for sentence in re.split(r"(?<=[.!?])\s+|\n", medication_text):
        if re.search(r"감량|증량|중단|유지.{0,8}적절", sentence) and not re.search(r"판단.{0,8}(불가|없|않)|결정.{0,8}(없|않)|근거.{0,8}부족|단정.{0,8}(없|않)", sentence):
            finding("medication_direction", "투약_조정_제안", sentence, "개별 약의 조정 방향을 제시하고 있습니다. 센서 위험점수와 일반 논문만으로는 환자별 적절성을 확인할 수 없습니다.", "복용 목적·실제 복용·증상 등을 의료진이 검토해야 하며, 현재 자료만으로 조정 방향은 판단할 수 없습니다.")
    if simulated and not demo and "시뮬레이션" not in report.get("현재_상태", ""):
        finding("simulation_not_declared", "현재_상태", report.get("현재_상태", ""), "가상 환자·가상 센서임이 첫 항목에 명시되어 있지 않습니다.", "시뮬레이션 기반 리포트임을 먼저 표시해야 합니다.")
    for field, value in report.items():
        if not isinstance(value, str):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n", value):
            cautious = re.search(r"단정.{0,8}(없|않)|판단.{0,8}(없|않)|의미하지|아닙니다|구분해야|확인.{0,5}필요", sentence)
            if re.search(r"보행[이가은 는]*불가능|걸을 수 없", sentence) and not cautious:
                finding("walking_incapacity", field, sentence, "보행 신호가 없는 것과 걸을 능력이 없는 것은 다릅니다. 이 표현은 원문 근거 확인이 필요합니다.", "지금 걷는 신호가 관측되지 않았습니다. 보행 가능 여부는 별도 평가가 필요합니다.")
            if re.search(r"낙상.{0,28}(발생하지 않았|없었|없습니다|없음)", sentence) and not re.search(r"기록|저장|보고|확인되지|판단|단정", sentence):
                finding("unobserved_fall_absence", field, sentence, "로그가 없다는 사실만으로 실제 낙상이 없었다고 확정할 수 없습니다.", "해당 기간에 저장된 낙상 이벤트가 없습니다. 실제 발생 여부는 이 자료만으로 확인할 수 없습니다.")
            if re.search(r"신뢰.{0,12}(수준|정보|데이터)", sentence) and re.search(r"0\.67|67\s*%", sentence) and not cautious:
                finding("availability_as_accuracy", field, sentence, "0.67은 3개 판정 신호 중 2개 확보를 뜻합니다. 내용의 정확성을 측정한 값이 아닙니다.", "판정 신호 3개 중 2개를 확보했습니다. 리포트 정확도는 별도로 검토해야 합니다.")
            if re.search(r"활동이 없었|활동은 없", sentence) and not re.search(r"기록|이벤트|저장|관측|현재", sentence):
                finding("no_events_as_inactivity", field, sentence, "이벤트가 기록되지 않은 기간을 실제 활동이 없었던 기간으로 단정할 수 없습니다.", "저장된 활동 이벤트가 없습니다. 활동량 전체를 판단할 자료는 부족합니다.")

    for c in checks:
        if c["status"] == "fail":
            finding("check_failed", c["name"], "", c["detail"], "원자료와 계산·저장 경로를 확인한 후 다시 작성해야 합니다.", "error")
    status = "demo_example" if demo else "needs_review" if findings else "limited" if any(c["status"] == "limited" for c in checks) else "checks_passed"
    return {"version": VERSION, "analysis_id": row["id"], "patient_id": row.get("patient_id"),
            "content_hash": content_hash(row), "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "status": status, "label": LABELS[status], "scope": SCOPE,
            "checks": checks, "findings": findings, "evidence": papers,
            "evidence_note": "문헌 저장소에서 조회한 근거입니다. 당시 모델에 전달된 문단은 아래 작성 입력에서 별도로 확인하세요.",
            "input_available": payload is not None, "direct_review": None}


def review_and_save(row, resolved=None):
    review = build_review(row, resolved)
    save_review(row, review)
    return review


def attach_direct_review(row, *, conclusion, findings, checked, sources=None):
    """Used by an explicit review task, not an automatic 'AI approved' stamp."""
    review = saved_review(row) or build_review(row)
    review["direct_review"] = {"reviewer": "Codex", "reviewed_at": datetime.now(timezone.utc).isoformat(),
                               "conclusion": conclusion, "findings": findings, "checked": checked,
                               "sources": sources or [], "scope": "개발 보조 AI의 원자료 대조 검토입니다. 의료진의 임상 검토를 대신하지 않습니다."}
    if findings and row.get("model_version") != "demo-history-seed":
        review.update(status="needs_review", label=LABELS["needs_review"])
    save_review(row, review)
    return review
