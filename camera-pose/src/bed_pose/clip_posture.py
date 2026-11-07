"""CLIP image evidence, with cached text and reproducible experimental decisions.

No bed coordinates, goals, sensors, silhouette overlays or ground truth inputs.
Relative softmax scores are not calibrated posture probabilities.
"""
from contextlib import nullcontext
import time
import base64
from copy import deepcopy
from importlib.metadata import version, PackageNotFoundError
from io import BytesIO
import math
import os
import threading
from pathlib import Path

_THREAD_LOCK = threading.Lock()
_CPU_THREADS = None

def configure_cpu_threads(torch):
    """Set the process-wide intra-op budget once, before any CLIP graph runs."""
    global _CPU_THREADS
    with _THREAD_LOCK:
        if _CPU_THREADS is None:
            requested = os.environ.get("BEDPOSE_CLIP_CPU_THREADS")
            if requested is not None:
                if not requested.isascii() or not requested.isdecimal() or not 1 <= int(requested) <= 64:
                    raise ValueError("BEDPOSE_CLIP_CPU_THREADS는 1~64 정수여야 합니다.")
                count = int(requested)
            else:
                count = min(4, torch.get_num_threads(), os.cpu_count() or 1)
            torch.set_num_threads(count)
            _CPU_THREADS = count
        return _CPU_THREADS


def resolve_device(device):
    if device not in ("auto", "cpu", "cuda", "mps"):
        raise ValueError("CLIP 실행 장치를 확인하세요.")
    if device != "auto":return device
    import torch
    if torch.cuda.is_available():return "cuda"
    mps=getattr(getattr(torch,'backends',None),'mps',None)
    return "mps" if mps is not None and mps.is_available() else "cpu"

import numpy as np

from .body_state import VERSION
from .clip_assets import MODEL_ID, read_assets,validate_manifest

PROVIDER = 'clip-posture-0.2'
LEGACY_PROVIDER = 'clip-posture-0.1'
from .image_quality import POLICY as CROP_POLICY
STATE_NAMES = ('lying', 'sitting', 'standing', 'other')
DIRECTION_NAMES = ('supine', 'side', 'prone')
PROMPTS = {
    'lying':['a photo of a person lying down.', 'a photo of a person resting in a lying position.'],
    'sitting':['a photo of a person sitting upright.', 'a photo of a seated person.'],
    'standing':['a photo of a person standing upright.', 'a photo of a person standing on their feet.'],
    'other':['a photo of a person kneeling.', 'a photo of a person crouching.', 'a photo of a person bending over.'],
    'supine':['a photo of a person lying on their back, face up.', 'a photo of a person resting on their back.'],
    'side':['a photo of a person lying on their side.', 'a photo of a person resting on the side of their body.'],
    'prone':['a photo of a person lying on their stomach, face down.', 'a photo of a person resting on their stomach.'],
}


def policy(state_min=.5, direction_min=.5, device='cpu', *, precision='fp32'):
    if (any(type(v) not in (int, float) or not math.isfinite(v) or not 0<=v<=1 for v in (state_min,direction_min))
            or device not in ('cpu','cuda','mps')):raise ValueError('CLIP 점수 기준 또는 실행 장치를 확인하세요.')
    if precision not in ('fp32','amp-fp16') or precision=='amp-fp16' and device!='cuda':
        raise ValueError('amp-fp16은 CUDA에서만 사용할 수 있습니다.')
    return {'version':PROVIDER if precision=='fp32' else 'clip-posture-0.3', 'state_min':float(state_min), 'direction_min':float(direction_min),
            'crop_quality':deepcopy(CROP_POLICY), 'prompts':deepcopy(PROMPTS), 'score_policy':'group_softmax_uncalibrated_no_runner_up_margin',
            'text_pooling':'mean_normalized_prompt_embeddings_then_normalize', 'device':device,
            'dtype':'float32' if precision=='fp32' else 'cuda_amp_fp16_float32_scores', 'threshold_validation':'unvalidated_initial_values', 'tie_tolerance':1e-8,
            'preprocess':'quality_joint_crop_10pct_then_aspect_preserving_square_pad_224_clip_mean_v2'}


def validate_policy(value):
    if not isinstance(value,dict):raise ValueError('Invalid CLIP decision policy')
    base = policy(value.get('state_min'), value.get('direction_min'), value.get('device'),
                  precision='amp-fp16' if value.get('version')=='clip-posture-0.3' else 'fp32')
    if value.get('version')==LEGACY_PROVIDER:
        base.pop('crop_quality')
        base.update(version=LEGACY_PROVIDER,preprocess='selected_joint_crop_10pct_then_aspect_preserving_square_pad_224_clip_mean_v1')
    if set(value) != set(base)|{'assets_manifest','runtime_versions'}:
        raise ValueError('Invalid CLIP policy fields')
    if any(value[k]!=v for k,v in base.items()):raise ValueError('Unsupported CLIP policy')
    if not isinstance(value['assets_manifest'],dict) or not isinstance(value['runtime_versions'],dict):
        raise ValueError('Missing CLIP provenance')
    validate_manifest(value['assets_manifest'])
    if (set(value['runtime_versions'])!={'torch','transformers','Pillow'}
            or any(v is not None and (not isinstance(v,str) or not v) for v in value['runtime_versions'].values())):
        raise ValueError('Invalid CLIP runtime versions')


def decide(cosines, logit_scale, configuration):
    validate_policy(configuration)
    names = STATE_NAMES+DIRECTION_NAMES
    if (not isinstance(cosines,dict) or set(cosines)!=set(names)
            or any(type(v) not in (int,float) or not math.isfinite(v) or not -1.00001<=v<=1.00001 for v in cosines.values())
            or type(logit_scale) not in (int,float) or not math.isfinite(logit_scale) or logit_scale<=0):
        raise ValueError('Invalid CLIP similarities')
    def group(labels, threshold):
        logits=[cosines[k]*logit_scale for k in labels]
        if not all(math.isfinite(x) for x in logits):raise ValueError('Invalid CLIP logits')
        exps=[math.exp(x-max(logits)) for x in logits];total=sum(exps)
        scores=dict(zip(labels,[x/total for x in exps]))
        from .posture_decision import choose
        return scores, choose(scores, threshold, configuration['tie_tolerance'])
    states, state = group(STATE_NAMES, configuration['state_min'])
    directions, direction = group(DIRECTION_NAMES, configuration['direction_min'])
    reasons=[]
    if state is None:reasons.append('clip_state_score_insufficient')
    elif state=='lying' and direction is None:reasons.append('clip_direction_score_insufficient')
    return {'state':state or 'unknown', 'lying_direction':direction if state=='lying' and direction else 'unknown',
            'scores':states, 'score_semantics':'uncalibrated_probability',
            'details':{'version':configuration['version'], 'cosine_scores':deepcopy(cosines), 'logit_scale':logit_scale,
                       'direction_scores':directions, 'reasons':reasons}}


def validate_recorded_decision(decision, profile):
    details=decision.get('details') if isinstance(decision,dict) else None
    if not isinstance(details,dict) or set(details)!={'version','cosine_scores','logit_scale','direction_scores','reasons'}:
        raise ValueError('Missing CLIP source scores')
    expected=decide(details['cosine_scores'],details['logit_scale'],profile['decision_policy'])
    from .replay_compare import equivalent
    if not equivalent(decision,expected):raise ValueError('CLIP decision differs from recorded scores and policy')


def square_image(rgb):
    from PIL import Image
    if not isinstance(rgb,np.ndarray) or rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[2]!=3 or min(rgb.shape[:2])<1:
        raise ValueError('CLIP input must be RGB uint8')
    image=Image.fromarray(rgb);w,h=image.size;scale=224/max(w,h)
    size=(max(1,round(w*scale)),max(1,round(h*scale)))
    resized=image.resize(size,Image.Resampling.BICUBIC)
    result=Image.new('RGB',(224,224),(123,117,104))
    result.paste(resized,((224-size[0])//2,(224-size[1])//2))
    return result


class ClipRuntime:
    """Real Transformers CLIP graph; tiny random graphs can test this boundary."""
    def __init__(self,directory,device,precision='fp32'):
        import torch
        from transformers import CLIPModel, CLIPProcessor
        if device=='cuda' and not torch.cuda.is_available():raise ValueError('CUDA를 사용할 수 없습니다. CPU를 선택하세요.')
        if device=='mps' and not getattr(getattr(torch,'backends',None),'mps',None):raise ValueError('MPS를 지원하는 PyTorch가 필요합니다.')
        if device=='mps' and not torch.backends.mps.is_available():raise ValueError('MPS를 사용할 수 없습니다. CPU를 선택하세요.')
        policy(device=device,precision=precision)
        self.precision=precision;self.last_metrics=None;self._pinned=None
        self._resource_at=0.;self._resources={}
        self.torch=torch;self.device=device
        threads=configure_cpu_threads(torch) if device=='cpu' else torch.get_num_threads()
        self.execution_info={'device':device,'torch_intraop_threads':threads,'precision':precision,
                             'score_dtype':'float32','weights_dtype':'float32','runtime_backend':'transformers'}
        self.model=CLIPModel.from_pretrained(directory,local_files_only=True,weights_only=True).float().to(device).eval()
        self.processor=CLIPProcessor.from_pretrained(directory,local_files_only=True,use_fast=False)
        texts=[p for name in STATE_NAMES+DIRECTION_NAMES for p in PROMPTS[name]]
        tokens=self.processor.tokenizer(texts,padding=True,return_tensors='pt')
        with torch.inference_mode():
            features=self.model.get_text_features(**{k:v.to(device) for k,v in tokens.items()})
            features=features/features.norm(dim=-1,keepdim=True)
            groups=[];offset=0
            for name in STATE_NAMES+DIRECTION_NAMES:
                length=len(PROMPTS[name]);mean=features[offset:offset+length].mean(dim=0)
                groups.append(mean/mean.norm());offset+=length
            self.text_features=torch.stack(groups)
            self.scale=float(self.model.logit_scale.exp().cpu())
        # Text features and scale are now fixed. Image inference never uses these modules.
        self.model.text_model=None
        self.model.text_projection=None
        self.execution_info['text_encoder_released']=True

    def resources(self):
        # Process-wide allocator counters, not memory attributable to one camera.
        now=time.monotonic()
        if now-self._resource_at>=1:
            t=self.torch
            try:
                if self.device=='cuda':
                    self._resources={'scope':'process_cuda_allocator','allocated_bytes':t.cuda.memory_allocated(),
                        'reserved_bytes':t.cuda.memory_reserved(),'peak_allocated_bytes':t.cuda.max_memory_allocated()}
                elif self.device=='mps' and hasattr(t.mps,'current_allocated_memory'):
                    self._resources={'scope':'process_mps_allocator','allocated_bytes':t.mps.current_allocated_memory()}
                else:self._resources={'scope':'not_available','allocated_bytes':None}
            except Exception as exc:
                self._resources={'scope':'not_available','allocated_bytes':None,'error':type(exc).__name__}
            self._resource_at=now
        return dict(self._resources)

    def similarities_many(self,images):
        if not images:raise ValueError('CLIP batch must not be empty')
        torch=self.torch
        started=time.perf_counter_ns()
        pixels=self.processor.image_processor(images=images,do_resize=False,do_center_crop=False,return_tensors='pt')['pixel_values']
        prepared=time.perf_counter_ns()
        with torch.inference_mode():
            if self.device=='cuda':
                if self._pinned is None or self._pinned.shape!=pixels.shape:
                    self._pinned=torch.empty(pixels.shape,dtype=pixels.dtype,pin_memory=True)
                self._pinned.copy_(pixels)
                device_pixels=self._pinned.to(self.device,non_blocking=True)
            else:device_pixels=pixels.to(self.device)
            transferred=time.perf_counter_ns()
            context=torch.autocast(device_type='cuda',dtype=torch.float16) if self.precision=='amp-fp16' else nullcontext()
            with context:
                feature=self.model.get_image_features(pixel_values=device_pixels)
            # Normalization, similarities and all decisions remain float32.
            feature=feature.float()
            feature=feature/feature.norm(dim=-1,keepdim=True)
            values=(feature@self.text_features.T).cpu().tolist()
        finished=time.perf_counter_ns()
        self.last_metrics={'scope':'host_wall_batch','batch_size':len(images),
            'preprocess_ms':(prepared-started)/1e6,'transfer_enqueue_ms':(transferred-prepared)/1e6,
            'inference_scores_readback_ms':(finished-transferred)/1e6,'total_ms':(finished-started)/1e6,
            'resources':self.resources()}
        return [(dict(zip(STATE_NAMES+DIRECTION_NAMES,value)),self.scale) for value in values]

    def similarities(self,image):
        return self.similarities_many([image])[0]

    def close(self):
        self.model=self.processor=self.text_features=self._pinned=None


class ClipBodyStateProvider:
    def __init__(self,directory,*,device='cpu',state_min=.5,direction_min=.5,
                 precision='fp32',shared=False,batch_size=1,batch_wait_ms=2.):
        self.directory=Path(directory).expanduser().resolve();self.assets=read_assets(self.directory)
        configuration=policy(state_min,direction_min,device,precision=precision)
        from .clip_pool import validate_options
        validate_options(batch_size,batch_wait_ms)
        if type(shared) is not bool or not shared and batch_size!=1:raise ValueError('Invalid shared CLIP configuration')
        self.precision=precision;self.shared=shared;self.batch_size=batch_size;self.batch_wait_ms=batch_wait_ms
        versions={}
        for package in ('torch','transformers','Pillow'):
            try:versions[package]=version(package)
            except PackageNotFoundError:versions[package]=None
        configuration.update(assets_manifest=deepcopy(self.assets),runtime_versions=versions)
        self.profile={'version':VERSION,'source':'camera_image', 'model_id':MODEL_ID,
                      'model_sha256':self.assets['files']['pytorch_model.bin'],
                      'preprocess_id':configuration['preprocess'], 'decision_policy':configuration,
                      'execution_kind':'model_inference'}
        self.runtime=None;self.last_input_preview=None;self.input_preview_enabled=True;self.preview_image=None

    def open(self):
        if self.runtime is not None:raise ValueError('CLIP provider is already open')
        if read_assets(self.directory,verify=True)!=self.assets:raise ValueError('CLIP 모델 구성이 변경됐습니다.')
        if any(v is None for v in self.profile['decision_policy']['runtime_versions'].values()):
            raise ValueError('CLIP 설치 필요: python -m pip install -e ".[clip]"')
        try:
            device=self.profile['decision_policy']['device']
            def create():
                return ClipRuntime(self.directory,device) if self.precision=='fp32' else ClipRuntime(self.directory,device,self.precision)
            if self.shared:
                from .clip_pool import acquire
                from .io_utils import object_hash
                key=(str(self.directory),object_hash(self.assets),device,self.precision,self.batch_size,self.batch_wait_ms)
                self.runtime=acquire(key,create,batch_size=self.batch_size,batch_wait_ms=self.batch_wait_ms)
            else:
                if self.batch_size!=1:raise ValueError('Batching requires shared CLIP execution')
                self.runtime=create()
        except ImportError as exc:raise ValueError('CLIP 설치 필요: python -m pip install -e ".[clip]"') from exc

    def estimate(self,observation):
        self.last_input_preview=None
        if self.runtime is None:raise ValueError('CLIP provider is not open')
        image=square_image(observation.rgb)
        self.preview_image=(observation.stream_frame_id,observation.candidate_id,image)
        cosines,scale=self.runtime.similarities(image)
        result=decide(cosines,scale,self.profile['decision_policy'])
        if not self.input_preview_enabled:return result
        try:
            with BytesIO() as buffer:
                image.save(buffer,format='PNG',compress_level=1)
                self.last_input_preview={'stream_frame_id':observation.stream_frame_id,'candidate_id':observation.candidate_id,
                                         'image':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')}
        except Exception:
            # A display-only failure must not discard a valid model decision.
            self.last_input_preview=None
        return result

    def input_preview(self,frame_id):
        packet=self.preview_image
        if packet is None or packet[0]!=frame_id:return None
        if self.last_input_preview and self.last_input_preview['stream_frame_id']==frame_id:return self.last_input_preview
        with BytesIO() as buffer:
            packet[2].save(buffer,format='PNG',compress_level=1)
            return {'stream_frame_id':packet[0],'candidate_id':packet[1],
                    'image':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')}

    def close(self):
        if self.runtime is not None:self.runtime.close()
        self.runtime=None;self.last_input_preview=None;self.input_preview_enabled=True;self.preview_image=None
