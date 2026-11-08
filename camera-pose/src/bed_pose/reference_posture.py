"""Experimental supervised reference matching; not a pretrained posture model."""
import math
from copy import deepcopy
from .posture import LABELS
from .io_utils import object_hash

CORE=(5,6,11,12,13,14,15,16)
METHOD='reference-posture-0.1'

def features(observation):
    if observation.bed_rectangle is None or isinstance(observation.bed_rectangle,dict) or len(observation.points)!=17:return None
    if any(observation.points[i] is None for i in CORE):return None
    p=observation.points
    hip=tuple((p[11][i]+p[12][i])/2 for i in (0,1))
    shoulder=tuple((p[5][i]+p[6][i])/2 for i in (0,1))
    scale=math.dist(hip,shoulder)
    if not math.isfinite(scale) or scale<=0:return None
    x1,y1,x2,y2=observation.bed_rectangle
    if not x2>x1 or not y2>y1:return None
    return [v for i in CORE for v in ((p[i][0]-hip[0])/scale,(p[i][1]-hip[1])/scale)]+[(hip[0]-x1)/(x2-x1),(hip[1]-y1)/(y2-y1)]

def distance(a,b):return math.sqrt(sum((x-y)**2 for x,y in zip(a,b))/len(a))

class ReferenceClassifier:
    def __init__(self,profile):
        self.profile=deepcopy(profile)
        if profile.get('version')!=METHOD or profile.get('label_source')!='operator_reference_not_verified':raise ValueError('Unknown reference profile')
        if set(profile)!={'version','label_source','width','height','bed_rectangle','max_distance','min_margin','references'}:raise ValueError('Reference profile fields')
        for key in ('max_distance','min_margin'):
            value=profile[key]
            if type(value) not in (int,float) or not math.isfinite(value) or value<=0:raise ValueError('거리와 차이 기준은 양의 유한한 값이어야 합니다.')
        if any(type(profile[k]) is not int or profile[k]<=0 for k in ('width','height')):raise ValueError('Reference geometry')
        box=profile['bed_rectangle']
        if (not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box)
            or not 0<=box[0]<box[2]<=profile['width'] or not 0<=box[1]<box[3]<=profile['height']):raise ValueError('Reference rectangle')
        refs=profile['references']
        if not isinstance(refs,dict) or set(refs)-set(LABELS):raise ValueError('Reference labels')
        for label,ref in refs.items():
            vector=ref['features']
            if not isinstance(vector,list) or len(vector)!=18 or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector):raise ValueError('Reference coordinates')
            if not isinstance(ref.get('source'),dict):raise ValueError('Reference provenance')
        self.model_id=METHOD+':'+object_hash(self.profile)

    @property
    def ready(self):return set(self.profile['references'])==set(LABELS)

    def evaluate(self,observation):
        if not self.ready:return None,'references_incomplete',{}
        if (observation.width,observation.height)!=(self.profile['width'],self.profile['height']) or list(observation.bed_rectangle or [])!=self.profile['bed_rectangle']:
            return None,'reference_geometry_changed',{}
        vector=features(observation)
        if vector is None:return None,'reference_joints_missing',{}
        scores={label:distance(vector,ref['features']) for label,ref in self.profile['references'].items()}
        ranked=sorted(scores,key=scores.get);best,second=ranked[:2]
        if scores[best]>self.profile['max_distance']:return None,'reference_too_far',scores
        if scores[second]-scores[best]<self.profile['min_margin']:return None,'reference_ambiguous',scores
        return best,None,scores

    def predict(self,observation):return self.evaluate(observation)[0]


def combine_views(views,max_skew_ns,confirmations,active,now_ns,fusion_policy=None,association_policy=None):
    """Equal mean of per-view normalized distances, receive-time gated; no 3D fusion."""
    result={'status':'held','label':None,'reasons':[],'views':[{k:value for k,value in v.items() if k!='image_profile'} for v in views],'experimental':True,
            'identity_verified':False,'exposure_synchronized':False,'method':fusion_policy['version'] if fusion_policy is not None else 'mean_reference_distance_v1'}
    reasons=result['reasons']
    if not active:reasons.append('group_not_recording')
    if len(views)!=2 or any(v['closed'] or not v.get('assessment') for v in views):reasons.append('view_unavailable')
    if reasons:return result
    times=[int(v['time_ns']) for v in views]
    result['delta_ns']=str(times[1]-times[0])
    if max_skew_ns is None or abs(times[1]-times[0])>max_skew_ns:reasons.append('pair_time_mismatch')
    if any(now_ns-t>v['max_age_ns'] or now_ns<t for v,t in zip(views,times)):reasons.append('stale_observation')
    if association_policy is not None:
        from .bed_association import POLICY,PRIORITY_POLICY
        if association_policy not in (POLICY,PRIORITY_POLICY):raise ValueError('Unsupported bed association policy')
        result['association_policy']=association_policy
        if any(not v.get('unique_bed_candidate') for v in views):reasons.append('bed_person_unavailable_or_ambiguous')
    else:
        eligible=[(stamp,ids) for stamp,ids in confirmations if stamp<=min(times)]
        confirmed=max(eligible,key=lambda x:x[0])[1] if eligible else {}
        for v in views:
            a=v['assessment']
            if not a.get('track_id') or a.get('track_id')!=confirmed.get(v['input_id']):reasons.append('operator_pair_confirmation_missing');break
    if fusion_policy is not None:
        if association_policy is None and any(not v.get('tracking_provisional') for v in views):reasons.append('view_features_unavailable')
        if reasons:return result
        from .multiview_posture import fuse
        fused=fuse(views,fusion_policy)
        fused['delta_ns']=result['delta_ns']
        if association_policy is not None:fused['association_policy']=association_policy
        return fused
    auto=[v.get('reference_profile',{}).get('version','').startswith('automatic-posture') if v.get('reference_profile') else False for v in views]
    if any(auto):
        result['method']='automatic_two_view_agreement_v1'
        if reasons:return result
        if not all(auto):reasons.append('mixed_classifier_modes')
        elif any(not v.get('tracking_provisional') or v['assessment']['status']!='predicted' for v in views):reasons.append('view_features_unavailable')
        elif views[0]['assessment']['label']!=views[1]['assessment']['label']:reasons.append('view_posture_conflict')
        else:result.update(status='predicted',label=views[0]['assessment']['label'])
        return result
    for v in views:
        a=v['assessment'];p=v.get('reference_profile')
        if (not p or not v.get('tracking_provisional') or set(a.get('reference_distances',{}))!=set(LABELS)):
            reasons.append('view_features_unavailable');break
    if reasons:return result
    scores={label:sum(v['assessment']['reference_distances'][label]/v['reference_profile']['max_distance'] for v in views)/2 for label in LABELS}
    ranked=sorted(scores,key=scores.get);best,second=ranked[:2]
    margin=max(v['reference_profile']['min_margin']/v['reference_profile']['max_distance'] for v in views)
    result['normalized_distances']=scores
    if scores[best]>1:reasons.append('reference_too_far')
    elif scores[second]-scores[best]<margin:reasons.append('reference_ambiguous')
    else:result.update(status='predicted',label=best)
    return result
