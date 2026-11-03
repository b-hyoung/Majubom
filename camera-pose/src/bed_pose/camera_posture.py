"""Immediate camera estimates for display; strict 3D/training decisions stay separate.

CLIP relative scores are combined with observed bed relations and optional model
geometry. No goal, ToF label, annotation or asserted ground truth is an input.
"""
from copy import deepcopy
import math
import numpy as np
from .automatic_posture import FORCED_DEFAULTS
from .bed_posture_3d import LABELS, classify
from .posture_decision import scores_valid
from .separated_posture import bed_relation

METHOD='camera-six-live-0.3'
NAMES=dict(LABELS,side='옆으로 누움 · 좌우 확인 중',standing='서 있음',other='기타 자세')


def geometry(pose,bed,relation):
    if not pose or not bed or pose.get('coordinateSystem')!=bed.get('coordinate_system'):
        return {},None
    p={j['id']:np.array([j['x'],j['y'],j['z']],float) for j in pose.get('joints',[])
       if j.get('valid') and all(isinstance(j.get(k),(int,float)) and math.isfinite(j[k]) for k in ('x','y','z'))}
    if not all(i in p for i in (5,6,11,12)):return {},None
    shoulders=(p[5]+p[6])/2;hips=(p[11]+p[12])/2
    torso=shoulders-hips;across=p[6]-p[5]
    length=np.linalg.norm(torso);width=np.linalg.norm(across)
    if not .08<length<2 or width<.02:return {},None
    normal=np.array(bed['normal'],float)
    if not np.isfinite(normal).all():return {},None
    upright=float(np.dot(torso/length,normal))
    anterior=np.cross(across,hips-shoulders);norm=np.linalg.norm(anterior)
    if norm<1e-6:return {},None
    face=float(np.dot(anterior/norm,normal));side=float(np.dot(across/width,normal))
    sit=1/(1+math.exp(-10*(upright-.5)))
    edge=relation.get('relation')=='edge' or relation.get('edge_posture_evidence',{}).get('supported',False)
    scores=dict(supine=(1-sit)*max(.02,(face+1)/2)**3,
                prone=(1-sit)*max(.02,(1-face)/2)**3,
                left_side=(1-sit)*max(.02,(side+1)/2)**3,
                right_side=(1-sit)*max(.02,(1-side)/2)**3,
                sitting=sit*(.08 if edge else .92),edge_sitting=sit*(.92 if edge else .08))
    thighs=[p[k]-p[h] for k,h in ((13,11),(14,12)) if k in p]
    if upright>.7 and thighs and all(np.linalg.norm(v)>.05 and np.dot(v/np.linalg.norm(v),normal)<-.8 for v in thighs):
        scores['standing']=sit*1.5
    strict=classify(pose,bed)
    return scores,strict.get('label')


def estimate_view(packet,region,bed):
    points=tuple(tuple(j[:2]) if j else None for j in packet['joints'])
    relation=bed_relation(points,region,FORCED_DEFAULTS,packet['width'],packet['height'])
    geo,strict=geometry(packet.get('mono_preview'),bed,relation)
    visible=sum(j is not None for j in packet['joints'])
    if strict and visible<14:
        strict=None  # Partial estimated body axes cannot override image direction.
    state=packet.get('image_body_state') or {}
    states=state.get('scores') or {};directions=(state.get('details') or {}).get('direction_scores') or {}
    image={};warnings=[]
    # Cross-legged sitting can hide ankles. Use current observed knees and an
    # upright torso, without treating the unverified 3D bed height as contact.
    pose=packet.get('mono_preview')
    if pose and bed and relation.get('relation')=='inside' and region:
        from .bed_geometry import contains
        xyz={j['id']:np.array([j[k] for k in ('x','y','z')]) for j in pose['joints'] if j.get('valid')}
        if all(i in xyz and packet['joints'][i] is not None for i in (5,6,11,12,13,14)):
            n=np.array(bed['normal']);torso=(xyz[5]+xyz[6]-xyz[11]-xyz[12])/2
            thighs=[xyz[k]-xyz[h] for k,h in ((13,11),(14,12))]
            if (np.linalg.norm(torso)>.08 and np.dot(torso/np.linalg.norm(torso),n)>.8
                and all(np.linalg.norm(v)>.08 and abs(np.dot(v/np.linalg.norm(v),n))<.7 for v in thighs)
                and all(contains(region,points[i]) for i in (11,12,13,14))):
                strict='sitting';warnings.append('seated_support_from_projected_knees')
    if scores_valid(states,('lying','sitting','standing','other')):
        image.update(standing=states['standing'],other=states['other'])
        edge=relation.get('relation')=='edge' or relation.get('edge_posture_evidence',{}).get('supported',False)
        image['edge_sitting']=states['sitting']*(.94 if edge else .06)
        image['sitting']=states['sitting']*(.06 if edge else .94)
        if scores_valid(directions,('supine','side','prone')):
            image.update(supine=states['lying']*directions['supine'],prone=states['lying']*directions['prone'])
            side=states['lying']*directions['side']
            a,b=geo.get('left_side',0),geo.get('right_side',0)
            if a+b>.01:
                image.update(left_side=side*a/(a+b),right_side=side*b/(a+b))
                warnings.append('left_right_from_unverified_geometry')
            else:image['side']=side
        else:image['side']=states['lying'];warnings.append('lying_direction_unavailable')
    if image:
        scores=image;source='clip_image_and_bed_relation'
        # The generic CLIP vocabulary is weak for reclined/cross-legged bodies.
        # A complete class-specific geometry decision may contribute only when
        # the same observed target also lies inside the registered bed image.
        # This is still an unverified estimate, never a training approval.
        if strict in LABELS and relation.get('relation') in ('inside','edge'):
            if max(image,key=image.get)!=strict:warnings.append('clip_geometry_disagreement')
            scores={k:.2*v for k,v in image.items()}
            scores[strict]=scores.get(strict,0)+.8
            source='clip_and_supported_bed_geometry'
    elif geo:scores=geo;source='model_geometry_fallback';warnings.append('clip_unavailable_for_frame')
    else:return None
    if relation.get('relation') is None:warnings.append('bed_region_unavailable')
    if relation.get('relation') in ('outside','boundary'):warnings.append('bed_location_uncertain')
    valid=[j[2] for j in packet['joints'] if j]
    quality=sum(valid)/len(valid) if valid else 0.
    coverage=len(valid)/17
    total=sum(scores.values());scores={k:v/total for k,v in scores.items()}
    return dict(camera=packet.get('source_camera','?'),scores=scores,weight=max(.1,quality*coverage),
                source=source,quality_score=quality,joints=len(valid),relation=relation,
                warnings=warnings,frame_id=packet['frame_id'],strict_geometry_label=strict)


class LivePostureEstimator:
    def __init__(self):self.reset()
    def reset(self):
        self.smoothed={};self.time=None;self.key=None;self.persons=None;self.current=None;self.since=None;self.count=0
        self.challenger=None;self.challenge_since=None;self.challenge_count=0

    def update(self,packets,annotation,bed,now):
        base=dict(method=METHOD,status='unavailable',label=None,label_ko='카메라 입력 확인 중',
                  quality_score=None,score_is_probability=False,accuracy_verified=False,
                  eligible_for_recording=False,labels=deepcopy(NAMES),warnings=[],views=[],scores={},
                  accuracy_target=.95,accuracy_status='not_evaluated')
        fresh=[(i,p) for i,p in enumerate(packets) if p and 0<=now-p['time_ns']<=350e6]
        base['observation_id']=str(tuple((p.get('input_id'),p['frame_id']) for _,p in fresh)) if fresh else None
        views=[]
        for i,p in fresh:
            if not p.get('processing_ok') or p.get('people_count')!=1:continue
            region=p.get('bed_region')
            resolutions=annotation.get('resolutions',[]) if isinstance(annotation,dict) else []
            if len(resolutions)==2 and resolutions[i]==[p['width'],p['height']]:
                try:
                    from .bed_geometry import make_quadrilateral
                    region=make_quadrilateral(annotation['points'][i],p['width'],p['height'])
                except (ValueError,TypeError,KeyError,IndexError):pass
            v=estimate_view(p,region,bed)
            if v:views.append(v)
        if not views:
            self.reset()
            if fresh and all(p.get('people_count')==0 for _,p in fresh):base.update(status='no_person',label_ko='사람 관절 미검출')
            elif fresh:base.update(status='observing',label_ko='관절 추적 중 · 자세 분석 중')
            return base
        # Prefer a visible bed target when another view contains a bystander.
        in_bed=[v for v in views if v['relation'].get('relation') in ('inside','edge')]
        selected=in_bed or views
        winners=[max(v['scores'],key=v['scores'].get) for v in selected]
        warnings=list(dict.fromkeys(w for v in selected for w in v['warnings']))
        if len(set(winners))>1:warnings.append('cross_view_disagreement')
        scores={}
        for v in selected:
            for k,s in v['scores'].items():scores[k]=scores.get(k,0)+s*v['weight']
        total=sum(scores.values());scores={k:v/total for k,v in scores.items()}
        persons=tuple((p.get('input_id'),p.get('track_id')) for _,p in fresh)
        key=tuple((p.get('input_id'),p['frame_id']) for _,p in fresh)
        stamp=max(p['time_ns'] for _,p in fresh)/1e9
        if persons!=self.persons or self.time is None or stamp-self.time>.5:self.reset()
        if key!=self.key:
            alpha=1 if self.time is None else 1-math.exp(-max(0,stamp-self.time)/.12)
            self.smoothed={k:alpha*scores.get(k,0)+(1-alpha)*self.smoothed.get(k,0) for k in set(scores)|set(self.smoothed)}
            label=max(self.smoothed,key=self.smoothed.get)
            if self.current is not None and label!=self.current:
                if label!=self.challenger:
                    self.challenger=label;self.challenge_since=stamp;self.challenge_count=0
                self.challenge_count+=1
                if stamp-self.challenge_since<.5 or self.challenge_count<3:label=self.current
            else:self.challenger=None;self.challenge_count=0
            if label!=self.current:self.current=label;self.since=stamp;self.count=0
            self.count+=1;self.key=key;self.time=stamp;self.persons=persons
        label=self.current
        base.update(status='stable' if stamp-self.since>=.5 and self.count>=3 else 'estimated',
                    label=label,label_ko=NAMES[label],quality_score=min(v['quality_score'] for v in selected),
                    relative_score=self.smoothed[label],scores=deepcopy(self.smoothed),views=views,
                    warnings=warnings,source_frame_ids=[p['frame_id'] for _,p in fresh],
                    observation_id=str(key),stable_seconds=max(0,stamp-self.since),identity_verified=False)
        return base
