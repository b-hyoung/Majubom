"""Versioned minimal CLIP crop coverage; not anatomical accuracy validation."""
import math

POLICY = {'version':'joint-crop-quality-0.1', 'min_visibility':0.5,
          'min_presence':0.5, 'required_groups':[[5,6],[11,12],[13,14,15,16]],
          'validation':'experimental_initial_policy_not_accuracy'}


def joint_points(row, candidate_id, width, height, *, allow_missing_presence=False):
    from .pose.mediapipe import COCO_INDICES
    people=[p for p in row.get('mapped_people') or [] if p['candidate_id']==candidate_id]
    raw=[p for p in row.get('raw_candidates') or [] if p['candidate_id']==candidate_id]
    points=[None]*17
    scores=raw[0].get('sdk_landmarks',{}).get('image',[]) if len(raw)==1 else []
    reasons=[]
    if len(people)!=1 or len(people[0]['joints'])!=17 or len(scores)!=33:
        reasons.append('crop_quality_source_unavailable')
    else:
        for i,j in enumerate(people[0]['joints']):
            score=scores[COCO_INDICES[i]];x,y=j.get('x_px'),j.get('y_px')
            confidence=all((k=='presence' and allow_missing_presence and score.get(k) is None) or
                           (type(score.get(k)) in (int,float) and math.isfinite(score[k])
                            and POLICY['min_'+k]<=score[k]<=1) for k in ('visibility','presence'))
            if (confidence and j.get('availability')=='predicted' and type(x) in (int,float)
                    and type(y) in (int,float) and math.isfinite(x) and math.isfinite(y)
                    and 0<=x<width and 0<=y<height):points[i]=(x,y)
    return tuple(points), reasons


def inspect_crop(row, candidate_id, width, height, *, allow_missing_presence=False):
    """Legacy region policy, independent of alternate person-region providers."""
    points,reasons=joint_points(row,candidate_id,width,height,allow_missing_presence=allow_missing_presence)
    if not reasons:
        for name,indices in zip(('shoulder','hip','lower_body'),POLICY['required_groups']):
            if not any(points[i] is not None for i in indices):reasons.append('crop_'+name+'_missing')
    valid=[p for p in points if p is not None]
    bounds=None
    if not reasons:
        xs,ys=zip(*valid);dx=max(xs)-min(xs);dy=max(ys)-min(ys)
        shoulder=next(points[i] for i in (5,6) if points[i] is not None)
        hip=next(points[i] for i in (11,12) if points[i] is not None)
        if dx<=0 or dy<=0 or shoulder==hip:reasons.append('crop_geometry_degenerate')
        else:bounds=[max(0,math.floor(min(xs)-.1*dx)),max(0,math.floor(min(ys)-.1*dy)),
                     min(width,math.ceil(max(xs)+.1*dx)+1),min(height,math.ceil(max(ys)+.1*dy)+1)]
    return tuple(points), {'version':'joint-crop-quality-0.2' if allow_missing_presence else POLICY['version'],'status':'held' if reasons else 'usable',
                           'reasons':reasons,'bounds':bounds,'usable_joints':len(valid),
                           'full_body_verified':False}
