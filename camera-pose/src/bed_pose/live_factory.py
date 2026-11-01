"""Construct independent engines without importing a desktop GUI."""
from pathlib import Path
import uuid
from .io_utils import atomic_json, file_hash
from .live_engine import LiveEngine


def make_engine(source, model, output_root, options_root, *, demo=False, clip_model=None,
                clip_device='cpu', clip_state_min=.5, clip_direction_min=.5, silhouette=False,
                clip_precision='fp32',clip_shared=True,clip_batch_size=1,clip_batch_wait_ms=2.,person_fallback=False,
                pose_device='cpu',pose_max_people=3):
    if type(pose_max_people) is not int or not 1<=pose_max_people<=3:
        raise ValueError('관절 추론 최대 인원은 1~3명입니다.')
    if pose_device not in ('cpu', 'browser_gpu'):
        raise ValueError('지원하지 않는 MediaPipe 실행 장치입니다.')
    if pose_device == 'browser_gpu' and silhouette:
        raise ValueError('GPU 실험 모드에서는 실루엣 마스크를 아직 지원하지 않습니다.')
    if demo:
        from .pose.fake import FakeAdapter
        return LiveEngine(source, FakeAdapter(), {'execution_mode': 'ordered_sequence'},
                          output_root, execution_kind='synthetic')
    from .live_sources import CameraSource
    if isinstance(source,CameraSource):
        from .latest_camera import LatestCameraSource
        source=LatestCameraSource(source)
    from .pose.mediapipe import MediaPipeAdapter, DEFAULT_OPTIONS, mapping_profile
    model = Path(model).expanduser().resolve()
    if not model.is_file():
        raise ValueError('추론 모델(.task)을 먼저 선택하세요.')
    options = Path(options_root)/(uuid.uuid4().hex+'.json')
    atomic_json(options, dict(DEFAULT_OPTIONS, num_poses=pose_max_people))
    settings = {'execution_mode': 'ordered_sequence', 'requested_device': pose_device,
                'assets': [{'role': 'pose_landmarker_task', 'path': str(model), 'sha256': file_hash(model)}],
                'adapter_options_path': str(options), 'adapter_options_sha256': file_hash(options)}
    provider=None
    if clip_model is not None:
        from .clip_posture import ClipBodyStateProvider
        provider=ClipBodyStateProvider(clip_model,device=clip_device,state_min=clip_state_min,direction_min=clip_direction_min,
            precision=clip_precision,shared=clip_shared,batch_size=clip_batch_size,batch_wait_ms=clip_batch_wait_ms)
    detector=None
    if person_fallback:
        if provider is None:raise ValueError('사람 영역 보완에는 CLIP이 필요합니다.')
        from .person_detector import PersonDetector
        detector=PersonDetector(device=clip_device)
    if pose_device == 'browser_gpu':
        from .pose.browser_gpu import BrowserGPUAdapter
        adapter = BrowserGPUAdapter()
    else:
        adapter = MediaPipeAdapter(silhouette=silhouette)
    return LiveEngine(source, adapter, settings, output_root,
                      profile=mapping_profile(),body_state_provider=provider,person_detector=detector)
