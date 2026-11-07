"""Independent camera-image evidence contract and posture composition.

This is not an image model: a configured provider must supply its own decision.
Predicted labels remain experimental and never become verified ground truth.
"""
from dataclasses import dataclass
from copy import deepcopy
import math
from .io_utils import json_text,strict_loads

VERSION='camera-body-state-0.1'
STATES=('lying','sitting','standing','other','unknown')
DIRECTIONS=('supine','side','prone','unknown')
PROFILE_KEYS={'version','source','model_id','model_sha256','preprocess_id','decision_policy','execution_kind'}


@dataclass(frozen=True)
class ImageBodyInput:
    rgb: object
    stream_frame_id: int
    clock_id: str
    received_ns: str
    candidate_id: str
    points: tuple
    image_bounds: tuple


def validate_profile(profile,execution_kind):
    if (not isinstance(profile,dict) or set(profile)!=PROFILE_KEYS or profile['version']!=VERSION
            or profile['source']!='camera_image' or profile['execution_kind']!=execution_kind
            or execution_kind not in ('synthetic','model_inference')
            or any(not isinstance(profile[k],str) or not profile[k].strip() for k in ('model_id','preprocess_id'))
            or not isinstance(profile['model_sha256'],str) or len(profile['model_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in profile['model_sha256'])):
        raise ValueError('Invalid independent image-state provider profile')
    if not isinstance(profile['decision_policy'],dict) or not profile['decision_policy']:
        raise ValueError('Body-state decision policy must be explicit')
    try:
        if strict_loads(json_text(profile['decision_policy']))!=profile['decision_policy']:
            raise ValueError('Decision policy must use JSON values')
    except (ValueError,TypeError) as exc:raise ValueError('Invalid body-state decision policy') from exc
    if profile['model_id']=='openai/clip-vit-base-patch16':
        from .clip_posture import validate_policy
        validate_policy(profile['decision_policy'])
        assets=profile['decision_policy']['assets_manifest']
        if (profile['execution_kind']!='model_inference' or assets.get('model_id')!=profile['model_id']
                or assets.get('files',{}).get('pytorch_model.bin')!=profile['model_sha256']
                or profile['preprocess_id']!=profile['decision_policy']['preprocess']):
            raise ValueError('CLIP provider asset identity mismatch')
    return deepcopy(profile)


def validate_decision(value):
    if not isinstance(value,dict) or set(value) not in ({'state','lying_direction','scores','score_semantics'},
            {'state','lying_direction','scores','score_semantics','details'}):
        raise ValueError('Invalid independent image-state output')
    if value['state'] not in STATES or value['lying_direction'] not in DIRECTIONS:
        raise ValueError('Unsupported body-state label')
    if value['state']!='lying' and value['lying_direction']!='unknown':
        raise ValueError('Lying direction requires a lying state')
    if (not isinstance(value['scores'],dict) or set(value['scores'])-set(STATES)
            or any(type(x) not in (int,float) or not math.isfinite(x) for x in value['scores'].values())
            or value['score_semantics'] not in ('logits','similarity','uncalibrated_probability','not_reported')
            or (value['score_semantics']=='not_reported' and value['scores'])
            or (value['score_semantics']=='uncalibrated_probability' and any(not 0<=x<=1 for x in value['scores'].values()))):
        raise ValueError('Invalid body-state score semantics')
    if 'details' in value and (not isinstance(value['details'],dict) or strict_loads(json_text(value['details']))!=value['details']):
        raise ValueError('Invalid image evidence details')
    return deepcopy(value)


def observe(row,rgb,assessment,provider,profile,*,allow_missing_presence=False):
    """Only the current image and selected visual target go to the provider."""
    result={'version':VERSION,'stream_frame_id':row['stream_frame_id'],'clock_id':row['clock_id'],
            'received_ns':row['received_ns'],'candidate_id':assessment['candidate_id'],
            'provider':deepcopy(profile),'status':'unavailable','reasons':[],
            'decision':None,'verified_gt':False}
    if provider is None:result['reasons']=['body_state_provider_not_configured'];return result
    if provider.profile!=profile:result.update(status='held',reasons=['body_state_provider_profile_changed']);return result
    if row['processing_status']!='ok':result['reasons']=['processing_failed'];return result
    selection=row.get('bed_target_selection') or {}
    if row.get('target_tracking',{}).get('status')=='held' and not (selection.get('status')=='selected' and selection.get('candidate_id')==assessment['candidate_id']):
        result['reasons']=['target_held'];return result
    people=[p for p in row.get('mapped_people') or [] if p['candidate_id']==assessment['candidate_id']]
    if len(people)!=1:result['reasons']=['body_state_target_unavailable'];return result
    # A provider gets no bed region, planned goal, sensor result or GT field.
    h,w=rgb.shape[:2]
    def point(j):
        x,y=j.get('x_px'),j.get('y_px')
        return (x,y) if (j.get('availability')=='predicted' and type(x) in (int,float) and type(y) in (int,float)
                         and math.isfinite(x) and math.isfinite(y) and 0<=x<w and 0<=y<h) else None
    points=tuple(point(j) for j in people[0]['joints'])
    if 'person_crop_observation' not in row:
        valid=[p for p in points if p is not None]
        if len(valid)<2 or max(p[0] for p in valid)==min(p[0] for p in valid) or max(p[1] for p in valid)==min(p[1] for p in valid):
            result.update(status='held',reasons=['body_state_target_unavailable']);return result
    if profile.get('decision_policy',{}).get('crop_quality'):
        from .image_quality import inspect_crop
        points,quality=inspect_crop(row,assessment['candidate_id'],w,h,allow_missing_presence=allow_missing_presence)
        fallback=row.get('person_crop_observation')
        if quality['status']!='usable' and fallback is not None:
            if (fallback.get('stream_frame_id')==row['stream_frame_id'] and fallback.get('clock_id')==row['clock_id']
                    and fallback.get('received_ns')==row['received_ns'] and fallback.get('candidate_id')==assessment['candidate_id']
                    and fallback.get('quality') is not None):quality=deepcopy(fallback['quality'])
        result['input_quality']=quality
        if quality['status']!='usable':
            result.update(status='held',reasons=['body_state_crop_quality_insufficient']);return result
        x1,y1,x2,y2=quality['bounds']
    else:
        valid=[p for p in points if p is not None]
        if len(valid)<2:
            result.update(status='held',reasons=['body_state_target_unavailable']);return result
        xs,ys=zip(*valid);dx=max(xs)-min(xs);dy=max(ys)-min(ys)
        if dx<=0 or dy<=0:
            result.update(status='held',reasons=['body_state_target_unavailable']);return result
        x1=max(0,math.floor(min(xs)-.1*dx));x2=min(w,math.ceil(max(xs)+.1*dx)+1)
        y1=max(0,math.floor(min(ys)-.1*dy));y2=min(h,math.ceil(max(ys)+.1*dy)+1)
    local=tuple((p[0]-x1,p[1]-y1) if p is not None else None for p in points)
    try:
        decision=validate_decision(provider.estimate(ImageBodyInput(rgb[y1:y2,x1:x2],row['stream_frame_id'],row['clock_id'],
                                                                 row['received_ns'],assessment['candidate_id'],local,(x1,y1,x2,y2))))
    except Exception as exc:
        result.update(status='held',reasons=['body_state_provider_failed'],
                      error={'code':'IMAGE_PROVIDER_ERROR','exception_type':type(exc).__name__})
        return result
    result.update(status='predicted' if decision['state']!='unknown' else 'held',decision=decision,
                  reasons=[] if decision['state']!='unknown' else ['body_state_unknown'])
    return result


def validate_observation(value,row,candidate,profile):
    expected={'stream_frame_id':row['stream_frame_id'],'clock_id':row['clock_id'],
              'received_ns':row['received_ns'],'candidate_id':candidate}
    if (not isinstance(value,dict) or set(value)-{'error','input_quality'}!={'version',*expected,'provider','status','reasons','decision','verified_gt'}
            or value.get('version')!=VERSION or value.get('verified_gt') is not False
            or any(value.get(k)!=v or type(value.get(k)) is not type(v) for k,v in expected.items())
            or value.get('provider')!=profile or value.get('status') not in ('unavailable','held','predicted')
            or not isinstance(value.get('reasons'),list) or any(not isinstance(r,str) for r in value['reasons'])):
        raise ValueError('Body-state identity or provider mismatch')
    if (profile and profile.get('decision_policy',{}).get('crop_quality') and value['decision'] is not None
            and (not isinstance(value.get('input_quality'),dict) or value['input_quality'].get('status')!='usable')):
        raise ValueError('Missing CLIP input quality')
    if 'input_quality' in value and isinstance(value['input_quality'],dict) and value['input_quality'].get('version')=='person-crop-quality-0.1':
        q=value['input_quality']
        fallback=row.get('person_crop_observation') or {}
        if q!=fallback.get('quality') or fallback.get('candidate_id')!=candidate or (q['status']=='held' and value['decision'] is not None):
            raise ValueError('Invalid detector fallback quality')
    elif 'input_quality' in value:
        q=value['input_quality']
        if (not isinstance(q,dict) or set(q)!={'version','status','reasons','bounds','usable_joints','full_body_verified'}
                or q['version'] not in ('joint-crop-quality-0.1','joint-crop-quality-0.2') or q['status'] not in ('usable','held')
                or q['full_body_verified'] is not False or type(q['usable_joints']) is not int
                or not 0<=q['usable_joints']<=17 or not isinstance(q['reasons'],list)
                or q['status']=='held' and (value['decision'] is not None or value['reasons']!=['body_state_crop_quality_insufficient'])
                or q['status']=='usable' and (q['reasons'] or not isinstance(q['bounds'],list) or len(q['bounds'])!=4)):
            raise ValueError('Invalid crop quality')
    if 'error' in value:
        error=value['error']
        if (not isinstance(error,dict) or set(error)!={'code','exception_type'}
                or error['code']!='IMAGE_PROVIDER_ERROR' or not isinstance(error['exception_type'],str)
                or not error['exception_type'].isidentifier() or value['status']!='held'
                or value['decision'] is not None or value['reasons']!=['body_state_provider_failed']):
            raise ValueError('Invalid image provider error')
    decision=validate_decision(value['decision']) if value['decision'] is not None else None
    if profile and profile['model_id']=='openai/clip-vit-base-patch16' and decision is not None:
        from .clip_posture import validate_recorded_decision
        validate_recorded_decision(decision,profile)
    if ((profile is None and (decision is not None or value['status']!='unavailable'))
            or (value['status']=='predicted' and (decision is None or decision['state']=='unknown' or value['reasons']))
            or (value['status']!='predicted' and (not value['reasons'] or decision is not None and decision['state']!='unknown'))):
        raise ValueError('Inconsistent body-state observation')
    return value


def read_observation(value,row,candidate,profile):
    """Validate recorded evidence before composition; do not rerun the model."""
    result={'status':'held','state':'unknown','lying_direction':'unknown','reasons':['body_state_observation_invalid']}
    if value is None:
        result.update(status='unavailable',reasons=['body_state_provider_not_configured']);return result
    try:validate_observation(value,row,candidate,profile)
    except (ValueError,TypeError):
        result['reasons']=['body_state_identity_or_provider_mismatch'];return result
    if 'error' in value:result['error']=deepcopy(value['error'])
    if 'input_quality' in value:result['input_quality']=deepcopy(value['input_quality'])
    if value.get('status') in ('unavailable','held') and value.get('decision') is None:
        result['reasons']=value.get('reasons') or ['body_state_unknown'];return result
    try:decision=validate_decision(value['decision'])
    except (KeyError,ValueError,TypeError):return result
    if 'details' in decision:
        result.update(details=decision['details'],scores=decision['scores'],score_semantics=decision['score_semantics'])
    if value.get('status')!='predicted' or decision['state']=='unknown':
        result['reasons']=['body_state_unknown'];return result
    result.update(status='predicted',state=decision['state'],lying_direction=decision['lying_direction'],
                  reasons=[],scores=decision['scores'],score_semantics=decision['score_semantics'])
    return result


def compose(state,relation):
    """Compatibility entry point for the shared final decision function."""
    from .posture_decision import finalize
    return finalize(state, relation)
