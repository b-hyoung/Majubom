"""Independent rule-fit scores, not probabilities. Close runners-up never veto.

Existing body/bed/target eligibility is separate from score selection. Weights
and cutoffs are versioned experimental settings, not fitted accuracy bounds.
"""
import math
from .posture import LABELS
from .lying_orientation import classify_lying


def fit(value,prototype,other,scale):
    """One-sided prototype fit with overlap; class scores need not sum to one."""
    distance=max(0.,value-prototype) if prototype<other else max(0.,prototype-value)
    return max(0.,min(1.,1-distance/(abs(other-prototype)*scale)))


def weighted(values):
    scale=max(weight for _,weight in values)
    total=math.fsum(weight/scale for _,weight in values)
    return max(0.,min(1.,math.fsum(value*((weight/scale)/total) for value,weight in values)))


def select_scores(scores,minimum,close_gap):
    if (set(scores)!=set(LABELS) or any(v is not None and
            (type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=1) for v in scores.values())):
        raise ValueError('Invalid posture scores')
    ranked=sorted(((label,v) for label,v in scores.items() if v is not None),key=lambda p:p[1],reverse=True)
    top=ranked[0] if ranked else (None,None);second=ranked[1] if len(ranked)>1 else (None,None)
    gap=top[1]-second[1] if second[1] is not None else None
    result={'version':'weighted-posture-0.1','score_semantics':'independent_rule_fit_not_probability',
            'scores':dict(scores),'minimum':minimum,'close_gap_threshold':close_gap,
            'best_candidate':top[0],'best_score':top[1],'runner_up':second[0],'runner_up_score':second[1],
            'gap':gap,'close_candidates':gap is not None and gap<close_gap,
            'margin_required':False,'label':None,'status':'held'}
    if not ranked:reason='posture_scores_unavailable'
    elif top[1]<minimum:reason='posture_score_below_threshold'
    elif gap is not None and gap<=1e-12:reason='posture_scores_tied'
    else:reason=None;result.update(label=top[0],status='predicted')
    result['reason']=reason
    return result


def evaluate_weighted(obs,cfg,baseline):
    label,reason,evidence=baseline
    scores=dict.fromkeys(LABELS);eligibility={name:['not_eligible_for_current_geometry'] for name in LABELS}
    components={};warnings=[]
    def finish(gate=None):
        decision=select_scores(scores,cfg['score_min'],cfg['score_close_gap'])
        decision.update(eligibility_reasons=eligibility,components=components,warnings=warnings)
        if gate:
            decision.update(label=None,status='held',reason=gate)
        evidence['score_decision']=decision
        return decision['label'],decision['reason'],evidence
    if label=='edge_sitting':
        # The existing sitting gate already requires valid legs, feet and edge.
        c=evidence['torso_vertical_cos'];knees=evidence['knee_angles'];edge=evidence['hip_edge_fraction']
        peak=cfg['score_knee_peak']
        knee=[fit(a,peak,cfg['bent_knee_min'] if a<peak else cfg['bent_knee_max'],1.) for a in knees]
        values={'torso':max(0.,min(1.,(c-cfg['lying_cos'])/(cfg['upright_cos']-cfg['lying_cos']))),
                'knees':min(knee),'edge':max(0.,1-edge/cfg['edge_fraction'])}
        components['edge_sitting']=values
        scores['edge_sitting']=weighted([(values[k],cfg['score_sitting_'+name+'_weight'])
            for k,name in (('torso','torso'),('knees','knee'),('edge','edge'))])
        eligibility['edge_sitting']=[]
        return finish()
    if label not in ('supine','side') and reason!='lying_orientation_ambiguous':
        evidence['orientation_decision']={'status':'not_used','source':'existing_gate','gate_reason':reason}
        eligibility={name:[reason] for name in LABELS}
        return finish(reason)
    _,orientation_reason,orientation=classify_lying(obs,cfg)
    evidence['orientation_decision']=orientation
    if orientation_reason in ('body_axis_inconsistent','shoulder_hip_orientation_conflict'):
        return finish(orientation_reason)
    f=orientation['body_2d'];scale=cfg['score_overlap_scale']
    pair_weights=[cfg['score_shoulder_weight'],cfg['score_hip_weight']]
    for name,prototype,other in (('side',cfg['side_span_max'],cfg['front_span_min']),
                                 ('supine',cfg['front_span_min'],cfg['side_span_max'])):
        components[name]={'body_2d':weighted([(fit(f[part]['transverse'],prototype,other,scale),w)
            for part,w in zip(('shoulders','hips'),pair_weights)])}
    node=orientation['world_3d']
    use_world=node['status']=='available' and orientation['world_orientation_reason']!='world_body_inconsistent'
    if use_world:
        world=node['features']
        for name,prototype,other in (('side',cfg['world_side_depth_min'],cfg['world_front_depth_max']),
                                     ('supine',cfg['world_front_depth_max'],cfg['world_side_depth_min'])):
            components[name]['world_3d']=weighted([(fit(abs(world[key]),prototype,other,scale),w)
                for key,w in zip(('shoulder_depth_fraction','hip_depth_fraction'),pair_weights)])
    orientation['source']='body_2d_world_3d' if use_world else 'body_2d'
    if orientation_reason=='orientation_evidence_conflict':warnings.append('orientation_evidence_conflict')
    for name in ('supine','side'):
        scores[name]=weighted([(v,cfg['score_2d_weight'] if k=='body_2d' else cfg['score_3d_weight'])
                               for k,v in components[name].items()])
        eligibility[name]=[]
    # Anterior evidence is required for supine even when another class can score.
    p=obs.points;across=(p[5][0]-p[6][0],p[5][1]-p[6][1])
    s=tuple((p[5][i]+p[6][i])/2 for i in (0,1));h=tuple((p[11][i]+p[12][i])/2 for i in (0,1))
    front_reason='front_facing_evidence_missing' if any(p[i] is None for i in (0,1,2)) else (
        'front_back_ambiguous' if across[0]*(h[1]-s[1])-across[1]*(h[0]-s[0])<=0 else None)
    if front_reason:scores['supine']=None;eligibility['supine']=[front_reason]
    result=finish()
    orientation['status']=evidence['score_decision']['status']
    return result
