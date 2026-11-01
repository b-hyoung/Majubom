"""Read-only, no-GT diagnostics; output rates are never recognition accuracy."""
from pathlib import Path
import math
import re
import uuid
from .config import record_validator
from .contracts import COCO17, IntegrityError, SCHEMA_VERSION
from .io_utils import atomic_json, file_hash, load_json, object_hash, strict_loads
from .recording import verify_records

POLICY = {'version': 'no-gt-v1', 'association_mode': 'none',
          'denominator': 'all_manifest_frames', 'percentile': 'linear_h=(n-1)*p',
          'timing_groups': 'frame_processing_status', 'common_joints': list(COCO17)}


def read_lines(path):
    with Path(path).open(encoding='utf-8') as stream:
        return [strict_loads(line) for line in stream]


def read_run(directory, include_keypoints=False, include_raw=False):
    directory = Path(directory).resolve()
    try:
        before = file_hash(directory / 'manifest.json')
        manifest = load_json(directory / 'manifest.json')
        verify_records(directory, manifest)
        source = load_json(directory / 'input_manifest.snapshot.json')
        record_validator().validate(manifest['requested_config'])
        for key in ('adapter_id', 'execution_mode', 'requested_device', 'warmup_frames',
                    'target_policy', 'error_policy', 'input_manifest_sha256'):
            if manifest['requested_config'][key] != manifest['resolved_config'][key]:
                raise IntegrityError('Requested and resolved settings disagree')
        if (manifest['resolved_config']['input_manifest_sha256'] != manifest['input_manifest_sha256']
                or manifest['adapter_profile']['adapter_id'] != manifest['resolved_config']['adapter_id']
                or manifest['adapter_profile']['execution_mode'] != manifest['resolved_config']['execution_mode']
                or manifest['adapter_profile']['execution_kind'] != manifest['execution_kind']):
            raise IntegrityError('Adapter metadata does not match the run configuration')
        if (file_hash(directory / 'input_manifest.snapshot.json') != manifest['input_manifest_sha256']
                or file_hash(directory / 'input_frames.jsonl') != source['frame_manifest_sha256']
                or source['video_id'] != manifest['video_id']
                or source['frame_count'] != manifest['expected_frames']
                or source['canonical_width'] != manifest['width']
                or source['canonical_height'] != manifest['height']
                or object_hash(manifest['adapter_profile']) != manifest['adapter_profile_sha256']
                or object_hash(manifest['mapping_profile']) != manifest['mapping_profile_sha256']
                or object_hash(manifest['requested_config']) != manifest['config_sha256']):
            raise IntegrityError('Run provenance does not match the input/config/profile snapshots')
        result = {'manifest': manifest, 'source': source,
                  'frames': read_lines(directory / 'frame_results.jsonl'),
                  'receipts': read_lines(directory / 'write_timings.jsonl'),
                  'input_frames': read_lines(directory / 'input_frames.jsonl'),
                  'keypoints': read_lines(directory / 'keypoints.jsonl') if include_keypoints else None,
                  'raw_results': read_lines(directory / 'raw_results.jsonl') if include_raw else None,
                  'manifest_sha256': before}
        # Detect changes during the multi-file read; do not evaluate a mixed snapshot.
        if file_hash(directory / 'manifest.json') != before or any(
                file_hash(directory / name) != digest
                for name, digest in manifest['artifact_sha256'].items()):
            raise IntegrityError('Run artifacts changed while reading')
        return result
    except IntegrityError:
        raise
    except Exception as exc:
        raise IntegrityError(f'Cannot read verified run ({type(exc).__name__})') from exc


def percentile(values, p):
    if not values or not 0 <= p <= 1 or any(not math.isfinite(v) for v in values):
        raise ValueError('Percentile needs finite samples and p in [0,1]')
    values = sorted(values)
    h = (len(values) - 1) * p
    low, high = math.floor(h), math.ceil(h)
    return values[low] + (values[high] - values[low]) * (h - low)


def _ns(value):
    if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]*', value):
        raise ValueError('Expected unsigned integer nanoseconds')
    return int(value)


def clock_valid(bundle):
    try:
        previous = 0
        for row, receipt in zip(bundle['frames'], bundle['receipts']):
            for stage in ('decode', 'adapter', 'mapping'):
                start, end = (row['timing'][stage + suffix] for suffix in ('_start_ns', '_end_ns'))
                if start is not None:
                    if _ns(start) < previous or _ns(end) < _ns(start):
                        return False
                    previous = _ns(end)
            if _ns(receipt['started_ns']) < previous:
                return False
            previous = _ns(receipt['finished_ns'])
        manifest = bundle['manifest']
        start, end = manifest['pipeline_started_ns'], manifest['data_finished_ns']
        if start is not None:
            first = next((r['timing']['decode_start_ns'] for r in bundle['frames']
                          if r['timing']['decode_start_ns'] is not None), None)
            if first is None or _ns(start) > _ns(first) or _ns(end) < previous or _ns(end) <= _ns(start):
                return False
        return True
    except (ValueError, TypeError, KeyError):
        return False


def build_evaluation(run_dir, scope='full'):
    if scope not in ('full', 'diagnostic_partial'):
        raise ValueError('Unknown evaluation scope')
    directory = Path(run_dir).resolve()
    evaluation_id = uuid.uuid4().hex
    policy = dict(POLICY, scope=scope)
    policy_hash = object_hash(policy)
    try:
        bundle = read_run(directory)
        manifest = bundle['manifest']
        integrity, error = 'verified', None
    except IntegrityError as exc:
        bundle = None
        try:
            manifest = load_json(directory / 'manifest.json')
            if not isinstance(manifest, dict):
                manifest = {}
        except (ValueError, OSError):
            manifest = {}
        integrity, error = 'invalid', str(exc)
    run_id = manifest.get('run_id')
    if not isinstance(run_id, str) or not run_id:
        run_id = 'unknown'
    input_hash = manifest.get('input_manifest_sha256', '')
    if not isinstance(input_hash, str) or not re.fullmatch('[a-f0-9]{64}', input_hash):
        input_hash = '0' * 64  # Diagnostic sentinel only; every metric is invalid.
    blocked = ('INPUT_INTEGRITY_ERROR' if bundle is None else
               'INCOMPLETE_RUN' if scope == 'full' and manifest['run_status'] != 'succeeded' else None)
    metrics = []
    validator = record_validator()

    def add(name, unit, value=None, reason=None, *, numerator=None, denominator=None,
            n=0, elapsed=None):
        reason = blocked or reason
        status = 'invalid' if reason in ('INPUT_INTEGRITY_ERROR', 'CLOCK_INVALID') else 'unavailable' if reason else 'computed'
        if status != 'computed':
            value, numerator, denominator, elapsed = None, None, None, None
        record = {'record_type': 'metric_result', 'schema_version': SCHEMA_VERSION,
                  'evaluation_id': evaluation_id, 'run_id': run_id, 'metric_name': name,
                  'status': status, 'value': value, 'unit': unit, 'scope': scope,
                  'numerator': numerator, 'denominator': denominator, 'n_samples': n,
                  'elapsed_seconds': elapsed, 'excluded_count': 0, 'reason': reason,
                  'input_manifest_sha256': input_hash, 'gt_sha256': None,
                  'policy_sha256': policy_hash}
        validator.validate(record)
        if status == 'computed' and (not math.isfinite(value) or
                (unit == 'ratio' and (numerator > denominator or value != numerator / denominator))):
            raise IntegrityError('Invalid computed metric arithmetic')
        metrics.append(record)

    frames = bundle['frames'] if bundle else []
    n = len(frames)
    candidate_count = sum(r['candidate_count'] is not None and r['candidate_count'] > 0 for r in frames)
    ok = sum(r['processing_status'] == 'ok' for r in frames)
    for name, count in (('candidate_output_rate', candidate_count), ('processing_ok_rate', ok)):
        add(name, 'ratio', count / n if n else None, None if n else 'ZERO_DENOMINATOR',
            numerator=count, denominator=n, n=n)
    clocks_ok = clock_valid(bundle) if bundle else False
    elapsed = None
    if bundle and clocks_ok and manifest['pipeline_started_ns'] is not None:
        elapsed = (_ns(manifest['data_finished_ns']) - _ns(manifest['pipeline_started_ns'])) / 1e9
    fps_reason = ('INCOMPLETE_RUN' if scope != 'full' or manifest.get('run_status') != 'succeeded' else
                  'CLOCK_INVALID' if not clocks_ok else 'NO_TIME' if elapsed is None else None)
    add('successful_pipeline_fps', 'frames_per_second', ok / elapsed if elapsed else None,
        fps_reason, numerator=ok, n=ok, elapsed=elapsed)
    timing_details = {}
    for stage in ('decode', 'adapter', 'mapping', 'write'):
        for status in ('ok', 'failed'):
            selected = [(i, r) for i, r in enumerate(frames) if r['processing_status'] == status]
            samples = []
            for index, row in selected:
                timing = bundle['receipts'][index] if stage == 'write' else row['timing']
                start_key, end_key = ('started_ns', 'finished_ns') if stage == 'write' else (stage + '_start_ns', stage + '_end_ns')
                if timing[start_key] is not None and timing[end_key] is not None:
                    samples.append((index, (_ns(timing[end_key]) - _ns(timing[start_key])) / 1e6))
            key = stage + ':' + status
            timing_details[key] = {'n_samples': len(samples), 'missing_count': len(selected) - len(samples),
                'first_measured_frame_id': samples[0][0] if samples else None,
                'first_measured_ms': samples[0][1] if samples and clocks_ok and not blocked else None,
                'frame_ids': [i for i, _ in samples]}
            reason = 'CLOCK_INVALID' if bundle and not clocks_ok else 'INSUFFICIENT_SAMPLES' if not samples else None
            for label, p in (('p50', 0.5), ('p95', 0.95)):
                add(f'{stage}_{status}_{label}_ms', 'milliseconds',
                    percentile([v for _, v in samples], p) if samples else None,
                    reason, n=len(samples))
    for joint in COCO17:
        add('target_joint_output_rate:' + joint, 'ratio', reason='NO_TARGET_ASSOCIATION')
    for name in ('pck_end_to_end', 'pck_conditional'):
        add(name, 'ratio', reason='NO_VERIFIED_GT')
    add('stationary_jitter_rms_px', 'pixels', reason='NO_VERIFIED_GT')
    config = manifest.get('resolved_config', {})
    if not isinstance(config, dict):
        config = {}
    code = manifest.get('code', {})
    if not isinstance(code, dict):
        code = {}
    from .runner import _code_version
    group = {'input_manifest_sha256': input_hash, 'scope': scope, 'association_mode': 'none',
             'execution_kind': manifest.get('execution_kind'), 'execution_mode': config.get('execution_mode'),
             'warmup_frames': config.get('warmup_frames'), 'environment': manifest.get('environment'),
             'code_source_sha256': code.get('source_sha256'), 'policy_sha256': policy_hash}
    return {'evaluation_id': evaluation_id, 'run_id': run_id, 'run_directory': str(directory),
            'run_status': manifest.get('run_status', 'unknown'), 'ledger_complete': manifest.get('ledger_complete', False),
            'execution_kind': manifest.get('execution_kind', 'unknown'), 'scope': scope,
            'association_mode': 'none', 'integrity': integrity, 'integrity_error': error,
            'evaluation_status': 'invalid' if any(m['status'] == 'invalid' for m in metrics) else 'completed',
            'evaluation_code': _code_version(), 'requested_config': manifest.get('requested_config'),
            'adapter_profile': manifest.get('adapter_profile'), 'mapping_profile': manifest.get('mapping_profile'),
            'expected_frames': manifest.get('expected_frames'), 'counts': manifest.get('counts'),
            'errors': manifest.get('errors'), 'input_manifest_sha256': input_hash,
            'run_manifest_sha256': bundle['manifest_sha256'] if bundle else None,
            'policy': policy, 'policy_sha256': policy_hash, 'timing_details': timing_details,
            'comparison_group': object_hash(group) if bundle else None,
            'eligible_for_comparison': bool(bundle and clocks_ok and scope == 'full' and manifest['run_status'] == 'succeeded'),
            'metrics': metrics}


def markdown_cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ').replace('<', '&lt;').replace('>', '&gt;')


def evaluation_markdown(report):
    lines = ['# Run diagnostics', '',
             f"Run: {markdown_cell(report['run_id'])}; status: {markdown_cell(report['run_status'])}; "
             f"kind: {markdown_cell(report['execution_kind'])}; integrity: {report['integrity']}", '',
             f"Scope: {report['scope']}; association_mode: none. Output rates are not recognition accuracy.", '',
             'No verified GT, target association, posture or event accuracy is provided.', '',
             '| Metric | Status | Value | Unit | Samples | Reason |', '|---|---|---:|---|---:|---|']
    for metric in report['metrics']:
        lines.append('| ' + ' | '.join(markdown_cell(metric[k]) for k in
                     ('metric_name', 'status', 'value', 'unit', 'n_samples', 'reason')) + ' |')
    return '\n'.join(lines) + '\n'


def evaluate_run(run_dir, scope='full'):
    if not Path(run_dir).is_dir():
        raise ValueError('Evaluation requires an existing run directory')
    report = build_evaluation(run_dir, scope)
    output = Path(run_dir).resolve() / 'evaluations' / report['evaluation_id']
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / 'manifest.json', {'complete': False, 'evaluation_id': report['evaluation_id']})
    atomic_json(output / 'aggregate_metrics.json', report)
    (output / 'report.md').write_text(evaluation_markdown(report), encoding='utf-8')
    atomic_json(output / 'manifest.json', {'complete': True, 'evaluation_id': report['evaluation_id'],
                'artifact_sha256': {name: file_hash(output / name) for name in ('aggregate_metrics.json', 'report.md')}})
    return output, report


def compare_runs(run_dirs, output, scope='full'):
    paths = [Path(p).resolve() for p in run_dirs]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('Request one or more distinct run directories')
    output = Path(output).resolve()
    if any(output == p or output in p.parents for p in paths):
        raise ValueError('Comparison output must not replace a requested run directory or its ancestor')
    reports = [build_evaluation(p, scope) for p in paths]
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / 'manifest.json', {'complete': False})
    lines = ['# Requested run comparison', '',
             'Only rows with the same comparison_group and eligible=true share evaluation conditions.',
             'Synthetic outputs are plumbing checks. No model accuracy ranking is made.', '',
             '| Run | Adapter | Kind | Run status | Integrity | Scope | Association | Eligible | Group | Candidate output | Processing OK | Pipeline FPS |',
             '|---|---|---|---|---|---|---|---|---|---:|---:|---:|']
    for report in reports:
        values = {m['metric_name']: m['value'] for m in report['metrics']}
        profile = report['adapter_profile'] if isinstance(report['adapter_profile'], dict) else {}
        cells = [report['run_directory'], profile.get('adapter_id', 'unknown'), report['execution_kind'], report['run_status'], report['integrity'],
                 report['scope'], report['association_mode'], report['eligible_for_comparison'],
                 report['comparison_group'], values['candidate_output_rate'], values['processing_ok_rate'],
                 values['successful_pipeline_fps']]
        lines.append('| ' + ' | '.join(map(markdown_cell, cells)) + ' |')
    summary = {'scope': scope, 'association_mode': 'none', 'runs': reports,
               'integrity': 'invalid' if any(r['integrity'] != 'verified' for r in reports) else 'verified',
               'evaluation_status': 'invalid' if any(r['evaluation_status'] == 'invalid' for r in reports) else 'completed'}
    atomic_json(output / 'comparison.json', summary)
    (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    atomic_json(output / 'manifest.json', {'complete': True,
                'artifact_sha256': {name: file_hash(output / name) for name in ('comparison.json', 'report.md')}})
    return output, summary
