"""Experimental six-posture geometry. No ToF input, learned probability or labels."""
from copy import deepcopy
import math
import numpy as np

LABELS={'supine':'바로 누움','prone':'엎드림','left_side':'왼쪽 누움',
        'right_side':'오른쪽 누움','sitting':'침대 위 앉음','edge_sitting':'걸터앉음'}
METHOD='bed-3d-six-0.2'

def cross2(a,b):return float(a[0]*b[1]-a[1]*b[0])


def bed_frame(corners, coordinate_system, verified=False, source='image_corners'):
    p=np.asarray(corners,float)
    if p.shape!=(4,3) or not np.isfinite(p).all():raise ValueError('침대 모서리 4개의 유한 3D 좌표가 필요합니다.')
    center=p.mean(0);_,_,vh=np.linalg.svd(p-center)
    normal=vh[-1]
    if normal[1]<0:normal=-normal
    long=(p[2]+p[3]-p[0]-p[1])/2;long-=normal*np.dot(long,normal)
    length=np.linalg.norm(long)
    if length<.1:raise ValueError('침대 길이가 너무 작습니다.')
    long/=length;wide=np.cross(normal,long);wide/=np.linalg.norm(wide)
    local=np.column_stack(((p-center)@wide,(p-center)@long))
    width=float(np.ptp(local[:,0]));height_error=float(np.max(np.abs((p-center)@normal)))
    if width<.1 or height_error>min(width,length)*.12:raise ValueError('침대 모서리의 평면/대응 관계를 확인하세요.')
    cross=[cross2(local[(i+1)%4]-local[i],local[(i+2)%4]-local[(i+1)%4]) for i in range(4)]
    if not(all(x>1e-6 for x in cross) or all(x<-1e-6 for x in cross)):raise ValueError('침대 모서리를 같은 순서로 지정하세요.')
    return dict(corners=p.tolist(),center=center.tolist(),normal=normal.tolist(),
                wide=wide.tolist(),long=long.tolist(),polygon=local.tolist(),
                width=width,length=float(length),plane_fit_error=height_error,
                coordinate_system=coordinate_system,verified=bool(verified),source=source)


def room_bed(config):
    b=config['bed'];w,h,d=b['size'];x,y,z=b['position'];a=math.radians(b['yaw_degrees'])
    points=[]
    for px,pz in [(-w/2,-d/2),(w/2,-d/2),(w/2,d/2),(-w/2,d/2)]:
        points.append([x+px*math.cos(a)+pz*math.sin(a),y+h/2,z-px*math.sin(a)+pz*math.cos(a)])
    return bed_frame(points,'room_world',config['dimensions_source']=='measured','configured_room_bed')


def annotate_preview_bed(values,candidate):
    from .experimental_preview import reconstruct_preview
    points=values.get('points');resolutions=values.get('resolutions')
    if (not isinstance(points,list) or len(points)!=2 or not isinstance(resolutions,list)
            or len(resolutions)!=2):raise ValueError('양쪽 영상의 같은 침대 모서리 네 점을 지정하세요.')
    pair=[]
    for index,(view,camera) in enumerate(zip(points,candidate['cameras'])):
        p=np.asarray(view,float);w,h=camera['image_resolution']
        if (resolutions is None or resolutions[index]!=[w,h] or p.shape!=(4,2) or not np.isfinite(p).all()
                or np.any(p[:,0]<0) or np.any(p[:,0]>w) or np.any(p[:,1]<0) or np.any(p[:,1]>h)):
            raise ValueError('모서리 좌표와 보정 해상도를 확인하세요.')
        pair.append(dict(joints=[[*v,1.] for v in p]+[None]*13))
    pose=reconstruct_preview(pair,candidate);joints=pose['joints'][:4]
    if not all(j['valid'] for j in joints):raise ValueError('모서리 삼각측량 실패 · 양쪽에서 같은 실제 모서리인지 확인하세요.')
    return bed_frame([[j['x'],j['y'],j['z']] for j in joints],'camera_a_preview',False,'manual_two_view_corners')


def classify(pose,bed):
    result=dict(method=METHOD,status='held',label=None,label_ko=None,reason=None,
                quality_score=None,warnings=[],evidence_level='full',required_stable_seconds=.6,score_is_probability=False,accuracy_verified=False,
                eligible_for_recording=False,features={},labels=LABELS)
    def held(reason):result['reason']=reason;return result
    if pose is None:return held('skeleton_unavailable')
    result['timestamp_ms']=pose.get('timestampMs');result['source']=pose.get('source','calibrated_stereo')
    if bed is None:return held('bed_registration_required')
    if pose.get('coordinateSystem')!=bed['coordinate_system']:return held('coordinate_system_mismatch')
    if pose.get('unit') not in ('m','unverified_candidate_units'):return held('coordinate_system_mismatch')
    if pose.get('jointDefinition','coco17')!='coco17':return held('joint_definition_mismatch')
    if not isinstance(pose.get('joints'),list):return held('malformed_skeleton')
    joints={};scores={}
    for j in pose.get('joints',[]):
        if not isinstance(j,dict) or type(j.get('id')) is not int or not 0<=j['id']<17:continue
        if j['id'] in joints:return held('duplicate_joint_id')
        if j.get('valid') and type(j.get('confidence')) in (int,float) and math.isfinite(j['confidence']) and .5<=j['confidence']<=1:
            p=[j.get(k) for k in ('x','y','z')]
            if all(type(v) in (int,float) and math.isfinite(v) for v in p):
                joints[j['id']]=np.array(p);scores[j['id']]=j['confidence']
    if not all(i in joints for i in (5,6,11,12)):return held('torso_joints_missing')
    p=joints;s=(p[5]+p[6])/2;hip=(p[11]+p[12])/2;torso=s-hip;length=np.linalg.norm(torso)
    if length<.08 or length>2:return held('degenerate_torso')
    def reduced(reason):
        result['warnings'].append(reason);result['evidence_level']='partial';result['required_stable_seconds']=1.2
    shoulder=p[6]-p[5];hips=p[12]-p[11]
    if min(np.linalg.norm(shoulder),np.linalg.norm(hips))<length*.1:return held('degenerate_body_width')
    if abs(np.dot(shoulder/np.linalg.norm(shoulder),torso/length))>.75:return held('body_axes_inconsistent')
    if np.dot(shoulder,hips)/(np.linalg.norm(shoulder)*np.linalg.norm(hips))<.3:return held('body_axes_inconsistent')
    n=np.array(bed['normal']);center=np.array(bed['center']);wide=np.array(bed['wide']);long=np.array(bed['long'])
    def local(v):return np.array([np.dot(v-center,wide),np.dot(v-center,long)])
    polygon=np.array(bed['polygon']);sign=1 if cross2(polygon[1]-polygon[0],polygon[2]-polygon[1])>0 else -1
    def relation(v):
        q=local(v);dist=[]
        for a,b in zip(polygon,np.roll(polygon,-1,axis=0)):
            edge=b-a;dist.append(sign*cross2(edge,q-a)/np.linalg.norm(edge))
        return min(dist)
    hip_edge=relation(hip);hip_height=float(np.dot(hip-center,n));upright=float(np.dot(torso/length,n))
    result['features']=dict(torso_length=float(length),upright_cos=upright,hip_height_torso=hip_height/length,
                            hip_edge_torso=float(hip_edge/length),bed_source=bed['source'],bed_verified=bed['verified'])
    if hip_edge < -length*.2 or abs(hip_height)>length*.85:return held('body_outside_bed')
    label=None
    if upright>=.65:
        knees=[(k,h) for k,h in ((13,11),(14,12)) if k in p]
        if not knees:return held('knees_required_for_sitting')
        if len(knees)==1:reduced('one_knee_occluded')
        thighs=[p[k]-p[h] for k,h in knees]
        if min(np.linalg.norm(v) for v in thighs)<length*.2:return held('degenerate_legs')
        if min(abs(np.dot(v/np.linalg.norm(v),n)) for v in thighs)>.8:return held('standing_or_transition')
        feet=[i for i in (15,16) if i in p]
        near_edge=hip_edge<length*.45
        below_outside=any(relation(p[i])<0 and np.dot(p[i]-center,n)<-length*.25 for i in feet)
        if near_edge:
            if not feet:return held('ankles_required_at_edge')
            if not below_outside:return held('edge_sitting_ambiguous')
            label='edge_sitting'
        else:label='sitting'
    elif abs(upright)<=.4:
        if min(relation(p[i]) for i in (5,6,11,12)) < -length*.2:return held('body_outside_bed')
        knees=[i for i in (13,14) if i in p]
        if knees:
            if len(knees)==1:reduced('one_knee_occluded')
            if any(abs(np.dot((p[i]-hip),n))>length*.75 for i in knees):return held('lying_or_sitting_ambiguous')
        else:
            if abs(upright)>.2 or abs(hip_height)>length*.5 or hip_edge<length*.25:
                return held('knees_required_for_lying')
            reduced('both_knees_occluded_strong_torso_only')
        anterior=np.cross(shoulder,hip-s);anterior/=np.linalg.norm(anterior)
        face_cos=float(np.dot(anterior,n));side_cos=float(np.dot(shoulder/np.linalg.norm(shoulder),n))
        result['features'].update(anterior_up_cos=face_cos,right_minus_left_up_cos=side_cos)
        if not knees and max(abs(face_cos),abs(side_cos))<.85:return held('lying_orientation_ambiguous')
        if abs(face_cos)>=.65:
            face_ids=[i for i in (0,1,2) if i in p]
            if not face_ids:return held('face_required_for_front_back')
            if 0 not in p:reduced('nose_occluded_using_eyes')
            face=p[0] if 0 in p else np.mean([p[i] for i in face_ids],axis=0)
            nose_height=float(np.dot(face-s,n))/length
            result['features']['nose_height_torso']=nose_height
            if face_cos*nose_height<.015:return held('face_and_torso_disagree')
            label='supine' if face_cos>0 else 'prone'
        elif abs(side_cos)>=.65:label='left_side' if side_cos>0 else 'right_side'
        else:return held('lying_orientation_ambiguous')
    else:return held('posture_transition_or_ambiguous')
    needed=[5,6,11,12]+[i for i in (13,14) if i in p]
    confidences=[scores[i] for i in needed]
    if pose.get('depth_is_model_estimate'):result['warnings'].append('monocular_depth_estimate')
    result.update(status='candidate',label=label,label_ko=LABELS[label],reason=None,
                  quality_score=float(min(confidences)))
    return result


class PostureFilter:
    """Causal same-candidate dwell, no predicted labels over missing observations."""
    def __init__(self):self.reset()
    def reset(self):self.label=None;self.start=None;self.last=None;self.count=0
    def update(self,result,now):
        result=deepcopy(result)
        if result['status']=='held':self.reset();return result
        if result['label']!=self.label or self.last is None or now-self.last>.5:
            self.label=result['label'];self.start=now;self.count=0
        self.count+=1;self.last=now;result['stable_seconds']=now-self.start
        if now-self.start>=result.get('required_stable_seconds',.6) and self.count>=3:result['status']='stable'
        return result


def check_view_agreement(decision,evidence):
    """A consistency check of correlated observations, not a vote or probability."""
    decision=deepcopy(decision);decision['view_evidence']=deepcopy(evidence)
    if decision['label'] is None:return decision
    conflicting=[e for e in evidence if e['label'] is not None and e['label']!=decision['label']]
    if conflicting:
        decision['warnings'].append('cross_view_disagreement')
        decision['required_stable_seconds']=max(1.2,decision['required_stable_seconds'])
        if any(e['quality_score']>=.65 for e in conflicting):
            decision.update(status='held',label=None,label_ko=None,reason='conflicting_pose_evidence')
    return decision
