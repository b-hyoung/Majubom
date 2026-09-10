"""
ai_agent.py — 12번 탭(시스템 확장) STEP 18-20: AI 자동 Query 생성 + Hybrid Search + 반복 검색 Agent
====================================================================================
2026-09-10 복구: git reset/clean으로 유실된 파일 재작성(원본과 동일한 설계로 복원).

흐름(12번 탭 STEP 16 그대로):
  현재 상태 분석 → 검색 필요 정보 판단 → Query 생성 → Vector DB 검색 → 검색 결과 분석
  → 정보 부족? → (부족하면) 추가 Query → 재검색 → (충분하면) SQL DB 환자 기록 결합 → 종합 분석 준비

이 모듈은 "종합 분석/향후위험 예측 리포트"(STEP 21-23, ai_analysis 테이블에 최종 저장하는 단계)
자체는 만들지 않는다 — 거기서 쓸 재료(질의·근거 논문·환자 기록)를 모으는 "조사(investigate)" 단계까지만
담당한다. LLM 호출은 ai_report.call_openai()를 그대로 재사용(키 로딩/에러 처리 중복 구현 금지).

역할 분리(27번 원칙 재확인):
  SQL DB(db.py)   → 정확한 환자 데이터(위험인자·투약·최근 활동 요약)
  Vector DB(paper_kb.py) → 의미 기반 논문 검색
  이 모듈은 그 둘을 "언제 무엇을 물을지"를 AI가 스스로 판단하게 하는 접착 계층이다.
"""
from __future__ import annotations

import json
import os

import requests

import ai_report
import db
import paper_kb

TOF_BASE = os.environ.get("MAJUBOM_TOF_BASE", "http://127.0.0.1:5001")
MMW_BASE = os.environ.get("MAJUBOM_MMW_BASE", "http://127.0.0.1:5002")

MAX_ROUNDS_DEFAULT = 2
QUERIES_PER_ROUND_DEFAULT = 3


# ── ① 현재 상태 수집 (SQL DB + 실서버 라이브 상태) ────────────────────────
def gather_patient_state(bed_id: str) -> dict:
    """환자 프로필·위험인자·투약(SQL DB)과, ToF/mmWave 실서버의 현재 판정을 한데 모은다.
    실서버가 꺼져있어도(개발 중 등) 예외를 던지지 않고 그 부분만 None으로 정직하게 비운다 —
    "조회 실패"와 "정보 없음"을 헷갈리지 않도록 각 필드에 상태를 남긴다."""
    patient = db.get_patient(bed_id)
    mmw_target_id = (patient or {}).get("mmw_target_id")

    state: dict = {
        "bed_id": bed_id,
        "patient_profile": patient,
        "risk_factors": db.list_risk_factors(bed_id) if patient else [],
        "medications": db.list_medications(bed_id) if patient else [],
        "recent_24h": db.get_patient_timeseries_summary(bed_id, window="24h"),
        "tof_behavior": None, "tof_behavior_error": None,
        "mmw_latest": None, "mmw_latest_error": None,
    }

    try:
        r = requests.get(f"{TOF_BASE}/tof/behavior", timeout=3)
        state["tof_behavior"] = r.json() if r.status_code == 200 else None
    except requests.exceptions.RequestException as e:
        state["tof_behavior_error"] = f"{type(e).__name__} — ToF 서버 미기동/네트워크 문제일 수 있음"

    if mmw_target_id:
        try:
            r = requests.get(f"{MMW_BASE}/mmw/latest", params={"target": mmw_target_id}, timeout=3)
            state["mmw_latest"] = r.json() if r.status_code == 200 else None
        except requests.exceptions.RequestException as e:
            state["mmw_latest_error"] = f"{type(e).__name__} — mmWave 서버 미기동/네트워크 문제일 수 있음"

    return state


# ── ② AI Query 자동 생성 (STEP 15-18) ────────────────────────────────────
QUERY_GEN_SYSTEM_PROMPT = (
    "당신은 치매환자 낙상예측시스템의 조사 보조원입니다. 아래 JSON은 한 환자의 현재 센서 상태·위험인자·"
    "투약 요약입니다. 이 상태를 근거로, 관련 연구 문헌에서 무엇을 더 확인해야 하는지 판단해 자연어 검색 "
    "질의를 만드세요. 절대 이 데이터에 없는 사실을 지어내지 말고, 오직 '무엇을 검색해야 하는가'만 판단하세요. "
    "환자가 진정제·향정신성 약물(예: 트라조돈, 벤조디아제핀 계열 등)을 복용 중이라면, 그 약물과 낙상·섬망·"
    "다제병용 위험의 관계를 확인하는 질의를 최소 1개 반드시 포함하세요 — 이는 통합분석 리포트의 '투약 "
    "조정 제안' 항목의 근거로 쓰입니다. "
    "반드시 아래 JSON 스키마로만 답하세요(다른 텍스트 금지): "
    '{"reasoning": "판단 근거 한두 문장", "queries": ["검색질의1", "검색질의2", ...]}'
)


def generate_queries(patient_state: dict, max_queries: int = QUERIES_PER_ROUND_DEFAULT) -> dict:
    """현재 상태를 LLM에 넘겨 검색 질의를 생성시킨다. 정직한 실패 shape은 ai_report.call_openai()와 동일."""
    user_content = (
        f"환자 현재 상태(JSON):\n{json.dumps(patient_state, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"위 상태에서 낙상 위험과 관련해 확인이 필요한 항목에 대해, 최대 {max_queries}개의 검색 질의를 만드세요."
    )
    result = ai_report.call_openai(system_prompt=QUERY_GEN_SYSTEM_PROMPT,
                                    user_content=user_content, json_mode=True)
    if not result["success"]:
        return result
    try:
        parsed = json.loads(result["text"])
        queries = list(parsed.get("queries") or [])[:max_queries]
        return {"success": True, "queries": queries, "reasoning": parsed.get("reasoning", "")}
    except (json.JSONDecodeError, AttributeError) as e:
        return {"success": False, "error_type": "parse_error",
                "message": f"LLM 응답이 JSON 스키마를 따르지 않음: {type(e).__name__}"}


# ── ③ 검색 결과 충분성 판단 (STEP 16의 "정보 부족 여부 판단") ─────────────
SUFFICIENCY_SYSTEM_PROMPT = (
    "당신은 조사가 충분한지 판단하는 보조원입니다. 아래에는 환자 상태와, 지금까지 검색된 논문 근거들이 "
    "주어집니다. 이 근거들이 환자의 현재 위험 상태를 설명하는 데 충분한지 판단하세요. 근거를 새로 "
    "지어내지 말고 주어진 것만 근거로 판단하세요. 반드시 아래 JSON 스키마로만 답하세요: "
    '{"sufficient": true/false, "reasoning": "판단 근거", "additional_queries": ["부족하면 추가로 검색할 질의", ...]}'
)


def judge_sufficiency(patient_state: dict, results_so_far: list[dict]) -> dict:
    user_content = (
        f"환자 상태(JSON):\n{json.dumps(patient_state, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"지금까지 검색된 근거(JSON):\n{json.dumps(results_so_far, ensure_ascii=False, indent=2)}"
    )
    result = ai_report.call_openai(system_prompt=SUFFICIENCY_SYSTEM_PROMPT,
                                    user_content=user_content, json_mode=True)
    if not result["success"]:
        return result
    try:
        parsed = json.loads(result["text"])
        return {"success": True, "sufficient": bool(parsed.get("sufficient")),
                "reasoning": parsed.get("reasoning", ""),
                "additional_queries": list(parsed.get("additional_queries") or [])}
    except (json.JSONDecodeError, AttributeError) as e:
        return {"success": False, "error_type": "parse_error",
                "message": f"LLM 응답이 JSON 스키마를 따르지 않음: {type(e).__name__}"}


# ── ④ 반복 검색 Agent 루프 (STEP 16, 20) ─────────────────────────────────
def investigate(bed_id: str, max_rounds: int = MAX_ROUNDS_DEFAULT,
                 max_queries_per_round: int = QUERIES_PER_ROUND_DEFAULT, top_k: int = 5) -> dict:
    """전체 루프: 상태수집 → query생성 → Vector 검색 → 충분성판단 → (부족하면) 추가검색 반복
    → SQL 환자기록은 ①에서 이미 gather_patient_state()로 모아뒀으므로 여기서 다시 붙인다(Hybrid).

    반환 shape: {"success":bool, "bed_id", "patient_state", "rounds":[...], "final_results":[...],
                 "sufficient":bool, "reasoning":str}
    이 결과는 STEP 21-23(통합분석/리포트)이 그대로 입력으로 받게 될 재료다 — 이 함수 자체는
    최종 리포트나 위험점수를 만들지 않는다."""
    patient_state = gather_patient_state(bed_id)

    rounds: list[dict] = []
    all_results: dict[str, dict] = {}  # chunk_id -> result (중복 검색 결과 제거)

    qgen = generate_queries(patient_state, max_queries=max_queries_per_round)
    if not qgen["success"]:
        return {"success": False, **{k: v for k, v in qgen.items() if k != "success"},
                "bed_id": bed_id, "patient_state": patient_state, "stage": "generate_queries"}

    queries = qgen["queries"]
    sufficiency = {"sufficient": False, "reasoning": ""}

    for round_i in range(max_rounds):
        if not queries:
            break
        round_results = []
        for q in queries:
            sr = paper_kb.search(q, top_k=top_k)
            if not sr["success"]:
                # Vector DB 쪽이 실패(키 미설정 등)해도 지금까지 모은 SQL 상태는 살려서 반환 —
                # "검색을 못 했다"와 "검색했는데 결과가 없다"를 구분해 정직하게 기록.
                return {"success": False, **{k: v for k, v in sr.items() if k != "success"},
                        "bed_id": bed_id, "patient_state": patient_state,
                        "rounds": rounds, "stage": "vector_search", "query": q}
            for r in sr["results"]:
                all_results[r["chunk_id"]] = r
                round_results.append(r)
        rounds.append({"round": round_i + 1, "queries": queries, "n_results": len(round_results)})

        sufficiency = judge_sufficiency(patient_state, list(all_results.values()))
        if not sufficiency["success"]:
            # 충분성 판단(LLM) 실패해도 지금까지 모은 검색결과는 유효하니 그대로 반환.
            break
        if sufficiency["sufficient"] or round_i == max_rounds - 1:
            break
        queries = sufficiency["additional_queries"][:max_queries_per_round]

    return {
        "success": True, "bed_id": bed_id, "patient_state": patient_state,
        "rounds": rounds, "final_results": list(all_results.values()),
        "sufficient": sufficiency.get("sufficient", False),
        "reasoning": sufficiency.get("reasoning", ""),
        "initial_query_reasoning": qgen.get("reasoning", ""),
    }


# ── 단독 테스트 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== ai_agent.py 자체 테스트 (2026-09-10 복구본, 실제 OpenAI 호출 발생) ===\n")

    key = ai_report.get_api_key()
    print(f"OPENAI_API_KEY 설정 여부: {'있음' if key else '없음'}\n")

    state = gather_patient_state("bed_01")
    print("[상태 수집] patient_profile 존재:", state["patient_profile"] is not None,
          "| risk_factors:", len(state["risk_factors"]), "| medications:", len(state["medications"]))
    print("  tof_behavior 조회:", "성공" if state["tof_behavior"] is not None else state.get("tof_behavior_error"))
    print("  mmw_latest 조회:", "성공" if state["mmw_latest"] is not None else state.get("mmw_latest_error"))

    result = investigate("bed_01", max_rounds=2, max_queries_per_round=2, top_k=3)
    print("\n[investigate] success =", result["success"])
    if result["success"]:
        print("  1차 질의 근거:", result["initial_query_reasoning"])
        for rd in result["rounds"]:
            print(f"  라운드 {rd['round']}: 질의={rd['queries']} → 결과 {rd['n_results']}건")
        print("  최종 충분 여부:", result["sufficient"], "-", result["reasoning"])
        print(f"  최종 근거 논문 chunk {len(result['final_results'])}개:")
        for r in result["final_results"][:5]:
            print(f"    [{r['paper_id']}/{r['section']}] {r['original_text'][:70]}")
    else:
        print("  실패 단계:", result.get("stage"), "-", result.get("message"))
