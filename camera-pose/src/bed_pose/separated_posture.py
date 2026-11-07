"""Independent body-shape and image-region observations, not physical occupancy.

No bed geometry, camera intrinsics or assumed gravity is passed to body_shape.
Shape candidates are deliberately not confirmed supine/side/sitting postures.
"""
import math
from dataclasses import dataclass

from .weighted_posture import evaluate_weighted,fit

VERSION='body-bed-separation-0.1'


@dataclass(frozen=True)
class BodyInput:
    points: tuple
    world_body: dict | None = None
    world_points: tuple | None = None


def _angle(a,b,c):
    u=tuple(x-y for x,y in zip(a,b));v=tuple(x-y for x,y in zip(c,b))
    size=math.hypot(*u)*math.hypot(*v)
    if size<=1e-12:return None
    return math.degrees(math.acos(max(-1.,min(1.,sum(x*y for x,y in zip(u,v))/size))))


def body_shape(obs,cfg):
    """Use the existing orientation scores without claiming a lying state."""
    result={'version':VERSION,'status':'held','label':None,'reasons':[],
            'semantics':'body_shape_candidate_not_gravity_or_support',
            'bed_used':False,'physical_posture_verified':False,'orientation':None,
            'seated_shape':{'status':'unavailable','score':None,'reasons':['lower_body_3d_unavailable']}}
    p=obs.points
    if len(p)!=17 or any(p[i] is None for i in (5,6,11,12)):
        result['reasons']=['automatic_torso_joints_missing'];return result
    try:
        label,reason,e=evaluate_weighted(obs,cfg,(None,'lying_orientation_ambiguous',{}))
    except (ValueError,OverflowError,ZeroDivisionError):
        result['reasons']=['degenerate_body_geometry'];return result
    decision=e['score_decision'].copy()
    names={'supine':'front_shape','side':'side_shape','edge_sitting':'seated_shape'}
    decision['scores']={names[k]:v for k,v in decision['scores'].items()}
    for key in ('components','eligibility_reasons'):
        decision[key]={names[k]:v for k,v in decision.get(key,{}).items()}
    for key in ('label','best_candidate','runner_up'):decision[key]=names.get(decision[key])
    if decision['status']=='predicted':decision['status']='candidate'
    decision.update(version='body-shape-score-0.1',score_semantics='independent_body_shape_fit_not_physical_posture')
    result['orientation']={'label':{'supine':'front_shape','side':'side_shape'}.get(label),
                           'status':'candidate' if label else 'held','reasons':[reason] if reason else [],
                           'score_decision':decision}
    result.update(label=result['orientation']['label'],status=result['orientation']['status'],reasons=result['orientation']['reasons'])
    if reason in ('body_axis_inconsistent','shoulder_hip_orientation_conflict'):
        return result
    xyz=obs.world_points
    # Flexed hips and knees describe shape only: curled lying can look the same.
    if (xyz is not None and len(xyz)==17 and all(len(v)==3 and all(type(x) in (int,float) and math.isfinite(x) for x in v) for v in xyz)
            and all(p[i] is not None for i in (5,6,11,12,13,14,15,16))):
        hips=[_angle(xyz[s],xyz[h],xyz[k]) for s,h,k in ((5,11,13),(6,12,14))]
        knees=[_angle(xyz[h],xyz[k],xyz[a]) for h,k,a in ((11,13,15),(12,14,16))]
        values=hips+knees
        bent=all(v is not None and cfg['bent_knee_min']<=v<=cfg['bent_knee_max'] for v in values)
        score=min(fit(v,cfg['score_knee_peak'],cfg['bent_knee_min'] if v<cfg['score_knee_peak'] else cfg['bent_knee_max'],1.) for v in values) if bent else None
        result['seated_shape']={'status':'candidate' if score is not None and score>=cfg['score_min'] else 'held',
                                'score':score,'hip_angles':hips,'knee_angles':knees,
                                'reasons':[] if score is not None and score>=cfg['score_min'] else ['seated_shape_not_established']}
        if result['seated_shape']['status']=='candidate':
            result.update(status='candidate',label='seated_shape',reasons=[])
    return result


def bed_relation(points,region,cfg,width,height):
    """2D projected membership only; no posture prediction is an input."""
    from .bed_geometry import contains,relative_xy
    result={'version':VERSION,'status':'unknown','relation':None,'reasons':[],
            'semantics':'image_region_membership_not_physical_occupancy',
            'physical_occupancy':'unknown','hip_edge_fraction':None}
    if cfg.get('version') in ('automatic-posture-rules-0.11','automatic-posture-rules-0.12'):
        result['torso_available']=(len(points)==17 and all(points[i] is not None for i in (5,6,11,12)))
    if region is None:result['reasons']=['bed_region_required'];return result
    if len(points)!=17 or any(points[i] is None for i in (11,12)):
        result['reasons']=['bed_relation_hips_missing'];return result
    try:
        from .live_recording import validate_region,make_region
        if isinstance(region,dict):validate_region(region,width,height)
        else:make_region(region,width,height)
        hips=[contains(region,points[i]) for i in (11,12)]
        if not any(hips):result.update(status='observed',relation='outside');return result
        if not all(hips):result.update(status='observed',relation='boundary');return result
        mid=tuple(sum(points[i][k] for i in (11,12))/2 for k in (0,1))
        uv=relative_xy(region,mid)
        if uv is None:result['reasons']=['bed_coordinate_unavailable'];return result
        edge=max(0.,min(*uv,1-uv[0],1-uv[1]))
        result.update(status='observed',relation='edge' if edge<=cfg['edge_fraction'] else 'inside',hip_edge_fraction=edge)
        if cfg.get('version') in ('automatic-posture-rules-0.9','automatic-posture-rules-0.10','automatic-posture-rules-0.11','automatic-posture-rules-0.12'):
            # Camera projection, not a physical bed-contact measurement. Both knees
            # must cross the same nearest edge while both hips remain inside.
            side=min(range(4),key=lambda i:(uv[0],1-uv[0],uv[1],1-uv[1])[i])
            knees=[relative_xy(region,points[i]) if points[i] is not None else None for i in (13,14)]
            valid=all(k is not None and all(math.isfinite(x) for x in k) for k in knees)
            crossing=valid and all(((k[0]<0 and 0<=k[1]<=1),
                                    (k[0]>1 and 0<=k[1]<=1),
                                    (k[1]<0 and 0<=k[0]<=1),
                                    (k[1]>1 and 0<=k[0]<=1))[side] for k in knees)
            supported=bool(crossing and edge<=cfg['knee_edge_hip_limit'])
            result['knee_edge_evidence']={'nearest_edge':side,'knees_cross_same_edge':bool(crossing),
                                          'hip_limit':cfg['knee_edge_hip_limit'],'supported':supported}
            if supported:result['relation']='edge'
            if cfg['version'] in ('automatic-posture-rules-0.10','automatic-posture-rules-0.11','automatic-posture-rules-0.12'):
                ankles=[relative_xy(region,points[i]) if points[i] is not None else None for i in (15,16)]
                ankles_valid=all(a is not None and all(math.isfinite(x) for x in a) for a in ankles)
                def outside_same_side(p):
                    return ((p[0]<0 and 0<=p[1]<=1), (p[0]>1 and 0<=p[1]<=1),
                            (p[1]<0 and 0<=p[0]<=1), (p[1]>1 and 0<=p[0]<=1))[side]
                def near_same_side(p):
                    distance=(p[0],1-p[0],p[1],1-p[1])[side]
                    orthogonal=p[1] if side<2 else p[0]
                    return distance<=cfg['foot_edge_knee_limit'] and 0<=orthogonal<=1
                foot_crossing=bool(ankles_valid and all(outside_same_side(a) for a in ankles))
                knee_near=bool(valid and all(near_same_side(k) for k in knees))
                foot_supported=bool(foot_crossing and knee_near and edge<=cfg['knee_edge_hip_limit'])
                result['foot_edge_evidence']={'nearest_edge':side,'ankles_cross_same_edge':foot_crossing,
                    'knees_near_same_edge':knee_near,'knee_limit':cfg['foot_edge_knee_limit'],
                    'supported':foot_supported,'semantics':'ankle_projection_not_physical_contact'}
                if foot_supported:result['relation']='edge'
                if cfg['version'] in ('automatic-posture-rules-0.11','automatic-posture-rules-0.12'):
                    # Hip proximity alone is not enough. Knees must be near or
                    # beyond the same edge, and both ankles beyond that edge.
                    result['edge_posture_evidence']={
                        'supported':bool(foot_crossing and (knee_near or crossing)
                                         and edge<=cfg['knee_edge_hip_limit']),
                        'knees_available':bool(valid),'ankles_available':bool(ankles_valid),
                        'semantics':'2d_projection_not_physical_support'}
                    result['sitting_inside_evidence']={
                        'supported':bool(valid and ankles_valid and edge>cfg['edge_fraction']
                                         and all(contains(region,points[i]) for i in (13,14,15,16))),
                        'semantics':'2d_projection_not_physical_support'}
    except (TypeError,ValueError,KeyError,ZeroDivisionError,OverflowError):
        result['reasons']=['bed_coordinate_unavailable']
    return result


def evaluate(obs,cfg):
    # Explicitly remove the region from the body's input contract.
    body=body_shape(BodyInput(obs.points,obs.world_body,obs.world_points),cfg)
    relation=bed_relation(obs.points,obs.bed_rectangle,cfg,obs.width,obs.height)
    candidate=None
    if body['label'] in ('front_shape','side_shape') and relation['relation'] in ('inside','edge'):
        candidate={'front_shape':'supine','side_shape':'side'}[body['label']]
    elif body['label']=='seated_shape' and relation['relation']=='edge':candidate='edge_sitting'
    context={'candidate_label':candidate,'status':'candidate' if candidate else 'unknown',
             'physical_posture_verified':False,'physical_occupancy':'unknown',
             'reasons':['physical_posture_unverified'] if candidate else ['context_candidate_unavailable']}
    e={'view':'auto','information_path':'independent_body_and_bed_relation',
       'body_assessment':body,'bed_relation':relation,'context_assessment':context}
    # Never promote shape + 2D overlap into a confirmed clinical posture/event.
    reason='physical_posture_unverified' if body['status']=='candidate' else (body['reasons'] or ['posture_scores_unavailable'])[0]
    return None,reason,e


def evaluate_with_body_state(obs,cfg):
    """A prediction can be emitted without being declared verified ground truth."""
    from .body_state import compose
    _,_,e=evaluate(obs,cfg)
    state=obs.body_state or {'status':'unavailable','state':'unknown','lying_direction':'unknown',
                             'reasons':['body_state_provider_not_configured']}
    if cfg['version']=='automatic-posture-rules-0.12':
        from .posture_decision import finalize_forced
        label,reason,e['forced_decision']=finalize_forced(state,e['bed_relation'])
    elif cfg['version']=='automatic-posture-rules-0.11':
        from .posture_decision import finalize_evidence
        label,reason=finalize_evidence(state,e['bed_relation'],obs.points)
    else:label,reason=compose(state,e['bed_relation'])
    e['body_state']=state
    e['context_assessment'].update(status='predicted' if label else 'held',label=label,candidate_label=label,
                                    reasons=[reason] if reason else [],
                                    evidence_source='independent_camera_image_state',
                                    physical_posture_verified=False)
    return label,reason,e
