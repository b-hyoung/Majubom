"""Bounded in-memory timeline of actual report stages, external prompts and tool results.

No files/DB are written. This displays generated search questions and explicit structured
outputs, never private model reasoning. ContextVar keeps concurrent patients isolated.
"""
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import inspect
import math
import re
import threading
import uuid

_ACTIVE = ContextVar('report_trace_run', default=None)
_LOCK = threading.Lock()
_RUNS = OrderedDict()
MAX_RUNS = 40
MAX_EVENTS = 160
_SECRET_KEYS = {'api_key', 'openai_api_key', 'authorization', 'password', 'wifi_pass', 'headers'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(value, depth=0):
    if depth > 8:
        return '[상세 깊이 제한]'
    if isinstance(value, str):
        value = re.sub(r'sk-[A-Za-z0-9_-]{12,}', '[REDACTED]', value)
        return value if len(value) <= 6000 else value[:6000] + ' … [화면 길이 제한]'
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:80]:
            if str(k).lower() in _SECRET_KEYS:
                continue
            if k == 'embeddings' and isinstance(v, list):
                out['embedding_summary'] = {'vectors': len(v), 'dimensions': len(v[0]) if v else 0}
            else:
                out[str(k)] = _clean(v, depth + 1)
        return out
    if isinstance(value, (tuple, list)):
        return [_clean(v, depth + 1) for v in value[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def emit(stage, status, title, details=None):
    """Observability must never change the clinical/report execution result."""
    try:
        run_id = _ACTIVE.get()
        if not run_id:
            return
        event = {'at': _now(), 'stage': stage, 'status': status, 'title': title,
                 'details': _clean(details or {})}
        with _LOCK:
            run = _RUNS.get(run_id)
            if run is None:
                return
            run['event_count'] += 1
            event['seq'] = run['event_count']
            run['events'].append(event)
            del run['events'][:-MAX_EVENTS]
    except Exception:
        pass


def traced_run(kind):
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if _ACTIVE.get() is not None:
                return fn(*args, **kwargs)
            params = signature.bind_partial(*args, **kwargs).arguments
            run_id = uuid.uuid4().hex
            run = {'id': run_id, 'bed_id': params.get('bed_id'), 'kind': kind,
                   'started_at': _now(), 'ended_at': None, 'status': 'running',
                   'event_count': 0, 'events': []}
            with _LOCK:
                _RUNS[run_id] = run
                while len(_RUNS) > MAX_RUNS:
                    # Retain active runs when possible, but always bound memory.
                    oldest = next((k for k, r in _RUNS.items() if r['status'] != 'running'), next(iter(_RUNS)))
                    del _RUNS[oldest]
            token = _ACTIVE.set(run_id)
            emit('run', 'running', '통합 리포트 실행' if kind == 'report' else '문헌 조사 실행',
                 {'configuration': params, 'source': f'{fn.__module__}.{fn.__name__}'})
            try:
                result = fn(*args, **kwargs)
                success = isinstance(result, dict) and result.get('success') is True
                emit('run', 'completed' if success else 'failed', '실행 완료' if success else '실행 실패',
                     {k: result.get(k) for k in ('analysis_id', 'stage', 'error_type', 'message', 'risk', 'sufficient') if k in result} if isinstance(result, dict) else {})
                with _LOCK:
                    if run_id in _RUNS:
                        _RUNS[run_id].update(status='completed' if success else 'failed', ended_at=_now())
                return result
            except Exception as exc:
                emit('run', 'failed', '실행 중 예외', {'error_type': type(exc).__name__, 'message': str(exc)})
                with _LOCK:
                    if run_id in _RUNS:
                        _RUNS[run_id].update(status='failed', ended_at=_now())
                raise
            finally:
                _ACTIVE.reset(token)
        return wrapped
    return decorate


def traced_stage(stage, title):
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if _ACTIVE.get() is None:
                return fn(*args, **kwargs)
            params = signature.bind_partial(*args, **kwargs).arguments
            emit(stage, 'running', title + ' · 시작', {'inputs': params, 'source': f'{fn.__module__}.{fn.__name__}'})
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                emit(stage, 'failed', title + ' · 예외', {'error_type': type(exc).__name__, 'message': str(exc)})
                raise
            failed = isinstance(result, dict) and result.get('success') is False
            emit(stage, 'failed' if failed else 'completed', title + (' · 실패' if failed else ' · 완료'),
                 {'outputs': result, 'note': '검색 질문과 명시적 구조화 응답·도구 결과를 표시합니다. 모델 내부 추론은 표시하지 않습니다.'})
            return result
        return wrapped
    return decorate


def snapshot(bed_id=None):
    import copy
    with _LOCK:
        runs = [copy.deepcopy(r) for r in reversed(list(_RUNS.values())) if bed_id is None or r['bed_id'] == bed_id][:20]
    return {'runs': runs, 'storage': 'RAM only', 'max_runs': MAX_RUNS, 'max_events_per_run': MAX_EVENTS,
            'note': '계측 이후 실행만 표시됩니다. 서버 재시작 시 사라집니다. 과거 리포트 내용을 실행 로그로 재구성하지 않습니다.'}
