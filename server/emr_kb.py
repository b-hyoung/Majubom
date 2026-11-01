"""Patient-scoped EMR vectors. SQLite remains the authoritative record store.

Default local vectors are deterministic character/word hashing (lexical similarity),
not neural semantic embeddings. Optional semantic indexing reuses paper_kb's API.
Never mix patients, embedding models, papers or report-history collections.
"""
from pathlib import Path
import hashlib
import math
import re
import threading

import db
from gait_prior import redact_names
import report_trace

LOCAL_MODEL = 'local-text-hash-v1'
DIMENSIONS = 512
TABLE = 'emr_chunks'
SEMANTIC_TABLE = 'emr_semantic_chunks'
STORE = str(Path(__file__).resolve().parent / 'lancedb_store')
_LOCK = threading.RLock()


def local_embedding(text):
    normalized = re.sub(r'\s+', ' ', str(text).lower()).strip()
    tokens = re.findall(r'\w+', normalized)
    features = tokens + [normalized[i:i+n] for n in (2, 3) for i in range(max(0, len(normalized)-n+1))]
    vector = [0.0] * DIMENSIONS
    for feature in features:
        digest = hashlib.sha256(feature.encode()).digest()
        vector[int.from_bytes(digest[:4], 'big') % DIMENSIONS] += 1 if digest[4] & 1 else -1
    norm = math.sqrt(sum(x*x for x in vector))
    return [x/norm for x in vector] if norm else vector


def chunks_for(patient_id):
    patient = db.get_patient(patient_id)
    if not patient:
        raise ValueError('unknown patient')
    chunks = []
    for row in db.list_emr_records(patient_id, limit=1000):
        text = redact_names(row['text'], [patient.get(k) for k in ('name','name_en','patient_name')])
        for part, start in enumerate(range(0, len(text), 700)):
            body = text[start:start+800]
            if not body.strip():
                continue
            chunks.append({'chunk_id': f"emr::{patient_id}::{row['id']}::{part}",
                           'patient_id': patient_id, 'record_id': int(row['id']),
                           'record_date': row.get('record_date') or '', 'source': row.get('source') or '',
                           'record_type': row.get('record_type') or '', 'original_text': body,
                           'content_hash': hashlib.sha256(body.encode()).hexdigest(),
                           'fictional': patient_id in ('bed_sim_calm','bed_sim_bedexit','bed_sim_gaitcaution','bed_sim_risk','bed_sim_fall')})
    return chunks


def _filter(patient_id):
    return "patient_id = '" + patient_id.replace("'", "''") + "'"


def _connection():
    import lancedb
    return lancedb.connect(STORE)


def _exists(conn, table):
    return table in conn.list_tables().tables


def _rows(table, patient_id):
    return table.search().where(_filter(patient_id)).limit(10000).to_list()


@report_trace.traced_stage('emr_index', '환자 EMR 전용 벡터 저장')
def build_index(patient_id, method='local'):
    if method not in ('local', 'semantic'):
        return {'success': False, 'error_type': 'bad_method', 'message': 'local 또는 semantic만 사용합니다.'}
    try:
        chunks = chunks_for(patient_id)
        if not chunks:
            return {'success': True, 'n_chunks': 0, 'n_newly_embedded': 0, 'method': method}
        with _LOCK:
            conn = _connection()
            table_name = TABLE if method == 'local' else SEMANTIC_TABLE
            model = LOCAL_MODEL
            if method == 'semantic':
                import paper_kb
                model = paper_kb.EMBEDDING_MODEL
            table = conn.open_table(table_name) if _exists(conn, table_name) else None
            if table is not None and method == 'semantic':
                first = table.search().limit(1).to_list()
                if first and first[0]['embedding_model'] != model:
                    return {'success':False,'error_type':'embedding_model_mismatch',
                            'message':'기존 semantic 테이블과 모델이 다릅니다. 원래 모델을 사용하거나 별도 모델 컬렉션으로 마이그레이션하세요.'}
            previous = {r['chunk_id']: r for r in _rows(table, patient_id)} if table else {}
            pending = [c for c in chunks if c['chunk_id'] not in previous or
                       previous[c['chunk_id']]['content_hash'] != c['content_hash'] or
                       previous[c['chunk_id']]['embedding_model'] != model]
            if pending:
                if method == 'local':
                    vectors = [local_embedding(c['original_text']) for c in pending]
                else:
                    result = paper_kb.embed_texts([c['original_text'] for c in pending])
                    if not result['success']:
                        return result
                    vectors = result['embeddings']
                records = [{**c, 'embedding_model': model, 'vector': v} for c,v in zip(pending, vectors)]
                if table is None:
                    table = conn.create_table(table_name, data=records)
                else:
                    table.merge_insert('chunk_id').when_matched_update_all().when_not_matched_insert_all().execute(records)
            return {'success': True, 'database': STORE, 'table': table_name, 'model': model,
                    'method': method, 'n_chunks': len(chunks), 'n_newly_embedded': len(pending),
                    'n_embedded': table.count_rows(_filter(patient_id)),
                    'note': '로컬 어휘·문자 유사도 벡터(의미 임베딩 아님)' if method == 'local' else '외부 API 의미 임베딩'}
    except ImportError as exc:
        return {'success': False, 'error_type': 'missing_dependency', 'message': f'EMR 벡터 저장 라이브러리 없음: {exc.name}'}
    except Exception as exc:
        return {'success': False, 'error_type': type(exc).__name__, 'message': 'EMR 벡터 저장을 완료하지 못했습니다.'}


def status(patient_id):
    """Read only: never embeds, writes tables, or creates a missing store."""
    chunks = chunks_for(patient_id)
    out = {'database': STORE, 'table': TABLE, 'semantic_table': SEMANTIC_TABLE,
           'model': LOCAL_MODEL, 'dimensions': DIMENSIONS, 'source_table': 'emr_records',
           'patient_filter': patient_id, 'n_source_records': len({c['record_id'] for c in chunks}),
           'n_source_chunks': len(chunks), 'n_embedded': 0, 'n_current': 0,
           'n_semantic_embedded': 0, 'n_semantic_current':0, 'chunks': [],
           'formula': 'word + character 2/3-grams → SHA256 signed bucket → 512 dimensions → L2 normalization; cosine similarity',
           'note': 'local은 어휘·문자 유사도입니다. semantic은 API 의미 임베딩이며 별도 테이블입니다.'}
    if not Path(STORE).exists():
        out['status'] = 'not_indexed' if chunks else 'empty'
        return out
    try:
        conn = _connection()
        if _exists(conn, TABLE):
            table = conn.open_table(TABLE)
            rows = _rows(table, patient_id)
            current = {c['chunk_id']: c['content_hash'] for c in chunks}
            out['n_embedded'] = table.count_rows(_filter(patient_id))
            out['n_current'] = sum(r.get('content_hash') == current.get(r['chunk_id']) for r in rows)
            out['chunks'] = [{k:v for k,v in r.items() if k != 'vector'} | {
                'vector_dimensions': len(r['vector']), 'vector_preview': r['vector'][:8],
                'current': r.get('content_hash') == current.get(r['chunk_id'])} for r in rows[:30]]
        if _exists(conn, SEMANTIC_TABLE):
            semantic = conn.open_table(SEMANTIC_TABLE)
            out['n_semantic_embedded'] = semantic.count_rows(_filter(patient_id))
            current = {c['chunk_id']:c['content_hash'] for c in chunks}
            out['n_semantic_current'] = sum(r.get('content_hash') == current.get(r['chunk_id']) for r in _rows(semantic,patient_id))
        out['status'] = 'empty' if not chunks else 'ready' if out['n_current'] == len(chunks) else 'pending'
    except ImportError:
        out['status'] = 'missing_dependency'
    except Exception:
        out['status'] = 'unavailable'
    return out


@report_trace.traced_stage('emr_search', '현재 환자의 EMR 벡터 검색')
def search(patient_id, query, top_k=5):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        return {'success': False, 'error_type': 'bad_query', 'message': '1~2000자 검색어가 필요합니다.'}
    if type(top_k) is not int or not 1 <= top_k <= 20:
        return {'success': False, 'error_type': 'bad_top_k', 'message': 'top_k는 1~20입니다.'}
    try:
        source = {c['chunk_id']:c['content_hash'] for c in chunks_for(patient_id)}
        if not Path(STORE).exists():
            return {'success': False, 'error_type': 'no_index', 'message': 'EMR 벡터 인덱스가 없습니다.'}
        conn = _connection()
        if not _exists(conn, TABLE):
            return {'success': False, 'error_type': 'no_index', 'message': 'EMR 벡터 인덱스가 없습니다.'}
        table = conn.open_table(TABLE)
        current_ids = [r['chunk_id'] for r in _rows(table,patient_id)
                       if source.get(r['chunk_id']) == r['content_hash'] and r['embedding_model'] == LOCAL_MODEL]
        coverage = {'n_source_chunks':len(source),'n_current':len(current_ids),
                    'complete':len(source)==len(current_ids)}
        if current_ids:
            eligible = ','.join("'"+cid.replace("'","''")+"'" for cid in current_ids)
            rows = table.search(local_embedding(query)).metric('cosine').where(
                _filter(patient_id)+f' AND chunk_id IN ({eligible})', prefilter=True).limit(top_k).to_list()
        else:
            rows = []
        results = [{k:v for k,v in r.items() if k not in ('vector','_distance')} |
                   {'score': round(1-r['_distance'], 5)} for r in rows
                   if source.get(r['chunk_id']) == r['content_hash']][:top_k]
        return {'success': True, 'patient_id': patient_id, 'query': query, 'method': LOCAL_MODEL,
                'table': TABLE, 'coverage':coverage, 'warning':None if coverage['complete'] else '일부 원문 벡터 미갱신; 원문 전체는 SQL에 유지됩니다.',
                'score_kind': 'lexical_cosine_not_probability', 'results': results}
    except Exception as exc:
        return {'success': False, 'error_type': type(exc).__name__, 'message': 'EMR 벡터 검색을 완료하지 못했습니다.'}
