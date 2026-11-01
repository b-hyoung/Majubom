"""Join common joint coordinates to image-space SDK scores for inspection only."""
import math
from .contracts import COCO17, IntegrityError

JOINT_LABELS_KO = ('코', '왼쪽 눈', '오른쪽 눈', '왼쪽 귀', '오른쪽 귀',
                   '왼쪽 어깨', '오른쪽 어깨', '왼쪽 팔꿈치', '오른쪽 팔꿈치',
                   '왼쪽 손목', '오른쪽 손목', '왼쪽 골반', '오른쪽 골반',
                   '왼쪽 무릎', '오른쪽 무릎', '왼쪽 발목', '오른쪽 발목')


def _score(candidate, source_index, image_block_id, score_type):
    def result(status, value=None):
        return {'status': status, 'value': value}
    if source_index is None:
        return result('mapping_undefined')
    blocks = [b for b in candidate.get('score_blocks', [])
              if b.get('source_block_id') == image_block_id and b.get('score_type') == score_type]
    if not blocks:
        return result('not_output')
    images = [b for b in candidate.get('pose_blocks', []) if b.get('block_id') == image_block_id]
    if len(blocks) != 1 or len(images) != 1:
        return result('invalid')
    values = blocks[0].get('values')
    shape = images[0].get('shape')
    if (not isinstance(values, list) or not isinstance(shape, list) or not shape
            or type(shape[0]) is not int or len(values) != shape[0]
            or type(source_index) is not int or not 0 <= source_index < len(values)):
        return result('invalid')
    value = values[source_index]
    if value is None:
        return result('not_output')
    if isinstance(value, dict) and value.get('nonfinite') in ('nan', 'pos_inf', 'neg_inf'):
        return result('non_finite')
    if type(value) not in (float, int):
        return result('invalid')
    if not math.isfinite(value):
        return result('non_finite')
    # No clamping, confidence threshold, score combination or acceptance rule.
    return result('reported', value)


def inspect_joints(mapped, raw, image_block_id='image'):
    if mapped is None:
        return []
    candidates = {}
    for candidate in raw['candidates'] if raw else []:
        identifier = candidate['candidate_id']
        if identifier in candidates:
            raise IntegrityError('Duplicate raw candidate in joint inspection')
        candidates[identifier] = candidate
    result = []
    for person in mapped['people']:
        candidate = candidates.get(person['raw_candidate_id'])
        if candidate is None or len(person['joints']) != 17:
            raise IntegrityError('Joint inspection has no matching raw candidate or common mapping')
        joints = []
        for index, joint in enumerate(person['joints']):
            joints.append({'joint_index': index, 'name': COCO17[index], 'label': JOINT_LABELS_KO[index],
                **{key: joint[key] for key in ('source_index', 'x_px', 'y_px', 'availability', 'reason', 'flags')},
                'visibility': _score(candidate, joint['source_index'], image_block_id, 'visibility'),
                'presence': _score(candidate, joint['source_index'], image_block_id, 'presence')})
        result.append({'candidate_id': person['candidate_id'], 'score_source_block': image_block_id,
                       'joints': joints})
    return result
