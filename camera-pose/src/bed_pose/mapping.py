"""Explicit 2D mapping with numeric quality, without anatomical guesses."""
from dataclasses import dataclass
import math
import numpy as np
from .contracts import COCO17, MappingError


@dataclass(frozen=True)
class MappingProfile:
    profile_id: str = 'synthetic-coco17-identity-v1'
    source_joint_definition_id: str = 'coco17'
    expected_source_count: int = 17
    source_indices: tuple = tuple(range(17))
    image_block_id: str = 'image'
    expected_source_names: tuple | None = None

    def __post_init__(self):
        if len(self.source_indices) != 17 or any(
                index is not None and (type(index) is not int or
                                       not 0 <= index < self.expected_source_count)
                for index in self.source_indices):
            raise ValueError('Invalid common joint mapping')


def raw_json_value(value):
    """Preserve non-finite source values without nonstandard JSON constants."""
    if isinstance(value, float) and not math.isfinite(value):
        return {'nonfinite': 'nan' if math.isnan(value) else
                'pos_inf' if value > 0 else 'neg_inf'}
    if isinstance(value, dict):
        return {key: raw_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [raw_json_value(item) for item in value]
    return value


def _matrix(block, width, height):
    transform = block['transform_to_canonical']
    if not isinstance(transform, dict) or transform['destination_space'] != 'canonical_pixels' or transform['source_space'] != block['coordinate_space']:
        raise MappingError('Coordinate space mismatch')
    matrix = np.asarray(transform['matrix'], dtype=float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or not np.array_equal(matrix[2], [0, 0, 1]) or np.linalg.det(matrix) == 0:
        raise MappingError('Invalid affine transformation')
    kind = transform['kind']
    if kind == 'identity':
        if block['coordinate_space'] != 'canonical_pixels' or not np.array_equal(matrix, np.eye(3)):
            raise MappingError('Invalid identity mapping')
    elif kind == 'normalized_to_canonical':
        if block['coordinate_space'] != 'normalized_image' or not np.array_equal(matrix, np.diag([width, height, 1])):
            raise MappingError('Invalid normalization mapping')
    elif kind != 'affine':
        raise MappingError('Unknown transform kind')
    return matrix


def map_keypoints(output, width, height, profile=None):
    profile = profile or MappingProfile()
    people = []
    ids = set()
    for candidate in output.candidates:
        identifier = candidate['candidate_id']
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise MappingError('Invalid or duplicate candidate ID')
        ids.add(identifier)
        blocks = [block for block in candidate['pose_blocks']
                  if block['block_id'] == profile.image_block_id]
        if len(blocks) != 1:
            raise MappingError('Missing or duplicate image coordinate block')
        block = blocks[0]
        if (block['joint_definition_id'] != profile.source_joint_definition_id or
                block['shape'] != [profile.expected_source_count, 2] or
                len(block['values']) != profile.expected_source_count):
            raise MappingError('Unexpected source joint definition/shape')
        if profile.source_joint_definition_id == 'coco17' and block['source_joint_names'] != list(COCO17):
            raise MappingError('Source joint order does not match COCO17')
        if profile.expected_source_names is not None and block['source_joint_names'] != list(profile.expected_source_names):
            raise MappingError('Source joint names do not match the mapping profile')
        matrix = _matrix(block, width, height)
        joints = []
        summary = {'finite_joint_count': 0, 'missing_joint_count': 0,
                   'not_applicable_count': 0, 'policy_status': 'not_assessed'}
        for source in profile.source_indices:
            joint = {'source_index': source, 'x_px': None, 'y_px': None,
                     'x_norm': None, 'y_norm': None, 'availability': 'missing',
                     'reason': 'not_output', 'flags': []}
            if source is None:
                joint.update(availability='not_applicable', reason='mapping_undefined')
                summary['not_applicable_count'] += 1
            else:
                pair = block['values'][source]
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    raise MappingError('Expected a two-coordinate source joint')
                if any(value is None for value in pair):
                    summary['missing_joint_count'] += 1
                elif any(type(value) not in (int, float) for value in pair):
                    raise MappingError('Source coordinates must be numeric or null')
                elif not all(math.isfinite(value) for value in pair):
                    joint['reason'] = 'non_finite'
                    summary['missing_joint_count'] += 1
                else:
                    x, y, _ = matrix @ [*pair, 1]
                    if not (math.isfinite(x) and math.isfinite(y)):
                        joint['reason'] = 'non_finite'
                        summary['missing_joint_count'] += 1
                    else:
                        joint.update(x_px=float(x), y_px=float(y),
                                     x_norm=float(x / width), y_norm=float(y / height),
                                     availability='predicted', reason=None)
                        if not (0 <= x < width and 0 <= y < height):
                            joint['flags'] = ['outside_image']
                        summary['finite_joint_count'] += 1
            joints.append(joint)
        people.append({'candidate_id': identifier, 'raw_candidate_id': identifier,
                       'joints': joints, 'numeric_summary': summary})
    return people
