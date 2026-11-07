"""Deterministic synthetic outputs for plumbing checks, never human inference."""
from dataclasses import asdict, dataclass
from bed_pose.contracts import AdapterCallError, COCO17, PoseOutput


@dataclass(frozen=True)
class FakeOptions:
    no_detection_at: tuple[int, ...] = ()
    fail_at: int | None = None
    people: int = 1
    missing_joints: tuple[int, ...] = ()

    def __post_init__(self):
        indices = self.no_detection_at + (() if self.fail_at is None else (self.fail_at,))
        if any(type(i) is not int or i < 0 for i in indices):
            raise ValueError('Fake frame indices must be nonnegative integers')
        if type(self.people) is not int or self.people < 0:
            raise ValueError('Fake people must be a nonnegative integer')
        if any(type(i) is not int or not 0 <= i < 17 for i in self.missing_joints):
            raise ValueError('Fake joint indices must lie in 0..16')

    def as_dict(self):
        return asdict(self)


class FakeAdapter:
    def __init__(self, options=None):
        self.options = options or FakeOptions()
        self.opened = False
        self.last_timestamp = None
        self.mode = None

    def open(self, settings):
        self.mode = settings['execution_mode']
        self.opened = True
        self.last_timestamp = None
        return {'adapter_id': 'fake', 'execution_kind': 'synthetic',
                'sdk_version': None, 'requested_device': 'none', 'actual_device': 'none',
                'joint_definition': 'coco17', 'internal_tracking': False,
                'internal_smoothing': False, 'max_returned_people': self.options.people,
                'coordinate_space': 'canonical_pixels', 'options': self.options.as_dict()}

    def reset_for_video(self):
        if not self.opened:
            raise RuntimeError('Adapter is closed')
        self.last_timestamp = None
        return {'method': 'documented_reset', 'synthetic': True, 'caches_retained': False}

    def estimate(self, model_input):
        if not self.opened:
            raise AdapterCallError('Synthetic adapter is closed')
        packet = model_input.frame
        stamp = model_input.model_timestamp_ms
        if self.mode == 'ordered_sequence':
            if stamp is None or (self.last_timestamp is not None and stamp <= self.last_timestamp):
                raise AdapterCallError('Synthetic sequence timestamp is not increasing')
            self.last_timestamp = stamp
        if packet.frame_id == self.options.fail_at:
            raise AdapterCallError('Intentional synthetic failure')
        candidates = []
        if packet.frame_id not in self.options.no_detection_at:
            for person in range(self.options.people):
                values = []
                for joint in range(17):
                    if joint in self.options.missing_joints:
                        values.append([None, None])
                    else:
                        values.append([float((joint * 3 + person) % packet.width),
                                       float((joint * 2 + person) % packet.height)])
                candidates.append({'candidate_id': f'p{person}', 'sdk_track_id': None,
                    'bbox': None, 'pose_blocks': [{
                        'block_id': 'image', 'joint_definition_id': 'coco17',
                        'source_joint_names': list(COCO17),
                        'coordinate_space': 'canonical_pixels', 'units': 'pixels',
                        'dtype': 'float64', 'shape': [17, 2], 'values': values,
                        'transform_to_canonical': {
                            'kind': 'identity', 'matrix': [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                            'source_space': 'canonical_pixels',
                            'destination_space': 'canonical_pixels'}}],
                    'score_blocks': []})
        return PoseOutput(tuple(candidates), stamp)

    def close(self):
        self.opened = False
        self.last_timestamp = None
