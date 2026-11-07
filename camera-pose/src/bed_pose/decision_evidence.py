"""Common camera evidence and diagnostic conflicts; never changes a decision.

Scores are relative, uncalibrated CLIP scores. Disagreement is not an error label.
A contribution weight describes arithmetic pooling, not observation reliability.
"""
from copy import deepcopy
from .posture_decision import choose, scores_valid

PROFILE = {'version': 'camera-decision-evidence-0.1',
           'purpose': 'diagnostic_only_no_decision_change',
           'time_policy': 'current_observation_no_smoothing',
           'score_semantics': 'uncalibrated_relative_scores',
           'conflict_policy': 'different_unique_group_winners_no_new_threshold'}
STATES = ('lying', 'sitting', 'standing', 'other')
DIRECTIONS = ('supine', 'side', 'prone')


def camera(assessment, *, input_id=None, time_ns=None, profile=None):
    """Normalize one camera without inventing missing quality or calibrated scores."""
    a = assessment or {}
    body = a.get('rule_features', {}).get('body_state') or {}
    details = body.get('details') or {}
    scores = body.get('scores') or {}
    directions = details.get('direction_scores') or {}
    cfg = (profile or {}).get('decision_policy') or {}
    valid = body.get('score_semantics') == 'uncalibrated_probability'
    scores = deepcopy(scores) if valid and scores_valid(scores, STATES) else {}
    directions = deepcopy(directions) if valid and scores_valid(directions, DIRECTIONS) else {}
    usable = (body.get('input_quality', {}).get('status') == 'usable' and not body.get('error')
              and bool(scores) and bool(directions))
    return {'fusion_usable': bool(usable), 'input_id': input_id, 'candidate_id': a.get('candidate_id'),
            'track_id': a.get('track_id'), 'stream_frame_id': a.get('stream_frame_id'),
            'clock_id': a.get('clock_id'), 'received_ns': a.get('received_ns'),
            'aligned_time_ns': time_ns, 'time_source': 'receive_time_approximation',
            'provider': {k: (profile or {}).get(k) for k in ('model_id', 'model_sha256', 'preprocess_id')},
            'quality': deepcopy(body.get('input_quality') or {'status': 'not_assessed', 'reasons': []}),
            'state_scores': scores, 'direction_scores': directions,
            'state_candidate': choose(scores, 0), 'direction_candidate': choose(directions, 0),
            'accepted_state': body.get('state', 'unknown'),
            'accepted_direction': body.get('lying_direction', 'unknown'),
            'thresholds': {k: cfg.get(k) for k in ('state_min', 'direction_min')},
            'status': a.get('status', 'unavailable'), 'label': a.get('label'),
            'reasons': list(a.get('reasons') or []),
            'body_reasons': list(body.get('reasons') or []),
            'error': deepcopy(body.get('error')),
            'state_used': False, 'direction_used': False,
            'state_weight': 0., 'direction_weight': 0.}


def single(row, profile=None):
    a = row.get('posture_assessment') or {}
    view = camera(a, profile=profile)
    # An unknown result may still be based on evaluated scores. This is evidence
    # use, not a claim that it passed the posture threshold or crop validation.
    view['state_used'] = bool(view['state_scores'])
    view['direction_used'] = view['state_used'] and view['accepted_state'] == 'lying' and bool(view['direction_scores'])
    view['state_weight'] = float(view['state_used'])
    view['direction_weight'] = float(view['direction_used'])
    return {'version': PROFILE['version'], 'mode': 'single_view', 'views': [view],
            'conflicts': [], 'diagnostic_only': True,
            'result': {k: deepcopy(a.get(k)) for k in ('status', 'label', 'reasons')}}


def group(views, result):
    normalized = [camera(v.get('assessment'), input_id=v['input_id'],
                         time_ns=v.get('time_ns'), profile=v.get('image_profile')) for v in views]
    fusion = result.get('fusion_evidence') or {}
    contributors = {v['input_id']: v for v in fusion.get('contributors', [])}
    for v in normalized:
        c = contributors.get(v['input_id']) or {}
        v['state_used'] = bool(c.get('used'))
        v['direction_used'] = v['state_used'] and bool(v['state_scores']) and max(v['state_scores'], key=v['state_scores'].get) == 'lying'
        v['exclusion_reasons'] = [] if v['state_used'] else list(c.get('reasons') or result.get('reasons') or ['fusion_view_evidence_unavailable'])
    for key in ('state', 'direction'):
        count = sum(v[key+'_used'] for v in normalized)
        for v in normalized:
            v[key+'_weight'] = 1./count if v[key+'_used'] and count else 0.
    conflicts = []
    for key in ('state', 'direction'):
        candidates = {v[key+'_candidate'] for v in normalized if v[key+'_used'] and v[key+'_candidate'] is not None}
        if len(candidates) > 1:
            conflicts.append(key+'_winner_disagreement')
    return {'version': PROFILE['version'], 'mode': fusion.get('mode', 'gated_or_not_image_fusion'),
            'views': normalized, 'conflicts': conflicts, 'diagnostic_only': True,
            'result': {k: deepcopy(result.get(k)) for k in ('status', 'label', 'reasons')}}
