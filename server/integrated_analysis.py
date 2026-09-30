"""
integrated_analysis.py — 12번 탭(시스템 확장) STEP 21-23: 통합분석 → 향후위험 신호 → AI 종합 리포트
====================================================================================
2026-09-10 복구: git reset/clean으로 유실된 파일 재작성(원본과 동일한 설계로 복원, "투약_조정_제안"
항목 포함).

STEP 18-20(ai_agent.investigate())이 모아준 재료(환자 SQL 기록 + 논문 근거)를 받아서:
  ① 위험도(risk_score)는 LLM이 아니라 이 모듈이 직접 계산한다(tof_logic/mmw_logic/behavior_fsm이
     이미 계산해둔 alert_level만 사용 — 새 숫자를 만들지 않는다. ai_report.py와 동일 철학).
  ② 직전 ai_analysis 기록을 함께 넘겨 "과거 데이터와의 비교"가 지어낸 말이 아니라 실제 이전 분석
     결과에 근거하게 한다.
  ③ LLM에는 "이 사실들을 15개 항목 리포트로 정리"만 시키고, 그 결과를 ai_analysis 테이블에 저장한다.

정직하게 명시할 한계: "향후 위험 가능성"은 실제 시계열 예측 모델(예: 24시간 후 위험도를 학습한 모델)이
아니라, 현재 시점의 alert_level 조합 + 최근 변화 추세를 LLM이 설명하는 수준이다 — 진짜 미래 예측을
한다고 과장하지 않는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import ai_agent
import ai_report
import db

DEFAULT_MODEL = ai_report.DEFAULT_MODEL
PROMPT_VERSION = "integrated_v6"   # v6: 현재 24시간 사건만 전달, 리포트 생성 이력은 활동에서 제외
REPORT_FIELDS = (
    "현재_상태", "최근_변화", "ToF_변화", "mmWave_변화", "보행_변화", "활동_변화",
    "침상_관련_행동", "낙상_위험도_설명", "주요_위험_요인", "과거_데이터와의_비교",
    "향후_위험_가능성", "관련_연구_근거", "투약_조정_제안", "데이터_신뢰도_설명",
    "추가_관찰이_필요한_항목",
)


def _gait_prior_facts(bed_id: str) -> dict | None:
    """gait_prior.py가 저장한 보행 기대값 요약. 없거나 읽지 못하면 None(지어내지 않음)."""
    try:
        import gait_prior
        with db.get_conn() as conn:
            prior = gait_prior.get_active_prior(conn, bed_id)
    except Exception:
        return None
    if not prior:
        return None
    return {"요약": gait_prior.summarize(prior), "센서_예측범위_밖": prior.get("out_of_scope")}

# tof_logic/mmw_logic/behavior_fsm이 공통으로 쓰는 4단계를 하나의 점수로 정렬한다(정성적 단계를
# 정량 지표로 바꾸되, 그 대응표 자체가 근거— LLM이 만든 숫자가 아니다).
RISK_LEVEL_SCORE = {"normal": 0.1, "caution": 0.4, "warning": 0.7, "critical": 0.95}


def compute_risk_score(patient_state: dict) -> dict:
    """이미 계산된 alert_level(ToF 자세·ToF 이탈·mmWave 보행 3종)만으로 위험도를 정량화한다.
    셋 중 가장 위험한 신호를 대표값으로 삼는다(평균을 쓰면 한 센서만 위험해도 희석되어 과소평가될
    수 있으므로 max를 택함 — 근거: 04번 탭 융합 설계의 "결정레벨에서 보수적으로 합친다"는 원칙과 동일)."""
    tof = patient_state.get("tof_behavior") or {}
    mmw = patient_state.get("mmw_latest") or {}

    posture_level = (tof.get("posture_baseline") or {}).get("alert_level")
    exit_level = (tof.get("exit_episode") or {}).get("alert_level")
    mmw_level = mmw.get("alert_level") if not mmw.get("measuring_held") and (mmw.get("quality") or {}).get("reliable") is not False else None
    # 개인 기준이 생기기 전의 '잠정' 보행 판정(문헌 기대 범위 비교)은 정식 위험 점수에 넣지 않고 따로 보여준다.
    # 단 높이 급강하(낙상) 절대 규칙에서 나온 critical은 잠정 여부와 관계없이 반영한다.
    provisional = {}
    if mmw_level and mmw.get("provisional") and mmw_level != "critical":
        provisional["mmwave_gait"] = mmw_level
        mmw_level = None

    components = {
        "tof_posture": posture_level, "tof_exit": exit_level, "mmwave_gait": mmw_level,
    }
    scored = {k: RISK_LEVEL_SCORE[v] for k, v in components.items() if v in RISK_LEVEL_SCORE}
    combined = max(scored.values()) if scored else None
    dominant = max(scored, key=scored.get) if scored else None

    return {
        "component_levels": components, "component_scores": scored,
        "combined_risk_score": combined, "dominant_signal": dominant,
        "provisional_levels": provisional,
        "method": "max_of_existing_alert_level_scores_v1",
    }


def compute_data_confidence(patient_state: dict, risk: dict) -> float | None:
    """데이터 신뢰도 — 몇 개 신호가 실제로 확보됐는지 비율로. 신호가 하나도 없으면 None(모른다를
    0으로 위장하지 않음)."""
    total = 3  # tof_posture, tof_exit, mmwave_gait
    have = len(risk["component_scores"])
    if have == 0:
        return None
    return round(have / total, 2)


# ── AI 종합 리포트 — 15개 항목(12번 탭 STEP 20, 투약_조정_제안 포함) ─────
INTEGRATED_SYSTEM_PROMPT = (
    "당신은 치매환자 낙상예측시스템의 통합분석 리포트 작성자입니다. 아래 JSON에는 이미 계산이 끝난 "
    "사실(환자 프로필·위험인자·투약, ToF/mmWave 현재 판정, 이미 계산된 위험점수, 직전 분석 기록, "
    "검색된 논문 근거)만 들어있습니다. 절대 새로운 수치를 만들어내거나 주어진 수치를 바꾸지 마세요. "
    "'입원_기록_원문'과 '간호_기록_원문'은 해당 환자의 저장된 기록이며 날짜·출처를 함께 참고하세요. "
    "'데이터_출처'가 simulation이면 현재_상태 첫 문장에 '시뮬레이션 기반 리포트입니다.'라고 명시하고, "
    "가상 센서 사건을 실제 환자에게 발생한 사건처럼 서술하지 마세요. "
    "quality.walking=false 또는 보행속도 0은 현재 걷는 신호가 없다는 뜻입니다. "
    "원문 기록의 명시적인 근거 없이 '보행 불가능'이나 '걸을 수 없음'으로 해석하지 마세요. "
    "이벤트 0건은 해당 기간에 저장된 이벤트가 없다는 뜻이며, 낙상이 없었다는 증거가 아닙니다. "
    "데이터_신뢰도_비율은 3개 판정 신호 중 확보된 신호의 비율입니다. AI 정확도나 의학적 신뢰도로 해석하지 마세요. "
    "계산된 위험점수는 단계 대응값이지 낙상 발생 확률이 아닙니다. "
    "직전 리포트는 AI가 쓴 과거 해석이며 검증된 환자 사실이 아닙니다. 오류를 그대로 반복하지 마세요. "
    "'최근_이벤트_상세'는 최근 24시간의 최대 20건입니다. 오래된 사건이나 리포트 생성 이력은 제외됩니다. "
    "활동 요약과 개별 이벤트가 없으면 최근 변화는 판단할 수 없습니다. 현재 신호만으로 '변화 없음'이라고 쓰지 마세요. "
    "67% 등의 신호 확보 비율을 mmWave 센서 자체의 신뢰도나 예측 정확도라고 쓰지 마세요. "
    "현재 센서 상태가 낙상 의심이면 현재의 의심 신호라고 쓰세요. 의심을 실제 낙상 확정으로 바꾸지 마세요. "
    "'최근_이벤트_상세'는 저장된 이벤트 로그입니다. 시뮬레이션의 이벤트는 가상 사건임을 표시하세요. "
    "낙상(level=critical, event_type에 fall 포함) "
    "이벤트가 있다면 '침상_관련_행동'과 '낙상_위험도_설명'에 그 발생 사실과 시각을 명시하고, 그 낙상 "
    "이벤트보다 앞선 시각의 이벤트들(주의·경고 수준 상승 등) 중 전조로 볼 만한 것이 있다면 함께 짚어 "
    "'최근_변화'에 서술하세요 — 이벤트 로그에 없는 전조는 지어내지 마세요. "
    "논문 근거는 주어진 것 외에 새로 지어내지 마세요. 정보가 부족한 항목은 빈 문자열이나 '정보 부족'으로 "
    "정직하게 표시하고 추측으로 채우지 마세요. '향후 위험 가능성'은 실제 예측 모델이 아니라 현재 판정과 "
    "최근 변화 추세에 대한 신중한 설명이어야 하며, 확정적으로 단정하지 마세요. "
    "'투약_조정_제안'에는 현재 투약과 관련해 담당 의료진이 확인할 사항만 적으세요. "
    "센서 위험점수나 일반적인 낙상 논문만으로 특정 약의 감량·증량·중단·유지가 적절하다고 판단하지 마세요. "
    "약물 분류·부작용을 주어진 근거에서 확인할 수 없으면 임의로 붙이지 마세요. "
    "환자별 복용 목적, 실제 복용 여부, 증상·혈압 등 평가 자료가 부족하면 '정보 부족으로 조정 판단 불가'라고 적으세요. "
    "절대 구체적인 용량·용법을 지시하지 마세요. 이 항목의 마지막 문장은 반드시 '이 제안은 참고용이며 최종 처방 변경은 담당 의사가 "
    "판단해야 합니다.'로 끝내세요. "
    "관련_연구_근거에는 실제 제공된 문단의 내용과 한계를 적고 문헌 ID를 [근거: ID] 형식으로 붙이세요. "
    "제목·저자만 있는 identity 구간은 연구 결과의 근거가 아닙니다. 연관성을 인과관계나 개인 예측으로 확대하지 마세요. "
    "'보행_기대범위_문헌기반'은 입원 기록과 문헌으로 만든 참고 범위일 뿐 낙상 확률이 아니며, 개인 보행 기록이 "
    "쌓이면 개인 기준이 우선합니다. '센서_예측범위_밖_위험'에 요인이 있으면 '주요_위험_요인'에서 센서가 미리 "
    "알기 어려운 유형(걸려 넘어짐·갑자기 힘이 빠지는 주저앉음 등)으로 구분해 적고, 이런 낙상을 센서가 예측한다고 쓰지 마세요. "
    "반드시 아래 JSON 스키마로만, 모든 키를 한국어 문자열 값으로 채워 답하세요: "
    '{"현재_상태": "", "최근_변화": "", "ToF_변화": "", "mmWave_변화": "", "보행_변화": "", '
    '"활동_변화": "", "침상_관련_행동": "", "낙상_위험도_설명": "", "주요_위험_요인": "", '
    '"과거_데이터와의_비교": "", "향후_위험_가능성": "", "관련_연구_근거": "", '
    '"투약_조정_제안": "", "데이터_신뢰도_설명": "", "추가_관찰이_필요한_항목": ""}'
)


def _build_payload(bed_id: str, investigation: dict, risk: dict, confidence: float | None,
                    prev_analysis: dict | None) -> dict:
    gait = _gait_prior_facts(bed_id)
    # Prior prose can contain hallucinations. Compare saved scores, never treat that prose as facts.
    previous = {k: prev_analysis.get(k) for k in ("id", "created_at", "risk_score", "confidence")
                if k in prev_analysis} if prev_analysis else None
    return {
        "분석_시점": datetime.now(timezone.utc).isoformat(),
        "환자_프로필": investigation["patient_state"].get("patient_profile"),
        "위험인자": investigation["patient_state"].get("risk_factors"),
        "투약": investigation["patient_state"].get("medications"),
        "입원_기록_원문": investigation["patient_state"].get("emr_records", []),
        "간호_기록_원문": investigation["patient_state"].get("nursing_records", []),
        "데이터_출처": investigation["patient_state"].get("data_source"),
        "검증_조건": investigation["patient_state"].get("evaluation_context"),
        "ToF_현재_판정": investigation["patient_state"].get("tof_behavior"),
        "mmWave_현재_판정": investigation["patient_state"].get("mmw_latest"),
        "최근_24시간_활동_요약": investigation["patient_state"].get("recent_24h"),
        "최근_이벤트_상세": investigation["patient_state"].get("recent_events"),
        "이벤트_조회_범위": "최근 24시간 중 최대 20건. AI 리포트 생성 이력 제외. 기록 없음은 사건 부재의 증거가 아님.",
        "계산된_위험점수": risk,
        "데이터_신뢰도_비율": confidence,
        "직전_분석_기록": previous,
        "검색된_논문_근거": [
            {"문헌_ID": r["chunk_id"], "논문": r["title"], "구간": r["section"], "내용": r["original_text"], "출처": r["source_url"]}
            for r in investigation.get("final_results", [])
        ],
        "논문_근거_충분성_판단": {"sufficient": investigation.get("sufficient"),
                          "reasoning": investigation.get("reasoning")},
        "보행_기대범위_문헌기반": gait["요약"] if gait else None,
        "센서_예측범위_밖_위험": gait["센서_예측범위_밖"] if gait else None,
    }


def generate_integrated_report(bed_id: str, max_rounds: int = 2, top_k: int = 5,
                                persist: bool = True, override_created_at: str | None = None) -> dict:
    """전체 STEP 21-23 파이프라인. 반환: {"success":bool, "analysis_id", "risk", "report", ...}
    또는 실패 시 ai_agent/ai_report와 동일한 정직한 실패 shape을 그대로 전파한다."""
    investigation = ai_agent.investigate(bed_id, max_rounds=max_rounds, top_k=top_k)
    if not investigation["success"]:
        return {**investigation, "stage": investigation.get("stage", "investigate")}

    risk = compute_risk_score(investigation["patient_state"])
    confidence = compute_data_confidence(investigation["patient_state"], risk)
    prev_analysis = db.get_latest_ai_analysis(bed_id, analysis_type="integrated_status")

    payload = _build_payload(bed_id, investigation, risk, confidence, prev_analysis)
    user_content = (
        "다음은 한 환자의 통합분석에 필요한 사실입니다. 이 사실만 근거로 15개 항목 리포트를 "
        "작성하세요.\n\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )
    result = ai_report.call_openai(system_prompt=INTEGRATED_SYSTEM_PROMPT,
                                    user_content=user_content, json_mode=True)
    if not result["success"]:
        return {**result, "stage": "generate_report", "risk": risk, "patient_state": investigation["patient_state"]}

    try:
        report = json.loads(result["text"])
    except json.JSONDecodeError as e:
        return {"success": False, "error_type": "parse_error",
                "message": f"LLM 응답이 JSON 스키마를 따르지 않음: {type(e).__name__}",
                "stage": "parse_report", "risk": risk}

    if not isinstance(report, dict) or any(not isinstance(report.get(key), str) for key in REPORT_FIELDS):
        invalid = [key for key in REPORT_FIELDS if not isinstance(report, dict) or not isinstance(report.get(key), str)]
        return {"success": False, "error_type": "invalid_report_schema",
                "message": "리포트는 15개 필수 항목을 문자열로 포함해야 합니다.",
                "invalid_fields": invalid,
                "stage": "parse_report", "risk": risk}

    evidence_ids = [r["chunk_id"] for r in investigation.get("final_results", [])]
    analysis_id = None
    review = None
    if persist:
        analysis_id = db.insert_ai_analysis(
            patient_id=bed_id, analysis_type="integrated_status",
            risk_score=risk["combined_risk_score"], summary=report.get("현재_상태", ""),
            detected_patterns=[report.get("주요_위험_요인", "")],
            predicted_risks=[report.get("향후_위험_가능성", "")],
            evidence_ids=evidence_ids, model_version=result.get("model", DEFAULT_MODEL),
            prompt_version=PROMPT_VERSION, confidence=confidence, report=report, risk=risk,
            created_at=override_created_at,
            input_snapshot=payload,
        )
        import report_review
        review = report_review.review_and_save(db.get_analysis_by_id(analysis_id), {
            "success": True, "found": investigation.get("final_results", []), "missing_chunk_ids": [],
        })
        db.insert_sensor_event(
            bed_id, "ai_analysis_completed", ts=override_created_at, level=(
                risk["component_levels"].get(risk["dominant_signal"]) if risk["dominant_signal"] else None),
            title="AI 통합분석 완료", note=report.get("현재_상태", "")[:200],
        )

    return {
        "success": True, "bed_id": bed_id, "analysis_id": analysis_id,
        "risk": risk, "data_confidence": confidence, "report": report,
        "evidence": investigation.get("final_results", []),
        "prev_analysis_compared": prev_analysis is not None,
        "review": review,
    }


# ── 단독 테스트 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== integrated_analysis.py 자체 테스트 (2026-09-10 복구본, 실제 OpenAI 호출 발생) ===\n")

    result = generate_integrated_report("bed_01", max_rounds=1, top_k=3)
    print("[generate_integrated_report] success =", result.get("success"))
    if result["success"]:
        print("  analysis_id:", result["analysis_id"])
        print("  risk:", result["risk"])
        print("  data_confidence:", result["data_confidence"])
        print("  prev_analysis_compared:", result["prev_analysis_compared"])
        print("\n  리포트 15개 항목:")
        for k, v in result["report"].items():
            print(f"    - {k}: {v[:100]}")
    else:
        print("  실패 단계:", result.get("stage"), "-", result.get("message"))
