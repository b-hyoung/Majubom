"""Screen targets and measured extrinsics; unscaled/unaligned estimates cannot drive room 3D."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import math
import numpy as np
from .calibration import board_points, corners, solve_samples, rigid, validate
from .io_utils import load_json


def pattern_config(value):
    if not isinstance(value, dict):raise ValueError('Pattern settings must be an object')
    kind=value.get('kind','charuco');columns=value.get('columns',9);rows=value.get('rows',6)
    width=value.get('width_m')
    if kind not in ('checkerboard','charuco','aruco') or any(type(v) is not int or not 3<=v<=16 for v in (columns,rows)):
        raise ValueError('Pattern must be checkerboard/charuco/aruco with 3–16 columns and rows')
    if width is not None and (type(width) not in (float,int) or not math.isfinite(width) or not .01<=width<=5):
        raise ValueError('전체 격자 가로 실측값을 미터로 입력하세요 (0.01–5m). 화면 사양/픽셀 수로 추측하지 마세요.')
    return dict(kind=kind,columns=columns,rows=rows,width_m=width)


def target(config):
    import cv2
    c=pattern_config(config);cols,rows=c['columns'],c['rows']
    width=c['width_m'] or 1. # Unitless layout is allowed for display only.
    dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_1000)
    unit=width/(cols-.3 if c['kind']=='aruco' else cols+1)
    if c['kind']=='charuco':board=cv2.aruco.CharucoBoard((cols+1,rows+1),unit,unit*.7,dictionary)
    elif c['kind']=='aruco':board=cv2.aruco.GridBoard((cols,rows),unit*.7,unit*.3,dictionary)
    else:board=None
    return c,board,dictionary,unit


def pattern_png(config):
    import cv2
    c,board,_,_=target(config);cols,rows=c['columns'],c['rows']
    if c['kind']=='checkerboard':
        tile=(np.indices((rows+1,cols+1)).sum(axis=0)%2*255).astype(np.uint8)
        image=np.repeat(np.repeat(tile,100,axis=0),100,axis=1)
    else:
        aspect=(rows-.3)/(cols-.3) if c['kind']=='aruco' else (rows+1)/(cols+1)
        image=board.generateImage((1200,round(1200*aspect)),marginSize=0,borderBits=1)
    ok,encoded=cv2.imencode('.png',image)
    if not ok:raise ValueError('Pattern encoding failed')
    return encoded.tobytes()


def detect(rgb, config):
    import cv2
    c,board,dictionary,unit=target(config)
    if c['width_m'] is None:raise ValueError('패턴의 실제 표시 가로 길이를 먼저 입력하세요.')
    if c['kind']=='checkerboard':
        found=corners(rgb,(c['columns'],c['rows']))
        return board_points(c['columns'],c['rows'],unit),found,np.arange(len(found))
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    if c['kind']=='charuco':
        found,ids,_,_=cv2.aruco.CharucoDetector(board).detectBoard(gray)
        if ids is None or len(ids)<8:raise ValueError('ChArUco 공통 코너가 부족합니다. 화면 반사/밝기/흐림/촬영 각도를 확인하세요.')
        ids=ids.ravel();return board.getChessboardCorners()[ids],found.astype(np.float32),ids
    found,ids,_=cv2.aruco.ArucoDetector(dictionary).detectMarkers(gray)
    if ids is None:raise ValueError('ArUco 마커 미검출 · 패턴 면적/반사/초점을 확인하세요.')
    mapping={int(i):v for i,v in zip(board.getIds(),board.getObjPoints())}
    obj=[];pix=[];keys=[]
    for points,identity in zip(found,ids.ravel()):
        if int(identity) in mapping:
            obj.extend(mapping[int(identity)]);pix.extend(points.reshape(4,2));keys.extend(int(identity)*4+np.arange(4))
    if len(obj)<12:raise ValueError('ArUco 마커가 3개 이상 필요합니다.')
    return np.asarray(obj,np.float32),np.asarray(pix,np.float32).reshape(-1,1,2),np.asarray(keys)


def screen_solve(directory, config, anchor, anchor_world=None):
    import cv2
    config=pattern_config(config)
    if config['width_m'] is None:raise ValueError('화면에 표시된 전체 패턴의 실제 가로 길이가 필요합니다.')
    records=sorted(Path(directory).glob('*/pair.json'))
    objects=[];views=[[],[]];names=[];rejected=[];size=None;bindings=None
    for path in records:
        metadata=load_json(path)
        if metadata.get('pattern')!=config:
            rejected.append(dict(pair_id=path.parent.name,reason='pattern_settings_differ'));continue
        if bindings is not None and metadata['bindings']!=bindings:raise ValueError('카메라 연결이 바뀐 촬영은 섞을 수 없습니다.')
        bindings=metadata['bindings']
        imgs=[cv2.imdecode(np.frombuffer((path.parent/(cid+'.png')).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR) for cid in ('camera_a','camera_b')]
        if any(v is None for v in imgs):raise ValueError('Capture image missing')
        sizes=[tuple(v.shape[1::-1]) for v in imgs]
        if sizes[0]!=sizes[1] or size is not None and sizes[0]!=size:raise ValueError('두 카메라 촬영 해상도를 동일하게 유지하세요.')
        size=sizes[0]
        try:
            found=[detect(cv2.cvtColor(v,cv2.COLOR_BGR2RGB),config) for v in imgs]
            shared=sorted(set(found[0][2].tolist())&set(found[1][2].tolist()))
            if len(shared)<8:raise ValueError('양쪽 공통 코너가 8개 미만입니다.')
            indices=[[int(np.flatnonzero(f[2]==i)[0]) for i in shared] for f in found]
            objects.append(found[0][0][indices[0]].copy())
            for i in range(2):views[i].append(found[i][1][indices[i]].copy())
            names.append(path.parent.name)
        except ValueError as exc:rejected.append(dict(pair_id=path.parent.name,reason=str(exc)))
    if len(objects)<10 or anchor not in names:
        raise ValueError(f'유효한 공통 촬영 10쌍과 기준 쌍이 필요합니다. accepted={names}; rejected={rejected}')
    anchor_obj=objects[names.index(anchor)]
    world=None
    if anchor_world is not None:
        if anchor_world.get('measured') is not True:raise ValueError('방 변환은 실제 위치/방향을 측정한 경우에만 measured=true로 입력하세요.')
        r,t=rigid(anchor_world['rotation'],anchor_world['translation'])
        world=(anchor_obj@r.T+t).astype(np.float32)
    else:
        # A screen held upright is NOT a floor anchor. Use its own frame until explicitly aligned.
        world=anchor_obj.copy()
    result=solve_samples(objects,views[0],views[1],size,names.index(anchor),world)
    result.update(method='screen_'+config['kind'],pattern=config,
                  coordinate_system='room_world' if anchor_world is not None else 'target_world',
                  room_alignment_verified=anchor_world is not None,
                  capture=dict(accepted_pairs=names,rejected_pairs=rejected,anchor_pair=anchor,bindings=bindings))
    return result


def camera_intrinsics(value):
    k=np.asarray(value['intrinsic_matrix'],float);d=np.asarray(value['distortion_coefficients'],float)
    size=value['image_resolution']
    if (k.shape!=(3,3) or not np.isfinite(k).all() or k[0,0]<=0 or k[1,1]<=0 or not np.allclose(k[2],[0,0,1])
            or d.ndim!=1 or len(d) not in (4,5,8,12,14) or not np.isfinite(d).all()
            or not isinstance(size,list) or len(size)!=2 or any(type(v) is not int or v<=0 for v in size)):
        raise ValueError('유효한 내부 행렬/왜곡 계수/촬영 해상도가 필요합니다.')
    return k,d,size


def measured_points_solve(payload):
    import cv2
    if len(payload['cameras'])!=2:raise ValueError('Two cameras required')
    cameras=[];errors=[];diagnostics=[]
    for index,entry in enumerate(payload['cameras']):
        k,d,size=camera_intrinsics(entry)
        world=np.asarray(entry['world_points_m'],np.float64);pixels=np.asarray(entry['image_points_px'],np.float64)
        if (world.ndim!=2 or world.shape[1]!=3 or len(world)<6 or pixels.shape!=(len(world),2)
                or not np.isfinite(world).all() or not np.isfinite(pixels).all()
                or np.linalg.matrix_rank(world-world.mean(axis=0))<2):
            raise ValueError('각 카메라에서 실측 기준점 6개 이상과 대응 픽셀이 필요합니다. 일직선 기준점은 사용할 수 없습니다.')
        ok,rv,t,inliers=cv2.solvePnPRansac(world,pixels,k,d,reprojectionError=3.,iterationsCount=200)
        if not ok or inliers is None or len(inliers)<6:raise ValueError('PnP 외부 파라미터 계산 실패 · 기준점 순서/픽셀/실측 단위를 확인하세요.')
        selected=inliers.ravel();rv,t=cv2.solvePnPRefineLM(world[selected],pixels[selected],k,d,rv,t)
        r=cv2.Rodrigues(rv)[0]
        if np.any((world@r.T+t.ravel())[:,2]<=0):raise ValueError('기준점이 카메라 뒤쪽에 복원되었습니다.')
        projected=cv2.projectPoints(world,rv,t,k,d)[0].reshape(-1,2)
        residual=np.linalg.norm(projected-pixels,axis=1);rms=float(np.sqrt(np.mean(residual**2)))
        errors.append(rms)
        center=(-r.T@t).ravel();position_error=None
        if entry.get('measured_camera_position_m') is not None:
            measured=np.asarray(entry['measured_camera_position_m'],float)
            if measured.shape!=(3,) or not np.isfinite(measured).all():raise ValueError('Invalid measured camera position')
            position_error=float(np.linalg.norm(center-measured))
            if position_error>payload.get('max_camera_position_error_m',.2):raise ValueError('추정 카메라 위치가 실측 위치와 맞지 않습니다. 기준점/내부값을 확인하세요.')
        cameras.append(dict(camera_id=['camera_a','camera_b'][index],intrinsic_matrix=k.tolist(),
            distortion_coefficients=d.tolist(),image_resolution=size,rotation=r.tolist(),translation=t.ravel().tolist(),reprojection_error=rms))
        diagnostics.append(dict(inliers=len(inliers),total=len(world),residual_px=residual.tolist(),camera_position_error_m=position_error))
    result=dict(version='room-stereo-1',unit='m',coordinate_system='room_world',method='measured_points_pnp',
        calibration_timestamp=datetime.now(timezone.utc).isoformat(),calibration_validity='measured',
        stereo_reprojection_error=max(errors),anchor_reprojection_error=max(errors),quality=dict(max_reprojection_error_px=3.,min_ray_angle_degrees=2.,min_visibility=.5),
        cameras=cameras,diagnostics=diagnostics,room_alignment_verified=True)
    if max(errors)>3:raise ValueError(f'전체 기준점 재투영 오차가 3px를 초과했습니다: {errors}')
    return validate(result)


def natural_pose(images, intrinsics):
    import cv2
    known=[camera_intrinsics(v) for v in intrinsics]
    detector=cv2.SIFT_create(nfeatures=4000)
    features=[detector.detectAndCompute(cv2.cvtColor(v,cv2.COLOR_RGB2GRAY),None) for v in images]
    if any(f[1] is None for f in features):raise ValueError('자연 특징점이 부족합니다. 질감 있는 정적인 공통 장면이 필요합니다.')
    matches=cv2.BFMatcher().knnMatch(features[0][1],features[1][1],k=2)
    good=[m for pair in matches if len(pair)==2 for m,n in [pair] if m.distance<.7*n.distance]
    if len(good)<20:raise ValueError('신뢰할 특징점 대응이 20개 미만입니다.')
    points=[np.array([features[i][0][m.queryIdx if i==0 else m.trainIdx].pt for m in good]) for i in range(2)]
    norm=[cv2.undistortPoints(p.reshape(-1,1,2),k,d).reshape(-1,2) for p,(k,d,_) in zip(points,known)]
    e,mask=cv2.findEssentialMat(norm[0],norm[1],np.eye(3),method=cv2.RANSAC,prob=.999,threshold=.0015)
    if e is None or e.shape!=(3,3):raise ValueError('Essential matrix 추정 실패 · 시차/평면 퇴화/대응점을 확인하세요.')
    count,r,t,mask=cv2.recoverPose(e,norm[0],norm[1],np.eye(3),mask=mask)
    h,hmask=cv2.findHomography(points[0],points[1],cv2.RANSAC,3.)
    planar=float(np.mean(hmask)) if hmask is not None else 0.
    if count<20 or count/len(good)<.5 or planar>.85:
        raise ValueError('자연 특징 상대 자세가 불안정합니다: 낮은 시차/평면 장면/이상치가 많습니다.')
    x=np.column_stack((norm[0],np.ones(len(good))));y=np.column_stack((norm[1],np.ones(len(good))))
    ex=x@e.T;ety=y@e;denom=np.sum(ex[:,:2]**2,axis=1)+np.sum(ety[:,:2]**2,axis=1)
    residual=np.sum(y*ex,axis=1)**2/np.maximum(denom,1e-15)
    sampson=float(np.median(residual[mask.ravel()!=0]))
    return dict(method='natural_features',calibration_validity='relative_pose_unscaled',coordinate_system='camera_a',
        unit=None,room_alignment_verified=False,matched_features=len(good),inliers=int(count),
        homography_inlier_fraction=planar,rotation_camera_a_to_b=r.tolist(),translation_direction=t.ravel().tolist(),
        epipolar_sampson_median_normalized=sampson,
        reason='이동 방향만 추정했습니다. 실제 기준선 길이와 방 좌표 정렬이 없어 미터 단위 3D 복원에 사용하지 않습니다.')
