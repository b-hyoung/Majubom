"""Append-only frame ledger; completion and execution outcome are independent."""
import json
import os
from pathlib import Path


class FrameLedger:
    def __init__(self, directory, expected_frames):
        if type(expected_frames) is not int or expected_frames < 1:
            raise ValueError('expected_frames must be positive')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.expected = expected_frames
        self.count = 0
        self.errors = 0
        self.closed = False
        self._manifest('running', False)
        self.stream = (self.directory / 'frames.jsonl').open('x', encoding='utf-8')

    def _manifest(self, outcome, complete):
        temp = self.directory / 'manifest.tmp'
        with temp.open('w', encoding='utf-8') as stream:
            json.dump({'run_status': outcome, 'ledger_complete': complete,
                       'expected_frames': self.expected, 'recorded_frames': self.count},
                      stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.directory / 'manifest.json')

    def append(self, frame_id, status, payload=None):
        if self.closed or type(frame_id) is not int or frame_id != self.count or self.count >= self.expected:
            raise ValueError('Frames must be written once in manifest order')
        if status not in ('ok', 'failed', 'not_processed'):
            raise ValueError('Invalid processing status')
        line = json.dumps({'frame_id': frame_id, 'processing_status': status,
                           'payload': payload}, allow_nan=False)
        self.stream.write(line + '\n')
        self.stream.flush()
        self.count += 1
        self.errors += status != 'ok'

    def finalize(self, outcome):
        if self.closed or outcome not in ('succeeded', 'failed', 'cancelled'):
            raise ValueError('Invalid finalization')
        if self.count != self.expected or (outcome == 'succeeded' and self.errors):
            raise ValueError('Cannot declare successful or complete execution')
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        self.closed = True
        self._manifest(outcome, True)

    def close(self):
        if not self.closed:
            self.stream.close()
            self.closed = True


# FrameLedger above remains a legacy prototype for compatibility. The executable
# benchmark exclusively uses ResultRecorder and the formal FrameResult contract.
import csv
from collections import Counter
from time import perf_counter_ns
from .config import record_validator
from .contracts import IntegrityError, SCHEMA_VERSION
from .io_utils import atomic_json, file_hash, json_text, load_json, strict_loads
from .validation import validate_bundle

_RESULT_FILES = ('raw_results.jsonl', 'keypoints.jsonl', 'frame_results.jsonl',
                 'write_timings.jsonl', 'failure_cases.csv')
_SOURCE_FIELDS = ('source_pts', 'time_base_num', 'time_base_den',
                  'source_duration_pts', 'timestamp_source')


def _read_required(stream):
    line = stream.readline()
    if not line:
        raise IntegrityError('Missing result record')
    return strict_loads(line)


def _verify_records(directory, manifest=None, check_hashes=True):
    """Verify all frame references and counts, without replaying the model."""
    from contextlib import ExitStack
    directory = Path(directory)
    manifest = manifest or load_json(directory / 'manifest.json')
    if manifest['schema_version'] != SCHEMA_VERSION:
        raise IntegrityError('Unknown run schema version')
    if type(manifest['expected_frames']) is not int or manifest['expected_frames'] < 1:
        raise IntegrityError('Invalid expected frame count')
    if any(type(manifest['counts'].get(key)) is not int for key in ('ok', 'failed', 'not_processed')):
        raise IntegrityError('Invalid manifest counters')
    if manifest['run_status'] not in ('succeeded', 'failed', 'cancelled'):
        raise IntegrityError('Run is not in a final state')
    if check_hashes and manifest['ledger_complete'] is not True:
        raise IntegrityError('Run ledger is incomplete')
    if check_hashes:
        expected_files = {*_RESULT_FILES, 'input_frames.jsonl', 'input_manifest.snapshot.json'}
        if set(manifest['artifact_sha256']) != expected_files:
            raise IntegrityError('Unexpected artifact hash inventory')
        for name, digest in manifest['artifact_sha256'].items():
            if file_hash(directory / name) != digest:
                raise IntegrityError('Artifact checksum mismatch')
    counts = Counter(ok=0, failed=0, not_processed=0)
    validator = record_validator()
    with ExitStack() as stack:
        streams = {name: stack.enter_context((directory / name).open(encoding='utf-8'))
                   for name in ('raw_results.jsonl', 'keypoints.jsonl',
                                'frame_results.jsonl', 'write_timings.jsonl', 'input_frames.jsonl')}
        n = 0
        for index, source_line in enumerate(streams['input_frames.jsonl']):
            spec = strict_loads(source_line)
            row = _read_required(streams['frame_results.jsonl'])
            if (row['run_id'] != manifest['run_id'] or row['video_id'] != manifest['video_id']
                    or row['frame_id'] != index or spec['frame_id'] != index
                    or spec['video_id'] != manifest['video_id']
                    or any(row[k] != spec[k] for k in _SOURCE_FIELDS)):
                raise IntegrityError('Frame identity/source timestamp mismatch')
            if row['timing']['clock_id'] != manifest['clock_id']:
                raise IntegrityError('Clock identity mismatch')
            raw = _read_required(streams['raw_results.jsonl']) if row['raw_ref'] else None
            mapped = _read_required(streams['keypoints.jsonl']) if row['keypoints_ref'] else None
            validate_bundle(row, raw, mapped, validator,
                            manifest['width'], manifest['height'])
            if raw is not None and raw['adapter_profile_sha256'] != manifest['adapter_profile_sha256']:
                raise IntegrityError('Adapter profile reference mismatch')
            if mapped is not None and mapped['mapping_profile_sha256'] != manifest['mapping_profile_sha256']:
                raise IntegrityError('Mapping profile reference mismatch')
            receipt = _read_required(streams['write_timings.jsonl'])
            if (receipt['frame_id'] != index or receipt['run_id'] != manifest['run_id']
                    or receipt['video_id'] != manifest['video_id']
                    or receipt['clock_id'] != manifest['clock_id']
                    or int(receipt['finished_ns']) < int(receipt['started_ns'])):
                raise IntegrityError('Write receipt mismatch')
            receipt_start, receipt_end = int(receipt['started_ns']), int(receipt['finished_ns'])
            stage_ends = [int(value) for key, value in row['timing'].items()
                          if key.endswith('_end_ns') and value is not None]
            if receipt_start < 0 or (stage_ends and receipt_start < max(stage_ends)):
                raise IntegrityError('Write receipt precedes frame processing')
            if receipt['schema_version'] != SCHEMA_VERSION:
                raise IntegrityError('Write receipt schema mismatch')
            counts[row['processing_status']] += 1
            n += 1
        if n != manifest['expected_frames']:
            raise IntegrityError('Unexpected input frame snapshot count')
        for name, stream in streams.items():
            if stream.readline():
                raise IntegrityError('Unreferenced/extra result records: ' + name)
    if sum(counts.values()) != manifest['expected_frames'] or dict(counts) != manifest['counts']:
        raise IntegrityError('Result counter mismatch')
    if manifest['run_status'] == 'succeeded' and (counts['failed'] or counts['not_processed'] or manifest['errors']):
        raise IntegrityError('Failed frame results cannot be declared successful')
    return dict(counts)


def verify_records(directory, manifest=None, check_hashes=True):
    try:
        return _verify_records(directory, manifest, check_hashes)
    except IntegrityError:
        raise
    except Exception as exc:
        raise IntegrityError(f'Result validation failed ({type(exc).__name__})') from exc


class ResultRecorder:
    """Formal multi-file frame recorder with fail-closed finalization."""
    def __init__(self, directory, manifest, source_manifest, rows, clock):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.manifest = dict(manifest, schema_version=SCHEMA_VERSION,
                             run_status='initializing', ledger_complete=False,
                             artifact_sha256={}, errors=[], counts={
                                 'ok': 0, 'failed': 0, 'not_processed': 0})
        self.rows, self.clock = rows, clock
        self.count = 0
        self.streams = {}
        self.closed = False
        self.broken = False
        self.validator = record_validator()
        self._save_manifest()
        try:
            atomic_json(self.directory / 'input_manifest.snapshot.json', source_manifest)
            with (self.directory / 'input_frames.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
                for row in rows:
                    stream.write(json_text(row) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            for name in _RESULT_FILES:
                self.streams[name] = (self.directory / name).open('x', encoding='utf-8', newline='')
            self.failure_writer = csv.writer(self.streams['failure_cases.csv'])
            self.failure_writer.writerow(['frame_id', 'failure_stage', 'failure_code'])
            self.streams['failure_cases.csv'].flush()
        except BaseException:
            self.broken = True
            self.close()
            raise

    def _save_manifest(self):
        atomic_json(self.directory / 'manifest.json', self.manifest)

    def running(self, **metadata):
        self.manifest.update(metadata, run_status='running')
        self._save_manifest()

    def append(self, row, raw=None, mapped=None):
        started = self.clock()
        if self.closed or self.broken:
            raise IntegrityError('Recorder is closed or incomplete')
        try:
            if (type(row['frame_id']) is not int or row['frame_id'] != self.count
                    or self.count >= len(self.rows)
                    or row['run_id'] != self.manifest['run_id']
                    or row['video_id'] != self.manifest['video_id']
                    or any(row[k] != self.rows[self.count][k] for k in _SOURCE_FIELDS)):
                raise IntegrityError('Frames must follow the fixed input manifest')
            validate_bundle(row, raw, mapped, self.validator,
                            self.manifest['width'], self.manifest['height'])
            serialized = [(name, json_text(value) + '\n') for name, value in (
                ('raw_results.jsonl', raw), ('keypoints.jsonl', mapped),
                ('frame_results.jsonl', row)) if value is not None]
            for name, line in serialized:
                self.streams[name].write(line)
                self.streams[name].flush()
            if row['processing_status'] != 'ok':
                self.failure_writer.writerow([row['frame_id'], row['failure_stage'], row['failure_code']])
                self.streams['failure_cases.csv'].flush()
            receipt = {'schema_version': SCHEMA_VERSION, 'run_id': row['run_id'],
                       'video_id': row['video_id'], 'frame_id': row['frame_id'],
                       'clock_id': self.manifest['clock_id'],
                       'started_ns': str(started), 'finished_ns': str(self.clock())}
            self.streams['write_timings.jsonl'].write(json_text(receipt) + '\n')
            self.streams['write_timings.jsonl'].flush()
            self.count += 1
            self.manifest['counts'][row['processing_status']] += 1
            return receipt
        except BaseException:
            self.broken = True
            raise

    def _close_streams(self, sync):
        errors = []
        for stream in self.streams.values():
            if not stream.closed:
                try:
                    if sync:
                        stream.flush()
                        os.fsync(stream.fileno())
                except Exception as exc:
                    errors.append(exc)
                finally:
                    try:
                        stream.close()
                    except Exception as exc:
                        errors.append(exc)
        if errors:
            self.broken = True
            raise errors[0]

    def finalize(self, outcome, errors=(), **metadata):
        if self.closed or outcome not in ('succeeded', 'failed', 'cancelled'):
            raise IntegrityError('Invalid recorder finalization')
        self.manifest.update(metadata, run_status=outcome, errors=list(errors))
        try:
            self._close_streams(sync=True)
            self.manifest['data_finished_ns'] = str(self.clock())
            if not self.broken and self.count == len(self.rows):
                counts = verify_records(self.directory, self.manifest, check_hashes=False)
                if counts != self.manifest['counts']:
                    raise IntegrityError('In-memory and persisted counts differ')
                if outcome == 'succeeded' and (counts['failed'] or counts['not_processed']):
                    raise IntegrityError('Cannot declare a failed pipeline successful')
                self.manifest['ledger_complete'] = True
            elif outcome == 'succeeded':
                raise IntegrityError('Cannot finalize an incomplete pipeline successfully')
            names = (*_RESULT_FILES, 'input_frames.jsonl', 'input_manifest.snapshot.json')
            self.manifest['artifact_sha256'] = {name: file_hash(self.directory / name)
                for name in names if (self.directory / name).is_file()}
            self._save_manifest()
        except Exception:
            self.manifest.update(run_status='failed', ledger_complete=False)
            self.manifest['errors'].append({'code': 'RECORDING_ERROR'})
            try:
                self._save_manifest()
            except Exception:
                pass
            raise
        finally:
            self.closed = True
            self._close_streams(sync=False)
        return dict(self.manifest)

    def close(self):
        self.closed = True
        self._close_streams(sync=False)
