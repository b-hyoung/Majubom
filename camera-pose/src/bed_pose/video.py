"""Streaming RGB decoding, original PTS manifests and input verification."""
from fractions import Fraction
from pathlib import Path
import hashlib
import os
import re
import sys
import av
import numpy as np
from .contracts import SCHEMA_VERSION, FramePacket, SourceError
from .io_utils import atomic_json, file_hash, json_text, load_json, strict_loads

_HEX = re.compile(r'^[a-f0-9]{64}$')
_SIGNED = re.compile(r'^-?(0|[1-9][0-9]*)$')
_UNSIGNED = re.compile(r'^(0|[1-9][0-9]*)$')
_FRAME_KEYS = {'schema_version', 'video_id', 'frame_id', 'source_pts',
               'time_base_num', 'time_base_den', 'source_duration_pts',
               'timestamp_source', 'pixel_sha256'}


def decoder_profile():
    return {'library': 'PyAV', 'version': av.__version__,
            'ffmpeg_libraries': {k: list(v) for k, v in av.library_versions.items()},
            'video_stream_ordinal': 0, 'pixel_format': 'rgb24',
            'rotation_policy': 'pure_right_angle_ccw_v1',
            'pixel_aspect_policy': 'square_or_unspecified',
            'color_conversion': 'ffmpeg_default_rgb24',
            'byteorder': sys.byteorder}


def canonical_rgb(frame):
    """Apply only verified pure rotation; reject shear, flip and scaling matrices."""
    rotation = frame.rotation
    if rotation not in (-180, -90, 0, 90, 180):
        raise SourceError('Unsupported non-right-angle display rotation')
    for item in frame.side_data:
        if item.type.name == 'DISPLAYMATRIX':
            values = np.frombuffer(bytes(item), dtype=np.int32)
            patterns = ((65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824),
                        (0, -65536, 0, 65536, 0, 0, 0, 0, 1073741824),
                        (-65536, 0, 0, 0, -65536, 0, 0, 0, 1073741824),
                        (0, 65536, 0, -65536, 0, 0, 0, 0, 1073741824))
            if tuple(values) not in patterns:
                raise SourceError('Unsupported display matrix (only pure rotations allowed)')
    rgb = frame.to_ndarray(format='rgb24')
    rgb = np.ascontiguousarray(np.rot90(rgb, rotation // 90))
    rgb.setflags(write=False)
    return rgb


def frame_spec(frame, rgb, video_id, frame_id):
    has_pts = frame.pts is not None and frame.time_base is not None
    return {'schema_version': SCHEMA_VERSION, 'video_id': video_id,
            'frame_id': frame_id,
            'source_pts': str(frame.pts) if has_pts else None,
            'time_base_num': frame.time_base.numerator if has_pts else None,
            'time_base_den': frame.time_base.denominator if has_pts else None,
            'source_duration_pts': str(frame.duration) if has_pts and
                frame.duration is not None and frame.duration >= 0 else None,
            'timestamp_source': 'pts' if has_pts else 'unavailable',
            'pixel_sha256': hashlib.sha256(rgb.tobytes()).hexdigest()}


def frame_time(row):
    if row['timestamp_source'] == 'unavailable':
        return None
    return Fraction(int(row['source_pts']) * row['time_base_num'], row['time_base_den'])


def _stream(container):
    if not container.streams.video:
        raise SourceError('Input contains no video stream')
    stream = container.streams.video[0]
    if stream.sample_aspect_ratio not in (None, 0, 1):
        raise SourceError('Non-square pixel aspect ratio is not supported')
    if stream.metadata.get('rotate', '0') not in ('0', '0.0'):
        raise SourceError('Legacy rotate metadata is not supported; use a display matrix')
    return stream


def prepare_input(video_path, output_dir, run_output_root=None):
    video_path = Path(video_path).resolve()
    digest = file_hash(video_path)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    rows_path = output_dir / 'frames.jsonl'
    manifest_path = output_dir / 'input_manifest.json'
    video_id = 'video_' + digest
    manifest = {'schema_version': SCHEMA_VERSION, 'video_id': video_id,
                'video_sha256': digest, 'video_path': str(video_path),
                'frame_manifest_path': 'frames.jsonl', 'frame_manifest_sha256': None,
                'frame_count': 0, 'complete': False, 'decoder_profile': decoder_profile(),
                'canonical_width': 0, 'canonical_height': 0,
                'timing_status': 'missing_pts', 'errors': []}
    atomic_json(manifest_path, manifest)
    previous = None
    missing = False
    monotonic = True
    rotation = None
    try:
        with rows_path.open('x', encoding='utf-8', newline='\n') as rows, av.open(str(video_path)) as container:
            stream = _stream(container)
            for index, frame in enumerate(container.decode(stream)):
                rgb = canonical_rgb(frame)
                if index == 0:
                    manifest['canonical_height'], manifest['canonical_width'] = rgb.shape[:2]
                    rotation = frame.rotation
                if rgb.shape[:2] != (manifest['canonical_height'], manifest['canonical_width']) or frame.rotation != rotation:
                    raise SourceError('Video geometry changes during the sequence')
                row = frame_spec(frame, rgb, video_id, index)
                time = frame_time(row)
                missing |= time is None
                if time is not None:
                    if previous is not None and time <= previous:
                        monotonic = False
                    previous = time
                rows.write(json_text(row) + '\n')
                manifest['frame_count'] += 1
            rows.flush()
            os.fsync(rows.fileno())
        if not manifest['frame_count']:
            raise SourceError('Input has no decodable frames')
        if file_hash(video_path) != digest:
            raise SourceError('Video changed during preparation')
        manifest['decoder_profile']['rotation_degrees'] = rotation
        manifest['frame_manifest_sha256'] = file_hash(rows_path)
        manifest['timing_status'] = ('missing_pts' if missing else
                                     'valid_pts' if monotonic else 'nonmonotonic_pts')
        manifest['complete'] = True
        atomic_json(manifest_path, manifest)
        root = Path(run_output_root or Path.cwd() / 'runs').resolve()
        try:
            relative_root = os.path.relpath(root, output_dir)
        except ValueError:
            relative_root = str(root)
        atomic_json(output_dir / 'run_config.fake.json', {
            'record_type': 'run_config', 'schema_version': SCHEMA_VERSION,
            'input_manifest_path': 'input_manifest.json',
            'input_manifest_sha256': file_hash(manifest_path),
            'output_root': relative_root, 'adapter_id': 'fake',
            'execution_mode': 'independent_frames', 'requested_device': 'none',
            'assets': [], 'adapter_options_path': None, 'adapter_options_sha256': None,
            'warmup_frames': 0, 'target_policy': 'none', 'error_policy': 'abort'})
        return manifest_path
    except BaseException as exc:
        manifest['complete'] = False
        manifest['errors'].append({'code': 'PREPARATION_ERROR', 'exception_type': type(exc).__name__})
        if rows_path.exists():
            manifest['frame_manifest_sha256'] = file_hash(rows_path)
        atomic_json(manifest_path, manifest)
        raise


def validate_input(manifest_path, expected_hash):
    path = Path(manifest_path).resolve()
    if file_hash(path) != expected_hash:
        raise ValueError('Input manifest checksum mismatch')
    manifest = load_json(path)
    required = {'schema_version', 'video_id', 'video_sha256', 'video_path',
                'frame_manifest_path', 'frame_manifest_sha256', 'frame_count',
                'complete', 'decoder_profile', 'canonical_width', 'canonical_height',
                'timing_status', 'errors'}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError('Invalid input manifest fields')
    if manifest['schema_version'] != SCHEMA_VERSION or manifest['complete'] is not True:
        raise ValueError('Input manifest is not complete or compatible')
    for field in ('frame_count', 'canonical_width', 'canonical_height'):
        if type(manifest[field]) is not int or manifest[field] < 1:
            raise ValueError('Invalid input size or frame count')
    profile = dict(manifest['decoder_profile'])
    rotation = profile.pop('rotation_degrees', None)
    if profile != decoder_profile() or rotation not in (-180, -90, 0, 90, 180):
        raise ValueError('Decoder environment/profile mismatch; prepare again')
    if not isinstance(manifest['video_id'], str) or not manifest['video_id']:
        raise ValueError('Invalid video identifier')
    for field in ('video_sha256', 'frame_manifest_sha256'):
        if not isinstance(manifest[field], str) or not _HEX.fullmatch(manifest[field]):
            raise ValueError('Invalid input checksum')
    if manifest['errors'] != [] or any(not isinstance(manifest[k], str) or not manifest[k] for k in ('video_path', 'frame_manifest_path')):
        raise ValueError('Incomplete input or invalid input paths')
    video_path = (path.parent / manifest['video_path']).resolve()
    frames_path = (path.parent / manifest['frame_manifest_path']).resolve()
    if file_hash(video_path) != manifest['video_sha256']:
        raise ValueError('Video checksum mismatch')
    if file_hash(frames_path) != manifest['frame_manifest_sha256']:
        raise ValueError('Frame manifest checksum mismatch')
    rows = []
    with frames_path.open(encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            row = strict_loads(line)
            if not isinstance(row, dict) or set(row) != _FRAME_KEYS:
                raise ValueError('Invalid frame manifest fields')
            if row['schema_version'] != SCHEMA_VERSION or row['video_id'] != manifest['video_id'] or type(row['frame_id']) is not int or row['frame_id'] != index:
                raise ValueError('Frame manifest identity/order mismatch')
            if row['timestamp_source'] == 'pts':
                if not isinstance(row['source_pts'], str) or not _SIGNED.fullmatch(row['source_pts']):
                    raise ValueError('Invalid PTS')
                if any(type(row[k]) is not int or row[k] < 1 for k in ('time_base_num', 'time_base_den')):
                    raise ValueError('Invalid time base')
                duration = row['source_duration_pts']
                if duration is not None and (not isinstance(duration, str) or not _UNSIGNED.fullmatch(duration)):
                    raise ValueError('Invalid frame duration')
            elif row['timestamp_source'] == 'unavailable':
                if any(row[k] is not None for k in ('source_pts', 'time_base_num', 'time_base_den', 'source_duration_pts')):
                    raise ValueError('Unknown PTS must remain null')
            else:
                raise ValueError('Invalid timestamp source')
            if row['pixel_sha256'] is not None and (not isinstance(row['pixel_sha256'], str) or not _HEX.fullmatch(row['pixel_sha256'])):
                raise ValueError('Invalid pixel checksum')
            rows.append(row)
    if len(rows) != manifest['frame_count']:
        raise ValueError('Frame count mismatch')
    times = [frame_time(row) for row in rows]
    status = ('missing_pts' if any(t is None for t in times) else
              'valid_pts' if all(b > a for a, b in zip(times, times[1:])) else 'nonmonotonic_pts')
    if status != manifest['timing_status']:
        raise ValueError('Timing status mismatch')
    return manifest, rows, video_path


def model_timestamps(rows, mode):
    if mode == 'independent_frames':
        return [None] * len(rows)
    times = [frame_time(row) for row in rows]
    if not times or any(t is None for t in times):
        raise ValueError('Ordered sequence requires original PTS')
    values = [int((time - times[0]) * 1000 // 1) for time in times]
    if any(b <= a for a, b in zip(times, times[1:])) or any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError('PTS do not form strictly increasing SDK milliseconds')
    return values


class VideoSource:
    def __init__(self, manifest, rows, video_path):
        self.manifest, self.rows, self.video_path = manifest, rows, video_path
        self.container = None
        self.index = 0

    def open(self):
        self.container = av.open(str(self.video_path))
        try:
            self.iterator = iter(self.container.decode(_stream(self.container)))
        except BaseException:
            self.close()
            raise

    def read_next(self):
        try:
            frame = next(self.iterator)
        except StopIteration:
            if self.index != len(self.rows):
                raise SourceError('Unexpected end of video') from None
            return None
        except Exception as exc:
            raise SourceError('Video decode failed') from exc
        if self.index >= len(self.rows):
            raise SourceError('Video contains extra frames')
        rgb = canonical_rgb(frame)
        row = frame_spec(frame, rgb, self.manifest['video_id'], self.index)
        expected = self.rows[self.index]
        if (rgb.shape[:2] != (self.manifest['canonical_height'], self.manifest['canonical_width'])
                or frame.rotation != self.manifest['decoder_profile']['rotation_degrees']):
            raise SourceError('Video geometry differs from preparation')
        for key in _FRAME_KEYS:
            if key == 'pixel_sha256' and expected[key] is None:
                continue
            if row[key] != expected[key]:
                raise SourceError('Decoded frame differs from preparation')
        result = FramePacket(self.manifest['video_id'], self.index, rgb, dict(expected))
        self.index += 1
        return result

    def close(self):
        if self.container is not None:
            self.container.close()
            self.container = None
