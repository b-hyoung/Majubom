"""
paper_kb.py — 12번 탭(시스템 확장) STEP 13-17: 논문 Knowledge Base → chunking → embedding → 검색
====================================================================================
2026-09-10 복구 메모: git reset/clean으로 유실됐던 파일을 재작성. 아래 add_lightweight_paper()
호출들(치매 임상 논문 45편 등록분)과 핵심 로직은 이전 세션에서 실제로 검증됐던 것과 동일하게
복원했다. server/lancedb_store/(임베딩 전체)와 server/paper_chunks.json(레거시 마이그레이션
원본)은 함께 유실되어 다시 만들 수 없으므로, build_index()를 재실행해 OpenAI에 재임베딩을
요청한다(text-embedding-3-small은 613 chunk 기준 비용이 사실상 무시 가능한 수준).

사용자 지침(확인됨): 기술/방법론 논문(ToF·mmWave·센서융합·오탐감소 등, 설계 결정을 직접 뒷받침하는
논문)은 전부 docs/papers/paper-NN.html — "초록 verbatim 번역 + 사실기반 요약 + 사용한 부분(20개
필드)" 페이지를 먼저 만든 뒤에야 chunking 대상이 된다. 반면 치매 임상/증상/행동 관련 논문은 번역
페이지 없이 초록·핵심사실만 뽑아 바로 지식베이스에 넣는 가벼운 방식(lightweight)을 쓴다(사용자
지시: "치매 환자 관련된 논문들은 번역본 안 만들어도 돼").

이 모듈이 만드는 것:
  1) extract_chunks_from_paper(path) — paper-NN.html 1개를 의미 단위 chunk 리스트로 분해
  2) add_lightweight_paper(...) — 번역페이지 없는 논문(주로 치매 임상 논문)을 초록/핵심사실만으로 등록
  3) embed_texts(texts) — OpenAI Embeddings API 호출(ai_report.py와 동일한 정직한 실패 패턴)
  4) build_index() — 위 두 경로로 모은 chunk를 LanceDB 테이블(server/lancedb_store/paper_chunks)에 저장
  5) search(query, top_k) — LanceDB의 벡터 검색(코사인 거리)으로 상위 chunk 반환

Vector DB — LanceDB(무료·오픈소스 Apache 2.0, 서버 프로세스 없이 파일 하나(폴더)로 동작하는
임베디드 벡터DB). 근사 최근접 검색·메타데이터 필터링 같은 진짜 벡터DB 기능을 쓸 수 있다.
"""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone

import lancedb
import requests
from rank_bm25 import BM25Okapi

import ai_report  # get_api_key() 재사용 — 키 로딩 로직을 중복 구현하지 않는다

HERE = os.path.dirname(os.path.abspath(__file__))
PAPERS_DIR = os.path.join(HERE, "..", "docs", "papers")
LEGACY_JSON_PATH = os.path.join(HERE, "paper_chunks.json")  # 마이그레이션 원본(유실됨 — 존재하면만 사용)
LANCEDB_DIR = os.path.join(HERE, "lancedb_store")
PAPERS_TABLE = "paper_chunks"

EMBEDDING_URL = "https://api.openai.com/v1/embeddings"
EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


def _connect():
    return lancedb.connect(LANCEDB_DIR)


def _table_exists(db_conn, name: str) -> bool:
    return name in db_conn.list_tables().tables


# ── 1) Chunking — paper-NN.html 1개 → chunk 리스트 ──────────────────────
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    """이 프로젝트가 직접 만든 paper-NN.html 전용 — 태그 제거 후 공백 정리."""
    return html.unescape(_TAG_RE.sub(" ", s)).strip()
_WS_RE = re.compile(r"\s+")


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", _strip_tags(s)).strip()


def extract_chunks_from_paper(path: str) -> list[dict]:
    """paper-NN.html 1개를 의미 단위 chunk로 분해. 각 chunk는 사용자 요구사항(12번 탭 STEP 13)의
    메타데이터(paper_id, title, authors, year, doi, source_url, chunk_id, original_text 등)를 갖는다."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    paper_id = os.path.splitext(os.path.basename(path))[0]  # "paper-01"

    def find(pattern: str, default: str = "") -> str:
        m = re.search(pattern, raw, re.S)
        return _clean(m.group(1)) if m else default

    title_en = find(r'<div class="paper-title-en">(.*?)</div>')
    title_ko = find(r'<div class="paper-title-ko">(.*?)</div>')
    meta = find(r'<div class="paper-meta">(.*?)</div>')
    src_url_m = re.search(r'<a class="src-link" href="([^"]+)"', raw)
    source_url = src_url_m.group(1) if src_url_m else ""
    year_m = re.search(r"\b(19|20)\d{2}\b", meta)
    year = year_m.group(0) if year_m else ""
    doi_m = re.search(r"DOI\s+([^\s<·]+)", meta)
    doi = doi_m.group(1) if doi_m else ""
    authors = meta.split("<br>")[0] if "<br>" in meta else ""
    authors = _clean(authors)

    base_meta = {
        "paper_id": paper_id, "title": title_en, "title_ko": title_ko,
        "authors": authors, "year": year, "doi": doi, "source_url": source_url,
    }

    chunks: list[dict] = []

    def add_chunk(section: str, text: str, extra: dict | None = None):
        text = text.strip()
        if not text:
            return
        cid = f"{paper_id}::{section}::{len(chunks)}"
        d = dict(base_meta)
        d.update({"chunk_id": cid, "section": section, "original_text": text})
        if extra:
            d.update(extra)
        chunks.append(d)

    # ① 제목/메타 — 논문 식별 자체가 검색 가능해야(예: "이 논문이 뭐였지" 같은 질의)
    add_chunk("identity", f"{title_en} ({title_ko}) — {authors}, {meta}")

    # ② 초록 (한글 번역만 사용 — 영문 원문은 별도 언어라 같은 임베딩 공간에서 중복 신호가 되기 쉬움)
    m = re.search(r'<p class="quote-ko">(.*?)</p>', raw, re.S)
    if m:
        add_chunk("abstract_ko", _clean(m.group(1)))

    # ③ 핵심 내용 요약 — box.summary 안의 각 <p>를 개별 chunk로(문단별로 검색 정밀도를 높임)
    summary_block_m = re.search(r'<div class="box summary">(.*?)</div>\s*</div>', raw, re.S)
    if summary_block_m:
        for p_m in re.finditer(r"<p>(.*?)</p>", summary_block_m.group(1), re.S):
            add_chunk("summary", _clean(p_m.group(1)))

    # ④ "사용한 부분" 표 — 필드(th)별로 1 chunk, 필드명을 텍스트 앞에 붙여 문맥 유지
    table_m = re.search(r'<table class="fieldtbl">(.*?)</table>', raw, re.S)
    if table_m:
        for row_m in re.finditer(r"<tr><th>(.*?)</th><td[^>]*>(.*?)</td></tr>", table_m.group(1), re.S):
            field = _clean(row_m.group(1))
            value = _clean(row_m.group(2))
            add_chunk("used_part", f"{field}: {value}", extra={"field": field})

    return chunks


def build_all_chunks() -> list[dict]:
    """docs/papers/paper-*.html 전부를 chunk화(기술/방법론 논문 — 번역페이지 있는 것만)."""
    chunks: list[dict] = []
    if not os.path.isdir(PAPERS_DIR):
        return chunks
    for fn in sorted(os.listdir(PAPERS_DIR)):
        if re.match(r"^paper-\d+\.html$", fn):
            chunks.extend(extract_chunks_from_paper(os.path.join(PAPERS_DIR, fn)))
    return chunks


# ── 1b) 가벼운 논문 등록 — 번역페이지 없이 초록/핵심사실만(치매 임상 논문 등) ─
LIGHTWEIGHT_PAPERS: list[dict] = []  # add_lightweight_paper()가 채움, build_all_chunks_lightweight()가 읽음


def add_lightweight_paper(paper_id: str, title: str, title_ko: str, authors: str, year: str,
                           doi: str, source_url: str, abstract_ko: str, key_facts: str = "") -> None:
    """번역 페이지(paper-NN.html)를 만들지 않고 논문 1편을 지식베이스 등록 대상에 추가한다.
    abstract_ko는 초록의 한국어 번역(또는 요약), key_facts는 검색에 도움이 될 핵심 수치·방법 요약.
    실제 chunk 생성·임베딩은 build_index()가 build_all_chunks_lightweight()를 통해 처리한다."""
    LIGHTWEIGHT_PAPERS.append({
        "paper_id": paper_id, "title": title, "title_ko": title_ko, "authors": authors,
        "year": year, "doi": doi, "source_url": source_url,
        "abstract_ko": abstract_ko, "key_facts": key_facts,
    })


def build_all_chunks_lightweight() -> list[dict]:
    """LIGHTWEIGHT_PAPERS에 등록된 논문들을 chunk화. 번역페이지 논문(identity/abstract_ko/summary/
    used_part 4종 section)보다 훨씬 단순하게 identity+abstract 2개 chunk만 만든다 —
    "사용한 부분" 20개 필드 같은 상세 분석은 이 경로에서는 만들지 않는다(사용자 지시)."""
    chunks: list[dict] = []
    for p in LIGHTWEIGHT_PAPERS:
        base_meta = {
            "paper_id": p["paper_id"], "title": p["title"], "title_ko": p["title_ko"],
            "authors": p["authors"], "year": p["year"], "doi": p["doi"],
            "source_url": p["source_url"], "lightweight": True,
        }
        chunks.append({**base_meta, "chunk_id": f"{p['paper_id']}::identity::0", "section": "identity",
                        "original_text": f"{p['title']} ({p['title_ko']}) — {p['authors']}, {p['year']}"})
        text = p["abstract_ko"]
        if p.get("key_facts"):
            text += " | 핵심사실: " + p["key_facts"]
        chunks.append({**base_meta, "chunk_id": f"{p['paper_id']}::abstract_ko::0", "section": "abstract_ko",
                        "original_text": text})
    return chunks


# ── 2) Embedding — ai_report.py와 동일한 정직한 실패 패턴 ────────────────
def embed_texts(texts: list[str], model: str = EMBEDDING_MODEL, timeout: float = 60.0) -> dict:
    """OpenAI Embeddings API 호출. 반환: {"success":bool, "embeddings":[[float,...],...]} 또는
    ai_report.call_openai()과 동일한 {"success":False,"error_type":...,"message":...}.
    키가 없으면 절대 가짜 벡터를 만들지 않고 정직하게 실패를 반환한다."""
    key = ai_report.get_api_key()
    if not key:
        return {
            "success": False, "error_type": "missing_key",
            "message": ".env 또는 환경변수에 OPENAI_API_KEY가 설정되어 있지 않습니다. "
                       "코드 문제가 아니라 키 미설정입니다 — 이 경우 검색은 임베딩 없이 동작할 수 없습니다.",
        }
    if not texts:
        return {"success": True, "embeddings": []}

    try:
        resp = requests.post(
            EMBEDDING_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "input": texts},
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return {"success": False, "error_type": "timeout",
                "message": f"OpenAI Embeddings API 응답 시간 초과({timeout}s) — 네트워크 문제일 가능성이 높습니다."}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error_type": "network",
                "message": "OpenAI Embeddings API 연결 실패 — 네트워크/방화벽 문제일 가능성이 높습니다."}
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
                "message": f"OpenAI Embeddings API 오류 응답: {snippet}"}

    try:
        data = resp.json()
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return {"success": False, "error_type": "parse_error",
                "message": f"응답 파싱 실패: {type(e).__name__}"}

    return {"success": True, "embeddings": vectors, "model": model,
            "usage": data.get("usage")}


# ── 3) 인덱스 구축 — LanceDB(server/lancedb_store/paper_chunks) ─────────
_CHUNK_FIELDS = ("chunk_id", "paper_id", "title", "title_ko", "authors", "year",
                  "doi", "source_url", "section", "original_text")


def _to_lance_record(chunk: dict, vector: list[float]) -> dict:
    rec = {k: str(chunk.get(k, "") or "") for k in _CHUNK_FIELDS}
    rec["lightweight"] = bool(chunk.get("lightweight", False))
    rec["vector"] = vector
    return rec


def _migrate_legacy_json_if_needed(db_conn) -> int | None:
    """예전 JSON 파일(server/paper_chunks.json)에 이미 임베딩해둔 chunk가 있으면 재임베딩 없이
    그대로 LanceDB 테이블로 옮긴다(API 비용 재발생 방지). 2026-09-10 유실 사고로 이 파일 자체가
    없어져, 지금은 항상 None을 반환하고 build_index()가 전량 재임베딩한다."""
    if _table_exists(db_conn, PAPERS_TABLE):
        return None
    if not os.path.exists(LEGACY_JSON_PATH):
        return None
    with open(LEGACY_JSON_PATH, "r", encoding="utf-8") as f:
        legacy = json.load(f)
    records = [_to_lance_record(c, c["embedding"]) for c in legacy.get("chunks", []) if c.get("embedding")]
    if not records:
        return None
    db_conn.create_table(PAPERS_TABLE, data=records)
    return len(records)


def build_index(force: bool = False) -> dict:
    """chunk 추출(번역페이지 + lightweight 등록분) + (키가 있으면) 임베딩 생성 후 LanceDB에 저장.
    반환: {"success":bool, "n_chunks":int, "n_embedded":int, ...} 또는 embed_texts()와 동일한 실패 shape.
    force=False이면 이미 임베딩된 chunk_id는 재임베딩하지 않아 API 비용을 절약한다."""
    chunks = build_all_chunks() + build_all_chunks_lightweight()
    if not chunks:
        return {"success": False, "error_type": "no_papers",
                "message": f"{PAPERS_DIR}에 논문이 없고 lightweight 등록분도 없습니다."}

    db_conn = _connect()
    _migrate_legacy_json_if_needed(db_conn)

    if force and _table_exists(db_conn, PAPERS_TABLE):
        db_conn.drop_table(PAPERS_TABLE)

    existing_ids: set[str] = set()
    if _table_exists(db_conn, PAPERS_TABLE):
        tbl = db_conn.open_table(PAPERS_TABLE)
        existing_ids = {r["chunk_id"] for r in tbl.to_arrow().to_pylist()}

    to_embed = [c for c in chunks if c["chunk_id"] not in existing_ids]
    if to_embed:
        result = embed_texts([c["original_text"] for c in to_embed])
        if not result["success"]:
            n_embedded = len(existing_ids)
            return {"success": False, **{k: v for k, v in result.items() if k != "success"},
                    "n_chunks": len(chunks), "n_embedded": n_embedded,
                    "note": "chunk 추출은 완료, 임베딩만 실패 — 이미 임베딩된 chunk는 그대로 검색 가능."}
        records = [_to_lance_record(c, vec) for c, vec in zip(to_embed, result["embeddings"])]
        if _table_exists(db_conn, PAPERS_TABLE):
            db_conn.open_table(PAPERS_TABLE).add(records)
        else:
            db_conn.create_table(PAPERS_TABLE, data=records)

    tbl = db_conn.open_table(PAPERS_TABLE)
    return {"success": True, "n_chunks": len(chunks), "n_embedded": tbl.count_rows(),
            "n_newly_embedded": len(to_embed)}


def resolve_chunks(chunk_ids: list[str]) -> dict:
    """12번 탭 STEP 26 — 근거 추적. ai_analysis.evidence_ids_json에 저장된 chunk_id 목록을
    받아 원문·출처(paper_id/title/section/original_text/source_url/doi)까지 되짚어 반환한다.
    임베딩 벡터 자체는 응답에 포함하지 않는다(불필요하게 크고, 호출자가 쓸 일이 없음)."""
    db_conn = _connect()
    if not _table_exists(db_conn, PAPERS_TABLE):
        return {"success": False, "error_type": "no_index",
                "message": "paper_kb.build_index()를 먼저 실행해야 합니다."}
    tbl = db_conn.open_table(PAPERS_TABLE)
    by_id = {r["chunk_id"]: r for r in tbl.to_arrow().to_pylist()}

    found, missing = [], []
    for cid in chunk_ids:
        r = by_id.get(cid)
        if r is None:
            missing.append(cid)
            continue
        found.append({k: v for k, v in r.items() if k != "vector"})

    return {"success": True, "found": found, "missing_chunk_ids": missing}


def search(query: str, top_k: int = 5) -> dict:
    """query를 임베딩해 LanceDB에서 코사인 거리 기준 top_k를 반환(거리를 1-거리=유사도로 환산).
    인덱스가 없거나 비어있으면(키 미설정 등) 정직하게 실패를 반환한다 —
    "검색 결과 없음"과 "애초에 검색할 수 없음"을 절대 같은 것처럼 보이게 하지 않는다."""
    db_conn = _connect()
    if not _table_exists(db_conn, PAPERS_TABLE):
        return {"success": False, "error_type": "no_index",
                "message": "paper_kb.build_index()를 먼저 실행해야 합니다."}
    tbl = db_conn.open_table(PAPERS_TABLE)
    n_candidates = tbl.count_rows()
    if n_candidates == 0:
        return {"success": False, "error_type": "no_embeddings",
                "message": "인덱스에 임베딩된 chunk가 없습니다(OPENAI_API_KEY 미설정 상태로 build_index()가 "
                           "실행됐을 가능성) — 키를 설정한 뒤 build_index()를 다시 실행하세요."}

    q_result = embed_texts([query])
    if not q_result["success"]:
        return q_result

    q_vec = q_result["embeddings"][0]
    rows = tbl.search(q_vec).metric("cosine").limit(top_k).to_list()
    return {
        "success": True, "query": query, "n_candidates": n_candidates,
        "results": [
            {"score": round(1 - r["_distance"], 4), "chunk_id": r["chunk_id"], "paper_id": r["paper_id"],
             "title": r["title"], "section": r["section"], "original_text": r["original_text"],
             "source_url": r["source_url"], "doi": r["doi"]}
            for r in rows
        ],
    }


def search_hybrid(query: str, top_k: int = 5, k_rrf: int = 60) -> dict:
    """벡터 검색(cosine) + BM25(키워드) 결과를 Reciprocal Rank Fusion(RRF)으로 결합한
    하이브리드 검색. 2026-09 문헌조사 근거: Sawarkar, Mangal & Solanki (2024), "Blended
    RAG: Improving RAG Accuracy with Semantic Search and Hybrid Query-Based Retrievers",
    arXiv:2404.07220 — dense+sparse 하이브리드가 데이터셋에 따라 순수 dense 단독 대비
    NDCG@10을 유의미하게 개선한다고 보고(그들의 Elasticsearch+ELSER 파이프라인 실험이며
    우리 자료가 아니므로 "그러니 낫다"가 아니라 "그래서 우리 데이터로 직접 비교할 가치가
    있다"). 정답셋(Recall@5) 기반 정량 비교는 아직 미실시 — 이 함수는 search()를 대체하지
    않고 별도 옵션으로 추가했다(ai_agent.py는 여전히 search()를 사용).

    RRF 자체는 Cormack, Clarke & Buettcher (2009)의 표준 랭킹 결합 공식(1/(k+rank) 합산)이며
    학습이 필요 없다 — 우리에게 없는 '융합 학습용 라벨 데이터'가 필요 없다는 뜻."""
    db_conn = _connect()
    if not _table_exists(db_conn, PAPERS_TABLE):
        return {"success": False, "error_type": "no_index",
                "message": "paper_kb.build_index()를 먼저 실행해야 합니다."}
    tbl = db_conn.open_table(PAPERS_TABLE)
    all_rows = [r for r in tbl.to_arrow().to_pylist()]
    n_candidates = len(all_rows)
    if n_candidates == 0:
        return {"success": False, "error_type": "no_embeddings",
                "message": "인덱스에 임베딩된 chunk가 없습니다(OPENAI_API_KEY 미설정 상태로 build_index()가 "
                           "실행됐을 가능성) — 키를 설정한 뒤 build_index()를 다시 실행하세요."}

    q_result = embed_texts([query])
    if not q_result["success"]:
        return q_result
    q_vec = q_result["embeddings"][0]

    # 1) 벡터 랭킹 — 전체 후보를 코사인 유사도로 정렬(순위만 쓰므로 전체가 필요)
    vec_rows = tbl.search(q_vec).metric("cosine").limit(n_candidates).to_list()
    vec_rank = {r["chunk_id"]: i for i, r in enumerate(vec_rows)}

    # 2) BM25 랭킹 — 저장된 원문으로 즉석 색인(613개 규모라 매 호출 재구축해도 가벼움)
    by_id = {r["chunk_id"]: r for r in all_rows}
    corpus_ids = [r["chunk_id"] for r in all_rows]
    bm25 = BM25Okapi([r["original_text"].lower().split() for r in all_rows])
    bm25_scores = bm25.get_scores(query.lower().split())
    bm25_order = sorted(range(len(corpus_ids)), key=lambda i: -bm25_scores[i])
    bm25_rank = {corpus_ids[i]: rank for rank, i in enumerate(bm25_order)}

    # 3) RRF 결합 — score = sum(1/(k_rrf + rank+1))
    fused = sorted(
        corpus_ids,
        key=lambda cid: -(
            (1.0 / (k_rrf + vec_rank[cid] + 1) if cid in vec_rank else 0.0)
            + (1.0 / (k_rrf + bm25_rank[cid] + 1) if cid in bm25_rank else 0.0)
        ),
    )

    results = []
    for cid in fused[:top_k]:
        r = by_id[cid]
        rrf = ((1.0 / (k_rrf + vec_rank[cid] + 1) if cid in vec_rank else 0.0)
               + (1.0 / (k_rrf + bm25_rank[cid] + 1) if cid in bm25_rank else 0.0))
        results.append({"score": round(rrf, 5), "chunk_id": cid, "paper_id": r["paper_id"],
                         "title": r["title"], "section": r["section"], "original_text": r["original_text"],
                         "source_url": r["source_url"], "doi": r["doi"],
                         "vector_rank": vec_rank.get(cid), "bm25_rank": bm25_rank.get(cid)})
    return {"success": True, "query": query, "n_candidates": n_candidates,
            "method": "hybrid_rrf_v1", "results": results}


# ── 2026-09 1차 확장(14→26편) — 기술논문 5편은 paper-15~19.html + docs/papers, 치매 임상 7편 경량 ──
add_lightweight_paper(
    paper_id="dementia-01",
    title="Gait characteristics and factors associated with fall risk in patients with dementia with Lewy bodies",
    title_ko="루이소체 치매 환자의 보행 특성과 낙상위험 관련요인",
    authors="Zhou Su, Mengran Liu, Jun Kuai, Tingting Yi, Yuechang Zheng, Congcong Wang, Junyu Peng, Xiaojun Tian",
    year="2025", doi="10.3389/fneur.2025.1670016",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12504080/",
    abstract_ko="루이소체 치매(DLB) 환자 63명을 대상으로 압력감지 보행로로 보행속도·보폭·대칭성·유각기 시간을 측정하고 "
                "인지기능과의 관계를 분석한 횡단연구. 인지기능이 낮을수록 보행속도가 유의하게 느려졌으며, 인지장애가 "
                "가장 심한 환자군은 보행속도가 22% 감소하고 보폭 단축·보행 대칭성 저하를 보였다. 보행속도 표준편차 "
                "1단위 감소마다 낙상위험이 33% 증가했고, 보폭 1cm 감소마다 낙상위험이 21% 증가했다. 반복 낙상 경험 "
                "환자는 보행지표가 유의하게 더 나빴다.",
    key_facts="보행속도 SD 1 감소당 낙상위험 +33%, 보폭 1cm 감소당 낙상위험 +21%, 중증 인지장애군 보행속도 -22%",
)
add_lightweight_paper(
    paper_id="dementia-02",
    title="Association Between Sedative-Hypnotic Medication Use and Fall-Related Emergency Department Visits Among Older Adults",
    title_ko="노인의 진정수면제 사용과 낙상관련 응급실 방문의 연관성",
    authors="Akinyele Oladimeji, Kwasi A. Opoku, Angel Dockery, Dana Alcin, Victor C. Ofochukwu, Azeberoje Osueni, Emeka K. Okobi",
    year="2026", doi="10.7759/cureus.107830",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13215657/",
    abstract_ko="2014~2020년 미국 국가병원외래의료조사(NHAMCS) 응급실 자료를 이용해 65세 이상 성인 21,227건(가중치 "
                "적용 시 약 1억 5,579만 건)의 방문을 분석한 후향적 단면연구. 낙상관련 방문은 고령층과 치매 동반 "
                "환자에서 더 흔했다. 다변량 보정 후 진정수면제 사용은 낙상관련 응급실 방문의 낮은 오즈비와 "
                "연관되었다(보정 오즈비 0.74, 95% CI 0.55–0.98, p=0.034) — 저자들은 잔여교란 가능성을 언급하며 "
                "종단연구의 필요성을 제기했다. 연령 증가와 치매 동반은 낙상관련 방문 오즈를 높이는 요인이었다.",
    key_facts="치매 동반 시 낙상관련 응급실 방문 오즈 증가, 진정수면제 사용 보정오즈비 0.74(95% CI 0.55-0.98) — "
              "'진정제=낙상위험 단순증가'로 단정할 수 없는 반례 데이터",
)
add_lightweight_paper(
    paper_id="dementia-03",
    title="Use of Medicines with Anticholinergic and Sedative Effect Before and After Initiation of Anti-Dementia Medications",
    title_ko="항치매약 시작 전후 항콜린성·진정효과 약물 사용 변화",
    authors="Svetla Gadzhanova, Elizabeth Roughead, Maxine Robinson",
    year="2015", doi="10.1007/s40801-015-0012-y",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC4883199/",
    abstract_ko="호주 의약품급여제도(PBS) 청구자료를 이용해 2009~2010년 콜린에스터라제 억제제 또는 메만틴을 처음 "
                "처방받은 65세 이상 24,110명을 대상으로, 항치매약 시작 전후 6개월간 항콜린성·진정효과 약물 사용 "
                "변화를 조사한 후향적 코호트연구. 항치매약 시작 전 30%, 시작 후 36%가 항콜린성 또는 진정효과 "
                "약물을 1개월 이상 사용했다. 6%는 시작 후 이런 약물을 중단했지만, 12%는 이전에 사용한 적 없다가 "
                "항치매약 시작 후 새로 복용을 시작했다. 저자들은 콜린에스터라제 억제제 처방 시 항콜린성 치료 "
                "병용 여부를 재검토할 필요가 있다고 결론지었다.",
    key_facts="치매환자 약 1/3이 항콜린성·진정효과 약물 병용, 항치매약 시작 후 신규 복용 시작 비율 12%",
)
add_lightweight_paper(
    paper_id="dementia-04",
    title="Using Ambient Assisted Living to Monitor Older Adults With Alzheimer Disease: Single-Case Study to Validate the Monitoring Report",
    title_ko="주변감지 기술(AAL)을 이용한 알츠하이머병 노인 모니터링: 모니터링 리포트 검증 단일사례연구",
    authors="Maxime Lussier, Aline Aboujaoudé, Mélanie Couture, Maxim Moreau, Catherine Laliberté, Sylvain Giroux, "
            "Hélène Pigot, Sébastien Gaboury, Kévin Bouchard, Patricia Belchior, Carolina Bottari, Guy Paré, "
            "Charles Consel, Nathalie Bier",
    year="2020", doi="10.2196/20215",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC7695528/",
    abstract_ko="재가 독립생활을 이어가는 알츠하이머병 90세 여성 1인을 490일간 주변감지(AAL) 센서로 모니터링하고, "
                "그 리포트가 실제 방문간호사의 임상 평가와 얼마나 일치하는지를 선형혼합모형으로 분석한 단일사례 "
                "검증연구. 수면·외출·이동성·요리·위생 등 일상활동 항목에서 모니터링 리포트가 보인 추세 변화 "
                "대부분이 간호사가 수집한 임상 정보와 일치했다. 저자들은 AAL 모니터링 리포트가 다른 정보원이 "
                "불완전할 때 임상적 의사결정을 뒷받침할 수 있는 유효하고 임상적으로 유의미한 정보를 제공한다고 "
                "결론지었다.",
    key_facts="490일 장기 모니터링, 활동 추세가 간호사 임상평가와 대부분 일치 — 비접촉 센서 리포트의 임상적 타당성 근거",
)
add_lightweight_paper(
    paper_id="dementia-05",
    title="Human-Centered Ambient and Wearable Sensing for Automated Monitoring in Dementia Care: A Scoping Review",
    title_ko="치매 돌봄에서의 자동 모니터링을 위한 인간중심 주변·웨어러블 센싱: 범위기술 문헌고찰",
    authors="Mason Kadem, Sarah Masri, Anthea Innes, Rong Zheng",
    year="2026", doi="arXiv:2603.05516",
    source_url="https://arxiv.org/abs/2603.05516",
    abstract_ko="2015~2025년 사이 가정·시설 환경에서 치매 환자를 모니터링하는 웨어러블·주변감지 기술에 관한 "
                "실증연구들을 검토한 범위기술 문헌고찰. 다섯 가지 핵심 구현 원칙을 제시한다 — (1) 돌봄제공자를 "
                "대체가 아니라 보강하는 이해관계자 참여형 인간중심 설계, (2) 표준화된 접근 대신 중증도·환경에 "
                "따라 자율성을 지원하는 개인맞춤형·적응형 솔루션, (3) 충분한 교육·지원을 동반한 기존 업무흐름과의 "
                "통합, (4) 특히 거주자·돌봄제공자에 대한 주변감지의 사전적 프라이버시·동의 고려, (5) 정량적 "
                "성과를 갖춘 비용효율적·윤리적·형평적·확장가능한 솔루션. 이 논문은 자동화와 자율성을 높이면서 "
                "치매 돌봄의 복잡한 과제를 다루는 센싱 시스템 개발을 위한 공백과 기회를 확인한다.",
    key_facts="치매 돌봄 센싱 시스템 설계원칙 5가지(인간중심·개인맞춤·업무통합·프라이버시·비용효율) — 우리 시스템의 "
              "설계 방향과 대조 가능한 체크리스트",
)
add_lightweight_paper(
    paper_id="dementia-06",
    title="Dangerous wandering: Elopements of older adults with dementia from long-term care facilities",
    title_ko="위험한 배회: 장기요양시설 치매 노인의 무단이탈(엘로프먼트)",
    authors="Myra A. Aud",
    year="2004", doi="10.1177/153331750401900602",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC10833955/",
    abstract_ko="장기요양시설에서 발생한 치매 노인의 무단이탈(엘로프먼트) 62건의 상황·환경적 위험·부상을 기술한 "
                "탐색적 질적연구. 이탈 보고서에 대한 내용분석 결과, ① 이탈 의도를 보였거나 반복 시도·이탈 이력이 "
                "있는 거주자에 대한 실효성 있는 예방조치 부재, ② 직원의 거주자 위치 인지 부족, ③ 이탈 경보장치의 "
                "비효과적 사용이라는 공통 패턴이 확인되었다.",
    key_facts="무단이탈 62건 분석, 반복 이탈 이력자에 대한 예방조치 미흡·직원의 위치인지 부족·경보장치 오작동이 공통 원인",
)
add_lightweight_paper(
    paper_id="dementia-07",
    title="A Systematic Review of Falls Risk of Frail Patients with Dementia in Hospital: Progress, Challenges, and Recommendations",
    title_ko="병원 내 노쇠·치매 환자의 낙상위험에 관한 체계적 문헌고찰: 진전, 과제, 권고사항",
    authors="Naomi Davey, Eimear Connolly, Paul Mc Elwaine, Sean P. Kennelly",
    year="2024", doi="10.2147/CIA.S400582",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11214555/",
    abstract_ko="2013~2023년 사이 발표된, 노쇠와 치매를 동반한 입원노인 대상 낙상예방 전략에 관한 문헌을 "
                "MEDLINE·Embase·CINAHL·PsycINFO에서 체계적으로 검색한 리뷰. 643건의 초기 검색 결과를 8편으로 "
                "압축했으며, 구조화된 다학제 병상 라운드(SIBR)가 다학제 소통과 케어플래닝 개선을 통해 낙상을 "
                "줄이는 주목할 만한 중재로 확인되었다. 다만 연속 세션이 진행될수록 가족 참여가 감소하는 경향이 "
                "있어 이를 유지할 전략이 필요하다고 지적했다. 저자들은 이 환자군의 인지적·기능적 어려움을 다루는 "
                "환자중심 중재를 옹호하며, 병원 환경에서의 포괄적 연구 확대를 촉구했다.",
    key_facts="643건 중 8편으로 압축된 체계적 리뷰, SIBR(구조화된 다학제 병상라운드)이 낙상감소에 효과적인 중재로 확인",
)

# ── 2026-09 2차 확장(26→46편) — 치매 임상 논문 20편 추가 ──────────────
add_lightweight_paper(
    paper_id="dementia-08",
    title="Behavioural and psychological symptoms of people with dementia in acute hospital settings: a systematic review and meta-analysis",
    title_ko="급성기 병원 치매환자의 행동심리증상(BPSD) 체계적 문헌고찰 및 메타분석",
    authors="Kanthee Anantapong, Aimorn Jiraphan, Warut Aunjitsakul, Katti Sathaporn, Nisan Werachattawan, "
            "Teerapat Teetharatkul, Pakawat Wiwattanaworaset, Nathan Davies, Elizabeth L. Sampson",
    year="2025", doi="10.1093/ageing/afaf013",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11784590/",
    abstract_ko="급성기 병원에 입원한 치매 노인의 행동심리증상(BPSD) 유병률과 위험요인·치료·결과를 파악하기 위해 "
                "Cochrane Library·MEDLINE·PsycINFO를 검색한 체계적 문헌고찰 및 메타분석(2024년 3월까지). "
                "15,101건 중 23개 연구·30편 논문을 포함했다. 메타분석 결과 입원 치매노인의 전체 BPSD 유병률은 "
                "60%(95% CI 43-78%)였으며, 공격성/초조(39%), 수면문제(38%), 섭식문제(36%), 과민성(32%) 순으로 "
                "흔했다. BPSD는 섬망·통증·불편한 처치 증가·향정신성약물 사용·보호자 스트레스 증가와 연관되었고, "
                "환자-직원 상호작용 부족과 단절된 퇴원계획이 응급재입원의 원인이 되었다.",
    key_facts="입원 치매환자 BPSD 유병률 60%, 공격성/초조 39%·수면문제 38%·섭식문제 36%·과민성 32% — 우리 "
              "시스템의 야간 이탈·뒤척임 감지 대상 행동과 직접 겹침",
)
add_lightweight_paper(
    paper_id="dementia-09",
    title="Delirium in Hospitalized Older Adults: A Narrative Review",
    title_ko="입원 노인의 섬망: 서술적 문헌고찰",
    authors="Janani Jeyakanthan, German Corso, Benyame Woldetsadik, Simonia Kotilo, Olubusola Esan",
    year="2026", doi="10.7759/cureus.109991",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13318095/",
    abstract_ko="입원 노인에서 흔하고 심각한 신경정신 증후군인 섬망에 관한 2005~2026년 문헌 서술적 고찰. "
                "섬망은 급성 내과질환 입원환자 상당수, 고관절골절 수술·심장수술 등 주요 수술환자에서는 더 높은 "
                "비율로 발생하며, 재원기간 연장·시설입소·기능저하·장기 인지장애·사망률 증가와 독립적으로 "
                "연관된다. 특히 무기력·위축·반응성 저하로 나타나는 저활동형 섬망은 미인지되는 경우가 많다. "
                "병태생리·위험요인·임상양상·진단체계를 정리하고, 약물적·비약물적 예방·관리 전략을 요약했다.",
    key_facts="섬망은 재원연장·시설입소·장기인지저하·사망률 증가와 독립적 연관, 저활동형 섬망은 특히 놓치기 "
              "쉬움 — 지속적 비접촉 모니터링이 저활동형(무기력) 변화 조기포착에 기여할 수 있는 근거",
)
add_lightweight_paper(
    paper_id="dementia-10",
    title="Effect of Dementia on Postoperative Mortality in Elderly Patients with Hip Fracture",
    title_ko="고령 고관절골절 환자에서 치매가 수술 후 사망률에 미치는 영향",
    authors="Yong-Chan Ha, Yonghan Cha, Jun-Il Yoo, Jiyoon Lee, Young-Kyun Lee, Kyung-Hoi Koo",
    year="2021", doi="10.3346/jkms.2021.36.e238",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC8490792/",
    abstract_ko="2004~2018년 고관절골절 수술을 받은 65세 이상 환자 2,346명(비치매군 2,196명, 치매군 150명)을 "
                "대상으로 한 후향적 연구. 1년 누적 사망률은 비치매군 13.6%, 치매군 24%로 치매군에서 유의하게 "
                "높았다. 다변량분석에서 치매는 연령·성별·동반질환과 함께 독립적 사망 예측인자였으며, 항치매약을 "
                "복용 중인 환자가 복용하지 않은 환자보다 예후가 더 나빴다. 치매는 고관절골절 수술 후 최소 1년 "
                "추적기간 동안 사망의 독립적 위험요인으로 확인되었다.",
    key_facts="고관절골절 수술 후 1년 사망률 비치매군 13.6% vs 치매군 24% — 치매환자 낙상의 결과가 훨씬 "
              "치명적임을 보여주는 정량적 근거",
)
add_lightweight_paper(
    paper_id="dementia-11",
    title="Interventions for preventing and reducing the use of physical restraints of older people in general hospital settings",
    title_ko="일반병원 노인 신체억제 사용 예방·감소를 위한 중재(코크란 리뷰)",
    authors="Jens Abraham, Julian Hirt, Christin Richter, Sascha Köpke, Gabriele Meyer, Ralph Möhler",
    year="2022", doi="10.1002/14651858.CD012476.pub2",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9404383/",
    abstract_ko="일반병원에서 낙상 예방과 문제행동 관리를 위해 사용되는 침상난간·억제대 등 신체억제 사용을 "
                "줄이기 위한 중재를 평가한 코크란 체계적 문헌고찰(2022년 4월까지). 무작위대조시험·통제임상시험 "
                "중 4개 연구(3개는 최소억제 정책 등 조직적 중재, 1개는 낙상위험군 압력센서 경보)가 선정기준을 "
                "충족했다. 결과는 일관되지 않았다 — 한 연구는 양쪽 군 모두 억제 사용이 증가했고, 다른 연구는 "
                "양쪽 모두 소폭 감소, 압력센서는 억제 유병률에 '거의 또는 전혀 영향 없음'으로 나타났다. 저자들은 "
                "'조직적 최소억제 정책 중재가 일반병원의 신체억제를 줄일 수 있는지 확신할 수 없다'고 결론지었다.",
    key_facts="압력센서 경보만으로는 억제대 사용 감소 효과 거의 없음(코크란 리뷰) — 단순 경보를 넘어선 통합적 "
              "접근(AI 통합분석·행동패턴 판단)의 필요성을 뒷받침",
)
add_lightweight_paper(
    paper_id="dementia-12",
    title="Sleep Disorders in Neurodegenerative Diseases with Dementia: A Comprehensive Review",
    title_ko="치매를 동반한 신경퇴행성 질환의 수면장애: 종합 리뷰",
    authors="Natalia Siwecka, Michał Golberg, Dominika Świerczewska, Beata Filipek, Karolina Pendrasik, "
            "Adrianna Bączek-Grzegorzewska, Mariusz Stasiołek, Mariola Świderek-Matysiak",
    year="2025", doi="10.3390/jcm14197119",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12525461/",
    abstract_ko="전세계 5,500만 명 이상에 영향을 미치는 치매는 알츠하이머병·파킨슨병·루이소체 치매·전두측두엽 "
                "치매·혈관성 치매 등 신경퇴행성 질환과 관련된 심각한 인지저하를 동반한다. 대부분의 신경퇴행성 "
                "질환 환자는 불면증·렘수면행동장애·수면관련호흡장애·일주기리듬장애 등 다양한 수면장애를 겪는다. "
                "수면 붕괴와 정신건강 사이에는 양방향 상호작용이 있어, 수면장애가 우울·불안·초조·환각 같은 "
                "신경정신증상을 직접 악화시키고 이런 증상이 다시 수면의 질을 떨어뜨리는 악순환을 만들어 질병 "
                "진행을 가속화하고 삶의 질을 저하시킨다. 주요 신경퇴행성 질환에서의 수면장애 기전과 치료전략, "
                "수면-정신건강 관계를 종합했다.",
    key_facts="치매 환자의 수면장애-신경정신증상 악순환(양방향 상호작용) — 야간 수면/각성 패턴 변화를 지속 "
              "추적하는 것이 조기경보로서 의미가 있음을 뒷받침",
)
add_lightweight_paper(
    paper_id="dementia-13",
    title="Critical spatiotemporal gait parameters for individuals with dementia: A systematic review and meta-analysis",
    title_ko="치매환자의 핵심 시공간 보행 파라미터: 체계적 문헌고찰 및 메타분석",
    authors="Rita Chiaramonte, Matteo Cioni",
    year="2020", doi="10.1142/S101370252130001X",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC8158408/",
    abstract_ko="치매 진행에 따라 영향받거나 변화하는 보행 파라미터를 더 큰 표본에서 규명하고, 단일과제/이중과제 "
                "중 어느 평가조건이 치매의 영향을 더 민감하게 반영하는지 확인하기 위한 메타분석. PubMed·EMBASE·"
                "Cochrane·Scopus·Web of Science를 검색해 최종 9편을 체계적 문헌고찰에 포함했다. 단일과제에서는 "
                "속도·케이던스·활보장·활보시간·활보시간변동성·입각기시간 등 대부분의 시공간 보행지표가 "
                "치매환자와 정상노인을 가장 잘 구분했다. 이중과제에서는 속도·활보장·활보시간변동성만 두 군을 "
                "구분했다. 낙상위험과의 연관성은 활보장 같은 공간지표보다 케이던스·활보시간·활보시간변동성·"
                "입각기시간 같은 시간지표에서 더 강했으며, 이중과제에서는 활보시간변동성만 낙상위험과 연관되었다.",
    key_facts="치매환자 낙상위험과 가장 강하게 연관된 보행지표는 활보시간변동성(stride time variability) — "
              "우리 mmw_logic.py의 z-score 대상 지표 선정에 참고할 수 있는 메타분석 근거",
)
add_lightweight_paper(
    paper_id="dementia-14",
    title="Combining cognitive stimulation therapy and fall prevention exercise (CogEx) in older adults with mild to moderate dementia: a feasibility randomised controlled trial",
    title_ko="경도~중등도 치매 노인 대상 인지자극치료+낙상예방운동 결합(CogEx) 실행가능성 무작위대조시험",
    authors="Elizabeth Binns, Ngaire Kerse, Kathy Peri, Gary Cheung, Denise Taylor",
    year="2020", doi="10.1186/s40814-020-00646-6",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC7382095/",
    abstract_ko="치매 환자는 인지장애가 보행·균형조절에 영향을 미쳐 낙상위험이 높다. 인지자극치료(CST)는 이 "
                "인구집단의 전반적 인지기능을 개선하는 것으로 알려져 있어, CST와 낙상예방 운동을 결합한 "
                "프로그램(CogEx)이 본 시험 실시가 가능한지 평가한 실행가능성 무작위대조시험. 요양시설 거주 "
                "노인 23명(CogEx군 10명, CST대조군 13명)을 7주간 주2회 1시간씩 세션에 배정했다. 결과, CST 구조 "
                "안에 운동을 편성하는 것은 가능했으나 '결합 프로그램의 충실도는 낮았다' — 진행자들이 균형훈련 "
                "효과가 제한적인 착석 자세로 운동을 주로 진행했다. 향후 진행자 교육 강화, 물리치료사 참여, 또는 "
                "다른 전달방식을 검토한 뒤 본 시험을 추진해야 한다고 결론지었다.",
    key_facts="인지자극+낙상예방운동 결합 프로그램은 실제 현장에서 충실도(fidelity)가 낮았음 — 운동중재만으로는 "
              "한계가 있어 지속적 모니터링·조기경보 같은 보완적 접근의 필요성을 뒷받침",
)
add_lightweight_paper(
    paper_id="dementia-15",
    title="Urinary tract infection-related delirium in Alzheimer's disease and related dementias: Clinical challenges and translational opportunities",
    title_ko="알츠하이머병 및 관련 치매에서의 요로감염(UTI) 관련 섬망: 임상적 과제와 중개연구 기회",
    authors="Sarah Kim, Sarah Kremen, Itai Danovitch, Shouri Lahiri",
    year="2026", doi="10.1002/alz.71184",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12865330/",
    abstract_ko="알츠하이머병 및 관련 치매(ADRD)는 전세계 수백만 명에 영향을 미치며 노인 이환의 주요 원인이다. "
                "ADRD 환자는 요로감염(UTI) 및 UTI 관련 섬망에 특히 취약해, 치매가 감염·섬망에 대한 취약성을 "
                "높이고 섬망이 다시 인지·기능저하를 가속화하는 자기지속적 악순환을 만든다. 이 리뷰는 ADRD에서의 "
                "UTI 및 UTI 관련 섬망의 역학·임상적 함의·진단지침을 정리하고, 인터루킨-6 매개 경로를 포함한 "
                "새로 밝혀지는 생물학적 기전과 근거기반 예방·관리 전략을 논의한다.",
    key_facts="치매-감염(UTI)-섬망의 자기지속적 악순환 — 급성 감염이 흔히 '갑작스러운 행동변화·의식저하'로만 "
              "나타나므로, 행동패턴 이상탐지가 감염의 조기 간접신호가 될 수 있음을 시사",
)
add_lightweight_paper(
    paper_id="dementia-16",
    title="Characterizing treatment initiation with central nervous system-active polypharmacy among adults with dementia",
    title_ko="치매환자의 중추신경계(CNS) 활성 다제병용 치료 개시 특성 분석",
    authors="Donovan T. Maust, Rachel C. Davis, Julie Strominger, Steven C. Marcus, Hyungjin Myra Kim, "
            "Tanner Caverly, Frederic C. Blow, Lauren P. Wallner, Sarah Krein, Sarah E. Vordenberg",
    year="2026", doi="10.1002/alz.71608",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13283909/",
    abstract_ko="중추신경계(CNS) 활성 다제병용(항우울제·항정신병제·항경련제·벤조디아제핀·비벤조디아제핀 "
                "수면제·오피오이드·근이완제를 31일 이상 동시 복용으로 정의)은 치매환자에게 상당한 위험을 준다. "
                "2021년 65세 이상 치매 진단·Medicare Part D 가입 지역사회 거주자 1,214,928명(평균연령 81.6세) "
                "중 7.8%(94,190명)가 새로 다제병용을 시작했다. 항우울제가 가장 흔한 계열(92.3%)이었고, 개별 "
                "약물로는 쿠에티아핀(30.9%)·가바펜틴(29.0%)·트라조돈(28.3%)이 상위를 차지했다. 다제병용 시작 "
                "시점에 43.8%는 한 명의 임상의가 모든 CNS 활성 약물을 처방했으며, 대부분은 일차진료의였다.",
    key_facts="치매환자 CNS활성 다제병용 신규발생 7.8%, 상위약물 쿠에티아핀·가바펜틴·트라조돈 — 우리 프로젝트 "
              "101호 환자 페르소나의 투약 목록(트라조돈 포함)과 실제로 겹치는 약물 데이터",
)
add_lightweight_paper(
    paper_id="dementia-17",
    title="An investigation of psychoactive polypharmacy and related gender-differences in older adults with dementia: a retrospective cohort study",
    title_ko="치매 노인의 향정신성 다제병용과 성별 차이에 관한 후향적 코호트연구",
    authors="Shanna C. Trenaman, Jack Quach, Susan K. Bowles, Susan Kirkland, Melissa K. Andrew",
    year="2023", doi="10.1186/s12877-023-04353-8",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC10590009/",
    abstract_ko="치매환자는 도전적 행동반응을 보일 수 있어 약물치료가 흔히 쓰이지만 효과는 제한적이며 "
                "다제병용으로 이어지기 쉽다. 2005~2015년 노바스코샤주 치매진단 노인 15,819명(평균연령 80.7세, "
                "여성 70%)을 대상으로 향정신성 다제병용(30일 이상 동시 3종 이상)을 조사한 후향적 코호트연구. "
                "99.4%가 적어도 1종의 향정신성 약물을 복용했고, 19.3%가 향정신성 다제병용 상태였다. 젊은 "
                "연령일수록 양성별 모두에서 다제병용 위험이 유의하게 높았다. 시탈로프람이 가장 흔히 처방된 "
                "약물이었고, 흔한 조합은 세로토닌계 약물과 항정신병제였다.",
    key_facts="치매환자 99.4%가 향정신성 약물 최소 1종 복용, 19.3%는 다제병용 — 진정제가 낙상위험을 높인다는 "
              "점에서 우리 위험인자 데이터베이스의 '진정제' 항목이 얼마나 흔한 현실인지 뒷받침",
)
add_lightweight_paper(
    paper_id="dementia-18",
    title="Identifying Fallers among Home Care Clients with Dementia and Parkinson's Disease",
    title_ko="치매·파킨슨병 재가돌봄 대상자 중 낙상 위험군 식별",
    authors="Symron Bansal, John P. Hirdes, Colleen J. Maxwell, Alexandra Papaioannou, Lora M. Giangregorio",
    year="2016", doi="10.1017/S0714980816000325",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC5092149/",
    abstract_ko="신경학적 질환이 있는 재가돌봄(HC) 대상자의 낙상에 관한 연구는 드물다. 최근 낙상력이 없는 "
                "재가돌봄 대상자 중 낙상위험을 높이는 요인을 확인하고, 치매·파킨슨증이 있는 경우와 없는 경우의 "
                "위험 프로파일이 다른지 온타리오주 지역사회 재가돌봄 자료(RAI-HC)로 분석한 후향적 코호트연구. "
                "불안정한 보행(unsteady gait)은 세 군 모두에서 낙상의 강력한 예측인자였다. 동반 파킨슨증은 "
                "치매군에서 낙상을 가장 강하게 예측했다. 경계성~경도 인지장애가 있는 대상자는 파킨슨증군과 "
                "대조군에서 낙상 확률이 더 높았다.",
    key_facts="불안정한 보행이 치매·파킨슨증·대조군 모두에서 낙상의 공통적 최강 예측인자 — mmWave 보행 안정성 "
              "지표가 치매 유무와 무관하게 범용적으로 유효한 신호임을 뒷받침",
)
add_lightweight_paper(
    paper_id="dementia-19",
    title="Multidisciplinary approach to reducing falls for people with dementia on an older adult mental health ward",
    title_ko="노인 정신건강 병동 치매환자 낙상 감소를 위한 다학제적 접근",
    authors="Clarissa Sorlie, Gavin Shields, Tracy Connellan, Nathaniel Addo, Marco Aurelio",
    year="2026", doi="10.1136/bmjoq-2025-003988",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13239358/",
    abstract_ko="노인 정신건강 병동에 입원한 치매환자는 낙상위험이 높아 중요한 환자안전 이슈다. 이 질향상 "
                "프로젝트는 2024년 9월까지 병동의 낙상률을 1,000재원일당 평균 5.4건에서 3.7건으로 30% 낮추는 "
                "것을 목표로, 다학제팀이 치료적 참여 강화, 지지적 간호관찰 강화, 약물관련 위험 완화, 병동환경 "
                "개선 등의 중재를 시험했다(Plan-Do-Study-Act 방법론 사용). 결과 목표를 크게 초과해, 낙상률을 "
                "1,000재원일당 1.4건으로 74% 감소시켰다. 다학제적 접근과 신속한 시험·학습을 위한 PDSA 방법론의 "
                "가치를 보여준 프로젝트로 결론지었다.",
    key_facts="다학제 중재로 치매병동 낙상률 74% 감소(5.4→1.4/1000재원일) — 단일 기술이 아니라 관찰강화+"
              "약물관리+환경개선을 결합해야 큰 효과가 난다는 근거, AI 통합분석·리포트가 이런 다학제 의사결정을 "
              "지원하는 도구로 자리매김할 수 있음",
)
add_lightweight_paper(
    paper_id="dementia-20",
    title="Access to a dementia-friendly garden on behavioural and psychological symptoms of dementia, falls and psychotropic medication use in residents of an aged care home in Melbourne, Australia",
    title_ko="치매친화적 정원 접근이 치매환자의 BPSD·낙상·향정신성약물 사용에 미치는 영향(멜버른 요양시설)",
    authors="Rhoda Lai, Mouhamed Foladkar, Gurnik Dhaliwal, Anika Kibria, Rosa C. Gualano, Madeleine L. Healy",
    year="2023", doi="10.1177/10398562231160363",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC10251460/",
    abstract_ko="요양시설 거주자는 실외 접근이 필요하며, 이는 치매환자의 행동심리증상(BPSD)과 삶의 질을 개선할 "
                "수 있다. 접근성 부족과 낙상위험 증가라는 장벽은 치매친화적 설계로 완화될 수 있다. 멜버른의 한 "
                "요양시설에서 치매친화적 정원 개장 후 첫 6개월간 거주자들을 추적한 전향적 코호트연구(참여자 "
                "19명). 신경정신행동검사(NPI-NH)와 향정신성약물 사용을 기준·3개월·6개월 시점에 수집했고, "
                "시설의 낙상률과 직원·보호자 피드백도 함께 수집했다. NPI-NH 총점은 감소했으나 유의하지는 "
                "않았다. 전반적 피드백은 긍정적이었고, 낙상률은 감소했다. 다만 정원 이용률은 낮았다.",
    key_facts="치매친화적 정원 개장 후 낙상률 감소 경향(소규모 파일럿, 통계적 유의성 제한적) — 정원 이용률이 "
              "낮았다는 한계를 정직하게 보고한 점이 특히 참고할 만함",
)
add_lightweight_paper(
    paper_id="dementia-21",
    title="Estimation of the global prevalence of dementia in 2019 and forecasted prevalence in 2050: an analysis for the Global Burden of Disease Study 2019",
    title_ko="2019년 전세계 치매 유병률 추정 및 2050년 예측(GBD 2019 분석)",
    authors="GBD 2019 Dementia Forecasting Collaborators",
    year="2022", doi="10.1016/S2468-2667(21)00249-8",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC8810394/",
    abstract_ko="전세계 및 지역별 치매 유병률을 위험요인 데이터와 인구통계 예측을 이용해 2019년부터 2050년까지 "
                "예측한 연구. 2019년 전세계 치매 환자는 약 5,740만 명으로 추정되었고, 2050년에는 1억 5,280만 "
                "명으로 증가할 것으로 예측되었다. 연령표준화 유병률(양성 합산)은 2019~2050년 사이 거의 변화가 "
                "없었으나(전세계 0.1% 변화) 절대 환자수는 크게 증가했다. 여성 대 남성 비율은 약 1.67:1로 유지될 "
                "것으로 예측되었다. 지역별 편차가 커서, 북아프리카/중동(367%)과 동부 사하라이남 아프리카(357%)"
                "에서 증가폭이 가장 컸고, 고소득 아시아태평양(53%)과 서유럽(74%)에서 가장 작았다. 인구증가와 "
                "고령화가 대부분의 증가를 견인했다.",
    key_facts="2019년 전세계 치매 환자 약 5,740만 명 → 2050년 1억 5,280만 명 예측 — 비접촉 치매 모니터링 "
              "시스템 수요의 거시적 성장 배경 데이터",
)
add_lightweight_paper(
    paper_id="dementia-22",
    title="Gait and Equilibrium in Subcortical Vascular Dementia",
    title_ko="피질하 혈관성 치매의 보행과 평형",
    authors="Rita Moretti, Paola Torre, Rodolfo M. Antonello, Francesca Esposito, Giuseppe Bellini",
    year="2011", doi="10.1155/2011/263507",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC3085296/",
    abstract_ko="피질하 혈관성 치매는 흔하지만 진단과 치료가 까다로운 임상 질환이다. 이 환자들은 고령이며 "
                "동반질환이 많아 여러 약물을 복용하는 경우가 잦다. 2007년 6월부터 2010년 6월까지 거동 가능한 "
                "외래환자 600여 명(68~94세)을 3년간 진단·추적하며, 약물치료 내용을 구체적으로 고려해 "
                "임상·신경학적으로 평가했다. 목적은 보행과 균형장애가 백질병변이나 인지저하 악화와 동시에 "
                "나타나는지를 규명하는 것이었으며, 약물 복용의 영향도 함께 검토되었다.",
    key_facts="피질하 혈관성 치매 600여 명 3년 추적 — 보행·균형 장애가 백질병변 및 인지저하 악화와 함께 "
              "나타나는지를 본 장기 코호트, 약물영향도 함께 고려",
)
add_lightweight_paper(
    paper_id="dementia-23",
    title="Neuropsychiatric or Behavioral and Psychological Symptoms of Dementia (BPSD): Focus on Prevalence and Natural History in Alzheimer's Disease and Frontotemporal Dementia",
    title_ko="치매의 신경정신/행동심리증상(BPSD): 알츠하이머병과 전두측두엽치매의 유병률·자연경과 중심",
    authors="Valentina Laganà, Francesco Bruno, Natalia Altomari, Giulia Bruni, Nicoletta Smirne, Sabrina Curcio, "
            "Maria Mirabelli, Rosanna Colao, Gianfranco Puccio, Francesca Frangipane, Chiara Cupidi, Giusy Torchia, "
            "Gabriella Muraca, Antonio Malvaso, Desirèe Addesi, Alberto Montesanto, Raffaele Di Lorenzo, "
            "Amalia Cecilia Bruni, Raffaele Maletta",
    year="2022", doi="10.3389/fneur.2022.832199",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9263122/",
    abstract_ko="신경정신증상 또는 치매의 행동심리증상(BPSD)은 거의 모든 치매환자에서 질병 경과 중 나타나는 "
                "이질적인 비인지 증상군이다. 행동변형 전두측두엽치매(bvFTD) 674명과 알츠하이머병(AD) 1,925명을 "
                "질병 3단계에 걸쳐 비교분석했다. BPSD는 전체 치매환자의 최대 90%에서 나타나며, 무감동·과민성·"
                "초조/공격성이 주된 특징이었다. 기분장애가 가장 먼저 나타나 진단 지표가 될 수 있었다. "
                "운동관련 행동은 질병 진행에 따라 증가했다. 증상 프로파일은 서로 유사했으나, bvFTD는 환각·"
                "우울·불안·과민성을 제외한 대부분의 증상에서 체계적으로 더 높은 유병률을 보였다.",
    key_facts="치매환자 최대 90%가 BPSD 경험, 기분장애가 가장 먼저 나타나는 조기 지표 — bvFTD는 AD보다 대부분 "
              "증상에서 더 심함(치매 유형별 차이 존재)",
)
add_lightweight_paper(
    paper_id="dementia-24",
    title="The treatment of behavioural and psychological symptoms in dementia: pragmatic recommendations",
    title_ko="치매 행동심리증상(BPSD)의 치료: 실용적 권고안",
    authors="Camille Mercier, Victoria Rollason, Mohamed Eshmawey, Aline Mendes, Giovanni B. Frisoni",
    year="2024", doi="10.1111/psyg.13116",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11578037/",
    abstract_ko="거의 모든 치매환자가 병의 경과 중 행동심리증상(BPSD)을 겪는다. 저자들은 원발성 BPSD(신경생물학적 "
                "원인)와 이차성 BPSD(환경적·기능적 원인)를 구분하고 그에 따라 치료 접근을 달리해야 한다고 "
                "제안하는 치료 워크플로우를 제시한다. 비약물적 중재가 통상 1차 치료가 되어야 하며, 원발성 "
                "증상이나 중증 사례에는 약물적 접근이 필요할 수 있다. 향정신성 약물 도입 시 '낮게 시작해 천천히 "
                "늘리기'와 '처방 후 재검토'의 원칙을 강조한다. 신경전달물질 수용체 프로파일에 기반한 약물 선택, "
                "다제병용 최소화, 3개월마다 치료효과 체계적 재평가를 핵심 권고로 제시한다.",
    key_facts="BPSD 치료는 원발성/이차성 구분 후 접근 — 이차성(환경적·기능적 원인) 파악에는 지속적 행동 "
              "모니터링이 유용할 수 있음, '3개월마다 재평가' 원칙은 baseline 재검증 주기와 유사한 발상",
)
add_lightweight_paper(
    paper_id="dementia-25",
    title="Urinary Tract Infection Induced Delirium in Elderly Patients: A Systematic Review",
    title_ko="노인의 요로감염 유발 섬망: 체계적 문헌고찰",
    authors="Chandrani Dutta, Khadija Pasha, Salomi Paul, Muhammad S. Abbas, Sondos T. Nassar, Tasniem Tasha, "
            "Anjali Desai, Anjana Bajgain, Asna Ali, Lubna Mohammed",
    year="2022", doi="10.7759/cureus.32321",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9827929/",
    abstract_ko="요로감염(UTI)은 영양불량·당뇨조절불량·방광조절저하로 인한 요저류/요실금·변비·장기입원·질위축·"
                "전립선비대·비위생적 생활환경·의식상태변화 등 노화관련 위험요인으로 노인에서 흔한 감염이다. "
                "UTI는 이 연령군에서 발열 없이 섬망·혼돈·어지럼증·졸음·낙상·요실금·식욕부진 등으로 더 비전형적으로 "
                "나타나 진단을 어렵게 만든다. 2017~2022년 문헌에서 최종 9편을 선정한 체계적 문헌고찰 결과, 65세 "
                "이상 노인에서 섬망과 UTI 사이에 '유효한 연관성'이 확인되었다.",
    key_facts="노인 UTI는 발열 없이 섬망·낙상·식욕부진으로 비전형적으로 나타남 — 급성 감염이 겉보기엔 '이유 "
              "없는 행동변화'로만 보일 수 있어, 이상탐지가 실제로는 감염의 첫 신호를 잡아낼 가능성을 시사",
)
add_lightweight_paper(
    paper_id="dementia-26",
    title="The Physical Environment and the Quality of Life and Behavior in People With Dementia: A Systematic Meta-Review",
    title_ko="물리적 환경이 치매환자의 삶의 질과 행동에 미치는 영향: 체계적 메타리뷰",
    authors="Arnout Siegelaar, Mark P. Mobach, Sarah Janus, Sytse U. Zuidema",
    year="2025", doi="10.1177/30495334251345092",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12220851/",
    abstract_ko="치매환자 돌봄환경의 물리적 설계는 신경정신증상을 줄이고 삶의 질을 높이는 데 중요한 요인으로 "
                "점점 더 인정받고 있다. 삶의 질·행동과 관련된 환경설계 사양에 대한 합의된 지식을 근거수준 평가와 "
                "함께 종합한 리뷰. 7개 데이터베이스에서 410편의 고유 리뷰를 검색해 관련성과 근거수준을 평가한 "
                "뒤 체계적 문헌고찰 11편을 최종 선정했다. 다양한 건축적 특징이 삶의 질과 행동에 도움이 되는 "
                "것으로 나타났으나 근거수준은 낮았다. 삶의 질·행동과 관련된 환경설계에 대한 합의된 지식은 "
                "방대하지만 근거의 질은 낮다. 감각의 과잉자극과 감각박탈 사이의 균형을 찾는 것이 치매환자를 "
                "위한 환경설계의 과제이며, 공간 분위기에 변화를 주는 것이 행동과 삶의 질에 유익할 수 있다.",
    key_facts="치매 환경설계 관련 근거의 양은 많지만 질(evidence level)은 낮음 — 환경 중재의 효과를 객관적으로 "
              "정량화하려면 지속적 센서 기반 행동 측정이 필요하다는 논리적 공백을 보여줌",
)
add_lightweight_paper(
    paper_id="dementia-27",
    title="The risk of delirium or dementia-related hospitalization among individuals living with dementia after long-term care entry: A population-based risk prediction model",
    title_ko="장기요양시설 입소 치매환자의 섬망·치매관련 입원 위험: 인구기반 위험예측모델",
    authors="Tesfahun C. Eshetie, Gillian E. Caughey, Catherine Lang, Craig Whitehead, Maria Crotty, Megan Corlis, "
            "Renuka Visvanathan, Maria C. Inacio",
    year="2025", doi="10.1002/alz.70487",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12371554/",
    abstract_ko="장기요양시설(LTCF) 입소 치매환자 중 섬망 또는 치매관련 입원 위험이 높은 대상자를 식별하면 "
                "개인 맞춤형 위험 완화를 지원할 수 있다. 호주 ROSA 국가 코호트(치매환자 207,343명, 2,655개 "
                "LTCF)를 이용해 LTCF 입소 후 365일 이내 섬망 또는 치매관련 입원의 예측인자를 규명하고, "
                "엘라스틱넷 벌점회귀와 Fine-Gray 모델로 위험예측모델을 개발했다. 365일 이내 5.2%(10,709명)가 "
                "섬망 또는 치매관련 입원을 겪었다. 40개 예측인자 중 응급실 빈번 방문 이력, 신체적 폭력 이력, "
                "남성, 과거 섬망 이력이 가장 강력했다. 모델의 AUC는 0.664(95% CI 0.650-0.676)로 적정 수준의 "
                "판별력과 합리적 보정력을 보였다.",
    key_facts="LTCF 입소 치매환자의 5.2%가 365일 내 섬망/치매관련 입원, 최강 예측인자는 응급실 빈번방문·"
              "폭력이력·남성·과거섬망 — 데이터 기반 위험예측모델의 판별력(AUC 0.664)이 아직 중간 수준임을 "
              "정직하게 보고, 센서 기반 실시간 신호를 더하면 개선 여지가 있음을 시사",
)

# ── 2026-09 3차 확장(46→64편) — 치매 임상 논문 18편 추가 ──────────────
add_lightweight_paper(
    paper_id="dementia-28",
    title="Workload-Related Issues among Nurses Caring for Patients with Behavioral and Psychological Symptoms of Dementia: A Scoping Review",
    title_ko="치매 행동심리증상(BPSD) 환자를 돌보는 간호사의 업무부담 문제: 범위기술 문헌고찰",
    authors="Younhee Kang, Chohee Bang",
    year="2024", doi="10.3390/healthcare12181893",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11430937/",
    abstract_ko="치매환자의 문제행동(공격성 등)이 간호 인력의 업무부담에 미치는 영향을 정리한 범위기술 "
                "문헌고찰(2013~2023년 문헌, Arksey & O'Malley 5단계 프레임워크). 병원 환경 치매환자의 "
                "70~95%가 공격성 등 문제행동을 보이며, 이런 행동이 간호사의 스트레스와 업무부담을 크게 늘려 "
                "신체·정신 건강 악화, 소진, 직무만족도 저하, 돌봄의 질 저하로 이어진다. 인력부족과 지원체계 "
                "미비가 문제를 악화시킨다. 저자들은 맞춤형 교육, 충분한 인력배치, 지원체계가 필수적이라고 "
                "결론지었다.",
    key_facts="병원 치매환자 70~95%가 공격성 등 문제행동 — 간호인력 업무부담 증가의 직접 원인, 인력부족이 악화요인",
)
add_lightweight_paper(
    paper_id="dementia-29",
    title="A comprehensive evaluation on the associations between hearing and vision impairments and risk of all-cause and cause-specific dementia",
    title_ko="청력·시력장애와 전체/원인별 치매위험의 연관성에 관한 종합평가(코호트·메타분석·멘델리안 무작위화)",
    authors="Fan Jiang, Qiuyue Dong, Sijia Wu, Xinhui Liu, Alimu Dayimu, Yingying Liu, Hanbing Ji, Le Wang, "
            "Tiemei Liu, Na Li, Xiaofei Li, Peipei Fu, Qi Jing, Chengchao Zhou, Hongkai Li, Lei Xu, Shanquan Chen, "
            "Haibo Wang",
    year="2024", doi="10.1186/s12916-024-03748-7",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11542226/",
    abstract_ko="감각장애와 치매위험의 연관성을 코호트연구·메타분석·멘델리안 무작위화 3가지 방법으로 검증한 "
                "연구. UK 바이오뱅크 90,893명 분석 결과 경도 청력장애는 전체치매 위험을 52%, 중증 청력장애는 "
                "80% 높였고, 시각장애는 55% 위험을 높였다. 청각+시각 이중 감각장애는 위험을 상당히 더 "
                "높였다. 31개 전향연구·937,908명 메타분석도 같은 방향을 확인했고, 멘델리안 무작위화 분석은 "
                "청력장애-치매 간 인과관계를 뒷받침했다. 저자들은 청력·시력 표준화 검사와 중재를 치매예방 "
                "전략에 포함해야 한다고 결론지었다.",
    key_facts="경도 청력장애 치매위험 +52%, 중증 청력장애 +80%, 시각장애 +55% — 감각손상이 단순 동반질환이 "
              "아니라 인과적 위험요인일 가능성(멘델리안 무작위화로 뒷받침)",
)
add_lightweight_paper(
    paper_id="dementia-30",
    title="Definition and Test-Retest Reliability of a Monitoring Method Integrating Accelerometric Actigraphy and Bluetooth Indoor Location Tracking Applied in a Long-Term Residential Unit for Persons With Dementia",
    title_ko="치매 장기요양시설 가속도계 액티그래피+블루투스 실내위치추적 결합 모니터링법 신뢰도",
    authors="Marco Rabuffetti, Pietro Davide Trimarchi, Alessia Gallucci, Ilaria Carpinella, Elena Kisel, "
            "Maria Patrizia Andriani, Ennio De Giovannini, Gaia Bailo, Fabrizio Giunco, Maurizio Ferrarin",
    year="2026", doi="10.2196/70188",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13193664/",
    abstract_ko="웨어러블 가속도계와 블루투스 실내위치추적을 결합한 모니터링 방법을 장기요양시설 치매환자 "
                "25명(19~21명 유효데이터)에게 적용해 3주·3개월 간격으로 반복측정한 종단관찰연구. 동연령대 "
                "건강대조군과 비교해 치매환자군은 격렬한 활동이 '거의 없었고' 중강도 활동시간이 84.3% "
                "감소했다. 수면효율은 대조군과 비슷했다. 거주자 간 활동패턴 동기화(사회적 상호작용 대리지표)는 "
                "53.1% 낮았다. 신체활동·수면효율 지표의 신뢰도는 우수(ICC 0.74~0.98)했으나 위치추적 신뢰도는 "
                "다소 불안정(ICC 0.37~0.78)했다. 일부 개인에서 배회 패턴도 식별했다.",
    key_facts="치매군 중강도 활동시간 대조군 대비 -84.3%, 거주자 간 활동 동기화 -53.1% — 웨어러블+실내위치 "
              "결합 신뢰도 ICC 0.74~0.98(활동) vs 0.37~0.78(위치, 상대적으로 불안정)",
)
add_lightweight_paper(
    paper_id="dementia-31",
    title="A One-Year Study Using Digital Biomarkers From Sensing Technologies to Assess Changes in Physical Activity Levels and Sleep Quality in Nursing Home Residents With Dementia",
    title_ko="센싱기술 디지털 바이오마커로 본 요양시설 치매환자 신체활동·수면질 1년 관찰연구",
    authors="Lydia D. Boyle, Monica Patrascu, Bettina S. Husebo, Kristoffer Haugarvoll, Ole Martin Steihaug, Brice Marty",
    year="2026", doi="10.2196/95194",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13524361/",
    abstract_ko="노르웨이 치매요양시설 2곳의 거주자 11명(79~93세)에게 스마트워치(가민)와 레이더 기반 "
                "수면감지기(Vital Things Somnofy)를 착용·설치해 기준시점·6개월·1년 시점에 각 7일씩 활동량과 "
                "수면의 질을 디지털 바이오마커로 측정한 1년 관찰연구. 야간 활동량(ENMO)과 4개 수면 "
                "바이오마커(총수면시간·수면효율·수면 중 각성시간·수면규칙성지수)에서 유의한 변화가 관찰됐다. "
                "수면 바이오마커의 종단 신뢰도는 중간~우수(0.54~0.92)였으나 활동량 지표의 신뢰도는 낮았다. "
                "순응도는 88~96%로 높았고 부작용은 없었다. 저자들은 임상적 의사결정에 디지털 바이오마커를 "
                "쓰려면 신중하고 잘 설계된 접근이 필요하다고 결론지었다.",
    key_facts="1년 장기추적, 착용순응도 88~96%(부작용 없음), 수면 바이오마커 신뢰도 0.54~0.92 vs 활동량 "
              "지표는 낮은 신뢰도 — 장기 웨어러블 데이터의 신뢰도가 지표마다 다르다는 정직한 보고",
)
add_lightweight_paper(
    paper_id="dementia-32",
    title="Wrist accelerometry for monitoring dementia agitation behaviour in clinical settings: A scoping review",
    title_ko="임상현장에서 치매 초조행동 모니터링을 위한 손목 가속도계 활용: 범위기술 문헌고찰",
    authors="James Chung-Wai Cheung, Bryan Pak-Hei So, Ken Hok Man Ho, Duo Wai-Chi Wong, Alan Hiu-Fung Lam, "
            "Daphne Sze Ki Cheung",
    year="2022", doi="10.3389/fpsyt.2022.913213",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9523077/",
    abstract_ko="치매환자의 초조행동 평가에 손목 가속도계를 활용하는 임상연구들을 정리한 범위기술 문헌고찰. "
                "CINAHL·PubMed·PsycInfo·EMBASE·Web of Science에서 검색해 9편을 선정했다. 가속도계로 측정한 "
                "활동수준(빈도·엔트로피)과 표준 초조행동 평가도구 사이에 유의한 연관성이 확인됐으나, 개별 "
                "초조 에피소드 발생 자체를 탐지하는 정확도는 만족스럽지 않았다. 초조행동이 잘 발생하는 "
                "시간대(주간·저녁)를 파악하는 데도 쓰였다. 저자들은 향후 연구에서 측정 파라미터·컷오프·"
                "측정기간을 표준화할 필요가 있다고 제안했다.",
    key_facts="손목 가속도계 활동수준-초조행동 표준평가도구 간 유의한 연관 확인, 그러나 개별 초조 에피소드 "
              "탐지 정확도는 불충분 — mmWave/ToF의 '활동수준 변화'도 유사한 한계(연관성은 있으나 이벤트 단위 "
              "정밀탐지는 별개 과제)를 가질 수 있음을 시사",
)
add_lightweight_paper(
    paper_id="dementia-33",
    title="Electronic Tracking Devices for People With Dementia: Content Analysis of Company Websites",
    title_ko="치매환자용 전자추적기기: 판매업체 웹사이트 내용분석",
    authors="Jared Howes, Yvonne Denier, Chris Gastmans",
    year="2022", doi="10.2196/38865",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9700241/",
    abstract_ko="치매 돌봄용 전자추적기기(GPS 등)를 판매하는 업체 웹사이트 29곳을 내용분석한 연구. 업체들은 "
                "배회행동에서 비롯되는 신체적 위험·심리적 스트레스·사회적 고립 등 치매환자·보호자의 취약성을 "
                "언급하며, 정보제공·의사소통 지원·사용자친화적 설계로 이를 해결한다고 홍보했다. 웹사이트 "
                "내용은 주로 비공식 돌봄제공자를 대상으로 했고, 치매환자 본인을 명시적으로 겨냥한 업체는 "
                "29곳 중 1곳(3%)뿐이었다. 안전 확보와 프라이버시라는 상충하는 가치를 어떻게 다루는지가 설계 "
                "선택에 반영되어 있었다.",
    key_facts="추적기기 업체 29곳 중 치매환자 본인을 대상으로 명시한 곳은 단 3% — 보호자 편의 중심 설계가 "
              "대부분, 환자 당사자 관점(프라이버시·자율성)이 상대적으로 소외됨을 시사",
)
add_lightweight_paper(
    paper_id="dementia-34",
    title="Impact of telehealth on health outcomes and quality of life in the older adults population: a systematic review",
    title_ko="노인 인구의 건강결과·삶의 질에 대한 원격의료의 영향: 체계적 문헌고찰",
    authors="Gonçalo Fernandes, Teodora Figueiredo, Elísio Costa, Luís Coelho, Dirk Loyens",
    year="2025", doi="10.3389/fdgth.2025.1708960",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12756505/",
    abstract_ko="65세 이상 성인 대상 원격의료 중재가 건강결과·삶의 질·웰빙에 미치는 영향을 정리한 체계적 "
                "문헌고찰. 5년간 3개 데이터베이스를 검색해 37편(리뷰 6편, 원저 31편)을 선정했다. 질병관리·"
                "재활·건강증진·임상의사결정지원·심리지원 등 다양한 중재가 포함됐다. 화상기반 프로그램이 더 "
                "효과적이었고, 전화 단독 방식은 원격모니터링과 결합했을 때 가장 유용했다. 전문가 지도·보호자 "
                "지원·실시간 피드백이 순응도를 높였다. 신체기능 개선·만성질환 관리 향상·예방가능한 입원 감소 "
                "등의 이점이 있었으나, 삶의 질과 비용효과성에 대한 근거는 일관되지 않았다. 원격의료는 기존 "
                "돌봄의 '연장선'으로 기능할 때 가장 효과적이라고 결론지었다.",
    key_facts="화상기반 원격중재가 전화단독보다 효과적, 원격모니터링과 결합 시 시너지 — 삶의질/비용효과성 "
              "근거는 아직 불충분(정직한 한계 보고)",
)
add_lightweight_paper(
    paper_id="dementia-35",
    title="Malnutrition, Frailty, and High Fall Risk in Older Adults: Examining Their Interactive Effects in a Cross-Sectional Study",
    title_ko="노인의 영양불량·노쇠와 고위험 낙상: 상호작용 효과 단면연구",
    authors="Honghong Wen, Heting Liang, Qingyun Mao, Shaoting Yang, Xiaoli Yuan",
    year="2026", doi="10.2147/CIA.S609884",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13366073/",
    abstract_ko="중국 10개 지역사회 노인 13,970명을 대상으로 노쇠·영양불량 척도(허약점수·간이영양평가·"
                "STEADI)를 측정해 낙상위험에 미치는 상호작용 효과를 분석한 단면연구. 고위험 낙상군은 "
                "24.1%였다. 노쇠도 없고 영양상태도 좋은 사람 대비, 노쇠+영양불량이 동시에 있는 사람은 "
                "낙상위험이 유의하게 높았다(오즈비 15.83, 95% CI 11.77-21.28). 곱셈 상호작용(오즈비 1.064)과 "
                "덧셈 상호작용(RERI 1.58, AP 0.10, SI 1.18) 모두 유의했다. 저자들은 노쇠와 영양불량이 "
                "낙상위험과 연관되며 둘 사이에 완만한 양의 상호작용이 있다고 결론지었다.",
    key_facts="노쇠+영양불량 동시 보유 시 낙상위험 오즈비 15.83(단독 대비 훨씬 높음) — 위험인자를 단독이 "
              "아니라 조합으로 봐야 한다는 정량적 근거",
)
add_lightweight_paper(
    paper_id="dementia-36",
    title="Frailty, malnutrition, healthcare utilization, and mortality in patients with dementia and cognitive impairment obtained from hospital administrative data",
    title_ko="병원 행정자료로 본 치매·인지장애 환자의 노쇠·영양불량과 의료이용·사망률",
    authors="Reshma Aziz Merchant, Ying Qiu Dong, Shikha Kumari, Diarmuid Murphy",
    year="2025", doi="10.3389/fmed.2025.1540050",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11897001/",
    abstract_ko="65세 이상 입원환자 중 치매/인지장애 진단을 받은 환자의 노쇠·영양불량 유병률과 재원일수·"
                "재입원·사망률에 미치는 영향을 조사한 단일기관 후향적 코호트연구(2022년 3월~2023년 12월). "
                "전체 입원환자의 8.6%(3,090명)가 치매/인지장애 진단을 받았고 이 중 33.7%가 영양불량이었다. "
                "병원노쇠위험점수(HFRS) 기준 26.0%가 중등도, 18.2%가 고위험 노쇠였다. 중앙값 재원일수 8일, "
                "30일/90일 재입원율 각각 23.2%/35.4%, 입원중 사망률 7.8%, 30일 사망률 14.0%였다. 고위험 "
                "HFRS(보정오즈비 1.511)·중증 노쇠(4.325)·말기 노쇠(39.762) 모두 입원중 사망과 유의하게 연관됐다.",
    key_facts="치매/인지장애 입원환자 33.7%가 영양불량, 말기 노쇠군의 입원중 사망 보정오즈비 39.762(극단적으로 "
              "높음) — 노쇠 단계가 심할수록 위험이 선형이 아니라 급격히 증가함을 보여주는 정량적 근거",
)
add_lightweight_paper(
    paper_id="dementia-37",
    title="Evaluation and management of urinary incontinence in nursing home residents: unique considerations for an at-risk population",
    title_ko="요양시설 거주자의 요실금 평가·관리: 고위험군 대상 특수 고려사항",
    authors="Ioana Marcu, Allison Powell, Lisa C. Hickman",
    year="2024", doi="10.21037/gpm-24-5",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13214941/",
    abstract_ko="요양시설 거주자의 과민성방광(요실금) 평가·관리를 다룬 리뷰. 요양시설 인구에서 과민성방광 "
                "유병률은 36~77%로 높고, 노쇠·인지저하·기능저하 등 동반 위험요인이 많다. 치료는 보존적 "
                "중재·약물치료·시술의 3단계로 이뤄지나 요양시설 거주자는 지역사회 거주자와 다른 접근이 "
                "필요하다. 항콜린성 약물은 노쇠·고령 환자에서 주의가 필요하며, 금기가 없다면 베타-3 작용제가 "
                "선호된다. 시설 환경과 간호인력과의 상호작용이 치료 접근과 결과에 큰 영향을 미친다.",
    key_facts="요양시설 과민성방광 유병률 36~77% — 항콜린성 약물은 인지저하 환자에 주의 필요(dementia-03/16/17의 "
              "항콜린성·진정제 위험 논의와 연결됨)",
)
add_lightweight_paper(
    paper_id="dementia-38",
    title="Urinary Incontinence and Alzheimer's Disease: Insights From Patients and Preclinical Models",
    title_ko="요실금과 알츠하이머병: 환자 및 전임상모델을 통한 통찰",
    authors="Sarah N. Bartolone, Prasun Sharma, Michael B. Chancellor, Laura E. Lamb",
    year="2021", doi="10.3389/fnagi.2021.777819",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC8718555/",
    abstract_ko="요실금은 인지기능이 정상인 노인보다 알츠하이머병 환자에서 더 흔하지만, 그 연관 기전은 아직 "
                "불분명하다. 배뇨중추에 아밀로이드반·신경섬유매듭이 축적되면 방광으로 가는 신호전달이 손상돼 "
                "배뇨장애를 유발할 수 있고, 병이 진행될수록 배뇨 욕구나 적절한 배뇨 시점·장소를 인식하는 "
                "능력도 떨어진다. 요실금 치료제(무스카린성·베타3 아드레날린 수용체 표적)는 종종 인지기능 "
                "부작용을 동반하며, 알츠하이머병 치료에 쓰이는 아세틸콜린에스터라제 억제제는 항무스카린제와 "
                "반대로 작용해 두 질환의 동시 관리를 어렵게 한다. 200여 개의 알츠하이머병 전임상모델이 있지만 "
                "배뇨기능장애 연구는 부족하다.",
    key_facts="알츠하이머 치료제(콜린에스터라제 억제제)와 요실금 치료제(항무스카린제)가 서로 반대 기전으로 "
              "작용 — 두 약물군의 동시 처방이 임상적으로 상충되는 흔한 딜레마임을 보여줌(투약_조정_제안 기능에 참고 가능)",
)
add_lightweight_paper(
    paper_id="dementia-39",
    title="The relationship between symptoms of depression and falls in older adults: A case-control study",
    title_ko="노인의 우울 증상과 낙상의 관계: 환자-대조군 연구",
    authors="Manizheh Moshtaghi, Sadegh Kargarian-Marvasti, Pouya Farokhnezhad Afshar, "
            "Seyedeh Melika Kharghani Moghaddam, Fatemeh Bahramnezhad",
    year="2025", doi="10.1016/j.jarlif.2025.100018",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12274764/",
    abstract_ko="노인의 우울 증상과 낙상 사이의 관계를 조사한 환자-대조군 연구. 낙상 이력이 있는 노인 "
                "400명(환자군)과 없는 노인 400명(대조군)을 지역보건센터 자료로 비교했다. 우울 증상은 "
                "GHQ-28로 평가했다. 결과, 우울 증상과 낙상 사이에 유의한 연관은 없었다(오즈비 1.321, "
                "p=0.203). 반면 75세 초과(오즈비 4.391)와 독거(오즈비 2.924)는 낙상위험을 유의하게 높였고, "
                "고졸 이상 학력은 낙상위험을 낮추는 방향으로 보고됐다. 저자들은 우울 증상 자체보다 고령·독거가 "
                "더 중요한 낙상 위험요인이라고 결론지었다.",
    key_facts="우울증상-낙상 연관성 통계적으로 유의하지 않음(오즈비 1.321, p=0.203) — dementia-02의 '진정제="
              "낙상위험 단순증가 아님' 사례와 마찬가지로, 통념과 다른 반례 데이터를 정직하게 포함",
)
add_lightweight_paper(
    paper_id="dementia-40",
    title="Observing and treating pain in people living with dementia in long-term care facilities",
    title_ko="장기요양시설 치매환자의 통증 관찰과 치료",
    authors="Sabine D. Kruijer, Wilco P. Achterberg, Annelore van Dalen-Kok, Monique A. A. Caljouw",
    year="2026", doi="10.3389/fpain.2026.1812648",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13194510/",
    abstract_ko="치매환자는 인지손상으로 의사소통능력이 저하돼 통증이 비전형적으로 나타나 평가가 어렵다. "
                "장기요양시설에서 통증을 어떻게 관찰·평가·관리하는지 조사한 순차적 설명 혼합방법연구(2024년 "
                "4~7월 전국 설문 387명 + 심층면담 20명). 의료진·간호진은 행동변화 같은 비언어적 신호를 "
                "관찰했을 때, 신체검진과 때로는 통증관찰척도로 뒷받침해 통증을 판단했다. 응답자의 약 절반만 "
                "통증관찰척도를 일상적으로 사용했다. 통증관리는 주로 약물(대개 아세트아미노펜) 중심이었고, "
                "주의분산·운동·음악치료 같은 비약물적 중재는 상대적으로 드물고 비체계적으로 쓰였다.",
    key_facts="치매 통증평가 시 응답자의 절반만 표준화된 관찰척도 사용, 관리는 대부분 약물(아세트아미노펜) "
              "위주 — 비언어적 행동변화가 통증의 주요 신호라는 점은 행동패턴 이상탐지가 통증 조기감지에도 "
              "참고자료가 될 수 있음을 시사",
)
add_lightweight_paper(
    paper_id="dementia-41",
    title="Passive Smart Home Monitoring for Delirium-Relevant Anomaly Detection in People Living With Dementia: Proof-of-Concept Study",
    title_ko="치매환자의 섬망 관련 이상탐지를 위한 수동형 스마트홈 모니터링: 개념증명 연구",
    authors="Cong Mou, Mian Wu, Shreyank N. Gowda, Beili Shao",
    year="2026", doi="10.2196/93258",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13309767/",
    abstract_ko="치매에 중첩된 섬망은 예후가 나쁘지만 재가환경에서는 잘 감지되지 않는다 — 현재 진단은 대면 "
                "임상평가(혼동평가법 등)에 의존하는데 병원 밖에서는 거의 쓰이지 않는다. 재가 치매환자의 "
                "주변감지 센서 데이터만으로 섬망과 일치하는 이상패턴을 탐지하는 이론기반 프레임워크를 개발한 "
                "개념증명 연구. 치매환자 13명의 데이터를 Isolation Forest와 LSTM 모델로 분석해 섬망 증상과 "
                "일치하는 디지털 지표 기반 이상치를 식별했다. 두 알고리즘 모두 약 15~16%의 이상치를 탐지했고, "
                "이상치는 짧은 시간대에 군집으로 나타나는 경향이 있었다. 활동 엔트로피·수면의 질·조기경보점수가 "
                "가장 영향력 있는 특징이었다. 기술적 실현가능성은 보였으나 실제 섬망 발생 여부를 확인할 정답 "
                "데이터(ground truth)가 없다는 것이 중요한 한계로 명시됐다.",
    key_facts="주변감지 센서만으로 섬망 관련 이상치 15~16% 탐지(Isolation Forest/LSTM), 정답 데이터 부재가 "
              "한계로 명시됨 — 우리 시스템의 이상탐지·통합분석 설계와 정확히 같은 문제의식(비접촉 센서로 "
              "섬망/감염의 간접신호 포착)을 공유하는 가장 유사한 선행연구",
)
add_lightweight_paper(
    paper_id="dementia-42",
    title="Smart home-assisted anomaly detection system for older adults: a deep learning approach with a comprehensive set of daily activities",
    title_ko="노인 대상 스마트홈 기반 이상탐지 시스템: 포괄적 일상활동을 고려한 딥러닝 접근",
    authors="Ander Cejudo, Andoni Beristain, Aitor Almeida, Kristin Rebescher, Cristina Martín, Iván Macía",
    year="2025", doi="10.1007/s11517-025-03308-y",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12106144/",
    abstract_ko="스마트홈은 노인의 건강·웰빙을 원격 모니터링해 건강결과 개선과 자립생활 지원에 활용될 수 "
                "있지만, 기존 접근은 제한된 일상활동만 고려하고 개인 간 데이터를 결합하지 않는 경우가 많다. "
                "41종의 일상활동 전체를 고려해 인구집단 수준에서 행동을 모델링하고 유의미한 이탈(이상치)을 "
                "탐지하는 딥러닝 기법을 제안한 연구. 일상 루틴 패턴을 클러스터링(실루엣 점수 0.18)하고, "
                "순환신경망으로 다음날 루틴을 예측(평균제곱오차 4.38%)한 뒤 오차를 정규분포로 모델링해 유의한 "
                "이탈을 식별했다. 이상치의 평균 이탈 활동 수는 학습/시험셋에서 각각 3.6개/3.0개였고, 시험셋 "
                "이상치의 60% 이상이 3개 이상의 활동에서 이탈을 보였다. 이 방법론은 확장 가능해 활동을 "
                "추가로 포함시킬 수 있다.",
    key_facts="41종 일상활동 통합 이상탐지, 다음날 루틴 예측 오차 4.38%, 이상치의 60% 이상이 3개 이상 활동에서 "
              "동시 이탈 — 단일 지표보다 여러 활동을 함께 보는 것이 이상탐지에 유리하다는 근거(통합분석의 "
              "다중신호 접근과 같은 방향)",
)
add_lightweight_paper(
    paper_id="dementia-43",
    title="Prevention and Care of Pressure Ulcers in Long-Term Bedridden Adult and Older Adult Patients in the Community: A Systematic Review",
    title_ko="지역사회 장기 와상 성인·노인 환자의 욕창 예방과 관리: 체계적 문헌고찰",
    authors="Liuren Meng, Samoraphop Banharak, Chakkarin Sommana, Khanisorn Ransinyo, Wuttipong Cheumnok, Junhong Tian",
    year="2026", doi="10.2147/TCRM.S592581",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13155244/",
    abstract_ko="장기 와상 성인·노인 환자의 지역사회 욕창 예방·관리 중재를 정리한 체계적 문헌고찰(2013년 "
                "3월~2024년 3월, 14개 데이터베이스, 최종 16편). 다차원적 중재경로(대면교육·가정방문·디지털"
                "도구(위챗/스마트폰앱)·다학제 협진·원격추적)와 3가지 표적 예방조치(체위관리+압력경감, 피부"
                "보전, 영양/수분지원)가 확인됐다. 체위변경+압력경감기구 병행은 욕창 발생률 감소와, 산화아연을 "
                "이용한 온도조절 피부관리는 피부발적 감소와, 고단백식+오메가3 보충은 피부탄력·장벽기능 개선과 "
                "연관됐다. 근거의 확실성은 매우 낮음~중간 수준이었고 16편 중 14편이 중국에서 수행돼 지리적 "
                "대표성이 제한적이라는 한계가 있었다.",
    key_facts="와상환자 욕창예방 3대 조치: 체위관리+압력경감, 피부보전, 영양지원 — 디지털도구(스마트폰앱) "
              "활용도 확인됐으나 근거 확실성은 낮음~중간 수준으로 정직하게 보고됨",
)
add_lightweight_paper(
    paper_id="dementia-44",
    title="Predicting Activity Duration in Smart Sensing Environments Using Synthetic Data and Partial Least Squares Regression: The Case of Dementia Patients",
    title_ko="합성데이터와 부분최소제곱회귀를 이용한 스마트센싱 환경의 활동시간 예측: 치매환자 사례",
    authors="Miguel Ortiz-Barrios, Eric Järpe, Matías García-Constantino, Ian Cleland, Chris Nugent, "
            "Sebastián Arias-Fonseca, Natalia Jaramillo-Rueda",
    year="2022", doi="10.3390/s22145410",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9318990/",
    abstract_ko="치매환자의 일상생활활동(ADL) 인식은 건강 진행상황을 추적하고 이후 진단·치료를 뒷받침하는 데 "
                "핵심적이다. 스마트홈 환경·개인별 ADL 수행방식의 차이에서 오는 불확실성이 문제인데, 실제 "
                "데이터 수집은 비용이 크고 모집이 어려워 데이터가 적고 개인 특성을 잘 반영하지 못하는 경우가 "
                "많다. 시뮬레이션은 효율적 대안이지만 합성데이터가 실제 데이터와 상당히 다를 수 있다. 이 "
                "논문은 부분최소제곱회귀(PLSR)로 합성 관측치를 이용해 실제 ADL 수행시간을 근사하는 방법을 "
                "제안한다. 8개 ADL을 포함한 사례연구에서, 일부 ADL은 시뮬레이션과 실측이 유의하게 달랐으나"
                "(p<0.05), 합성변수를 보정하면 실제 활동시간을 높은 정확도(예측 R²>90%)로 예측할 수 있었다.",
    key_facts="시뮬레이션(합성)데이터 보정으로 실제 ADL 수행시간 예측 정확도 R²>90% 달성 — 실측 데이터가 "
              "부족할 때 시뮬레이션 데이터를 보정해 쓰는 접근법, virtual_patient.py/simulate_data.py 시뮬레이션 "
              "활용 전략과 같은 방향의 근거",
)
add_lightweight_paper(
    paper_id="dementia-45",
    title="Association of serum albumin level, feeding route, and mobility status with pressure ulcer presence in bedridden adults receiving long-term care",
    title_ko="장기요양 와상 성인의 혈청알부민·급식경로·이동성 상태와 욕창 발생의 연관성",
    authors="Melike Karabulut Ozer, Latife Merve Yıldız, Dilara Canbay Ozdemir, Ersin Ozer, Ozgür Enginyurt",
    year="2026", doi="10.1186/s12875-026-03291-9",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13162452/",
    abstract_ko="욕창은 대부분 예방 가능한 합병증이자 지역사회·재가돌봄의 중요한 환자안전 이슈다. 일차진료 "
                "재가돌봄 코호트에서의 근거는 부족했다. 이동성 제한이 있는 재가돌봄 환자 328명을 대상으로 한 "
                "후향적 단면연구. 전체 욕창 유병률은 8.2%였고, 완전 와상 환자(16.4%)가 부분거동 환자(4.1%)"
                "보다 유의하게 높았다. 욕창이 있는 환자는 알부민 수치가 더 낮았고(35.8±4.1 대 38.0±4.5 g/L) "
                "Norton 점수도 낮았다. 경구 섭취는 비경구 경로에 비해 욕창 오즈가 상당히 낮았다. 다변량 "
                "모델의 AUC는 0.760이었으나, 저자들은 외부검증 없이 실행가능한 서비스기획 권고로 해석해서는 "
                "안 된다고 명시했다.",
    key_facts="완전 와상 환자 욕창 유병률 16.4% vs 부분거동 4.1%(4배 차이), 예측모델 AUC 0.760(외부검증 필요, "
              "정직하게 명시) — 부동성(이탈/뒤척임 빈도)이 욕창 위험과도 직결됨을 뒷받침, ToF의 뒤척임/이탈 "
              "지표가 욕창 예방 목적으로도 확장 해석될 수 있음",
)


# ── 2026-09 4차 확장 — 하드웨어·AI 알고리즘 최신 문헌 재검토(9편, latest-01~09) ──
# 치매 임상 논문(p2, dementia-01~45)은 사용자 지침대로 이번 재검토 대상에서 제외했다.
# 아래 9편은 3개 연구 에이전트가 검색·원문 확인한 결과이며, docs/ai_plan.html의
# "🆕 최신 논문 반영(2026-09)" 모드에 논문별 전체 비교·코드 변경 내역이 있다.
add_lightweight_paper(
    paper_id="latest-01",
    title="Benchmarking Time-Series Artificial Intelligence Architectures for Wearable Sensor-Based Fall Prediction: A Synthetic Data Simulation Framework",
    title_ko="웨어러블 센서 낙상예측을 위한 시계열 AI 아키텍처 벤치마크",
    authors="Sykes, Maghsoudimehrabani, Al-Shanoon",
    year="2026", doi="10.3390/s26113326",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13259050/",
    abstract_ko="웨어러블 센서 기반 낙상예측을 위한 8가지 시계열 AI 아키텍처(로지스틱회귀·RF·XGBoost·LSTM·GRU·TCN·"
                "CNN-LSTM·Transformer)를 합성 데이터(1,000시퀀스, 정상 70%/실족-불안정 20%/낙상전조 10%)로 "
                "벤치마크했다. 분류지표(정확도·F1·AUROC)에서는 고전적 방법(로지스틱회귀·XGBoost·RF)이 딥러닝 "
                "계열보다 우세했고 LSTM이 가장 낮았다. 그러나 실제 경보 임계값(확률 0.70, 3표본 지속) 운용 "
                "기준에서는 GRU가 낙상전조 이벤트의 73.9%를 실제로 경보했고(오경보 28.7%, 중앙값 리드타임 "
                "11.8초), LSTM과 Transformer는 경보를 전혀 발생시키지 못했다(0%).",
    key_facts="GRU 경보재현 73.9%(리드타임 11.8초) vs LSTM/Transformer 0% — 소수클래스·이벤트임박예측 과제에서 "
              "GRU가 LSTM보다 실제 경보 트리거에 강건함을 시사. 마주봄 mmWave/exit_seq/analyze_v2.py의 자체 "
              "GRU 재실험(정확도 73.2±2.6% vs LSTM 71.1±5.5%) 동기가 된 논문",
)
add_lightweight_paper(
    paper_id="latest-02",
    title="An Empirical Survey of Data Augmentation for Time Series Classification with Neural Networks",
    title_ko="시계열 분류를 위한 데이터 증강 기법 실증 서베이",
    authors="Iwana, Uchida",
    year="2021", doi="10.1371/journal.pone.0254841",
    source_url="https://doi.org/10.1371/journal.pone.0254841",
    abstract_ko="시계열 분류를 위한 12가지 데이터 증강 기법(지터링·회전·스케일링·크기왜곡·순열·window slicing·"
                "time warping·window warping·SPAWNER·wDBA·RGW·DGW)을 UCR 128개 데이터셋 × 6종 신경망으로 "
                "실증 비교했다. window warping과 window slicing이 평균 순위가 가장 높았고, 패턴혼합 계열 중에서는 "
                "DGW가 가장 큰 향상을 보였다(연산비용은 더 큼). 증강으로 인한 정확도 향상은 학습표본 수가 "
                "적을수록 더 크게 나타났다.",
    key_facts="12개 증강기법 실증비교, window-warp/slice 평균순위 최상위, 작은 데이터셋일수록 증강 효과가 큼 — "
              "마주봄 mmWave/exit_seq/analyze_v2.py에 window-slice/warp를 추가로 구현해 기존 지터+타임워프 "
              "증강과 비교(LSTM 기준 F1 44.9±14.1→46.8±11.1, recall 41%→44%)",
)
add_lightweight_paper(
    paper_id="latest-03",
    title="Accurate predictions on small data with a tabular foundation model (TabPFN)",
    title_ko="소규모 표형데이터용 탭형 파운데이션 모델(TabPFN)",
    authors="Hollmann 외 6명",
    year="2025", doi="10.1038/s41586-024-08328-6",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11711098/",
    abstract_ko="수백만 개의 합성 데이터셋으로 사전학습해 베이즈추론을 근사하는 트랜스포머(TabPFN)를 제시한다. "
                "별도의 데이터셋별 경사하강 학습이나 하이퍼파라미터 튜닝 없이, 학습+평가 데이터 전체를 한 번의 "
                "순전파 입력으로 받아 분류한다. 29개 분류 데이터셋(전부 1만행·500특징·10클래스 이하) 기준으로 "
                "CatBoost 등 그래디언트부스팅 기본값을 크게 앞섰다. 다만 반박 벤치마크(Bansal & Gangwani 2025, "
                "arXiv:2512.00888)는 수천 행 규모 데이터셋에서 RF가 TabPFN을 근소하게 이기고 추론은 40배 이상 "
                "빠르다고 보고했다.",
    key_facts="소규모(≤1만행) 표형데이터 전용 파운데이션 모델, scikit-learn API 드롭인 가능. 마주봄 데이터"
              "(2,320행·128특징·6클래스)로 직접 실측 시도했으나(TOF/ml/compare_rf_tabpfn.py) 온라인 라이선스 "
              "로그인 게이트로 이 환경에서 실행 불가 — RF는 97.9% 재현 확인, TabPFN 비교는 미완",
)
add_lightweight_paper(
    paper_id="latest-04",
    title="Sleep Position Classification using Transfer Learning for Bed-based Pressure Sensors",
    title_ko="저해상도 침상 압력센서 자세분류 — 전이학습(교차피험자 평가)",
    authors="Papillon 외 6명",
    year="2025", doi="",
    source_url="https://arxiv.org/abs/2505.08111",
    abstract_ko="저해상도 침상 압력센서 그리드(약 144 sensel, 마주봄 ToF 128차원과 비슷한 규모)로 4개 수면자세"
                "(앙와위·복와위·좌우측와위)를 분류했다. 교차피험자 5-fold 교차검증(폴드당 학습 약 90명·평가 약 "
                "22명, 동일인 중복 없음)에서 ImageNet 사전학습 ViTMAE를 미세조정한 모델이 정확도 77.0%(F1 "
                "73.1)로 가장 우수했고, Random Forest는 32.2%(F1 25.2, 4지선다 chance=25%)로 거의 무작위 "
                "수준까지 떨어졌다.",
    key_facts="교차피험자 평가에서 RF 정확도 32.2%(거의 chance 수준) vs 사전학습 ViTMAE 77.0% — 저해상도 "
              "침상센서에서 RF의 단일세션 고정확도가 다른 피험자에게 일반화되지 않을 수 있음을 시사하는 외부 "
              "근거(마주봄 ToF RF 97.9%도 같은 우려 대상 — 1명·1세션 결과라는 기존 내부 경고를 뒷받침)",
)
add_lightweight_paper(
    paper_id="latest-05",
    title="An Unsupervised Data-Driven Anomaly Detection Approach for Adverse Health Conditions in People Living With Dementia: Cohort Study",
    title_ko="치매환자 이상탐지 — 개인화 통계기법(Contextual Matrix Profile), 실제 임상사건 검증",
    authors="Bijlani, Nilforooshan, Kouchaki",
    year="2022", doi="",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9531007/",
    abstract_ko="치매환자 15명(9,363 환자-일)의 가정 내 주변감지 센서(PIR·도어·전력·조도·수면매트) 기록에서, "
                "환자 1명씩 개인화한 다차원 Contextual Matrix Profile(거리기반 통계기법, 딥러닝 아님)로 이상을 "
                "탐지했다. 실제 요로감염 31건·입원 10건이라는 임상 정답에 대해 평균 재현율 84.3%(15명 전원 "
                "33% 이상 재현), 624일 여정당 경보 32.1건(5.1%일)을 기록했다. LODA·COPOD·ABOD 등 다른 비지도 "
                "이상탐지 기법과도 비교했다.",
    key_facts="환자 1명씩 개인화, 딥러닝 아닌 통계적 거리기반(Matrix Profile), 실제 UTI·입원 정답 대비 재현율 "
              "84.3% — 마주봄 TOF/ml/lstm_anomaly.py(정답 라벨 없이 99백분위 임계값만 사용, 랜덤 85/15 분할로 "
              "누수 의심)의 유력한 대안 후보로 제안. 후속연구(2024)는 대조학습·그래프신경망으로 발전했으나 "
              "65~102명 규모 코호트가 필요해, 1인 데이터인 마주봄 현 단계에는 이 논문 쪽 개인화 통계기법이 더 "
              "적합 — 아직 코드로 옮기지 않음(미구현)",
)
add_lightweight_paper(
    paper_id="latest-06",
    title="Intelligent fall risk prediction and real-time warning system for elderly care based on multimodal deep learning and wearable sensor fusion",
    title_ko="웨어러블 다중센서 낙상위험 예측 — early fusion이 late fusion보다 우수",
    authors="Li, Liu, Wu, Zhu, Liu",
    year="2026", doi="10.1038/s41598-026-56750-9",
    source_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC13527089/",
    abstract_ko="지역사회 노인 120명(6개월)의 가속도계·자이로·족저압력·PPG 웨어러블 데이터를 원시 텐서 단계에서 "
                "결합하는 early fusion(8-head attention 기반 CNN-LSTM)과, 개별 모달리티 판정을 나중에 합치는 "
                "late fusion을 같은 데이터·같은 파이프라인에서 직접 비교했다. Early fusion+attention이 정확도 "
                "94.2%·F1 90.6%·AUC 0.967로, late fusion(정확도 89.7%·F1 84.9%·AUC 0.937)보다 우수했다 — "
                "활동 종류에 따라 모달리티 중요도가 달라 late fusion은 이를 반영하지 못한다는 설명이다. 다만 "
                "평가셋의 낙상 이벤트는 6건뿐이라 이 우위가 안정적인지는 저자들도 유보적이다.",
    key_facts="같은 데이터로 early vs late fusion 직접비교(early 우세, F1 90.6 vs 84.9) — 웨어러블 4종 센서 "
              "실험이라 마주봄의 ToF+mmWave 레이더 조합과는 하드웨어가 전혀 다르고, 학습에 58,762개 라벨된 "
              "윈도우가 필요해 마주봄의 융합 라벨 데이터 부족 상황에는 그대로 적용 불가. server/"
              "integrated_analysis.py의 max() 결합을 대체할, 학습 없이 쓸 수 있는 검증된 대안은 이 논문을 "
              "포함한 2026-09 재검토에서 찾지 못함 — max() 유지",
)
add_lightweight_paper(
    paper_id="latest-07",
    title="Retrieval-Augmented Generation for Large Language Models: A Survey",
    title_ko="검색증강생성(RAG) 기술 서베이",
    authors="Gao 외 9명",
    year="2023", doi="",
    source_url="https://arxiv.org/abs/2312.10997",
    abstract_ko="검색증강생성(RAG)을 Naive RAG·Advanced RAG·Modular RAG 3단계로 정리한 서베이. Advanced RAG "
                "단계에서는 검색 전 질의 최적화와 검색 후 재랭킹이 표준 구성요소로 다뤄지며, dense 임베딩과 "
                "BM25 같은 sparse 검색을 결합하는 하이브리드 검색과 reciprocal rank fusion 결합이 이미 기본 "
                "기법 수준으로 취급된다.",
    key_facts="하이브리드(dense+sparse) 검색과 재랭킹을 RAG의 표준 구성요소로 정리 — 마주봄이 이미 계획하던 "
              "'벡터 vs BM25 vs 결합 비교'가 특이한 실험이 아니라 이 분야의 표준 다음 단계임을 뒷받침",
)
add_lightweight_paper(
    paper_id="latest-08",
    title="Blended RAG: Improving RAG Accuracy with Semantic Search and Hybrid Query-Based Retrievers",
    title_ko="Blended RAG — dense+sparse 하이브리드 검색으로 NDCG@10 개선",
    authors="Sawarkar, Mangal, Solanki",
    year="2024", doi="",
    source_url="https://arxiv.org/abs/2404.07220",
    abstract_ko="Elasticsearch의 dense kNN 벡터검색과 ELSER 희소 인코더를 필드 단위 하이브리드 질의로 결합했다. "
                "Natural Questions에서 NDCG@10 0.67(단일 dense 대비 +5.8%p), TREC-COVID에서 NDCG@10 "
                "0.87(+8.2%p)을 보고했다. 단, SQuAD에서는 dense 단독(94.89%)이 sparse 단독(90.7%)보다 나아, "
                "하이브리드의 이득은 데이터셋마다 다르다고 명시했다.",
    key_facts="하이브리드 검색이 데이터셋에 따라 NDCG@10을 5.8~8.2%p 개선(항상 이기는 것은 아님, 데이터셋 "
              "의존적). 마주봄은 이 발견에 착안해 server/paper_kb.py에 벡터(LanceDB cosine)+BM25(rank_bm25)를 "
              "Reciprocal Rank Fusion(RRF, k=60)으로 결합하는 search_hybrid() 함수를 2026-09에 추가했다 — "
              "기존 search()는 그대로 두고 별도 옵션으로 추가했으며, 정답셋 기반 Recall@5 정량 비교는 아직 "
              "하지 않았다",
)
add_lightweight_paper(
    paper_id="latest-09",
    title="MTEB: Massive Text Embedding Benchmark",
    title_ko="MTEB — 대규모 텍스트 임베딩 벤치마크",
    authors="Muennighoff, Tazi, Magne, Reimers",
    year="2022", doi="",
    source_url="https://arxiv.org/abs/2210.07316",
    abstract_ko="8개 과제 유형·58개 데이터셋·112개 언어에 걸쳐 33개 임베딩 모델을 벤치마크했다. 가장 중요한 "
                "발견은 모든 과제에서 보편적으로 1등인 단일 임베딩 모델은 없다는 것이다 — 검색·분류·군집화 등 "
                "과제별로 최적 모델이 달라진다.",
    key_facts="'보편적 1위 모델 없음'이 핵심 결론 — 마주봄이 현재 쓰는 text-embedding-3-small(OpenAI 자체 "
              "발표 MTEB 평균 62.3, ada-002 61.0·large 64.6 대비)의 우열을 이 벤치마크만으로 단정할 수 없고, "
              "도메인(치매·낙상 논문) 자체 Recall@5 평가가 필요하다는 근거로 인용",
)


# ── 단독 테스트 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== paper_kb.py 자체 테스트 (2026-09-10 복구본) ===\n")

    chunks = build_all_chunks() + build_all_chunks_lightweight()
    print(f"[chunking] {len(chunks)}개 chunk 추출됨 (paper 수: "
          f"{len(set(c['paper_id'] for c in chunks))}개)")

    key = ai_report.get_api_key()
    print(f"\nOPENAI_API_KEY 설정 여부: {'있음' if key else '없음'}")

    result = build_index()
    print("\n[build_index]", {k: v for k, v in result.items() if k != "message"}, result.get("message", ""))

    if result.get("n_embedded", 0) > 0:
        sr = search("보행속도 감소와 낙상 위험의 관계", top_k=3)
        print("\n[search 예시 — 벡터 단독]", sr.get("success"))
        for r in sr.get("results", []):
            print(f"  {r['score']:.3f}  {r['paper_id']}/{r['section']}  {r['original_text'][:60]}")

        hr = search_hybrid("보행속도 감소와 낙상 위험의 관계", top_k=3)
        print("\n[search_hybrid 예시 — 벡터+BM25 RRF]", hr.get("success"))
        for r in hr.get("results", []):
            print(f"  {r['score']:.5f}  vec#{r['vector_rank']} bm25#{r['bm25_rank']}  "
                  f"{r['paper_id']}/{r['section']}  {r['original_text'][:60]}")
