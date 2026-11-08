"""Frame-matched visual features for comparison; never posture labels or accuracy."""
import math

PROFILE={'version':'posture-evidence-0.1','policy':'features_only_no_labels_no_fusion',
         'body_frame':'shoulder_midpoint_to_hip_midpoint',
         'world_source':'mediapipe33_monocular_estimate_meters_hip_origin',
         'world_axes':'model_axes_not_registered_to_bed_or_gravity',
         'quality_policy':'numeric_availability_not_correctness',
         'time_policy':'same_processed_frame_no_smoothing'}
BODY=(5,6,11,12)
WORLD_INDICES=(11,12,23,24)
WORLD_NAMES=('left_shoulder','right_shoulder','left_hip','right_hip')


def _number(v):return type(v) in (int,float) and math.isfinite(v)
def _sub(a,b):return tuple(x-y for x,y in zip(a,b))
def _mid(a,b):return tuple(x/2+y/2 for x,y in zip(a,b))
def _dot(a,b):return math.fsum(x*y for x,y in zip(a,b))
def _norm(a):return math.hypot(*a)


def _angle(a,b):
    an,bn=_norm(a),_norm(b)
    if not an or not bn:return None
    c=abs(_dot(tuple(x/an for x in a),tuple(x/bn for x in b)))
    return math.degrees(math.acos(min(1.,max(0.,c))))


def body_geometry(points,dimensions):
    sl,sr,hl,hr=points
    shoulder,hip=_mid(sl,sr),_mid(hl,hr)
    torso=_sub(hip,shoulder);length=_norm(torso)
    if not math.isfinite(length) or length<=0:raise ValueError('degenerate_torso')
    t=tuple(v/length for v in torso)
    ds,dh=_sub(sl,sr),_sub(hl,hr)
    def pair(d):
        axial=_dot(d,t)
        transverse=_sub(d,tuple(axial*x for x in t))
        return {'transverse':_norm(transverse)/length,'axial':abs(axial)/length}
    result={'shoulders':pair(ds),'hips':pair(dh),'line_angle_degrees':_angle(ds,dh)}
    if dimensions==3:
        sn,hn=_norm(ds),_norm(dh)
        normal=(ds[1]*torso[2]-ds[2]*torso[1],ds[2]*torso[0]-ds[0]*torso[2],ds[0]*torso[1]-ds[1]*torso[0])
        nn=_norm(normal)
        if not sn or not hn or not nn or not all(math.isfinite(v) for v in (sn,hn,nn)):
            raise ValueError('degenerate_world_geometry')
        result.update(shoulder_depth_fraction=ds[2]/sn,hip_depth_fraction=dh[2]/hn,
                      torso_normal_world=[v/nn for v in normal])
    def finite(value):
        if isinstance(value,dict):return all(finite(v) for v in value.values())
        if isinstance(value,list):return all(finite(v) for v in value)
        return value is None or _number(value)
    if not finite(result):raise ValueError('nonfinite_features')
    return result


def _held(reason):return {'status':'held','reasons':[reason],'features':None}


def _body_2d(person,width,height):
    joints=person.get('joints',[])
    if len(joints)!=17:return _held('body_joints_missing')
    points=[]
    for i in BODY:
        j=joints[i];x,y=j.get('x_px'),j.get('y_px')
        if (j.get('availability')!='predicted' or not _number(x) or not _number(y)
                or not 0<=x<width or not 0<=y<height):return _held('body_joints_missing')
        points.append((x,y))
    try:features=body_geometry(points,2)
    except (ValueError,OverflowError):return _held('degenerate_body_geometry')
    return {'status':'available','reasons':[],'features':features}


def world_body_evidence(row,person):
    # Match the same raw candidate and the exact adapter joint mapping. A
    # candidate index or COCO index must never be used as a world-landmark index.
    raw=[p for p in row.get('raw_candidates') or [] if isinstance(p,dict) and p.get('candidate_id')==person['candidate_id']]
    if len(raw)!=1:return _held('world_candidate_unavailable')
    candidate=raw[0]
    if candidate.get('world_coordinate_semantics')!='monocular_estimate_meters_hip_origin':
        return _held('world_coordinates_not_provided')
    try:
        from .pose.mediapipe import MP33
        blocks=[b for b in candidate['pose_blocks'] if isinstance(b,dict) and b.get('block_id')=='image']
        if (len(blocks)!=1 or blocks[0].get('joint_definition_id')!='mediapipe33'
                or blocks[0].get('source_joint_names')!=list(MP33)
                or len(person['joints'])!=17
                or tuple(person['joints'][i].get('source_index') for i in BODY)!=WORLD_INDICES):
            return _held('world_mapping_mismatch')
        landmarks=candidate['sdk_landmarks']['world']
        if len(landmarks)!=33:return _held('world_mapping_mismatch')
        points=[tuple(landmarks[i][k] for k in ('x','y','z')) for i in WORLD_INDICES]
        if any(not _number(v) for p in points for v in p):return _held('world_coordinates_invalid')
        features=body_geometry(points,3)
        scores={}
        for space in ('image','world'):
            source=candidate['sdk_landmarks'].get(space,[])
            values=[]
            for i in WORLD_INDICES:
                point=source[i] if isinstance(source,list) and len(source)==33 and isinstance(source[i],dict) else {}
                values.append({key:point.get(key) if _number(point.get(key)) else None for key in ('visibility','presence')})
            scores[space]=values
        return {'status':'available','reasons':[],'features':features,
                'semantics':PROFILE['world_source'],'joint_names':list(WORLD_NAMES),
                'source_indices':list(WORLD_INDICES),'scores':scores}
    except (KeyError,IndexError,TypeError,ValueError,OverflowError):
        return _held('world_coordinates_invalid')


def compare_frame(row,width,height,assessment):
    """Uses only observations and the baseline's selected candidate, never GT/goals."""
    result={'version':PROFILE['version'],'stream_frame_id':row['stream_frame_id'],
            'clock_id':row['clock_id'],'received_ns':row['received_ns'],
            'candidate_id':assessment['candidate_id'],'track_id':assessment.get('track_id'),
            'experimental':True,'quality_status':'not_assessed','identity_verified':False,
            'baseline':{k:assessment[k] for k in ('status','label','reasons','classifier_id')},
            'body_2d':None,'world_3d':None,'combined':None}
    gate=None
    if row['processing_status']!='ok':gate='processing_failed'
    elif row.get('target_tracking',{}).get('status')=='held':gate='target_held'
    elif row.get('target_tracking',{}).get('status')=='tracking_provisional' and assessment['candidate_id']!=row['target_tracking'].get('candidate_id'):
        gate='target_candidate_mismatch'
    elif (assessment['stream_frame_id'],assessment['clock_id'],assessment['received_ns'])!=(row['stream_frame_id'],row['clock_id'],row['received_ns']):
        gate='assessment_frame_mismatch'
    matches=[p for p in row.get('mapped_people') or [] if p.get('candidate_id')==assessment['candidate_id']]
    if not gate and (assessment['candidate_id'] is None or len(matches)!=1):gate='selected_candidate_unavailable'
    if gate:
        result['body_2d']=_held(gate);result['world_3d']=_held(gate)
    else:
        result['body_2d']=_body_2d(matches[0],width,height)
        result['world_3d']=world_body_evidence(row,matches[0])
    reasons=[key+'_unavailable' for key in ('body_2d','world_3d') if result[key]['status']!='available']
    result['combined']={'status':'held' if reasons else 'available','reasons':reasons,
                        'label':None,'classifier_status':'not_configured'}
    decision=assessment.get('rule_features',{}).get('orientation_decision')
    if assessment.get('rule_features',{}).get('information_path')=='independent_body_and_bed_relation':
        result['combined']['classifier_status']='used_for_body_shape_only'
    if decision is not None:
        # The bundle is not a separate classifier: reflect the final rule's use
        # without duplicating its label or changing old record calculations.
        result['combined']['classifier_status']=('used_by_automatic_rules'
            if decision['source'] in ('body_2d_world_3d','bed_registered_model_3d') else 'not_used_by_automatic_rules')
    return result
