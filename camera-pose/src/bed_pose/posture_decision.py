"""Shared instantaneous decision functions; no temporal or quality policy changes."""

import math


def scores_valid(scores, names):
    return (isinstance(scores, dict) and set(scores) == set(names)
            and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in scores.values())
            and abs(math.fsum(scores.values())-1) < 1e-6)


def choose(scores, threshold, tie_tolerance=1e-8):
    """Select a unique maximum above the existing threshold; no runner-up margin."""
    if not scores:
        return None
    best = max(scores, key=scores.get)
    return best if (scores[best] >= threshold and
                    sum(abs(v-scores[best]) <= tie_tolerance for v in scores.values()) == 1) else None


def finalize(state, relation):
    """Compose image posture and independent bed relation with the existing rules."""
    if state['status'] != 'predicted':
        return None, (state['reasons'] or ['body_state_unknown'])[0]
    if state['state'] == 'lying':
        direction = state['lying_direction']
        if direction in ('supine', 'side'):
            return direction, None
        return None, 'prone_out_of_scope' if direction == 'prone' else 'lying_direction_evidence_missing'
    if state['state'] == 'sitting':
        if relation['status'] == 'observed' and relation['relation'] == 'edge':
            return 'edge_sitting', None
        return None, 'bed_edge_relation_unconfirmed'
    return None, 'standing_or_other_out_of_scope'


def finalize_evidence(state, relation, points=None):
    """Experimental 0.11 composition; coordinates are 2D observations only."""
    if state['status'] != 'predicted':
        return None, (state['reasons'] or ['body_state_unknown'])[0]
    if relation.get('status') != 'observed' or relation.get('relation') not in ('inside','edge'):
        return None, 'bed_relation_unconfirmed'
    if ((points is not None and (len(points)!=17 or any(points[i] is None for i in (5,6,11,12))))
            or (points is None and not relation.get('torso_available'))):
        return None, 'automatic_torso_joints_missing'
    if state['state']=='sitting':
        edge=relation.get('edge_posture_evidence',{})
        if not edge.get('knees_available') or not edge.get('ankles_available'):
            return None, 'edge_leg_evidence_missing'
        if edge.get('supported'):
            return 'edge_sitting', None
        if relation.get('sitting_inside_evidence',{}).get('supported'):
            return 'sitting', None
        return None, 'edge_leg_evidence_unconfirmed'
    if state['state']=='lying':
        direction=state['lying_direction']
        if direction in ('supine','side','prone'):return direction, None
        return None, 'lying_direction_evidence_missing'
    return None, 'standing_or_other_out_of_scope'


def finalize_forced(state, relation):
    """Always select a best available image class, never invent missing scores."""
    scores=state.get('scores') or {}
    names=('lying','sitting','standing','other')
    if not scores_valid(scores,names):
        return None,(state.get('reasons') or ['body_state_unknown'])[0],{'available':False}
    winner=max(names,key=scores.get)
    directions=(state.get('details') or {}).get('direction_scores') or {}
    notes=[]
    if state.get('status')!='predicted':notes.append('below_state_threshold_or_tied')
    if sum(abs(scores[x]-scores[winner])<=1e-8 for x in names)>1:notes.append('state_tie')
    if winner=='lying':
        if scores_valid(directions,('supine','side','prone')):
            label=max(('supine','side','prone'),key=directions.get)
            if state.get('lying_direction')=='unknown':notes.append('below_direction_threshold_or_tied')
        else:
            label='lying';notes.append('direction_unavailable')
    elif winner=='sitting':
        edge=relation.get('edge_posture_evidence',{})
        if edge.get('supported'):label='edge_sitting'
        elif relation.get('status')=='observed' and relation.get('relation')=='edge':
            label='edge_sitting';notes.append('edge_based_on_hip_only')
        else:
            label='sitting'
            if not relation.get('sitting_inside_evidence',{}).get('supported'):notes.append('bed_sitting_location_unconfirmed')
    else:label=winner
    return label,None,{'available':True,'method':'maximum_relative_image_score',
                       'state_candidate':winner,'state_score':scores[winner],
                       'warnings':notes,'physical_posture_verified':False}
