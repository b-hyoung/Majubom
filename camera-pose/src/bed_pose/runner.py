"""Synchronous video benchmark orchestration with explicit adapter selection."""
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter_ns
import os
import platform
import subprocess
import uuid
from importlib.metadata import PackageNotFoundError, version
import av
import numpy as np
from .config import load_run_config
from .contracts import AdapterCallError, IntegrityError, ModelInput, SCHEMA_VERSION, SourceError
from .io_utils import file_hash, load_json, object_hash
from .mapping import MappingProfile, map_keypoints, raw_json_value
from .pose.fake import FakeAdapter, FakeOptions
from .recording import ResultRecorder
from .video import VideoSource, model_timestamps, validate_input


@dataclass(frozen=True)
class ValidatedRun:
    config: dict
    requested_config: dict
    input_manifest: dict
    rows: list
    video_path: Path
    timestamps: list
    fake_options: FakeOptions


def validate_run(config_path, fake_options=None):
    config_path = Path(config_path).resolve()
    before = file_hash(config_path)
    requested = load_json(config_path)
    config = load_run_config(config_path)
    if file_hash(config_path) != before:
        raise ValueError('Configuration changed while loading')
    requested.setdefault('warmup_frames', 0)
    if config['adapter_id'] == 'fake':
        if config['assets'] or config['adapter_options_path'] is not None:
            raise ValueError('Fake adapter does not load assets or external SDK options')
    elif config['adapter_id'] == 'mediapipe_pose':
        from .pose.mediapipe import validated_options
        validated_options(config)
        if fake_options is not None and fake_options != FakeOptions():
            raise ValueError('Fake test options cannot be applied to a real model')
    else:
        raise ValueError('This model adapter is not implemented')
    manifest, rows, video_path = validate_input(
        config['input_manifest_path'], config['input_manifest_sha256'])
    if config['warmup_frames'] > len(rows):
        raise ValueError('warmup_frames exceeds input frame count')
    options = fake_options or FakeOptions()
    indices = options.no_detection_at + (() if options.fail_at is None else (options.fail_at,))
    if any(index >= len(rows) for index in indices):
        raise ValueError('Fake injection frame lies outside the input')
    return ValidatedRun(config, requested, manifest, rows, video_path,
                        model_timestamps(rows, config['execution_mode']), options)


def _code_version():
    package = Path(__file__).resolve().parent
    source_files = {str(path.relative_to(package)): file_hash(path)
                    for path in sorted(package.rglob('*'))
                    if path.is_file() and path.suffix in ('.py', '.json', '.html')}
    result = {'revision': None, 'dirty': None,
              'source_sha256': object_hash(source_files), 'source_files': source_files}
    try:
        result['package_version'] = version('bed-pose-benchmark')
    except PackageNotFoundError:
        result['package_version'] = None
    try:
        root = package.parents[1]
        result['revision'] = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
            capture_output=True, text=True, check=True).stdout.strip()
        result['dirty'] = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=root,
            capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    return result


def _frame_row(spec, run_id, clock_id):
    return {'record_type': 'frame_result', 'schema_version': SCHEMA_VERSION,
            'run_id': run_id, 'video_id': spec['video_id'], 'frame_id': spec['frame_id'],
            'segment_id': 0,
            **{key: spec[key] for key in ('source_pts', 'time_base_num', 'time_base_den',
                                          'source_duration_pts', 'timestamp_source')},
            'model_timestamp_ms': None, 'sdk_call_started': False,
            'processing_status': 'ok', 'failure_stage': None, 'failure_code': None,
            'error_message': None, 'candidate_count': None,
            'target': {'status': 'not_configured', 'candidate_id': None, 'reason': None},
            'raw_ref': None, 'keypoints_ref': None,
            'timing': {'clock_id': clock_id, **{stage + suffix: None
                for stage in ('decode', 'adapter', 'mapping')
                for suffix in ('_start_ns', '_end_ns')}}}


def run_benchmark(run, adapter=None, source_factory=VideoSource):
    if run.config['adapter_id'] == 'mediapipe_pose':
        from .pose.mediapipe import MediaPipeAdapter, mapping_profile
        adapter = adapter or MediaPipeAdapter()
        profile = mapping_profile()
        execution_kind = 'model_inference'
    else:
        adapter = adapter or FakeAdapter(run.fake_options)
        profile = MappingProfile()
        execution_kind = 'synthetic'
    run_id = uuid.uuid4().hex
    origin = perf_counter_ns()
    clock = lambda: perf_counter_ns() - origin
    clock_id = f'{run_id}:{os.getpid()}'
    mapping_hash = object_hash(asdict(profile))
    directory = Path(run.config['output_root']) / run_id
    manifest = {'run_id': run_id, 'video_id': run.input_manifest['video_id'],
                'execution_kind': execution_kind, 'clock_id': clock_id,
                'clock_origin_ns': str(origin), 'clock_function': 'perf_counter_ns',
                'expected_frames': len(run.rows),
                'width': run.input_manifest['canonical_width'],
                'height': run.input_manifest['canonical_height'],
                'input_manifest_sha256': run.config['input_manifest_sha256'],
                'requested_config': run.requested_config, 'resolved_config': run.config,
                'config_sha256': object_hash(run.requested_config),
                'testing_options': run.fake_options.as_dict() if execution_kind == 'synthetic' else None,
                'mapping_profile': asdict(profile), 'mapping_profile_sha256': mapping_hash,
                'adapter_profile': {}, 'adapter_profile_sha256': object_hash({}),
                'code': _code_version(), 'environment': {
                    'python': platform.python_version(), 'platform': platform.platform(),
                    'av': av.__version__, 'numpy': np.__version__,
                    'jsonschema': version('jsonschema')},
                'pipeline_started_ns': None, 'data_finished_ns': None}
    try:
        recorder = ResultRecorder(directory, manifest, run.input_manifest, run.rows, clock)
    except OSError as exc:
        raise IntegrityError('Could not initialize result storage') from exc
    source = None
    errors = []
    outcome = 'succeeded'
    adapter_hash = object_hash({})
    try:
        init_start = clock()
        metadata = adapter.open(run.config)
        if (metadata.get('adapter_id') != run.config['adapter_id'] or
                metadata.get('execution_kind') != execution_kind):
            raise ValueError('Adapter metadata does not match the requested execution')
        metadata = dict(metadata, execution_mode=run.config['execution_mode'])
        adapter_hash = object_hash(metadata)
        recorder.manifest.update(adapter_profile=metadata, adapter_profile_sha256=adapter_hash)
        warmup_start = clock()
        if run.config['warmup_frames']:
            warm_source = source_factory(run.input_manifest, run.rows, run.video_path)
            try:
                warm_source.open()
                for index in range(run.config['warmup_frames']):
                    frame = warm_source.read_next()
                    if frame is None:
                        raise SourceError('Warm-up reached unexpected end of video')
                    adapter.estimate(ModelInput(frame, run.timestamps[index]))
            finally:
                warm_source.close()
            adapter.close()
            reopened_metadata = dict(adapter.open(run.config), execution_mode=run.config['execution_mode'])
            if reopened_metadata != metadata:
                raise ValueError('Adapter configuration changed after warm-up')
        warmup_end = clock()
        reset_start = clock()
        reset = adapter.reset_for_video()
        reset['elapsed_ns'] = str(clock() - reset_start)
        source = source_factory(run.input_manifest, run.rows, run.video_path)
        source.open()
        recorder.running(adapter_profile=metadata, adapter_profile_sha256=adapter_hash,
                         initialization_elapsed_ns=str(clock() - init_start),
                         warmup_elapsed_ns=str(warmup_end - warmup_start),
                         reset=reset)
        for spec, timestamp in zip(run.rows, run.timestamps):
            row = _frame_row(spec, run_id, clock_id)
            raw = mapped = None
            stage = 'decode'
            if recorder.manifest['pipeline_started_ns'] is None:
                recorder.manifest['pipeline_started_ns'] = str(clock())
            try:
                row['timing']['decode_start_ns'] = str(clock())
                packet = source.read_next()
                if packet is None:
                    raise SourceError('Unexpected end of video')
                row['timing']['decode_end_ns'] = str(clock())
                stage = 'inference'
                row['model_timestamp_ms'] = str(timestamp) if timestamp is not None else None
                row['timing']['adapter_start_ns'] = str(clock())
                output = adapter.estimate(ModelInput(packet, timestamp))
                row['sdk_call_started'] = output.sdk_call_started
                if output.model_timestamp_ms != timestamp:
                    raise AdapterCallError('Adapter returned an inconsistent timestamp')
                row['timing']['adapter_end_ns'] = str(clock())
                row['candidate_count'] = len(output.candidates)
                raw = {'record_type': 'raw_result', 'schema_version': SCHEMA_VERSION,
                       'run_id': run_id, 'video_id': row['video_id'], 'frame_id': row['frame_id'],
                       'record_id': f"frame:{row['frame_id']}",
                       'adapter_profile_sha256': adapter_hash,
                       'model_timestamp_ms': row['model_timestamp_ms'],
                       'candidates': raw_json_value(output.candidates)}
                stage = 'mapping'
                row['timing']['mapping_start_ns'] = str(clock())
                people = map_keypoints(output, packet.width, packet.height, profile)
                row['timing']['mapping_end_ns'] = str(clock())
                mapped = {'record_type': 'mapped_result', 'schema_version': SCHEMA_VERSION,
                          'run_id': run_id, 'video_id': row['video_id'], 'frame_id': row['frame_id'],
                          'record_id': f"frame:{row['frame_id']}",
                          'mapping_profile_sha256': mapping_hash, 'people': people}
            except (Exception, KeyboardInterrupt) as exc:
                timing_stage = 'adapter' if stage == 'inference' else stage
                if row['timing'].get(timing_stage + '_start_ns') is not None:
                    row['timing'][timing_stage + '_end_ns'] = str(clock())
                cancelled = isinstance(exc, KeyboardInterrupt)
                row.update(processing_status='failed',
                           failure_stage='runner' if cancelled else stage,
                           failure_code='USER_CANCELLED' if cancelled else {
                               'decode': 'DECODE_ERROR', 'inference': 'INFERENCE_ERROR',
                               'mapping': 'MAPPING_ERROR'}[stage],
                           error_message=type(exc).__name__,
                           sdk_call_started=row['sdk_call_started'] or bool(getattr(exc, 'sdk_call_started', False)))
                outcome = 'cancelled' if cancelled else 'failed'
                errors.append({'code': row['failure_code'], 'frame_id': row['frame_id'],
                               'exception_type': type(exc).__name__})
            if raw is not None:
                row['raw_ref'] = {'file': 'raw_results.jsonl', 'record_id': raw['record_id']}
            if mapped is not None:
                row['keypoints_ref'] = {'file': 'keypoints.jsonl', 'record_id': mapped['record_id']}
            recorder.append(row, raw, mapped)
            if outcome != 'succeeded':
                break
        if outcome == 'succeeded':
            if source.read_next() is not None:
                raise SourceError('Unexpected additional video frames')
            if file_hash(run.video_path) != run.input_manifest['video_sha256']:
                raise SourceError('Video changed during execution')
    except KeyboardInterrupt:
        outcome = 'cancelled'
        errors.append({'code': 'USER_CANCELLED'})
    except Exception as exc:
        outcome = 'failed'
        errors.append({'code': 'RECORDING_ERROR' if recorder.broken else 'EXECUTION_ERROR',
                       'exception_type': type(exc).__name__})
    finally:
        cleanup_start = clock()
        for resource in (source, adapter):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    outcome = 'failed'
                    errors.append({'code': 'CLEANUP_ERROR', 'exception_type': type(exc).__name__})
        cleanup_ns = str(clock() - cleanup_start)
    try:
        if not recorder.broken:
            for spec in run.rows[recorder.count:]:
                row = _frame_row(spec, run_id, clock_id)
                row.update(processing_status='not_processed', failure_stage='runner',
                           failure_code='USER_CANCELLED' if outcome == 'cancelled' else 'ABORTED_AFTER_ERROR')
                recorder.append(row)
        result = recorder.finalize(outcome, errors, cleanup_elapsed_ns=cleanup_ns)
        return directory, result
    except Exception as exc:
        raise IntegrityError('Result recording could not be completed') from exc
    finally:
        try:
            recorder.close()
        except Exception as exc:
            raise IntegrityError('Result streams could not be closed') from exc
