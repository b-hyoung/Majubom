"""Unverified live two-camera preview; never installs metric room calibration."""
from copy import deepcopy
import numpy as np
from .calibration import rigid


def prepare(candidate):
    cameras = candidate.get('cameras', [])
    if len(cameras) != 2:
        raise ValueError('임시 표시에 사용할 두 카메라 보정 후보가 필요합니다.')
    for c in cameras:
        k = np.asarray(c['intrinsic_matrix'], float)
        d = np.asarray(c['distortion_coefficients'], float)
        size = c['image_resolution']
        rigid(c['rotation'], c['translation'])
        if (k.shape != (3, 3) or not np.isfinite(k).all() or min(k[0,0], k[1,1]) <= 0
                or not np.allclose(k[2], [0,0,1]) or d.ndim != 1 or len(d) not in (4,5,8,12,14)
                or not np.isfinite(d).all() or len(size) != 2
                or any(type(v) is not int or v <= 0 for v in size)):
            raise ValueError('임시 보정 후보의 카메라 행렬/해상도가 올바르지 않습니다.')
    ca, cb = cameras
    ra, ta = np.asarray(ca['rotation']), np.asarray(ca['translation'])
    rb, tb = np.asarray(cb['rotation']), np.asarray(cb['translation'])
    if np.linalg.norm(-ra.T @ ta + rb.T @ tb) < 1e-6:
        raise ValueError('카메라 간 거리가 0인 후보는 표시할 수 없습니다.')
    # D maps OpenCV camera A coordinates to the Three.js viewing frame.
    d = np.diag([1., -1., -1.])
    display = []
    for c in cameras:
        r, t = np.asarray(c['rotation']), np.asarray(c['translation'])
        relative = r @ ra.T
        display.append(dict(deepcopy(c), rotation=(relative @ d).tolist(),
                            translation=(t-relative @ ta).tolist()))
    return dict(cameras=deepcopy(cameras),
                quality=dict(max_reprojection_error_px=20., min_ray_angle_degrees=2., min_visibility=.5),
                display_cameras=display)


def reconstruct_preview(pair, candidate):
    from .room_monitor import reconstruct
    pose = reconstruct(pair, candidate)
    a = candidate['cameras'][0]
    r, t = np.asarray(a['rotation']), np.asarray(a['translation'])
    for joint in pose['joints']:
        if joint['valid']:
            p = (r @ np.array([joint['x'], joint['y'], joint['z']]) + t) * [1,-1,-1]
            if np.isfinite(p).all() and np.linalg.norm(p) <= 20:
                joint.update(x=float(p[0]), y=float(p[1]), z=float(p[2]))
            else:
                joint.update(valid=False, x=None, y=None, z=None, reason='preview_range_exceeded')
    pose.update(personId='unverified-live-stereo', coordinateSystem='camera_a_preview',
                unit='unverified_candidate_units', accuracy_verified=False,
                source='live_two_camera_triangulation', eligible_for_recording=False)
    return pose


def monocular_preview(packet,candidate,slot):
    """Register SDK-estimated body geometry to this camera's observed pixels."""
    import cv2
    world=packet.get('world_joints')
    if world is None or packet.get('people_count')!=1 or not packet.get('processing_ok'):
        return None
    if not isinstance(world,(list,tuple)) or len(world)!=17 or len(packet.get('joints',[]))!=17:return None
    indices=[i for i,j in enumerate(packet['joints']) if j is not None and world[i] is not None]
    if len(indices)<6 or not all(i in indices for i in (5,6,11,12)):return None
    c=candidate['cameras'][slot];k=np.asarray(c['intrinsic_matrix'],float);d=np.asarray(c['distortion_coefficients'],float)
    obj=np.asarray([world[i] for i in indices],float);pixels=np.asarray([packet['joints'][i][:2] for i in indices],float)
    if not np.isfinite(obj).all() or np.linalg.norm(obj[indices.index(5)]-obj[indices.index(11)])<.08:return None
    try:
        ok,rv,tv=cv2.solvePnP(obj,pixels,k,d,flags=cv2.SOLVEPNP_SQPNP)
        if not ok:return None
        rv,tv=cv2.solvePnPRefineLM(obj,pixels,k,d,rv,tv)
        projected=cv2.projectPoints(obj,rv,tv,k,d)[0].reshape(-1,2)
        errors=np.linalg.norm(projected-pixels,axis=1)
        if np.median(errors)>15 or np.percentile(errors,90)>35:return None
        camera_points=obj@cv2.Rodrigues(rv)[0].T+tv.reshape(3)
        if np.any(camera_points[:,2]<=.1):return None
        r,t=np.asarray(c['rotation']),np.asarray(c['translation'])
        a=candidate['cameras'][0];ra,ta=np.asarray(a['rotation']),np.asarray(a['translation'])
        xyz=(((camera_points-t)@r)@ra.T+ta)*[1,-1,-1]
        joints=[dict(id=i,valid=False,x=None,y=None,z=None,confidence=None,reason='joint_not_observed') for i in range(17)]
        for j,i in enumerate(indices):
            if errors[j]<=35 and np.linalg.norm(xyz[j])<=20:
                joints[i].update(valid=True,x=float(xyz[j,0]),y=float(xyz[j,1]),z=float(xyz[j,2]),
                    confidence=packet['joints'][i][2],reason=None,reprojection_error_px=float(errors[j]),
                    reconstruction='mediapipe_world_pnp')
        if sum(j['valid'] for j in joints)<6:return None
        return dict(personId='unverified-live-target',timestampMs=__import__('time').time_ns()//1000000,
            coordinateSystem='camera_a_preview',unit='unverified_candidate_units',jointDefinition='coco17',
            joints=joints,source='single_camera_mediapipe_pnp',source_camera='AB'[slot],
            source_frame_ids=[packet['frame_id']],accuracy_verified=False,eligible_for_recording=False,
            identity_verified=False,exposure_synchronized=False,depth_is_model_estimate=True)
    except (cv2.error,ValueError,np.linalg.LinAlgError):return None


def select_live_pose(stereo,monocular):
    def count(p):return sum(j['valid'] for j in p['joints']) if p else 0
    def core(p):return bool(p) and all(any(j['id']==i and j['valid'] for j in p['joints']) for i in (5,6,11,12))
    def score(p):
        if not p:return 0
        return count(p)+sum(j['valid'] and j['id'] in (13,14,15,16) for j in p['joints'])
    best=max((p for p in monocular if p is not None),key=score,default=None)
    if core(stereo) and count(stereo)>=8 and score(stereo)>=.9*score(best):
        stereo['personId']='unverified-live-target';return stereo
    if best is not None:return deepcopy(best)
    if count(stereo)>0:
        stereo['personId']='unverified-live-target';return stereo
    return None


def sanitize_pose(pose):
    """Reject impossible coordinates/limb spikes; never synthesize hidden joints."""
    if pose is None:return None
    pose=deepcopy(pose);joints=pose.get('joints')
    if not isinstance(joints,list) or len(joints)!=17:return None
    seen=set()
    for j in joints:
        i=j.get('id')
        if type(i) is not int or i in seen or not 0<=i<17:return None
        seen.add(i)
        if j.get('valid'):
            values=[j.get(k) for k in ('x','y','z','confidence')]
            if (any(type(v) not in (float,int) or not np.isfinite(v) for v in values)
                    or not .5<=values[3]<=1 or np.linalg.norm(values[:3])>20):
                j.update(valid=False,x=None,y=None,z=None,confidence=None,reason='invalid_observation')
    indexed={j['id']:j for j in joints}
    def point(i):return np.array([indexed[i][k] for k in ('x','y','z')])
    if all(indexed[i]['valid'] for i in (5,6,11,12)):
        size=np.linalg.norm((point(5)+point(6)-point(11)-point(12))/2)
        if not .08<size<2:return None
        # Broad ratios exclude spikes without imposing a fixed body size.
        for parent,child,maximum in [(5,7,1.4),(7,9,1.4),(6,8,1.4),(8,10,1.4),
                                      (11,13,1.8),(13,15,1.8),(12,14,1.8),(14,16,1.8)]:
            if not indexed[parent]['valid']:
                indexed[child].update(valid=False,x=None,y=None,z=None,confidence=None,reason='parent_joint_unavailable')
            elif indexed[child]['valid']:
                distance=np.linalg.norm(point(child)-point(parent))
                if not size*.06<distance<size*maximum:
                    indexed[child].update(valid=False,x=None,y=None,z=None,confidence=None,reason='implausible_limb_geometry')
    pose['valid_joint_count']=sum(j['valid'] for j in joints)
    return pose if pose['valid_joint_count'] else None
