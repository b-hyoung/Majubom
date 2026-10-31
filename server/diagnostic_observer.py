"""Read-only diagnostic snapshots; bounded RAM metadata, no diagnostic persistence.

The observer never evaluates a sensor or writes a baseline. Instrumented operations
are the original operations, and errors from them retain their original behavior.
"""
from collections import OrderedDict, deque
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import sqlite3
import threading


class Observer:
    def __init__(self, max_scopes=16, max_events=80):
        self._lock = threading.RLock()
        self._scopes = OrderedDict()
        self.max_scopes = max_scopes
        self.max_events = max_events

    def record(self, scope, stage, values=None, event=False):
        try:
            with self._lock:
                state = self._scopes.setdefault(scope, {'latest': {}, 'operations': deque(maxlen=self.max_events),
                                                        'events': deque(maxlen=self.max_events)})
                self._scopes.move_to_end(scope)
                while len(self._scopes) > self.max_scopes:
                    self._scopes.popitem(last=False)
                entry = {'at': datetime.now(timezone.utc).isoformat(), 'stage': stage, **deepcopy(values or {})}
                if stage == 'storage':
                    state['operations'].append(entry)
                else:
                    state['latest'][stage] = entry
                if event:
                    # Event history is operation metadata only, never a sensor sample archive.
                    state['events'].append({k: deepcopy(v) for k, v in entry.items()
                                            if k in ('at', 'stage', 'status', 'event_type', 'level', 'row_id',
                                                     'table', 'reason', 'alarm', 'error')})
        except Exception:
            pass  # A diagnostic failure must not prevent the existing clinical operation.

    def snapshot(self, scope):
        with self._lock:
            state = self._scopes.get(scope)
            if not state:
                return {'latest': {}, 'operations': [], 'events': []}
            return deepcopy({'latest': state['latest'], 'operations': list(state['operations']),
                             'events': list(state['events'])})

    def latest_stage(self, scope, stage):
        with self._lock:
            return deepcopy(self._scopes.get(scope, {}).get('latest', {}).get(stage))

    def write(self, scope, table, function, *args, labels=None, **kwargs):
        """Execute exactly one original DB call; report the returned row ID or failure."""
        try:
            row_id = function(*args, **kwargs)
        except Exception as exc:
            self.record(scope, 'storage', {'table': table, 'row_id': None, 'status': 'failed',
                                         'labels': labels, 'error': str(exc)}, event=True)
            raise
        self.record(scope, 'storage', {'table': table, 'row_id': row_id,
                                     'status': 'committed', 'labels': labels}, event=True)
        return row_id


observer = Observer()


def read_json(path, scope=None):
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        return {'status': 'read', 'value': data.get(scope) if scope else data}
    except FileNotFoundError:
        return {'status': 'missing', 'value': None}
    except Exception as exc:
        return {'status': 'failed', 'value': None, 'error': str(exc)}


def metric_statistics(baseline, metrics, floors):
    """Display exact accumulators alongside unrounded population μ/σ; no updates."""
    out = {}
    for metric in metrics:
        stat = (baseline or {}).get(metric) or {}
        n = int((baseline or {}).get('n') or 0)
        row = {'n': n, 'sum': stat.get('sum'), 'sumsq': stat.get('sumsq'), 'sigma_floor': floors[metric]}
        if n and stat.get('sum') is not None and stat.get('sumsq') is not None:
            mean = stat['sum'] / n
            variance = max(0.0, stat['sumsq'] / n - mean * mean)
            row.update(mean=mean, variance=variance, sigma_raw=math.sqrt(variance),
                       sigma_used=max(math.sqrt(variance), floors[metric]))
        if 'v_n' in stat:
            vn = stat['v_n']
            row['valid_only'] = {k: stat.get(k) for k in ('v_n', 'v_sum', 'v_sumsq')}
            if vn:
                vm = stat['v_sum'] / vn
                vv = max(0.0, stat['v_sumsq'] / vn - vm * vm)
                row['valid_only'].update(mean=vm, variance=vv, sigma_used=max(math.sqrt(vv), floors[metric]))
        out[metric] = row
    return out


def database_snapshot(path, sensor, scope, bed_id='bed_01'):
    """Existing stored rows verified via SQLite mode=ro. No init/create calls."""
    out = {'database': str(path), 'mode': 'read_only', 'rows': {}, 'errors': {}}
    conn = None
    try:
        conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=0.15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        if sensor == 'tof':
            queries = {'tof_readings': ('SELECT id,timestamp,sensor,resolution,min_mm,valid_zones,occupied,in_bed FROM tof_readings WHERE sensor IN (?,?) ORDER BY id DESC LIMIT 4', ('tof1', 'tof2'))}
        else:
            queries = {'mmw_readings': ('SELECT * FROM mmw_readings WHERE target_id=? ORDER BY id DESC LIMIT 3', (scope,))}
        queries.update({
            'sensor_clean_data': ('SELECT * FROM sensor_clean_data WHERE patient_id=? AND sensor_type=? ORDER BY id DESC LIMIT 8', (bed_id, sensor)),
            'activity_log': ('SELECT * FROM activity_log WHERE bed_id=? ORDER BY id DESC LIMIT 20', (bed_id,)),
            'baseline_history': ('SELECT * FROM baseline_history WHERE bed_id=? ORDER BY id DESC LIMIT 6', (bed_id,)),
            'ai_analysis': ('SELECT id,patient_id,created_at,analysis_type,risk_score,risk_json FROM ai_analysis WHERE patient_id=? ORDER BY id DESC LIMIT 1', (bed_id,)),
        })
        for table, (sql, args) in queries.items():
            try:
                out['rows'][table] = [dict(r) for r in conn.execute(sql, args)]
            except sqlite3.Error as exc:
                out['errors'][table] = str(exc)
    except sqlite3.Error as exc:
        out['error'] = str(exc)
    finally:
        if conn is not None:
            conn.close()
    return out


def algorithm(id, name, formula, source, *, inputs=None, outputs=None, active=False, note=None):
    return dict(id=id, name=name, formula=formula, source=source,
                inputs=inputs, outputs=outputs, active=active, note=note)


def combined_snapshot(storage):
    rows = storage['rows'].get('ai_analysis') or []
    latest = rows[0] if rows else None
    risk = None
    if latest and latest.get('risk_json'):
        try:
            risk = json.loads(latest['risk_json'])
        except (ValueError, TypeError):
            pass
    return {'source': 'server/integrated_analysis.py:compute_risk_score',
            'formula': 'max(level_score(ToF posture), level_score(ToF exit), level_score(mmWave gait)); normal=0.1, caution=0.4, warning=0.7, critical=0.95',
            'gates': ['보류·신뢰 불가 mmWave 제외', '잠정 mmWave 제외, 단 critical 포함'],
            'note': '리포트 실행 시 계산되는 실제 기존 판정의 결합. 진단 조회는 재평가·저장하지 않음. 낙상 확률이 아님.',
            'latest_persisted_report': latest, 'risk': risk}


def comparison_details(logic, before, after, result, prior):
    """Read-only explanation of the evaluated branch, retaining actual z outputs."""
    rows = {}
    raw = result.get('raw') or {}
    with_prior = bool(prior and (prior.get('metrics') or {}).get('speed'))
    prior_bed = deepcopy(before)
    if with_prior and prior_bed and prior_bed.get('n') and not logic._valid_ready(prior_bed):
        logic._reset_valid_from_legacy(prior_bed)  # Only this private diagnostic copy.
    for m in logic.METRICS:
        row = {'input': raw.get(m), 'z_stored_in_result': (result.get('zscore') or {}).get(m),
               'legacy_sum_before': ((before or {}).get(m) or {}).get('sum'),
               'legacy_sum_after': ((after or {}).get(m) or {}).get('sum'),
               'included_in_baseline': int((after or {}).get('n', 0)) > int((before or {}).get('n', 0)),
               'sigma_floor': logic.SIGMA_FLOOR[m]}
        if result.get('zscore') is None:
            row.update(comparison_executed=False, comparison_mean=None, comparison_sigma=None,
                       note='이번 패킷은 실제 Z-score 비교 없음: 보행 게이트 보류 또는 기준선 학습 중')
            rows[m] = row
            continue
        row['comparison_executed'] = True
        if with_prior:
            n, mean, sd = logic._personal(prior_bed, m)
            value = logic._metric_value(raw, m)
            pr = (prior.get('metrics') or {}).get(m)
            row.update(valid_input=value, n_personal_before=n, personal_mean_before=mean,
                       personal_sigma_before=sd,
                       included_in_valid_baseline=row['included_in_baseline'] and value is not None)
            if pr:
                sw = logic._within_sd(n, sd, float(pr['sigma_within_prior']), logic.SIGMA_FLOOR[m])
                post_mean, post_var = logic._posterior(float(pr['mean']), float(pr['tau']), n, mean, sw)
                row.update(comparison_mean=post_mean, comparison_sigma=max(sd or 0, logic.SIGMA_FLOOR[m]),
                           prior_mean=pr['mean'], prior_tau=pr['tau'], within_sigma=sw,
                           posterior_variance=post_var)
                if value is not None:
                    k = n + 1
                    mean_k = ((mean or 0) * n + value) / k
                    row.update(provisional_k=k, provisional_mean=mean_k,
                               provisional_sigma=math.sqrt(float(pr['tau'])**2 + sw**2/k))
        else:
            stats = result.get('baseline') or {}
            row.update(comparison_mean=stats.get(m+'_mu'), comparison_sigma=stats.get(m+'_sigma'),
                       note='실제 기존 compute_zscore가 사용하는 반올림 baseline 수치')
        rows[m] = row
    return rows


def mmwave_snapshot(logic, store, latest, db_path, target='room_01', bed_id='bed_01'):
    state = observer.snapshot('mmwave:' + target)
    current = deepcopy(store.get(target))
    evaluation = state['latest'].get('evaluation', {})
    result = deepcopy(latest.get(target))
    storage = database_snapshot(db_path, 'mmwave', target, bed_id)
    storage['operations'] = state['operations']
    inputs = (result or {}).get('raw')
    comparison = comparison_details(logic, evaluation.get('before'), evaluation.get('after'), result or {}, evaluation.get('prior')) if result and evaluation.get('status') == 'evaluated' else {}
    if evaluation.get('comparison_order') == 'before_update_prior':
        evaluation['baseline_used'] = {m: {k: row.get(k) for k in ('n_personal_before', 'personal_mean_before', 'personal_sigma_before', 'comparison_mean', 'comparison_sigma', 'prior_mean', 'provisional_mean', 'provisional_sigma')} for m,row in comparison.items()}
    persisted = read_json(logic.BASELINE_PATH, target)
    persisted['matches_current'] = persisted.get('value') == current if persisted['status'] == 'read' else None
    algorithms = [
        algorithm('window_features', '선택 트랙 6초 원시 표본 → 보행 지표',
                  'v=√(vx²+vy²); speed=Σv/N; CV=std(v)/mean(v); sway=std(|(p−p₀)×unit(p_end−p₀)|); freeze=count(v<0.15)/N; height_drop=max(z)−min(z)',
                  'mmWave/send_mmw.py:_gait_from_samples/select_patient',
                  inputs=evaluation.get('diagnostic_inputs'), outputs=inputs,
                  active=(evaluation.get('diagnostic_inputs') or {}).get('status') == 'computed',
                  note='N은 6초 트랙 표본 수, 아래 baseline n은 보행 구간 수. 표본 10개 미만은 계산 보류이며 전송 0을 실측 평균으로 해석하지 않음. height_drop은 높이 범위라 시간 순서 하강 확인과는 다름.'),
        algorithm('stride_fft', '속도 FFT 기반 보폭 추정',
                  'fs=1/median(Δt>0); FFT(v−mean(v)); peak within 0.5..3Hz ≥2·band_mean; stride=mean(v)/cadence; stride_cv=std(step_strides)/mean(step_strides)',
                  'mmWave/send_mmw.py:_estimate_stride',
                  inputs=(evaluation.get('diagnostic_inputs') or {}).get('stride'),
                  outputs={k: (inputs or {}).get(k) for k in ('stride_length','stride_cv')},
                  active=(evaluation.get('diagnostic_inputs') or {}).get('status') == 'computed',
                  note='20표본·평균속도≥0.1·fs≥1 조건. 실패 0은 prior 유효값 집계에서 제외.'),
        algorithm('baseline', '보행 기준선 누적', 'n←n+1; sum←sum+x; sumsq←sumsq+x²; μ=sum/n; var=max(sumsq/n−μ²,0); σ=max(√var, floor)', 'server/mmw_logic.py:update_baseline/baseline_stats', inputs=inputs, outputs=metric_statistics(current, logic.METRICS, logic.SIGMA_FLOOR), active=bool(evaluation), note='프레임 전체가 아닌 보행 구간 지표 1개를 누적. reliable/patient_locked/walking 게이트 통과 시에만 갱신.'),
        algorithm('zscore', '개인 평소 대비 Z-score', 'z_i=(x_i−μ_i)/σ_i; S=Σ|z_i|; thresholds=3/6/9', 'server/mmw_logic.py:compute_zscore/evaluate', inputs={'raw': inputs, 'baseline_used': evaluation.get('baseline_used')}, outputs=(result or {}).get('zscore'), active=(result or {}).get('zscore') is not None, note='기존 경로는 이번 구간을 포함한 반올림 baseline과 비교; EMR prior 경로는 이번 구간 반영 전의 개인/문헌 혼합 평균과 비교. baseline.comparison에서 실제 분자·분모·유효값 포함 여부를 표시.'),
        algorithm('prior', '문헌 prior / 개인 혼합', 'precision=1/τ₀²+n/σw²; μpost=(μ₀/τ₀²+n·μpersonal/σw²)/precision; z=(x−μpost)/max(σpersonal,floor); provisional z=(μk−μ₀)/√(τ₀²+σw²/k)', 'server/mmw_logic.py:_evaluate_with_prior', inputs=evaluation.get('prior'), outputs={'expected': (result or {}).get('expected'), 'baseline_source': (result or {}).get('baseline_source'), 'provisional': (result or {}).get('provisional')}, active=bool(evaluation.get('prior')), note='유효 보폭 실패값은 valid_only 집계에서 제외. S에 충분한 지표(4개)가 있으면 임계값을 metrics_used/6로 조정.'),
        algorithm('fall', '높이 급강하 절대 규칙', f'reliable=true AND height_drop>{logic.HEIGHT_DROP_FALL}m → critical, alarm=true', 'server/mmw_logic.py:evaluate', inputs={'height_drop': (inputs or {}).get('height_drop'), 'quality': (result or {}).get('quality')}, outputs={'level': (result or {}).get('alert_level'), 'alarm': (result or {}).get('alarm'), 'reasons': (result or {}).get('reasons')}, active=result is not None, note='환자 귀속·walking 불통과여도 reliable이면 절대 급강하는 감시. unreliable은 급강하 규칙도 실행하지 않음.'),
        algorithm('warming', '학습·정식 알람 보류', f'n<{logic.MIN_SAMPLES} OR age_days<{logic.WARMUP_DAYS} → 정식 알람 보류; 급강하 예외', 'server/mmw_logic.py:evaluate', inputs=(result or {}).get('baseline'), outputs={'measuring_held': (result or {}).get('measuring_held'), 'reasons': (result or {}).get('reasons')}, active=result is not None),
    ]
    return {'sensor': 'mmwave', 'scope_id': target, 'bed_id': bed_id, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'latest': state['latest'], 'current_result': result, 'algorithms': algorithms,
            'baseline': {'path': logic.BASELINE_PATH, 'current': current, 'persisted': persisted,
                         'before': evaluation.get('before'), 'after': evaluation.get('after'),
                         'metrics': metric_statistics(current, logic.METRICS, logic.SIGMA_FLOOR), 'comparison': comparison},
            'storage': storage, 'events': state['events'], 'combined': combined_snapshot(storage),
            'diagnostic_storage': 'bounded RAM only; 원시 점군·진단 데이터 신규 저장 없음'}


def tof_snapshot(server, bed_id='bed_01'):
    state = observer.snapshot('tof')
    storage = database_snapshot(server.db.DB_PATH, 'tof', 'tof', bed_id)
    storage['operations'] = state['operations']
    ingest = state['latest'].get('ingest')
    posture = state['latest'].get('posture')
    distance_means = {}
    for sid in ('tof1', 'tof2'):
        ds = (server.latest.get(sid) or {}).get('distances_mm') or []
        valid = [d for d in ds if isinstance(d, (int, float)) and math.isfinite(d) and d > 0]
        distance_means[sid] = {'n_valid': len(valid), 'sum_mm': sum(valid), 'mean_mm': sum(valid)/len(valid) if valid else None,
                               'stored': False, 'note': '최신 프레임 d>0 존의 화면 설명용 평균. 운영 DB는 min_mm/valid_zones 저장하며 거리 평균을 저장하지 않음.'}
    algorithms = [
        algorithm('distance_mean', '최신 거리 평균 설명', 'mean_mm=Σ valid distance_mm / n_valid; d>0, finite', 'server/diagnostic_observer.py:tof_snapshot', outputs=distance_means, active=bool(ingest), note='프레임 평균은 표시용이며 시간 누적 baseline 평균과 다름.'),
        algorithm('occupied', '빈 침대 대비 점유존', f'0<b≤{server.ROI_MAX_MM}mm AND d>0 AND d<b−{server.PRESENCE_DELTA_MM}mm; 미보정은 0<d<{server.ABS_FALLBACK_MM}', 'server/tof_server.py:occupied_zones', inputs={'baseline_set': {sid: server.baseline[sid] is not None for sid in ('tof1','tof2')}}, outputs=deepcopy(server.presence), active=bool(ingest)),
        algorithm('presence', '양쪽 ToF 재실 히스테리시스', f'진입 max(occ1,occ2)≥{server.PRESENCE_MIN_ZONES}, {server.CONFIRM_ENTER}연속; 유지≥{server.STAY_MIN_ZONES}; 이탈 {server.CONFIRM_EXIT}연속', 'server/tof_server.py:update_presence', outputs=deepcopy(server.presence), active=bool(ingest)),
        algorithm('posture', 'ROI 거리 → Random Forest 자세', 'concat(ROI-mask(tof1[64]),ROI-mask(tof2[64])) → 128 features; argmax(predict_proba); occ1+occ2=0 → empty; 좌우 전환 6초 내 2회 → tossing', 'TOF/ml/predict_posture.py + server/tof_server.py:get_latest', inputs={'model_loaded': server._POSTURE is not None}, outputs=posture, active=posture is not None, note='기존 /tof/latest 호출 때만 실행. 진단 조회가 RF/뒤척임 카운터를 추가로 진행시키지 않음. 자세 라벨은 tof_readings에 저장되지 않음.'),
        algorithm('edge_risk', '점유존 중심·가장자리 위험', f'Ymean=Σhit_y/n_pts; lying AND n_pts≥{server.RISK_MIN_PTS} AND |Ymean|>{server.EDGE_Y_M}m; {server.RISK_CONFIRM}연속; ratio=min(|Ymean|/{server.BED_HALF_W},1.5)', 'server/tof_server.py:_body_mean_y/_fall_risk', inputs=(posture or {}).get('body_mean'), outputs=deepcopy(server.risk_latest), active=posture is not None, note='두 ToF의 baseline 안 점유존만 사용. 원시 거리 평균이 아닌 보정된 좌우 좌표 평균. 낙상 위험 자세와 실제 낙상 사건은 구분.'),
        algorithm('lstm', '선택 LSTM-AE 참고값', '12 features; Xn=(X−model_μ)/model_σ; error=mean((LSTM_AE(Xn)−Xn)²); error>threshold → anomaly; ratio>0.7 → warn', 'TOF/ml/tof_anomaly.py', inputs=state['latest'].get('lstm'), outputs=deepcopy(server.anomaly_latest), active=server._ANOM is not None, note='기존 수신 시 실행하는 참고값. 현재 주 위험 판정의 직접 입력 아님.'),
        algorithm('behavior', 'ToF 행동 / 이탈 Z-score', '행동 μ=sum/n; σ=max(√(sumsq/n−μ²),floor); S=Σ|z| 임계 1.5/3/4.5; 이탈 z=(duration−μbefore)/σbefore', 'server/tof_logic.py / server/behavior_fsm.py', active=False, note='ToF HTTP 수신 경로에서 호출하지 않음. 저장된 행동 기준선은 아래 persisted 참조; 리포트 경로의 실제 실행은 AI 처리 로그로 확인.'),
    ]
    return {'sensor':'tof', 'scope_id': bed_id, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'latest': state['latest'], 'scope_note': '공유 ToF 물리 스트림은 bed_01에만 귀속. 다른 bed_id 조회는 물리 센서를 다른 환자로 바꾸지 않음.', 'algorithms': algorithms,
            'baseline': {'path': server.BASELINE_FILE, 'current': deepcopy(server.baseline),
                         'persisted': read_json(server.BASELINE_FILE), 'before': state['latest'].get('calibration', {}).get('before'),
                         'after': state['latest'].get('calibration', {}).get('after'), 'update_method': '수동 calibrate 최신 프레임 복사, 연속 평균 아님',
                         'behavior': read_json(str(Path(server.HERE)/'tof_behavior_baseline.json'),bed_id),
                         'exit_duration': read_json(str(Path(server.HERE)/'behavior_fsm_baseline.json'),bed_id)},
            'storage': storage, 'events': state['events'], 'combined': combined_snapshot(storage),
            'diagnostic_storage': 'bounded RAM only; 추가 진단 데이터 저장 없음'}


def patient_target(path, bed_id):
    """Match the clinical bed/target mapping without creating a database."""
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.15)) as conn:
            conn.execute('PRAGMA query_only=ON')
            row = conn.execute('SELECT mmw_target_id FROM patients WHERE bed_id=?', (bed_id,)).fetchone()
            return (row[0] if row else None) or ('room_01' if bed_id == 'bed_01' else None)
    except sqlite3.Error:
        return 'room_01' if bed_id == 'bed_01' else None
