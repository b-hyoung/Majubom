"""
analysis_kb.py — 12번 탭(시스템 확장) STEP 25: 과거 AI 분석 결과의 Vector화
====================================================================================
2026-09-10 복구: git reset/clean으로 유실된 파일 재작성(원본과 동일한 LanceDB 기반 설계로 복원).

paper_kb.py와 임베딩 로직(embed_texts)은 재사용하지만, 저장소(컬렉션)는 완전히 분리한다 —
같은 LanceDB 디렉터리(server/lancedb_store) 안에 별도 테이블 "analysis_chunks"를 둔다
(paper_chunks 테이블과 절대 안 섞임). 사용자 요구사항(12번 탭 SECTION 22) 그대로: "과거 AI
분석 결과"와 "논문"을 같은 인덱스에 섞지 않는다 — 검색 시 "이 논문에서 나온 결과"와
"이 환자의 과거 패턴"이 뒤섞여 나오면 안 되기 때문이다.

환자 식별정보와 분석용 지식의 분리(SECTION 22 원칙): embedding 대상 텍스트에는 환자 이름 같은
직접식별자를 넣지 않는다 — bed_id(예: "bed_01")는 애초에 병상 코드이지 이름이 아니므로 그대로 써도
되지만, ai_analysis.summary/report에 이름이 섞여 들어간 문장이 있다면 그대로 embedding하지 않고
지운다(아래 _strip_identifiers 참고). 이 조치가 완벽한 비식별화를 보장하지는 않는다는 것도 정직하게
명시한다 — 1인실 단일 환자 시스템이라 "환자 A" 수준의 비식별화가 실질적 의미를 갖긴 어렵다(코퍼스에
환자가 1명뿐이므로). 다인실로 확장될 때 이 부분을 다시 검토해야 한다.
"""
from __future__ import annotations

import json
import re

import db
import paper_kb  # embed_texts(), _connect(), _table_exists() 재사용 — 임베딩/연결 로직 중복 구현 금지

ANALYSIS_TABLE = "analysis_chunks"
_CHUNK_FIELDS = ("chunk_id", "analysis_id", "patient_id", "analysis_type", "created_at", "original_text")


def _strip_identifiers(text: str, patient_name: str | None) -> str:
    """환자 이름이 본문에 그대로 등장하면 "환자"로 치환 — 완벽한 비식별화는 아니고
    가장 명백한 직접식별자(이름)만 제거하는 최소 조치임을 문서에도 명시."""
    if not text or not patient_name:
        return text or ""
    return re.sub(re.escape(patient_name), "환자", text)


def extract_chunk_from_analysis(row: dict, patient_name: str | None) -> dict | None:
    """ai_analysis 테이블의 한 행 → 검색 가능한 chunk 1개. summary + 주요 위험요인 +
    향후위험 서술을 하나로 합쳐 "이 시점 환자 패턴"을 검색 가능하게 만든다."""
    report = {}
    if row.get("report_json"):
        try:
            report = json.loads(row["report_json"])
        except json.JSONDecodeError:
            report = {}

    parts = [
        row.get("summary") or "",
        report.get("주요_위험_요인", ""),
        report.get("향후_위험_가능성", ""),
    ]
    text = " | ".join(p for p in parts if p)
    text = _strip_identifiers(text, patient_name)
    if not text.strip():
        return None

    return {
        "chunk_id": f"analysis::{row['id']}",
        "analysis_id": row["id"],
        "patient_id": row["patient_id"],
        "analysis_type": row.get("analysis_type") or "",
        "risk_score": row.get("risk_score"),
        "created_at": row.get("created_at") or "",
        "original_text": text,
    }


def _to_lance_record(chunk: dict, vector: list[float]) -> dict:
    rec = {k: str(chunk.get(k, "") or "") for k in _CHUNK_FIELDS}
    rec["risk_score"] = float(chunk.get("risk_score") or 0.0)
    rec["vector"] = vector
    return rec


def build_index(patient_id: str, force: bool = False) -> dict:
    """한 환자의 과거 ai_analysis 이력 전부를 chunk화 + embedding해 LanceDB
    (server/lancedb_store/analysis_chunks)에 저장. 반환 shape은 paper_kb.build_index()와
    동일한 정직한 실패 패턴. force=True면 이 환자의 기존 chunk만 지우고 다시 만든다."""
    patient = db.get_patient(patient_id)
    patient_name = (patient or {}).get("name")

    rows = db.list_ai_analysis(patient_id, limit=1000)
    chunks = []
    for r in rows:
        c = extract_chunk_from_analysis(r, patient_name)
        if c:
            chunks.append(c)

    if not chunks:
        return {"success": False, "error_type": "no_analyses",
                "message": f"{patient_id}에 대한 ai_analysis 기록이 없습니다."}

    db_conn = paper_kb._connect()

    if force and paper_kb._table_exists(db_conn, ANALYSIS_TABLE):
        tbl = db_conn.open_table(ANALYSIS_TABLE)
        tbl.delete(f"patient_id = '{patient_id}'")

    existing_ids: set[str] = set()
    if paper_kb._table_exists(db_conn, ANALYSIS_TABLE):
        tbl = db_conn.open_table(ANALYSIS_TABLE)
        existing_ids = {r["chunk_id"] for r in tbl.to_arrow().to_pylist()
                         if r["patient_id"] == patient_id}

    to_embed = [c for c in chunks if c["chunk_id"] not in existing_ids]
    if to_embed:
        result = paper_kb.embed_texts([c["original_text"] for c in to_embed])
        if not result["success"]:
            return {"success": False, **{k: v for k, v in result.items() if k != "success"},
                    "n_chunks": len(chunks), "n_embedded": len(existing_ids),
                    "note": "chunk 추출은 완료, 임베딩만 실패 — 이미 임베딩된 chunk는 그대로 검색 가능."}
        records = [_to_lance_record(c, vec) for c, vec in zip(to_embed, result["embeddings"])]
        if paper_kb._table_exists(db_conn, ANALYSIS_TABLE):
            db_conn.open_table(ANALYSIS_TABLE).add(records)
        else:
            db_conn.create_table(ANALYSIS_TABLE, data=records)

    tbl = db_conn.open_table(ANALYSIS_TABLE)
    n_embedded = sum(1 for r in tbl.to_arrow().to_pylist() if r["patient_id"] == patient_id)
    return {"success": True, "n_chunks": len(chunks), "n_embedded": n_embedded,
            "n_newly_embedded": len(to_embed)}


def search_similar_patterns(patient_id: str, query: str, top_k: int = 5) -> dict:
    """"현재 환자와 유사한 과거 분석 패턴" 검색 — paper_chunks와 완전히 분리된 LanceDB
    테이블 analysis_chunks에서만, 그리고 이 환자 것만 검색한다(SECTION 22 원칙: 논문
    컬렉션과 섞지 않음)."""
    db_conn = paper_kb._connect()
    if not paper_kb._table_exists(db_conn, ANALYSIS_TABLE):
        return {"success": False, "error_type": "no_index",
                "message": "analysis_kb.build_index()를 먼저 실행해야 합니다."}

    tbl = db_conn.open_table(ANALYSIS_TABLE)
    n_candidates = tbl.count_rows(f"patient_id = '{patient_id}'")
    if n_candidates == 0:
        return {"success": False, "error_type": "no_embeddings",
                "message": f"{patient_id}에 대한 임베딩된 분석 이력이 없습니다."}

    q_result = paper_kb.embed_texts([query])
    if not q_result["success"]:
        return q_result

    q_vec = q_result["embeddings"][0]
    rows = (tbl.search(q_vec).metric("cosine")
            .where(f"patient_id = '{patient_id}'")
            .limit(top_k).to_list())
    return {
        "success": True, "query": query, "n_candidates": n_candidates,
        "results": [
            {"score": round(1 - r["_distance"], 4), "analysis_id": r["analysis_id"],
             "created_at": r["created_at"], "risk_score": r["risk_score"],
             "original_text": r["original_text"]}
            for r in rows
        ],
    }


# ── 단독 테스트 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== analysis_kb.py 자체 테스트 (2026-09-10 복구본, LanceDB) ===\n")

    result = build_index("bed_01")
    print("[build_index]", {k: v for k, v in result.items() if k != "message"}, result.get("message", ""))

    if result.get("n_embedded", 0) > 0:
        sr = search_similar_patterns("bed_01", "낙상 위험이 높은 상태", top_k=3)
        print("\n[search_similar_patterns]", sr.get("success"))
        for r in sr.get("results", []):
            print(f"  {r['score']:.3f}  analysis_id={r['analysis_id']}  risk={r['risk_score']}  "
                  f"{r['original_text'][:60]}")
    else:
        print("\n[search] 임베딩된 분석 이력이 없어 검색 예시 생략.")
