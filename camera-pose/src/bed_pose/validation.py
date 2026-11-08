"""Cross-field and cross-record validation in addition to JSON Schema."""
import math
from .contracts import IntegrityError, SCHEMA_VERSION


def validate_frame(row, validator):
    validator.validate(row)
    previous_end = None
    for stage in ('decode', 'adapter', 'mapping'):
        start, end = row['timing'][stage + '_start_ns'], row['timing'][stage + '_end_ns']
        if start is not None:
            if int(end) < int(start) or (previous_end is not None and int(start) < previous_end):
                raise IntegrityError('Frame timing is not monotonic')
            previous_end = int(end)
    if row['target'] != {'status': 'not_configured', 'candidate_id': None, 'reason': None}:
        raise IntegrityError('Only target_policy=none is implemented')
    if row['processing_status'] == 'not_processed' and row['sdk_call_started']:
        raise IntegrityError('An unprocessed frame cannot call an SDK')
    for key in ('raw_ref', 'keypoints_ref'):
        reference = row[key]
        if reference is not None and reference['record_id'] != f"frame:{row['frame_id']}":
            raise IntegrityError('Record reference does not match frame identity')


def validate_bundle(row, raw, mapped, validator, width, height):
    validate_frame(row, validator)
    for reference, value, kind in ((row['raw_ref'], raw, 'raw_result'),
                                   (row['keypoints_ref'], mapped, 'mapped_result')):
        if (reference is None) != (value is None):
            raise IntegrityError('Missing or unexpected referenced record')
        if value is not None:
            for key in ('schema_version', 'run_id', 'video_id', 'frame_id'):
                if value.get(key) != row[key]:
                    raise IntegrityError('Record identity mismatch')
            if value.get('record_type') != kind or value.get('record_id') != reference['record_id']:
                raise IntegrityError('Record type/reference mismatch')
    raw_ids = []
    if raw is not None:
        raw_ids = [candidate['candidate_id'] for candidate in raw['candidates']]
        if len(set(raw_ids)) != len(raw_ids) or len(raw_ids) != row['candidate_count']:
            raise IntegrityError('Raw candidate count/identity mismatch')
    if mapped is not None:
        people = mapped['people']
        if [person['candidate_id'] for person in people] != raw_ids:
            raise IntegrityError('Mapped candidates do not match original candidates')
        for person in people:
            if person['raw_candidate_id'] != person['candidate_id'] or len(person['joints']) != 17:
                raise IntegrityError('Invalid common joint count or candidate reference')
            counts = {'finite_joint_count': 0, 'missing_joint_count': 0, 'not_applicable_count': 0}
            for joint in person['joints']:
                coordinates = [joint[k] for k in ('x_px', 'y_px', 'x_norm', 'y_norm')]
                if joint['availability'] == 'predicted':
                    if (any(type(c) not in (int, float) or not math.isfinite(c) for c in coordinates)
                            or joint['reason'] is not None):
                        raise IntegrityError('Invalid predicted coordinate')
                    if not math.isclose(coordinates[2], coordinates[0] / width, abs_tol=1e-12) or not math.isclose(coordinates[3], coordinates[1] / height, abs_tol=1e-12):
                        raise IntegrityError('Invalid coordinate normalization')
                    counts['finite_joint_count'] += 1
                elif joint['availability'] in ('missing', 'not_applicable'):
                    if any(c is not None for c in coordinates) or joint['reason'] is None:
                        raise IntegrityError('Missing coordinates must remain null')
                    counts['missing_joint_count' if joint['availability'] == 'missing' else 'not_applicable_count'] += 1
                else:
                    raise IntegrityError('Unknown joint availability')
            if person['numeric_summary'] != dict(counts, policy_status='not_assessed'):
                raise IntegrityError('Joint quality counters are inconsistent')
