"""User-triggered axis references; never training labels or camera recalibration."""
from copy import deepcopy
import numpy as np


def tilt_reference(angles,cameras):
    if (not isinstance(angles,list) or len(angles)!=2 or
            any(type(a) not in (int,float) or not np.isfinite(a) or not 0<=a<90 for a in angles)):
        raise ValueError('수평 기준 하향각 A/B를 0~90도 미만으로 입력하세요.')
    up=[]
    for angle,c in zip(angles,cameras):
        pitch=np.radians(angle)
        camera_up=np.array([0.,-np.cos(pitch),-np.sin(pitch)])
        up.append(np.asarray(c['rotation']).T@camera_up)
    disagreement=float(np.degrees(np.arccos(np.clip(np.dot(up[0],up[1]),-1,1))))
    # A is the primary display reference. Never independently rotate each skeleton.
    return up[0],dict(source='manual_camera_pitch',pitch_degrees=angles,
        roll_assumed_zero=True,primary_camera='A',up_disagreement_degrees=disagreement,
        accuracy_verified=False,used_for_training=False)


def reference(history,kind,now,up=None):
    if kind not in ('standing','lying'):raise ValueError('서 있음 또는 침상 누움 기준을 선택하세요.')
    vectors=[];stamps=[]
    for stamp,pose in history:
        if now-stamp>1.5:continue
        p={j['id']:np.array([j['x'],j['y'],j['z']]) for j in pose['joints'] if j['valid']}
        if not all(i in p for i in (5,6,11,12)):continue
        v=(p[5]+p[6]-p[11]-p[12])/2
        if not np.isfinite(v).all() or np.linalg.norm(v)<.12:continue
        vectors.append(v/np.linalg.norm(v));stamps.append(stamp)
    if len(vectors)<5 or max(stamps)-min(stamps)<.3:raise ValueError('몸통 관절이 안정적으로 보이도록 1~2초 멈춘 뒤 다시 눌러 주세요.')
    axis=np.median(vectors,axis=0)
    if np.linalg.norm(axis)<.5:raise ValueError('몸통 방향이 일정하지 않습니다. 잠깐 멈춰 주세요.')
    axis/=np.linalg.norm(axis)
    if np.percentile(np.degrees(np.arccos(np.clip(np.array(vectors)@axis,-1,1))),80)>15:
        raise ValueError('몸통 방향이 흔들립니다. 같은 자세를 잠깐 유지해 주세요.')
    if kind=='standing':return axis
    if up is None:raise ValueError('서 있음 기준을 먼저 등록하세요.')
    axis=-axis # Shoulder-to-hip direction is the bed's longitudinal direction.
    projected=axis-np.dot(axis,up)*up
    if np.linalg.norm(projected)<.8:raise ValueError('누운 몸통이 서 있음 기준에 비해 충분히 수평이 아닙니다.')
    return projected/np.linalg.norm(projected)


def rotation(up,long=None):
    up=np.asarray(up,float)
    if up.shape!=(3,) or not np.isfinite(up).all() or np.linalg.norm(up)<1e-8:
        raise ValueError('유효한 위쪽 축이 필요합니다.')
    up=up/np.linalg.norm(up)
    if long is None:
        seed=np.array([0.,0.,1.])
        if abs(np.dot(seed,up))>.9:seed=np.array([1.,0.,0.])
        long=seed-np.dot(seed,up)*up
    else:
        long=np.asarray(long,float)
        if long.shape!=(3,) or not np.isfinite(long).all():raise ValueError('유효한 길이 축이 필요합니다.')
        long=long-np.dot(long,up)*up
    if np.linalg.norm(long)<1e-8:raise ValueError('위쪽과 길이 축은 평행할 수 없습니다.')
    long/=np.linalg.norm(long);right=np.cross(up,long);right/=np.linalg.norm(right)
    return np.stack([right,up,long])


def bed_transform(bed,height=.65):
    """Rigid display transform: bed normal up, long axis Z, centre at display height."""
    if type(height) not in (int,float) or not np.isfinite(height) or not .1<=height<=2:
        raise ValueError('침상 윗면 높이는 0.1~2m 범위의 유한 값이어야 합니다.')
    matrix=rotation(bed['normal'],bed['long'])
    offset=np.array([0.,height,0.])-matrix@np.asarray(bed['center'],float)
    return matrix,offset


def pose_in_display(pose,matrix,offset=None):
    if pose is None:return None
    pose=deepcopy(pose)
    for j in pose['joints']:
        if j['valid']:
            v=matrix@np.array([j['x'],j['y'],j['z']])+(np.zeros(3) if offset is None else offset)
            j.update(x=float(v[0]),y=float(v[1]),z=float(v[2]))
    pose['coordinateSystem']='bed_preview' if offset is not None else 'camera_a_preview_aligned'
    return pose


def cameras_in_display(cameras,matrix,offset=None):
    cameras=deepcopy(cameras)
    for c in cameras:
        r=np.asarray(c['rotation'])@matrix.T
        c['rotation']=r.tolist()
        if offset is not None:c['translation']=(np.asarray(c['translation'])-r@offset).tolist()
    return cameras
