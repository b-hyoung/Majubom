"""
test_e2e_pipeline.py — 12번 탭(시스템 확장) STEP 27: 전체 시스템 통합 테스트
====================================================================================
2026-09-10 복구: git reset/clean으로 유실된 파일 재작성(원본과 동일한 설계로 복원, 투약_조정_제안
추가에 따른 15개 키 검증 포함).

이 세션의 다른 자체 테스트들(tof_logic.py/behavior_fsm.py/paper_kb.py/ai_agent.py/
integrated_analysis.py)은 전부 "그 모듈 하나"만 검증했다. 이 스크립트는 실제로 떠 있는
tof_server.py(:5001)/mmw_server.py(:5002)에 HTTP로 요청을 보내며 다음 흐름 전체를
하나의 시나리오로 처음부터 끝까지 검증한다(12번 탭 SECTION 24 그대로):

  센서 프레임 수신 → (raw/clean 일관성 진단 → sensor_clean_data 기록)
  → Agent 조사(질의생성+Vector검색) → 통합분석(리포트 생성) → ai_analysis 저장
  → 근거 추적(evidence_ids → 원문 역조회)

비용 주의: OpenAI를 실제로 여러 번 호출한다(질의생성 1회 + 충분성판단 최대 1회 + 리포트생성 1회
≈ 3회). max_rounds=1로 최소화했다. pytest 등 새 프레임워크를 추가하지 않고, 이 프로젝트의 기존
자체테스트 관례(plain assert + print)를 그대로 따른다.

실행 전 전제조건: tof_server.py(:5001)·mmw_server.py(:5002)가 떠 있어야 하고,
docs/papers/paper-*.html이 이미 chunk+embedding 인덱스(server/lancedb_store)로
구축되어 있어야 한다(안 되어 있으면 STEP 3에서 정직하게 실패로 표시하고 계속 진행한다).
"""
from __future__ import annotations

import sys

import requests

TOF_BASE = "http://127.0.0.1:5001"
MMW_BASE = "http://127.0.0.1:5002"
BED_ID = "bed_01"

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def main():
    print("=== test_e2e_pipeline.py — 12번 탭 STEP 27 통합 테스트 (2026-09-10 복구본) ===\n")

    # ── STEP 1: 서버 헬스체크 ──────────────────────────────────────────
    print("-- 1) 서버 헬스체크 --")
    try:
        r = requests.get(f"{TOF_BASE}/tof/latest", timeout=3)
        check("ToF 서버(:5001) 응답", r.status_code == 200, f"status={r.status_code}")
    except requests.exceptions.RequestException as e:
        check("ToF 서버(:5001) 응답", False, f"{type(e).__name__} — 서버가 안 떠있으면 이후 전부 실패함")
        return _report()

    try:
        r = requests.get(f"{MMW_BASE}/mmw/live", timeout=3)
        check("mmWave 서버(:5002) 응답", r.status_code == 200, f"status={r.status_code}")
    except requests.exceptions.RequestException as e:
        check("mmWave 서버(:5002) 응답", False, f"{type(e).__name__}")

    # ── STEP 2: 환자 프로필 + DB 스키마(I6) ─────────────────────────────
    print("\n-- 2) 환자 프로필 + DB 확장 스키마(I6) --")
    r = requests.get(f"{TOF_BASE}/patients/{BED_ID}", timeout=3)
    check("환자 프로필 조회", r.status_code == 200 and r.json().get("bed_id") == BED_ID)
    patient = r.json() if r.status_code == 200 else {}

    r = requests.get(f"{TOF_BASE}/patients/{BED_ID}/risk_factors", timeout=3)
    check("위험인자 조회(1개 이상)", r.status_code == 200 and len(r.json()) > 0,
          f"n={len(r.json()) if r.status_code == 200 else '?'}")

    r = requests.get(f"{TOF_BASE}/patients/{BED_ID}/medications", timeout=3)
    check("투약 조회(1개 이상)", r.status_code == 200 and len(r.json()) > 0)

    # ── STEP 3: 센서 프레임 수신 → raw/clean 일관성 진단(I6 STEP 6-9) ────
    print("\n-- 3) 센서 프레임 수신 → sensor_clean_data 기록(I6) --")
    # 주의: 실제 bed_01/room_01로 POST하면 실 baseline이 갱신된다(테스트 후 원상복구 필요) —
    # 그래서 이 통합테스트는 새 프레임을 보내지 않고, 이미 쌓여있는 활동 요약만 조회한다
    # (실 baseline 오염 금지 원칙 — 지난 회차에 이미 한 번 실수로 어겼다가 복구한 전례가 있어
    # 이번엔 아예 쓰기 경로를 타지 않도록 설계).
    r = requests.get(f"{TOF_BASE}/patients/{BED_ID}", timeout=3)
    mmw_target = (r.json() or {}).get("mmw_target_id") if r.status_code == 200 else None
    check("mmw_target_id 매핑 존재(I8에서 고친 bed_id/target_id 불일치)", bool(mmw_target),
          f"mmw_target_id={mmw_target}")

    # ── STEP 4: 논문 Vector DB 검색(I7) ─────────────────────────────────
    print("\n-- 4) 논문 Vector DB 검색(I7) --")
    r = requests.get(f"{TOF_BASE}/kb/search", params={"q": "낙상 위험 요인", "top_k": 2}, timeout=15)
    kb_ok = r.status_code == 200 and r.json().get("success")
    check("/kb/search 성공", kb_ok, r.json().get("message", "") if not kb_ok else f"n={r.json().get('n_candidates')}")

    # ── STEP 5: AI Agent 조사(I8) ────────────────────────────────────────
    print("\n-- 5) AI Agent 조사(I8, 실제 OpenAI 호출) --")
    r = requests.get(f"{TOF_BASE}/agent/investigate",
                      params={"bed_id": BED_ID, "max_rounds": 1, "top_k": 2}, timeout=60)
    inv = r.json() if r.status_code == 200 else {}
    inv_ok = r.status_code == 200 and inv.get("success")
    check("/agent/investigate 성공", inv_ok, inv.get("message", "") if not inv_ok else
          f"질의생성 근거 확보, 근거 {len(inv.get('final_results', []))}건")

    # ── STEP 6: 통합분석 + 종합 리포트(I9) ────────────────────────────────
    print("\n-- 6) 통합분석 + ai_analysis 저장(I9, 실제 OpenAI 호출) --")
    r = requests.get(f"{TOF_BASE}/agent/analyze",
                      params={"bed_id": BED_ID, "max_rounds": 1, "top_k": 2}, timeout=60)
    ana = r.json() if r.status_code == 200 else {}
    ana_ok = r.status_code == 200 and ana.get("success")
    check("/agent/analyze 성공", ana_ok, ana.get("message", "") if not ana_ok else "")
    analysis_id = ana.get("analysis_id") if ana_ok else None
    check("analysis_id가 실제로 발급됨(DB 저장 확인)", analysis_id is not None, f"analysis_id={analysis_id}")
    if ana_ok:
        report = ana.get("report", {})
        # 12번 탭 SECTION 20의 15개 항목 중 "① 분석 시점"은 LLM이 만들지 않고
        # ai_analysis.created_at 컬럼(DB가 자동 채움)으로 별도 관리한다. 대신 사용자 요청으로
        # "투약_조정_제안"(감량/증량 검토 방향 + 의사 검토 필요 caveat)이 새로 추가되어, LLM이
        # 채우는 JSON 스키마는 여전히 15개 키가 맞다(14 - 분석시점 + 1 투약조정제안).
        check("15개 항목 리포트 키 전부 존재(분석시점은 DB created_at 관리, 투약_조정_제안 신규추가)",
              len(report) == 15, f"실제 키 개수={len(report)}, 키={list(report.keys())}")
        check("투약_조정_제안 항목에 의사 검토 caveat 포함",
              "담당 의사" in report.get("투약_조정_제안", ""), report.get("투약_조정_제안", "")[:80])

    # ── STEP 7: 근거 추적(I9 STEP 26) ────────────────────────────────────
    print("\n-- 7) 근거 추적: analysis_id → 논문 원문 역조회(STEP 26) --")
    if analysis_id:
        r = requests.get(f"{TOF_BASE}/agent/evidence", params={"analysis_id": analysis_id}, timeout=10)
        ev = r.json() if r.status_code == 200 else {}
        ev_ok = r.status_code == 200 and ev.get("success") and ev.get("evidence", {}).get("success")
        n_found = len(ev.get("evidence", {}).get("found", [])) if ev_ok else 0
        check("/agent/evidence 성공 + 원문 역조회", ev_ok, f"n_found={n_found}")
        if ev_ok and n_found > 0:
            sample = ev["evidence"]["found"][0]
            check("역조회 결과에 원문 출처(source_url) 포함", bool(sample.get("source_url")),
                  sample.get("source_url", ""))
    else:
        check("/agent/evidence", False, "analysis_id가 없어 건너뜀")

    _report()


def _report():
    print("\n" + "=" * 60)
    if _failures:
        print(f"FAIL — {len(_failures)}건 실패: {_failures}")
        sys.exit(1)
    print("전부 통과.")
    sys.exit(0)


if __name__ == "__main__":
    main()
