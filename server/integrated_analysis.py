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
PROMPT_VERSION = "integrated_v1"

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
    mmw_level = mmw.get("alert_level")

    components = {
        "tof_posture": posture_level, "tof_exit": exit_level, "mmwave_gait": mmw_level,
    }
    scored = {k: RISK_LEVEL_SCORE[v] for k, v in components.items() if v in RISK_LEVEL_SCORE}
    combined = max(scored.values()) if scored else None
    dominant = max(scored, key=scored.get) if scored else None

    return {
        "component_levels": components, "component_scores": scored,
        "combined_risk_score": combined, "dominant_signal": dominant,
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
    "논문 근거는 주어진 것 외에 새로 지어내지 마세요. 정보가 부족한 항목은 빈 문자열이나 '정보 부족'으로 "
    "정직하게 표시하고 추측으로 채우지 마세요. '향후 위험 가능성'은 실제 예측 모델이 아니라 현재 판정과 "
    "최근 변화 추세에 대한 신중한 설명이어야 하며, 확정적으로 단정하지 마세요. "
    "'투약_조정_제안' 항목은 특히 신중하게 작성하세요: 주어진 '투약' 목록과 낙상 위험점수·검색된 논문 근거"
    "(특히 진정제·향정신성약물·다제병용과 낙상/섬망의 관계를 다루는 논문)만 근거로, 감량 검토가 필요한지 "
    "증량 검토가 필요한지 현 상태 유지가 적절한지 판단하되, 절대 구체적인 용량·용법을 지시하지 마세요 "
    "(당신은 처방 권한이 없습니다 — 방향성과 근거만 제시). 근거가 부족하면 '정보 부족으로 판단 불가'라고 "
    "정직하게 쓰세요. 이 항목의 마지막 문장은 반드시 '이 제안은 참고용이며 최종 처방 변경은 담당 의사가 "
    "판단해야 합니다.'로 끝내세요. "
    "반드시 아래 JSON 스키마로만, 모든 키를 한국어 문자열 값으로 채워 답하세요: "
    '{"현재_상태": "", "최근_변화": "", "ToF_변화": "", "mmWave_변화": "", "보행_변화": "", '
    '"활동_변화": "", "침상_관련_행동": "", "낙상_위험도_설명": "", "주요_위험_요인": "", '
    '"과거_데이터와의_비교": "", "향후_위험_가능성": "", "관련_연구_근거": "", '
    '"투약_조정_제안": "", "데이터_신뢰도_설명": "", "추가_관찰이_필요한_항목": ""}'
)


def _build_payload(bed_id: str, investigation: dict, risk: dict, confidence: float | None,
                    prev_analysis: dict | None) -> dict:
    return {
        "분석_시점": datetime.now(timezone.utc).isoformat(),
        "환자_프로필": investigation["patient_state"].get("patient_profile"),
        "위험인자": investigation["patient_state"].get("risk_factors"),
        "투약": investigation["patient_state"].get("medications"),
        "ToF_현재_판정": investigation["patient_state"].get("tof_behavior"),
        "mmWave_현재_판정": investigation["patient_state"].get("mmw_latest"),
        "최근_24시간_활동_요약": investigation["patient_state"].get("recent_24h"),
        "계산된_위험점수": risk,
        "데이터_신뢰도_비율": confidence,
        "직전_분석_기록": prev_analysis,
        "검색된_논문_근거": [
            {"논문": r["title"], "구간": r["section"], "내용": r["original_text"], "출처": r["source_url"]}
            for r in investigation.get("final_results", [])
        ],
        "논문_근거_충분성_판단": {"sufficient": investigation.get("sufficient"),
                          "reasoning": investigation.get("reasoning")},
    }


def generate_integrated_report(bed_id: str, max_rounds: int = 2, top_k: int = 5,
                                persist: bool = True) -> dict:
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

    evidence_ids = [r["chunk_id"] for r in investigation.get("final_results", [])]
    analysis_id = None
    if persist:
        analysis_id = db.insert_ai_analysis(
            patient_id=bed_id, analysis_type="integrated_status",
            risk_score=risk["combined_risk_score"], summary=report.get("현재_상태", ""),
            detected_patterns=[report.get("주요_위험_요인", "")],
            predicted_risks=[report.get("향후_위험_가능성", "")],
            evidence_ids=evidence_ids, model_version=result.get("model", DEFAULT_MODEL),
            prompt_version=PROMPT_VERSION, confidence=confidence, report=report,
        )
        db.insert_sensor_event(
            bed_id, "ai_analysis_completed", level=risk.get("dominant_signal") and
            ("caution" if risk["combined_risk_score"] and risk["combined_risk_score"] < 0.7 else "warning"),
            title="AI 통합분석 완료", note=report.get("현재_상태", "")[:200],
        )

    return {
        "success": True, "bed_id": bed_id, "analysis_id": analysis_id,
        "risk": risk, "data_confidence": confidence, "report": report,
        "evidence": investigation.get("final_results", []),
        "prev_analysis_compared": prev_analysis is not None,
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
