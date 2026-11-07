"""Uncalibrated 2D face/foot diagnostics, never posture labels or score inputs."""
import math

PROFILE = {'version': 'native-pose-cues-0.1', 'joint_definition': 'mediapipe33',
           'minimum_visibility': .5, 'minimum_presence': .5,
           'coordinate_space': 'canonical_pixels', 'use': 'diagnostic_only',
           'validation': 'experimental_not_accuracy_validated'}


def assess(row, width, height):
    selection = row.get('posture_assessment') or {}
    cid = selection.get('candidate_id')
    result = {'version': PROFILE['version'], 'stream_frame_id': row['stream_frame_id'],
              'candidate_id': cid, 'affects_prediction': False,
              'face': {'status': 'unavailable', 'reason': 'target_unavailable'},
              'feet': {side: {'status': 'unavailable', 'reason': 'target_unavailable'}
                       for side in ('left', 'right')}}
    if row.get('processing_status') != 'ok' or (row.get('target_tracking') or {}).get('status') == 'held' or cid is None:
        return result
    candidates = [p for p in row.get('raw_candidates') or [] if p.get('candidate_id') == cid]
    if len(candidates) != 1:
        return result
    points = candidates[0].get('sdk_landmarks', {}).get('image')
    if not isinstance(points, list) or len(points) != 33:
        result['face']['reason'] = 'native_landmarks_unavailable'
        for foot in result['feet'].values(): foot['reason'] = 'native_landmarks_unavailable'
        return result

    def point(i):
        p = points[i]
        if not isinstance(p, dict): return None
        x, y, v, presence = (p.get(k) for k in ('x', 'y', 'visibility', 'presence'))
        if not all(type(a) in (int, float) and math.isfinite(a) for a in (x, y, v, presence)):
            return None
        if not (0 <= x < 1 and 0 <= y < 1 and .5 <= v <= 1 and .5 <= presence <= 1): return None
        return (x * width, y * height)

    def sub(a, b): return (a[0]-b[0], a[1]-b[1])
    def norm(a): return math.hypot(*a)
    def dot(a, b): return sum(x*y for x, y in zip(a, b))
    def mid(a, b): return ((a[0]+b[0])/2, (a[1]+b[1])/2)
    def unavailable(reason): return {'status': 'unavailable', 'reason': reason}

    nose, le, re, ls, rs = [point(i) for i in (0,7,8,11,12)]
    if any(p is None for p in (nose,le,re,ls,rs)):
        result['face'] = unavailable('missing_low_confidence_or_outside_face_shoulders')
    else:
        ear_axis = sub(re, le)
        ear_span, shoulder_span = norm(ear_axis), norm(sub(rs, ls))
        if min(ear_span, shoulder_span) <= 1e-6:
            result['face'] = unavailable('degenerate_face_or_shoulders')
        else:
            result['face'] = {'status': 'available', 'reason': None,
                'nose_lateral_offset_ear_span': dot(sub(nose, mid(le,re)), ear_axis)/(ear_span**2),
                'ear_span_shoulder_span': ear_span/shoulder_span}
    for side, knee, ankle, heel, toe in (('left',25,27,29,31),('right',26,28,30,32)):
        k,a,h,t = [point(i) for i in (knee,ankle,heel,toe)]
        if any(p is None for p in (k,a,h,t)):
            result['feet'][side] = unavailable('missing_low_confidence_or_outside_foot_leg')
            continue
        shin, foot = sub(a,k), sub(t,h)
        sn, fn = norm(shin), norm(foot)
        if min(sn, fn) <= 1e-6:
            result['feet'][side] = unavailable('degenerate_foot_or_leg')
            continue
        result['feet'][side] = {'status': 'available', 'reason': None,
            'foot_shin_angle_degrees': math.degrees(math.acos(max(-1,min(1,dot(shin,foot)/(sn*fn))))),
            'heel_toe_shin_ratio': fn/sn,
            'ankle_heel_shin_ratio': norm(sub(h,a))/sn}
    return result
