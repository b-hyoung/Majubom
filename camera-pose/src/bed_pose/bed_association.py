"""Bed-priority frame selection; image overlap is not verified identity or occupancy."""
import math
from .io_utils import object_hash

POLICY='unique-person-in-bed-v1'  # Historical groups retain their original behavior.
PRIORITY_POLICY='bed-priority-v2'
PROFILE={'version':'bed-target-selection-0.1', 'policy':'both_hips_inside_unique_candidate',
         'joint_quality':'finite_in_image_and_reported_visibility_presence_at_least_0.5',
         'ambiguous_policy':'no_selection', 'identity_verified':False}


def select(row, region, width, height):
    """Select a single candidate whose two visible mapped hip points fall in the bed."""
    result={'version':PROFILE['version'],'status':'unavailable','candidate_id':None,
            'reason':None,'in_bed_candidate_ids':[],
            'bed_region_sha256':object_hash(region) if region is not None else None,'identity_verified':False}
    if region is None:
        result['reason']='bed_region_missing';return result
    if row.get('processing_status')!='ok':
        result['reason']='processing_failed';return result
    from .bed_geometry import contains
    for person in row.get('mapped_people') or []:
        joints=person.get('joints') or []
        if len(joints)<=12:continue
        raw=[p for p in (row.get('raw_candidates') or []) if p.get('candidate_id')==person.get('candidate_id')]
        image_scores=raw[0].get('sdk_landmarks',{}).get('image') if len(raw)==1 else None
        positions=[]
        for index in (11,12):
            joint=joints[index];x,y=joint.get('x_px'),joint.get('y_px')
            if (joint.get('availability')!='predicted' or type(x) not in (int,float) or type(y) not in (int,float)
                    or not math.isfinite(x) or not math.isfinite(y) or not 0<=x<width or not 0<=y<height):break
            if isinstance(image_scores,list):
                source=joint.get('source_index')
                if type(source) is not int or not 0<=source<len(image_scores):break
                score=image_scores[source]
                if not isinstance(score,dict):break
                visibility=score.get('visibility');presence=score.get('presence')
                def good(v):return type(v) in (int,float) and math.isfinite(v) and .5<=v<=1
                if not good(visibility) or presence is not None and not good(presence):break
            positions.append((x,y))
        if len(positions)==2 and all(contains(region,p) for p in positions):
            result['in_bed_candidate_ids'].append(person['candidate_id'])
    ids=result['in_bed_candidate_ids']
    if len(ids)==1:result.update(status='selected',candidate_id=ids[0])
    else:result['reason']='no_bed_candidate' if not ids else 'multiple_bed_candidates'
    return result


def eligible(candidate_count, assessment, region, *, selection=None, policy=POLICY):
    if region is None or not isinstance(assessment,dict):return False
    if policy==PRIORITY_POLICY:
        return (isinstance(selection,dict) and selection.get('version')==PROFILE['version']
                and selection.get('bed_region_sha256')==object_hash(region)
                and selection.get('status')=='selected' and selection.get('candidate_id')==assessment.get('candidate_id'))
    if policy!=POLICY or type(candidate_count) is not int or candidate_count!=1:return False
    relation=assessment.get('rule_features',{}).get('bed_relation') or {}
    return (bool(assessment.get('candidate_id')) and relation.get('status')=='observed'
            and relation.get('relation') in ('inside','edge'))
