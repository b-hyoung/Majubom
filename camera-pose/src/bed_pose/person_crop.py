"""Current-frame detector fallback; no previous box reuse or person re-identification."""
import math
import time
from .image_quality import inspect_crop

VERSION='person-crop-observation-0.1'


def quality(row,candidate,width,height,boxes,profile):
    points,original=inspect_crop(row,candidate,width,height)
    q=dict(original,version='person-crop-quality-0.1',source='person_detection',bounds=None,
           status='held',reasons=[],joint_reasons=list(original['reasons']))
    tracking=row.get('target_tracking',{})
    if tracking.get('status')!='tracking_provisional' or tracking.get('candidate_id')!=candidate:
        q['reasons']=['person_crop_target_not_tracked'];return points,q
    if not any(points[i] is not None for i in (5,6)) or not any(points[i] is not None for i in (11,12)):
        q['reasons']=['person_crop_torso_anchors_missing'];return points,q
    anchors=[points[i] for i in (5,6,11,12) if points[i] is not None]
    if len(set(anchors))<2:q['reasons']=['person_crop_torso_anchors_missing'];return points,q
    def inside(b,p):return b[0]<=p[0]<=b[2] and b[1]<=p[1]<=b[3]
    valid=[]
    for item in boxes:
        if (not isinstance(item,dict) or set(item)!={'bounds','score'} or type(item['score']) not in (int,float)
                or not math.isfinite(item['score']) or not profile['score_min']<=item['score']<=1
                or not isinstance(item['bounds'],list) or len(item['bounds'])!=4
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in item['bounds'])):
            raise ValueError('Invalid recorded detector box')
        b=item['bounds']
        if not (0<=b[0]<b[2]<=width and 0<=b[1]<b[3]<=height):continue
        if all(inside(b,p) for p in anchors):valid.append(b)
    if len(valid)!=1:q['reasons']=['person_crop_target_ambiguous' if valid else 'person_crop_no_matching_box'];return points,q
    b=valid[0];margin=profile['crop_policy']['boundary_margin_px'];minimum=profile['crop_policy']['min_box_side_px']
    if b[0]<=margin or b[1]<=margin or b[2]>=width-margin or b[3]>=height-margin:q['reasons']=['person_crop_boundary_contact'];return points,q
    if b[2]-b[0]<minimum or b[3]-b[1]<minimum:q['reasons']=['person_crop_too_small'];return points,q
    # Reject overlap with other detections, or another pose inside this box.
    for item in boxes:
        other=item['bounds']
        if other is b:continue
        if min(b[2],other[2])>max(b[0],other[0]) and min(b[3],other[3])>max(b[1],other[1]):
            q['reasons']=['person_crop_multiple_people'];return points,q
    for person in row.get('mapped_people') or []:
        if person['candidate_id']==candidate:continue
        for j in person['joints']:
            x,y=j.get('x_px'),j.get('y_px')
            if j.get('availability')=='predicted' and type(x) in (int,float) and type(y) in (int,float) and math.isfinite(x) and math.isfinite(y) and inside(b,(x,y)):
                q['reasons']=['person_crop_multiple_people'];return points,q
    q.update(status='usable',bounds=[math.floor(b[0]),math.floor(b[1]),math.ceil(b[2]),math.ceil(b[3])])
    return points,q


def observe(row,rgb,selection,detector):
    h,w=rgb.shape[:2];candidate=selection['candidate_id']
    _,joint=inspect_crop(row,candidate,w,h)
    result={'version':VERSION,'stream_frame_id':row['stream_frame_id'],'clock_id':row['clock_id'],
            'received_ns':row['received_ns'],'candidate_id':candidate,'status':'not_needed',
            'boxes':[],'quality':None,'error':None,'elapsed_ms':0.}
    if joint['status']=='usable':return result
    result['status']='held'
    # Never spend a detector pass when the selected current target is missing.
    points,_=inspect_crop(row,candidate,w,h)
    eligible=(row['processing_status']=='ok' and row.get('target_tracking',{}).get('status')=='tracking_provisional'
              and row.get('target_tracking',{}).get('candidate_id')==candidate
              and any(points[i] is not None for i in (5,6)) and any(points[i] is not None for i in (11,12)))
    if eligible:
        start=time.perf_counter_ns()
        try:
            result['boxes']=detector.detect(rgb)
            quality(row,candidate,w,h,result['boxes'],detector.profile)
        except Exception as exc:
            result['boxes']=[]
            result['error']={'code':'PERSON_DETECTOR_ERROR','exception_type':type(exc).__name__}
        result['elapsed_ms']=(time.perf_counter_ns()-start)/1e6
    _,result['quality']=quality(row,candidate,w,h,result['boxes'],detector.profile)
    if result['error']:result['quality']['reasons']=['person_crop_detector_failed']
    result['status']=result['quality']['status']
    return result


def validate(value,row,width,height,profile):
    from .person_detector import validate_profile
    validate_profile(profile)
    from .posture import assess_frame
    selection=assess_frame(row,width,height,None);candidate=selection['candidate_id']
    if (not isinstance(value,dict) or set(value)!={'version','stream_frame_id','clock_id','received_ns','candidate_id','status','boxes','quality','error','elapsed_ms'}
            or value['version']!=VERSION or any(value[k]!=row[k] or type(value[k]) is not type(row[k]) for k in ('stream_frame_id','clock_id','received_ns'))
            or value['candidate_id']!=candidate or not isinstance(value['boxes'],list)
            or type(value['elapsed_ms']) not in (int,float) or not math.isfinite(value['elapsed_ms']) or value['elapsed_ms']<0):raise ValueError('Invalid person crop provenance')
    _,joint=inspect_crop(row,candidate,width,height)
    if joint['status']=='usable':
        if value['status']!='not_needed' or value['boxes'] or value['quality'] is not None or value['error'] is not None or value['elapsed_ms']!=0:raise ValueError('Unexpected detector invocation')
        return
    points,_=inspect_crop(row,candidate,width,height)
    eligible=(row['processing_status']=='ok' and row.get('target_tracking',{}).get('status')=='tracking_provisional'
              and row.get('target_tracking',{}).get('candidate_id')==candidate
              and any(points[i] is not None for i in (5,6)) and any(points[i] is not None for i in (11,12)))
    if not eligible and (value['boxes'] or value['error'] is not None or value['elapsed_ms']!=0):raise ValueError('Detector evidence without current tracked anchors')
    _,q=quality(row,candidate,width,height,value['boxes'],profile)
    if value['error'] is not None:
        e=value['error']
        if (not isinstance(e,dict) or set(e)!={'code','exception_type'} or e['code']!='PERSON_DETECTOR_ERROR'
                or not isinstance(e['exception_type'],str) or not e['exception_type'].isidentifier() or value['boxes']):raise ValueError('Invalid detector error')
        q['reasons']=['person_crop_detector_failed']
    if value['quality']!=q or value['status']!=q['status']:raise ValueError('Person crop differs from recorded detections')
