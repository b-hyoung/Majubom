"""
ai_report.py — LLM 호출 공용 계층 (OpenAI) + 주간/누적 AI 리포트 생성
====================================================================
2026-09-10 복구: git reset/clean으로 유실된 파일 재작성. 이 프로젝트 전체가 공유하는
"정직한 실패" 패턴의 근원 모듈 — API 키가 없거나 호출이 실패하면 절대 가짜 데이터를
만들지 않고 {"success": False, "error_type": ..., "message": ...} 를 정직하게 반환한다.
paper_kb.py/ai_agent.py/integrated_analysis.py가 전부 get_api_key()/call_openai()를
그대로 재사용한다(키 로딩·에러 처리 중복 구현 금지).
"""
from __future__ import annotations

import json
import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", ".env")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def _parse_env_file(path: str) -> dict:
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def get_api_key() -> str | None:
    """OPENAI_API_KEY를 환경변수 → .env 순으로 조회. 반환값을 절대 출력/로깅하지 말 것."""
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    return _parse_env_file(ENV_PATH).get("OPENAI_API_KEY") or None


# ── 프롬프트 구성 (숫자는 weekly_report.py가 이미 계산한 값만 사용) ───────
SYSTEM_PROMPT = (
    "당신은 치매환자 낙상예측시스템의 임상 보조 리포트 작성자입니다. "
    "아래 JSON은 이미 계산이 끝난 사실입니다. 절대 새로운 수치를 만들어내거나 "
    "주어진 수치를 변경하지 마세요. 당신의 역할은 이 사실들을 보호자/의료진이 "
    "이해하기 쉬운 한국어 서술문으로 정리하고, 임상적으로 합리적인 맥락(왜 이 변화가 "
    "우려되는지)을 짧게 덧붙이는 것뿐입니다. 확정 진단이나 처방을 내리지 말고, "
    "'재평가 권장' 수준의 신중한 어조를 유지하세요."
)


def build_report_payload(sim_result: dict, report: dict) -> dict:
    """virtual_patient.simulate_month() 결과 + weekly_report.generate_report() 결과를
    LLM에 그대로 넘길 최소 사실 집합으로 정리. 리포트는 "이번 주까지"만 봐야 하므로,
    이 리포트의 기준일(report_date) 이후에 일어난 이벤트는 제외한다."""
    cutoff = report["report_date"]  # "YYYY-MM-DD"
    events_so_far = [e for e in sim_result.get("events", []) if e["t"][:10] <= cutoff]
    fall_events = [e for e in events_so_far if e["type"] == "FALL_SUSPECTED"]
    exit_events = [e for e in events_so_far if e["type"] == "exit"]
    return {
        "환자_프로필": sim_result.get("profile"),
        "관측_기간_요약": {
            "기준일": cutoff,
            "이_시점까지_총_침상이탈_건수": len(exit_events),
            "이_시점까지_낙상의심_융합경보_건수": len(fall_events),
            "낙상의심_상세": [{"시각": e["t"], "근거": e.get("reasons")} for e in fall_events],
        },
        "주간_리포트_판정": report.get("overall"),
        "주간_리포트_권장조치": report.get("recommendation"),
        "주간_리포트_세부근거": report.get("findings_text", []),
        "주간_리포트_세부근거_구조화": report.get("findings", []),
        "데이터_출처_주의사항": sim_result.get("note"),
    }


def build_cumulative_payload(sim_result: dict, cum: dict) -> dict:
    return {
        "환자_프로필": sim_result.get("profile"),
        "기준일": cum.get("as_of"),
        "누적_요약": cum,
        "데이터_출처_주의사항": (sim_result.get("note") or "") +
            " 이 문서는 주간 비교가 아니라 관측 시작부터 지금까지의 누적 추세를 다룹니다.",
    }


def call_openai(payload: dict = None, model: str = DEFAULT_MODEL, timeout: float = 30.0,
                 system_prompt: str | None = None, user_content: str | None = None,
                 json_mode: bool = False) -> dict:
    """OpenAI Chat Completions 호출. 하위호환: payload만 주면 기존 주간리포트 방식대로
    SYSTEM_PROMPT + payload를 JSON 문자열로 넣어 호출한다. system_prompt/user_content를
    주면(paper_kb 질의생성, ai_agent, integrated_analysis 등) 그걸 그대로 쓴다.
    json_mode=True면 OpenAI의 JSON 강제 응답 모드를 켠다.

    반환: {"success": True, "text": str, "model": str, "usage": dict|None}
    또는 {"success": False, "error_type": str, "message": str}"""
    key = get_api_key()
    if not key:
        return {"success": False, "error_type": "missing_key",
                "message": ".env 또는 환경변수에 OPENAI_API_KEY가 설정되어 있지 않습니다. "
                           "코드 문제가 아니라 키 미설정입니다."}

    if system_prompt is None:
        system_prompt = SYSTEM_PROMPT
    if user_content is None:
        user_content = ("다음은 이미 계산된 사실입니다. 이 사실만으로 리포트를 작성하세요.\n\n"
                         + json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body, timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return {"success": False, "error_type": "timeout",
                "message": f"OpenAI API 응답 시간 초과({timeout}s)."}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error_type": "network",
                "message": "OpenAI API 연결 실패 — 네트워크/방화벽 문제일 가능성이 높습니다."}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error_type": "request_error",
                "message": f"요청 중 예외 발생: {type(e).__name__}"}

    if resp.status_code == 401:
        return {"success": False, "error_type": "auth",
                "message": "인증 실패(401) — API 키가 비어있거나 유효하지 않습니다."}
    if resp.status_code == 429:
        return {"success": False, "error_type": "rate_limit",
                "message": "요청 한도 초과(429) — 크레딧/쿼터 문제일 수 있습니다."}
    if resp.status_code != 200:
        snippet = resp.text[:300] if resp.text else ""
        return {"success": False, "error_type": f"http_{resp.status_code}",
                "message": f"OpenAI API 오류 응답: {snippet}"}

    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        return {"success": False, "error_type": "parse_error",
                "message": f"응답 파싱 실패: {type(e).__name__}"}

    return {"success": True, "text": text, "model": model, "usage": data.get("usage")}


def generate_ai_report(sim_result: dict, report: dict) -> dict:
    payload = build_report_payload(sim_result, report)
    return call_openai(payload=payload)


def generate_cumulative_ai_report(sim_result: dict, cum: dict) -> dict:
    payload = build_cumulative_payload(sim_result, cum)
    return call_openai(payload=payload)


# ── 단독 테스트 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== ai_report.py 자체 테스트 (복구본) ===\n")
    key = get_api_key()
    print(f"OPENAI_API_KEY 설정 여부: {'있음' if key else '없음'}")
    if key:
        r = call_openai(system_prompt="당신은 테스트 응답기입니다.", user_content="ok라고만 답하세요.")
        print("[call_openai 테스트]", r.get("success"), r.get("text"))
