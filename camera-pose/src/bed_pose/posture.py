"""Frame-scoped posture interface with explicit abstention and experimental classifiers."""
from dataclasses import dataclass
import math
from typing import Protocol

LABELS=('supine','side','edge_sitting')
PREDICTION_LABELS=LABELS+('sitting','prone','lying','standing','other')
PROFILE={'version':'posture-interface-0.1','classifier':'not_configured',
         'quality_policy':'finite_in_image_coordinates_only_not_accuracy',
         'temporal_policy':'current_frame_only_no_smoothing'}

@dataclass(frozen=True)
class PostureInput:
    # Only visual observations are passed to classifiers; no goals, GT or sensors.
    points: tuple
    width: int
    height: int
    bed_rectangle: tuple | dict | None
    # Optional same-candidate model estimate. Never a measured bed height.
    world_body: dict | None = None
    world_points: tuple | None = None
    body_state: dict | None = None

class PostureClassifier(Protocol):
    model_id: str
    def predict(self, observation: PostureInput) -> str | None: ...


def assess_frame(row,width,height,region,classifier: PostureClassifier | None=None):
    tracking=row.get('target_tracking',{'status':'not_configured'})
    people=row.get('mapped_people') or []
    reasons=[]
    if row['processing_status']!='ok':reasons.append('processing_failed')
    candidate=None
    selection=row.get('bed_target_selection')
    if isinstance(selection,dict) and selection.get('reason') not in ('bed_region_missing','no_bed_candidate'):
        if selection.get('status')=='selected':
            matches=[p for p in people if p['candidate_id']==selection.get('candidate_id')]
            if len(matches)==1:candidate=matches[0]
            else:reasons.append('bed_target_unavailable')
        else:reasons.append(selection.get('reason') or 'bed_target_unavailable')
    elif tracking['status']=='held':reasons.append('target_held')
    elif tracking['status']=='tracking_provisional':
        matches=[p for p in people if p['candidate_id']==tracking.get('candidate_id')]
        if len(matches)==1:candidate=matches[0]
        else:reasons.append('tracked_candidate_missing')
    elif len(people)==1:candidate=people[0]
    elif not people:reasons.append('no_candidate')
    else:reasons.append('ambiguous_target')
    points=[]
    for joint in candidate['joints'] if candidate else []:
        x,y=joint.get('x_px'),joint.get('y_px')
        valid=(joint.get('availability')=='predicted' and type(x) in (float,int) and type(y) in (float,int)
               and math.isfinite(x) and math.isfinite(y) and 0<=x<width and 0<=y<height)
        points.append((x,y) if valid else None)
    # Version 0.11 uses the source model's visibility rather than treating
    # every finite coordinate as an observed joint. Presence is optional for
    # browser GPU output; missing visibility cannot establish joint quality.
    if (candidate and getattr(classifier,'profile',{}).get('version') in ('automatic-posture-rules-0.11','automatic-posture-rules-0.12')):
        raw=[p for p in row.get('raw_candidates',[]) if p.get('candidate_id')==candidate['candidate_id']]
        scores=raw[0].get('sdk_landmarks',{}).get('image',[]) if len(raw)==1 else []
        for i,joint in enumerate(candidate['joints']):
            source=joint.get('source_index')
            score=scores[source] if type(source) is int and 0<=source<len(scores) else {}
            visibility=score.get('visibility') if isinstance(score,dict) else None
            presence=score.get('presence') if isinstance(score,dict) else None
            def good(v):return type(v) in (int,float) and math.isfinite(v) and .5<=v<=1
            if not good(visibility) or (presence is not None and not good(presence)):points[i]=None
    if candidate and not any(p is not None for p in points):reasons.append('no_usable_coordinates')
    if classifier is None:reasons.append('classifier_not_configured')
    result={'version':PROFILE['version'],'stream_frame_id':row['stream_frame_id'],
        'clock_id':row['clock_id'],'received_ns':row['received_ns'],
        'status':'held' if any(r!='classifier_not_configured' for r in reasons) else 'unavailable',
        'label':None,'reasons':reasons,'candidate_id':candidate['candidate_id'] if candidate else None,
        'track_id':tracking.get('track_id') if not isinstance(selection,dict) or selection.get('reason') in ('bed_region_missing','no_bed_candidate') or (candidate is not None and tracking.get('status')=='tracking_provisional' and tracking.get('candidate_id')==candidate.get('candidate_id')) else None,'identity_verified':False,'quality_status':'not_assessed',
        'usable_joints':sum(p is not None for p in points),'total_joints':len(points),
        'bed_region_available':region is not None,'classifier_id':getattr(classifier,'model_id',None)}
    if reasons:return result
    try:
        from .bed_geometry import observation_region
        from .reference_posture import ReferenceClassifier
        from .automatic_posture import AutomaticClassifier,METHOD,WORLD_METHOD,AUTO_METHOD,SEPARATED_METHOD,BODY_STATE_METHODS
        world=None
        if isinstance(classifier,AutomaticClassifier) and classifier.profile['version'] in (WORLD_METHOD,METHOD,AUTO_METHOD,SEPARATED_METHOD,*BODY_STATE_METHODS):
            from .posture_evidence import world_body_evidence
            world=world_body_evidence(row,candidate)
        pose=None
        if isinstance(classifier,AutomaticClassifier) and classifier.profile['version'] in (AUTO_METHOD,SEPARATED_METHOD,*BODY_STATE_METHODS):
            from .viewpoint_posture import world_points
            pose=world_points(row,candidate)
        separated=isinstance(classifier,AutomaticClassifier) and classifier.profile['version'] in (SEPARATED_METHOD,*BODY_STATE_METHODS)
        state=None
        if isinstance(classifier,AutomaticClassifier) and classifier.profile['version'] in BODY_STATE_METHODS:
            from .body_state import read_observation
            state=read_observation(row.get('body_state_observation'),row,candidate['candidate_id'],classifier.body_state_profile)
        observation=PostureInput(tuple(points),width,height,region if separated else observation_region(region),world,pose,state)
        if isinstance(classifier,AutomaticClassifier):
            label,reason,evidence=classifier.evaluate(observation)
            result.update(rule_features=evidence,experimental=True)
            if reason:
                result.update(status='held',reasons=[reason])
                return result
        elif isinstance(classifier,ReferenceClassifier):
            label,reason,distances=classifier.evaluate(observation)
            result.update(reference_distances=distances,experimental=True)
            if reason:
                result.update(status='held',reasons=[reason])
                return result
        else:label=classifier.predict(observation)
        if label is None:result.update(status='held',reasons=['insufficient_or_out_of_scope'])
        elif type(label) is not str or label not in PREDICTION_LABELS:result.update(status='held',reasons=['invalid_classifier_output'])
        else:result.update(status='predicted',label=label)
    except Exception:
        result.update(status='held',reasons=['classifier_failed'])
    return result


def group_assessment(observations):
    # Latest previews are NOT time-aligned classifier inputs.
    return {'status':'unavailable','label':None,'reasons':['live_pairing_not_connected','classifier_not_configured'],
            'views':observations,'identity_verified':False,'exposure_synchronized':False}


def load_classifier(manifest):
    automatic=manifest.get('automatic_posture_profile');reference=manifest.get('reference_posture_profile')
    if automatic and reference:raise ValueError('Multiple active posture classifiers')
    if automatic:
        from .automatic_posture import AutomaticClassifier
        return AutomaticClassifier(automatic,body_state_profile=manifest.get('body_state_provider_profile'))
    if reference:
        from .reference_posture import ReferenceClassifier
        return ReferenceClassifier(reference)
    return None
