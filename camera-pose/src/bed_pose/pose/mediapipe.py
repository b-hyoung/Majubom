"""Optional MediaPipe Tasks adapter; no implicit model downloads or fake fallback."""
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
import math
from pathlib import Path
from ..contracts import AdapterCallError, PoseOutput
from ..io_utils import file_hash, load_json
from ..mapping import MappingProfile

SDK_VERSION = '0.10.35'
MP33 = ('nose', 'left_eye_inner', 'left_eye', 'left_eye_outer',
        'right_eye_inner', 'right_eye', 'right_eye_outer', 'left_ear', 'right_ear',
        'mouth_left', 'mouth_right', 'left_shoulder', 'right_shoulder',
        'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist',
        'left_pinky', 'right_pinky', 'left_index', 'right_index',
        'left_thumb', 'right_thumb', 'left_hip', 'right_hip',
        'left_knee', 'right_knee', 'left_ankle', 'right_ankle',
        'left_heel', 'right_heel', 'left_foot_index', 'right_foot_index')
COCO_INDICES = (0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28)
DEFAULT_OPTIONS = {'num_poses': 1, 'min_pose_detection_confidence': 0.5,
                   'min_pose_presence_confidence': 0.5, 'min_tracking_confidence': 0.5}


def mapping_profile():
    return MappingProfile(profile_id='mediapipe33-to-coco17-v1',
                          source_joint_definition_id='mediapipe33',
                          expected_source_count=33, source_indices=COCO_INDICES,
                          expected_source_names=MP33)


def validated_options(settings):
    if settings['requested_device'] != 'cpu':
        raise ValueError('This adapter currently supports explicit requested_device=cpu only')
    if len(settings['assets']) != 1 or settings['assets'][0]['role'] != 'pose_landmarker_task':
        raise ValueError('MediaPipe requires exactly one pose_landmarker_task asset')
    asset = settings['assets'][0]
    if file_hash(asset['path']) != asset['sha256']:
        raise ValueError('MediaPipe model asset checksum mismatch')
    path = settings['adapter_options_path']
    if path is None or file_hash(path) != settings['adapter_options_sha256']:
        raise ValueError('MediaPipe options checksum mismatch')
    options = load_json(path)
    if not isinstance(options, dict) or set(options) != set(DEFAULT_OPTIONS):
        raise ValueError('MediaPipe options must explicitly contain num_poses and the three confidence settings')
    if type(options['num_poses']) is not int or options['num_poses'] < 1:
        raise ValueError('num_poses must be a positive integer')
    for key in set(options) - {'num_poses'}:
        value = options[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f'{key} must be a finite number in [0,1]')
    return options


class MediaPipeAdapter:
    def __init__(self,*,silhouette=False):
        if type(silhouette) is not bool:raise ValueError('silhouette must be boolean')
        self.silhouette=silhouette
        self.task = None
        self.mp = None
        self.last_timestamp = None

    def open(self, settings):
        if self.task is not None:
            raise ValueError('MediaPipe adapter is already open')
        options = validated_options(settings)
        try:
            installed_version = version('mediapipe')
        except PackageNotFoundError as exc:
            raise ValueError('MediaPipe extra is missing; install with pip install -e .[mediapipe]') from exc
        if installed_version != SDK_VERSION:
            raise ValueError(f'Install the tested mediapipe=={SDK_VERSION} version')
        import mediapipe as mp
        self.mp = mp
        self.mode = settings['execution_mode']
        if self.mode not in ('independent_frames', 'ordered_sequence'):
            raise ValueError('Unsupported MediaPipe execution mode')
        # Use the checked bytes, avoiding a path replacement between verification and SDK loading.
        model = Path(settings['assets'][0]['path']).read_bytes()
        import hashlib
        if hashlib.sha256(model).hexdigest() != settings['assets'][0]['sha256']:
            raise ValueError('MediaPipe model changed while loading')
        vision = mp.tasks.vision
        mode = (vision.RunningMode.IMAGE if self.mode == 'independent_frames'
                else vision.RunningMode.VIDEO)
        self.sdk_options = vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=model,
                                             delegate=mp.tasks.BaseOptions.Delegate.CPU),
            running_mode=mode, output_segmentation_masks=self.silhouette, **options)
        self.task = vision.PoseLandmarker.create_from_options(self.sdk_options)
        self.last_timestamp = None
        return {'adapter_id': 'mediapipe_pose', 'execution_kind': 'model_inference',
                'implementation_validation': 'sdk_boundary_tests_only_model_execution_pending',
                'sdk_version': SDK_VERSION, 'requested_device': 'cpu', 'actual_device': 'cpu',
                'joint_definition': 'mediapipe33', 'coordinate_space': 'normalized_image',
                'internal_tracking': self.mode == 'ordered_sequence',
                'internal_smoothing': self.mode == 'ordered_sequence' and options['num_poses'] == 1,
                'max_returned_people': options['num_poses'], 'options': options,
                'assets': settings['assets'], 'estimated_world_coordinates': True,
                'segmentation_masks': self.silhouette, 'stable_person_ids': False}

    def reset_for_video(self):
        if self.task is None:
            raise RuntimeError('MediaPipe adapter is closed')
        self.close()
        self.task = self.mp.tasks.vision.PoseLandmarker.create_from_options(self.sdk_options)
        self.last_timestamp = None
        return {'method': 'recreate_task', 'caches_retained': False}

    def estimate(self, model_input):
        if self.task is None:
            raise AdapterCallError('MediaPipe adapter is closed')
        stamp = model_input.model_timestamp_ms
        if self.mode == 'ordered_sequence':
            if (type(stamp) is not int or not 0 <= stamp < 2**63 or
                    (self.last_timestamp is not None and stamp <= self.last_timestamp)):
                raise AdapterCallError('MediaPipe video timestamp must be increasing int64 milliseconds')
        elif stamp is not None:
            raise AdapterCallError('Independent MediaPipe image input must not have a model timestamp')
        packet = model_input.frame
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=packet.rgb)
        try:
            if self.mode == 'ordered_sequence':
                result = self.task.detect_for_video(image, stamp)
                self.last_timestamp = stamp
            else:
                result = self.task.detect(image)
            candidates = self._candidates(result, packet.width, packet.height)
            observation=preview=None
            if self.silhouette:
                from ..silhouette import capture
                try:observation=capture(result,candidates,model_input)
                except Exception:observation=None
                from ..silhouette_overlay import capture_preview
                try:preview=capture_preview(result,candidates,model_input)
                except Exception:preview=None
            return PoseOutput(tuple(candidates), stamp, sdk_call_started=True,
                              silhouette_observation=observation,silhouette_preview=preview)
        except Exception as exc:
            raise AdapterCallError(f'MediaPipe call/result conversion failed: {type(exc).__name__}',
                                   sdk_call_started=True) from exc

    @staticmethod
    def _candidates(result, width, height):
        if len(result.pose_landmarks) != len(result.pose_world_landmarks):
            raise ValueError('MediaPipe image/world candidate counts differ')
        candidates = []
        for index, (image, world) in enumerate(zip(result.pose_landmarks, result.pose_world_landmarks)):
            if len(image) != 33 or len(world) != 33:
                raise ValueError('MediaPipe must return 33 image and world landmarks per pose')
            # Retain all SDK landmark fields, including z/visibility/presence, separately from XY mapping.
            original = {'image': [asdict(point) for point in image],
                        'world': [asdict(point) for point in world]}
            block = {'block_id': 'image', 'joint_definition_id': 'mediapipe33',
                     'source_joint_names': list(MP33), 'coordinate_space': 'normalized_image',
                     'units': 'normalized', 'dtype': 'python_float_from_sdk', 'shape': [33, 2],
                     'values': [[p.x, p.y] for p in image], 'transform_to_canonical': {
                         'kind': 'normalized_to_canonical',
                         'matrix': [[width, 0, 0], [0, height, 0], [0, 0, 1]],
                         'source_space': 'normalized_image', 'destination_space': 'canonical_pixels'}}
            candidates.append({'candidate_id': f'p{index}', 'sdk_track_id': None,
                               'bbox': None, 'pose_blocks': [block],
                               'sdk_landmarks': original,
                               'world_coordinate_semantics': 'monocular_estimate_meters_hip_origin',
                               'score_blocks': [{'score_type': name, 'source_block_id': space,
                                                 'values': [getattr(p, name) for p in points]}
                                                for space, points in (('image', image), ('world', world))
                                                for name in ('visibility', 'presence')]})
        return candidates

    def close(self):
        task, self.task = self.task, None
        self.last_timestamp = None
        if task is not None:
            task.close()


def prepare_configuration(base_config, model_path, output_directory, *,
                          execution_mode='independent_frames', num_poses=1):
    """Create an explicit, hashed real-model configuration in a new directory."""
    from ..config import load_run_config, record_validator
    from ..io_utils import atomic_json
    base = load_run_config(base_config)
    model_path = Path(model_path).resolve()
    output_directory = Path(output_directory).resolve()
    if execution_mode not in ('independent_frames', 'ordered_sequence'):
        raise ValueError('Unsupported MediaPipe execution mode')
    if type(num_poses) is not int or num_poses < 1:
        raise ValueError('num_poses must be a positive integer')
    model_hash = file_hash(model_path)
    output_directory.mkdir(parents=True, exist_ok=False)
    options_path = output_directory / 'mediapipe_options.json'
    atomic_json(options_path, dict(DEFAULT_OPTIONS, num_poses=num_poses))
    config = dict(base, adapter_id='mediapipe_pose', requested_device='cpu',
                  execution_mode=execution_mode,
                  assets=[{'role': 'pose_landmarker_task', 'path': str(model_path),
                           'sha256': model_hash}],
                  adapter_options_path=str(options_path),
                  adapter_options_sha256=file_hash(options_path))
    record_validator().validate(config)
    validated_options(config)
    path = output_directory / 'run_config.mediapipe.json'
    atomic_json(path, config)
    return path
