"""Live recording contract: received timestamps, explicit unknowns, no video data."""
from .replay_compare import equivalent
from collections import Counter
from fractions import Fraction
from pathlib import Path
import math
import os
from .contracts import IntegrityError
from .io_utils import atomic_json, file_hash, json_text, load_json, strict_loads, object_hash

LIVE_SCHEMA = 'live-0.3-draft'
SUPPORTED_SCHEMAS = ('live-0.1-draft', 'live-0.2-draft', LIVE_SCHEMA)
FILES = ('live_frames.jsonl', 'control_events.jsonl')


def unsigned_ns(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise IntegrityError('Invalid live timestamp')
    return int(value)


def validate_region(region, width, height):
    from .bed_geometry import is_quad,make_quadrilateral
    if is_quad(region):
        if region!=make_quadrilateral(region.get('vertices'),width,height):raise ValueError('네 점 영역의 메타데이터가 맞지 않습니다.')
        return
    if (not isinstance(region, dict) or set(region) != {'x1', 'y1', 'x2', 'y2', 'width', 'height', 'normalized', 'method'}
            or region['width'] != width or region['height'] != height or region['method'] != 'manual_rectangle'):
        raise ValueError('침대 영역의 영상 크기 또는 형식이 맞지 않습니다.')
    box = [region[k] for k in ('x1', 'y1', 'x2', 'y2')]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
        raise ValueError('침대 영역 좌표가 유효하지 않습니다.')
    x1, y1, x2, y2 = box
    if not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height:
        raise ValueError('침대 영역을 영상 안의 사각형으로 지정하세요.')
    expected = [x1/width, y1/height, x2/width, y2/height]
    if region['normalized'] != expected:
        raise ValueError('침대 영역 정규화 좌표가 맞지 않습니다.')


def make_region(box, width, height):
    x1, y1, x2, y2 = box
    region = {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'width': width, 'height': height,
              'normalized': [x1/width, y1/height, x2/width, y2/height], 'method': 'manual_rectangle'}
    validate_region(region, width, height)
    return region


def validate_selection(selection, effective_frame):
    if (selection['effective_stream_frame_id'] != effective_frame
            or type(effective_frame) is not int or effective_frame < 0):
        raise IntegrityError('Invalid bed selection frame boundary')
    selected = selection['selected_stream_frame_id']
    if selected is not None and (type(selected) is not int or not 0 <= selected < effective_frame):
        raise IntegrityError('Invalid selected preview frame')
    applied = unsigned_ns(selection['applied_time_ns'])
    unsigned_ns(selection['applied_utc_ns'])
    if selection['request'] is not None and unsigned_ns(selection['request']['requested_time_ns']) > applied:
        raise IntegrityError('Bed selection request occurs after application')
    return applied


def _verify(directory, manifest, hashes=True):
    if (any(type(manifest[k]) is not int or manifest[k] < 1 for k in ('width','height'))
            or type(manifest['initial_bed_revision']) is not int or manifest['initial_bed_revision'] < (0 if manifest['schema_version']==LIVE_SCHEMA else 1)
            or type(manifest['recorded_frames']) is not int or manifest['recorded_frames'] < 0
            or type(manifest['recorded_events']) is not int or manifest['recorded_events'] < 2
            or any(type(manifest['counts'][k]) is not int or manifest['counts'][k] < 0 for k in ('ok','failed'))):
        raise IntegrityError('Invalid live manifest counters or geometry')
    if manifest['schema_version'] not in SUPPORTED_SCHEMAS:
        raise IntegrityError('Unknown live schema')
    for key in ('adapter_profile','source_profile','mapping_profile','requested_settings'):
        if object_hash(manifest[key]) != manifest[key+'_sha256']:
            raise IntegrityError('Live profile/settings checksum mismatch')
    if manifest['execution_mode'] != manifest['requested_settings']['execution_mode']:
        raise IntegrityError('Live execution mode mismatch')
    if hashes:
        if manifest['ledger_complete'] is not True or manifest['run_status'] not in ('succeeded', 'failed'):
            raise IntegrityError('Live measurement is incomplete')
        if set(manifest['artifact_sha256']) != set(FILES) or any(
                file_hash(directory/name) != manifest['artifact_sha256'][name] for name in FILES):
            raise IntegrityError('Live artifact checksum mismatch')
    region = manifest['initial_bed_region']
    if region is not None:validate_region(region, manifest['width'], manifest['height'])
    elif (manifest['schema_version']!=LIVE_SCHEMA
          or (manifest.get('automatic_posture_profile') or {}).get('version') not in ('automatic-posture-rules-0.7','automatic-posture-rules-0.8','automatic-posture-rules-0.9','automatic-posture-rules-0.10','automatic-posture-rules-0.11','automatic-posture-rules-0.12')):
        raise IntegrityError('Initial bed region is required by the recorded mode')
    selection = manifest['initial_bed_selection']
    if selection is None:
        if region is not None or manifest['initial_bed_revision']!=0:raise IntegrityError('Missing initial bed selection')
    elif (selection['effective_stream_frame_id'] > manifest['first_stream_frame_id']
            or validate_selection(selection, selection['effective_stream_frame_id']) > unsigned_ns(manifest['started_ns'])):
        raise IntegrityError('Initial bed selection occurs after measurement start')
    if 'bed_target_selection_profile' in manifest:
        from .bed_association import PROFILE as BED_TARGET_PROFILE
        if manifest['bed_target_selection_profile']!=BED_TARGET_PROFILE:raise IntegrityError('Unsupported bed target profile')
    if 'decision_evidence_profile' in manifest:
        from .decision_evidence import PROFILE as DECISION_PROFILE
        if manifest['decision_evidence_profile']!=DECISION_PROFILE:raise IntegrityError('Unsupported decision evidence profile')
    if 'posture_assessment_profile' in manifest:
        from .posture import PROFILE
        if manifest['posture_assessment_profile']!=PROFILE:raise IntegrityError('Unsupported posture assessment profile')
    if 'posture_evidence_profile' in manifest:
        from .posture_evidence import PROFILE as EVIDENCE_PROFILE
        if manifest['posture_evidence_profile']!=EVIDENCE_PROFILE:raise IntegrityError('Unsupported posture evidence profile')
    if 'native_pose_cues_profile' in manifest:
        from .native_pose_cues import PROFILE as NATIVE_PROFILE
        if manifest['native_pose_cues_profile']!=NATIVE_PROFILE:raise IntegrityError('Unsupported native pose cues profile')
    if 'silhouette_profile' in manifest:
        from .silhouette import PROFILE as SILHOUETTE_PROFILE
        if manifest['silhouette_profile']!=SILHOUETTE_PROFILE:raise IntegrityError('Unsupported silhouette profile')
    from .posture import load_classifier
    reference_model=load_classifier(manifest)
    state_mode=(manifest.get('automatic_posture_profile') or {}).get('version') in ('automatic-posture-rules-0.8','automatic-posture-rules-0.9','automatic-posture-rules-0.10','automatic-posture-rules-0.11','automatic-posture-rules-0.12')
    if state_mode and 'body_state_provider_profile' not in manifest:
        raise IntegrityError('Missing body-state provider profile')
    if 'person_crop_profile' in manifest:
        from .person_detector import validate_profile as validate_detector
        try:validate_detector(manifest['person_crop_profile'])
        except (ValueError,TypeError,KeyError) as exc:raise IntegrityError('Invalid person crop profile') from exc
        if not state_mode:raise IntegrityError('Person crop requires image provider')
    provider_profile=manifest.get('body_state_provider_profile')
    if provider_profile is not None:
        from .body_state import validate_profile
        if not state_mode:raise IntegrityError('Unexpected body-state provider profile')
        try:validate_profile(provider_profile,manifest['execution_kind'])
        except ValueError as exc:raise IntegrityError('Invalid body-state provider profile') from exc
    stabilizer=None
    if 'blackbox_segment' in manifest:
        from .blackbox_storage import validate_segment
        try:
            validate_segment(manifest['blackbox_segment'])
            if (manifest.get('storage_class')!='blackbox' or 'initial_stability_checkpoint' not in manifest
                    or 'stability_profile' not in manifest):
                raise ValueError('Invalid blackbox segment metadata')
        except (ValueError,TypeError) as exc:raise IntegrityError('Invalid blackbox segment metadata') from exc
    if 'stability_profile' in manifest:
        from .posture_stability import Stabilizer,profile as stability_profile
        try:
            if manifest['stability_profile']!=stability_profile(manifest['stability_profile']):raise ValueError('Invalid stability profile')
            stabilizer=Stabilizer(manifest['stability_profile'])
            if 'initial_stability_checkpoint' in manifest:
                if manifest.get('storage_class')!='blackbox' or 'blackbox_segment' not in manifest:
                    raise ValueError('Unexpected stability checkpoint')
                stabilizer.restore(manifest['initial_stability_checkpoint'])
                if stabilizer.sequence is not None and stabilizer.sequence >= manifest['first_stream_frame_id']:
                    raise ValueError('Invalid checkpoint frame boundary')
        except (ValueError,TypeError) as exc:raise IntegrityError('Invalid stability profile') from exc
    revision = manifest['initial_bed_revision']
    regions={manifest['initial_bed_revision']:manifest['initial_bed_region']}
    changes = []
    event_types = []
    previous_effective_id = manifest['first_stream_frame_id']
    with (directory/'control_events.jsonl').open(encoding='utf-8') as stream:
        previous_time = 0
        for index, line in enumerate(stream):
            event = strict_loads(line)
            if event['type']=='bed_region_changed':regions[event['revision']]=event['region']
            if (event['event_index'] != index or event['measurement_id'] != manifest['measurement_id']
                    or event['clock_id'] != manifest['clock_id'] or unsigned_ns(event['time_ns']) < previous_time):
                raise IntegrityError('Invalid control event identity/order')
            previous_time = unsigned_ns(event['time_ns'])
            request = event.get('request')
            if request is not None:
                if unsigned_ns(request['requested_time_ns']) > previous_time:
                    raise IntegrityError('Control request occurs after its application')
                unsigned_ns(request['requested_utc_ns'])
            event_types.append(event['type'])
            effective_id = event['effective_stream_frame_id']
            if type(effective_id) is not int or not previous_effective_id <= effective_id <= manifest['first_stream_frame_id']+manifest['recorded_frames']:
                raise IntegrityError('Invalid event frame boundary')
            previous_effective_id = effective_id
            if event['type']=='target_reselected':
                if event['tracking']['identity_verified'] is not False or not event['tracking']['track_id']:
                    raise IntegrityError('Invalid provisional target selection')
            if event['type'] == 'playback_changed':
                if manifest['source_profile']['kind'] != 'video_file' or type(event['paused']) is not bool:
                    raise IntegrityError('Invalid playback control')
            if event['type'] == 'bed_region_changed':
                if validate_selection(event['selection'], effective_id) > previous_time:
                    raise IntegrityError('Bed selection metadata occurs after the control event')
                if event['revision'] <= revision:
                    raise IntegrityError('Bed revisions are not increasing')
                revision = event['revision']
                if event['region'] is not None:
                    validate_region(event['region'], manifest['width'], manifest['height'])
                changes.append((event['effective_stream_frame_id'], revision))
    if (not event_types or event_types[0] != 'measurement_started' or event_types[-1] != 'measurement_stopped'
            or event_types.count('measurement_started') != 1 or event_types.count('measurement_stopped') != 1
            or len(event_types) != manifest['recorded_events']
            or any(kind not in ('measurement_started', 'measurement_stopped', 'bed_region_changed', 'playback_changed', 'target_reselected') for kind in event_types)):
        raise IntegrityError('Live measurement control events are incomplete')
    last_event_time = previous_time
    counts = Counter(ok=0, failed=0)
    previous_id, previous_time, previous_model_ms = -1, 0, None
    previous_media_id, media_origin = None, None
    previous_capture_id=None
    latest_capture=manifest['source_profile'].get('capture_policy')=='latest_camera_frame_v1'
    if latest_capture and manifest['source_profile']['kind']!='camera':
        raise IntegrityError('Latest capture timing requires a camera source')
    previous_fields={}
    with (directory/'live_frames.jsonl').open(encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            row = strict_loads(line)
            if (row['schema_version'] != manifest['schema_version'] or row['measurement_id'] != manifest['measurement_id']
                    or row['frame_id'] != index or row['stream_id'] != manifest['stream_id']
                    or row['clock_id'] != manifest['clock_id']
                    or row['stream_frame_id'] != manifest['first_stream_frame_id'] + index):
                raise IntegrityError('Invalid live frame identity/order')
            if manifest['source_profile'].get('capture_policy')=='latest_camera_frame_v1':
                capture=row.get('camera_capture',{})
                capture_id=unsigned_ns(capture.get('capture_frame_id'))
                skipped=unsigned_ns(capture.get('skipped_before'))
                if previous_capture_id is not None and (capture_id<=previous_capture_id or skipped!=capture_id-previous_capture_id-1):
                    raise IntegrityError('Invalid camera capture sequence/skipped count')
                previous_capture_id=capture_id
            previous_id = row['stream_frame_id']
            expected_revision = manifest['initial_bed_revision']
            for effective_id, change_revision in changes:
                if effective_id <= previous_id:
                    expected_revision = change_revision
            if row['bed_region_revision'] != expected_revision:
                raise IntegrityError('Live frame references the wrong bed revision')
            previous_record_time=previous_time
            if latest_capture:previous_time=0  # Capture may overlap the preceding analysis.
            for field in ('read_started_ns', 'received_ns', 'inference_started_ns', 'inference_finished_ns', 'mapping_finished_ns', 'record_started_ns'):
                value = row[field]
                if value is not None:
                    value = unsigned_ns(value)
                    if value < previous_time:
                        raise IntegrityError('Live frame timing is not monotonic')
                    if latest_capture:
                        if value < previous_fields.get(field,0):
                            raise IntegrityError('Live stage timing moved backwards')
                        if field=='read_started_ns' and value < previous_fields.get('received_ns',0):
                            raise IntegrityError('Camera read overlaps a preceding camera read')
                        if field=='inference_started_ns' and value < previous_record_time:
                            raise IntegrityError('Sequential analyses overlap')
                        previous_fields[field]=value
                    previous_time = value
            unsigned_ns(row['received_utc_ns'])
            if row['camera_exposure_ns'] is not None or row['timestamp_source'] != 'received_after_read':
                raise IntegrityError('Unverified camera exposure timestamp')
            model_ms = unsigned_ns(row['model_timestamp_ms'])
            if manifest['source_profile']['kind'] == 'video_file':
                media = row['source_media']
                media_ns = unsigned_ns(media['relative_time_ns'])
                if (type(media['source_frame_id']) is not int or media['source_frame_id'] != row['stream_frame_id']
                        or previous_media_id is not None and media['source_frame_id'] != previous_media_id+1
                        or type(media['time_base_num']) is not int or media['time_base_num'] <= 0
                        or type(media['time_base_den']) is not int or media['time_base_den'] <= 0):
                    raise IntegrityError('Invalid video source identity/time base')
                absolute = Fraction(int(media['source_pts'])*media['time_base_num'], media['time_base_den'])
                if media_origin is None:
                    # Floor to ns permits less than 1 ns rounding uncertainty.
                    media_origin = absolute-Fraction(media_ns, 1_000_000_000)
                if abs((absolute-media_origin)*1_000_000_000-media_ns) >= 1 or model_ms != media_ns//1_000_000:
                    raise IntegrityError('Video model time differs from source PTS')
                previous_media_id = media['source_frame_id']
            elif 'source_media' in row or model_ms != unsigned_ns(row['received_ns']) // 1_000_000:
                raise IntegrityError('Live SDK time differs from the recorded receive time')
            collision = (row['processing_status'] == 'failed' and row['error'] is not None
                         and row['error']['code'] == 'MODEL_TIMESTAMP_COLLISION' and not row['sdk_call_started'])
            if previous_model_ms is not None and model_ms <= previous_model_ms and not collision:
                raise IntegrityError('Live model timestamp is not increasing')
            if not collision:
                previous_model_ms = model_ms
            from .posture import assess_frame
            if 'bed_target_selection_profile' in manifest or 'bed_target_selection' in row:
                from .bed_association import select as select_bed_target
                if ('bed_target_selection_profile' not in manifest or 'bed_target_selection' not in row
                        or row['bed_target_selection']!=select_bed_target(row,regions[row['bed_region_revision']],manifest['width'],manifest['height'])):
                    raise IntegrityError('Bed target selection differs from source observations')
            if state_mode != ('body_state_observation' in row):
                raise IntegrityError('Missing or unexpected independent body-state observation')
            if 'person_crop_profile' in manifest or 'person_crop_observation' in row:
                from .person_crop import validate as validate_crop
                if 'person_crop_profile' not in manifest or 'person_crop_observation' not in row:raise IntegrityError('Missing person crop extension')
                try:validate_crop(row['person_crop_observation'],row,manifest['width'],manifest['height'],manifest['person_crop_profile'])
                except (ValueError,TypeError,KeyError) as exc:raise IntegrityError('Invalid person crop evidence') from exc
            if state_mode:
                from .body_state import validate_observation
                selection=assess_frame(row,manifest['width'],manifest['height'],None)
                try:validate_observation(row['body_state_observation'],row,selection['candidate_id'],provider_profile)
                except (ValueError,TypeError) as exc:raise IntegrityError('Invalid body-state observation provenance') from exc
                start=unsigned_ns(row['body_state_started_ns']);finish=unsigned_ns(row['body_state_finished_ns'])
                lower=unsigned_ns(row['mapping_finished_ns'] or row['inference_finished_ns'] or row['received_ns'])
                if not lower<=start<=finish<=unsigned_ns(row['record_started_ns']):
                    raise IntegrityError('Invalid body-state processing interval')
            expected_assessment=assess_frame(row,manifest['width'],manifest['height'],regions[row['bed_region_revision']],reference_model) if reference_model else None
            expected_prediction=({'status':'experimental' if expected_assessment['label'] else 'held','label':expected_assessment['label']} if reference_model else {'status':'not_implemented','label':None})
            if (row['posture_prediction'] != expected_prediction
                    or row['event_predictions'] != [] or row['event_analysis_status'] != 'not_implemented'
                    or row['bed_occupancy_status'] != 'not_implemented'
                    or row['target_resolution'] != {'status': 'not_configured', 'candidate_id': None}
                    or any(row[k] is not None for k in ('target_posture', 'verified_gt', 'sensor_results'))):
                raise IntegrityError('Unsupported live prediction or label injection')
            if ('posture_assessment_profile' in manifest or reference_model) and 'posture_assessment' not in row:
                raise IntegrityError('Missing posture assessment')
            if 'posture_assessment' in row:
                from .posture import assess_frame
                if not equivalent(row['posture_assessment'],assess_frame(row,manifest['width'],manifest['height'],regions[row['bed_region_revision']],reference_model)):
                    raise IntegrityError('Posture assessment differs from source frame')
            if stabilizer is not None or 'stable_posture' in row:
                from .posture_stability import frame as stabilize
                if 'posture_assessment' not in row:raise IntegrityError('Missing posture assessment for stability')
                if stabilizer is None or 'stable_posture' not in row or not equivalent(row['stable_posture'],stabilize(stabilizer,row)):
                    raise IntegrityError('Stable posture differs from original observation sequence')
            if 'decision_evidence_profile' in manifest or 'decision_evidence' in row:
                if 'posture_assessment' not in row:raise IntegrityError('Missing posture assessment for decision evidence')
                from .decision_evidence import PROFILE as DECISION_PROFILE, single
                if (manifest.get('decision_evidence_profile')!=DECISION_PROFILE or 'decision_evidence' not in row
                        or not equivalent(row['decision_evidence'],single(row,provider_profile))):
                    raise IntegrityError('Decision evidence differs from source frame')
            if 'native_pose_cues_profile' in manifest or 'native_pose_cues' in row:
                from .native_pose_cues import PROFILE as NATIVE_PROFILE, assess as assess_native
                if 'posture_assessment' not in row:raise IntegrityError('Missing posture assessment for native cues')
                if manifest.get('native_pose_cues_profile') != NATIVE_PROFILE or 'native_pose_cues' not in row:
                    raise IntegrityError('Missing or unsupported native pose cues profile')
                if not equivalent(row['native_pose_cues'],assess_native(row,manifest['width'],manifest['height'])):
                    raise IntegrityError('Native pose cues differ from source frame')
            if 'posture_evidence_profile' in manifest or 'posture_evidence' in row:
                from .posture_evidence import compare_frame
                if 'posture_evidence_profile' not in manifest or 'posture_evidence' not in row:
                    raise IntegrityError('Missing posture evidence or profile')
                if 'posture_assessment' not in row:
                    raise IntegrityError('Missing posture assessment for evidence')
                expected_evidence=compare_frame(row,manifest['width'],manifest['height'],row['posture_assessment'])
                if not equivalent(row['posture_evidence'],expected_evidence):
                    raise IntegrityError('Posture evidence differs from source frame')
            if 'silhouette_profile' in manifest or 'silhouette_assessment' in row or 'silhouette_observation' in row:
                from .silhouette import assess as assess_silhouette
                if ('silhouette_profile' not in manifest or 'silhouette_assessment' not in row
                        or 'silhouette_observation' not in row or 'posture_assessment' not in row):
                    raise IntegrityError('Missing silhouette observation, assessment or profile')
                if not equivalent(row['silhouette_assessment'],assess_silhouette(row,manifest['width'],manifest['height'])):
                    raise IntegrityError('Silhouette evidence differs from source samples')
            if 'previous_processing_ns' in row:unsigned_ns(row['previous_processing_ns'])
            observation=row.get('body_state_observation') or {}
            if 'input_quality' in observation:
                from .image_quality import inspect_crop
                _,quality=inspect_crop(row,observation['candidate_id'],manifest['width'],manifest['height'],
                    allow_missing_presence=(manifest.get('automatic_posture_profile') or {}).get('version') in ('automatic-posture-rules-0.11','automatic-posture-rules-0.12'))
                if quality['status']!='usable' and row.get('person_crop_observation',{}).get('quality') is not None:
                    quality=row['person_crop_observation']['quality']
                if quality!=observation['input_quality']:raise IntegrityError('Crop quality differs from source joints')
            tracking=row.get('target_tracking')
            if tracking is not None and tracking != {'status':'not_configured','candidate_id':None}:
                from .target_tracking import TrackingConfig
                TrackingConfig.load(tracking['config'])
                if (tracking['status'] not in ('tracking_provisional','held') or tracking['identity_verified'] is not False
                        or tracking['frame_id']!=row['stream_frame_id'] or not tracking['track_id']
                        or tracking['status']=='held' and tracking['candidate_id'] is not None
                        or tracking['status']=='tracking_provisional' and tracking['candidate_id'] not in
                            [p['candidate_id'] for p in row['mapped_people'] or []]):
                    raise IntegrityError('Invalid provisional tracking result')
            status = row['processing_status']
            if status not in counts:
                raise IntegrityError('Unknown live frame status')
            if status == 'ok':
                if row['candidate_count'] != len(row['raw_candidates']) or row['candidate_count'] != len(row['mapped_people']):
                    raise IntegrityError('Live candidate count mismatch')
                if [p['candidate_id'] for p in row['raw_candidates']] != [p['candidate_id'] for p in row['mapped_people']]:
                    raise IntegrityError('Live candidate identity mismatch')
                if row['error'] is not None:
                    raise IntegrityError('Successful frame has an error')
            elif row['error'] is None:
                raise IntegrityError('Failed frame has no error')
            counts[status] += 1
    if dict(counts) != manifest['counts'] or sum(counts.values()) != manifest['recorded_frames']:
        raise IntegrityError('Live frame counters differ')
    if manifest['run_status'] == 'succeeded' and (counts['failed'] or manifest['errors']):
        raise IntegrityError('Failed live measurement declared successful')
    if unsigned_ns(manifest['finished_ns']) < max(previous_time, last_event_time, unsigned_ns(manifest['started_ns'])):
        raise IntegrityError('Invalid live recording end time')
    return dict(counts)


def verify_live(directory):
    directory = Path(directory)
    try:
        manifest = load_json(directory/'manifest.json')
        counts = _verify(directory, manifest)
        if manifest.get('video_recording'):
            from .camera_video import verify_video
            verify_video(directory,manifest)
        return {'measurement_id': manifest['measurement_id'], 'run_status': manifest['run_status'],
                'ledger_complete': True, 'counts': counts, 'integrity': 'verified'}
    except IntegrityError:
        raise
    except Exception as exc:
        raise IntegrityError(f'Live record validation failed ({type(exc).__name__})') from exc


class LiveRecorder:
    def __init__(self, directory, manifest, clock):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.clock, self.broken = clock, False
        self.streams = {}
        self.event_count = 0
        self.video = None
        self.manifest = dict(manifest, recorded_events=0, schema_version=LIVE_SCHEMA, run_status='recording',
                             ledger_complete=False, artifact_sha256={}, recorded_frames=0,
                             counts={'ok': 0, 'failed': 0}, errors=[])
        atomic_json(self.directory/'manifest.json', self.manifest)
        try:
            for name in FILES:
                self.streams[name] = (self.directory/name).open('x', encoding='utf-8', newline='\n')
        except Exception:
            self.broken = True
            self.close()
            raise

    def _write(self, name, row):
        if self.broken:
            raise IntegrityError('Live recorder is incomplete')
        try:
            self.streams[name].write(json_text(row)+'\n')
            self.streams[name].flush()
        except Exception:
            self.broken = True
            raise IntegrityError('Live record write failed') from None

    def event(self, kind, **values):
        self._write('control_events.jsonl', dict(values, type=kind, event_index=self.event_count,
            measurement_id=self.manifest['measurement_id'], clock_id=self.manifest['clock_id'], time_ns=str(self.clock())))
        self.event_count += 1
        self.manifest['recorded_events'] = self.event_count

    def frame(self, row, rgb=None):
        row = dict(row, schema_version=LIVE_SCHEMA, measurement_id=self.manifest['measurement_id'],
                   frame_id=self.manifest['recorded_frames'], record_started_ns=str(self.clock()))
        if self.video is not None:
            try:self.video.frame(rgb,row,row['frame_id'])
            except Exception as exc:
                self.broken=True
                raise IntegrityError('Video recording failed') from exc
        self._write('live_frames.jsonl', row)
        self.manifest['recorded_frames'] += 1
        self.manifest['counts'][row['processing_status']] += 1

    def close(self):
        errors = []
        for stream in self.streams.values():
            if not stream.closed:
                try:
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

    def finalize(self, outcome='succeeded', errors=()):
        self.manifest.update(run_status=outcome, errors=list(errors))
        try:
            if self.video is not None:
                self.manifest['video_artifact']=self.video.finish()
                self.video=None
            self.close()
            self.manifest['finished_ns'] = str(self.clock())
            if self.broken:
                raise IntegrityError('Live recording failed')
            _verify(self.directory, self.manifest, hashes=False)
            self.manifest['artifact_sha256'] = {name: file_hash(self.directory/name) for name in FILES}
            self.manifest['ledger_complete'] = True
            atomic_json(self.directory/'manifest.json', self.manifest)
            return self.manifest
        except Exception:
            self.manifest.update(run_status='failed', ledger_complete=False)
            self.manifest['errors'].append({'code': 'RECORDING_ERROR'})
            atomic_json(self.directory/'manifest.json', self.manifest)
            raise
