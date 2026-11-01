"""Static-background stereo estimation; no guessed intrinsics, scale, or room axes."""
from datetime import datetime, timezone
import math
import numpy as np
from .calibration_methods import camera_intrinsics
from .calibration import rigid
from .io_utils import object_hash

POLICY = dict(version='static-background-quality-1', min_matches=40, min_inliers=30,
              min_inlier_ratio=.55, max_homography_ratio=.85, min_coverage=.08,
              min_ray_angle_degrees=1.5, max_reprojection_rms_px=2.,
              max_reprojection_p95_px=3., max_rotation_spread_degrees=2.,
              max_translation_spread_degrees=5.)


class CalibrationFailure(ValueError):
    def __init__(self, reason, metrics=None):
        super().__init__(reason)
        self.metrics = metrics or {}


def validate_inputs(value):
    if not isinstance(value, dict):raise ValueError('자동 보정 입력은 JSON 객체여야 합니다.')
    result = dict(detector=value.get('detector', 'SIFT'), baseline_m=value.get('baseline_m'),
                  cameras=value.get('cameras'), room_transform=value.get('room_transform'))
    if result['detector'] not in ('SIFT', 'ORB'):raise ValueError('SIFT 또는 ORB를 선택하세요.')
    baseline = result['baseline_m']
    if baseline is not None and (type(baseline) not in (float,int) or not math.isfinite(baseline) or not .01<=baseline<=100):
        raise ValueError('카메라 렌즈 중심 사이 실측 거리를 미터로 입력하세요 (0.01~100m).')
    if result['cameras'] is not None:
        if not isinstance(result['cameras'], list) or len(result['cameras'])!=2:raise ValueError('카메라 A/B 내부값 두 개가 필요합니다.')
        for c in result['cameras']:camera_intrinsics(c)
    transform=result['room_transform']
    if transform is not None:
        if transform.get('measured') is not True:raise ValueError('카메라 A→방 변환은 실제 측정한 경우만 입력하세요.')
        rigid(transform['rotation'], transform['translation'])
    return result


def person_boxes(row, width, height):
    """All available MediaPipe candidates, not just the selected/tracked person."""
    boxes=[]
    for candidate in row.get('raw_candidates') or []:
        points=(candidate.get('sdk_landmarks') or {}).get('image') or []
        xy=[]
        for p in points:
            if (all(type(p.get(k)) in (float,int) and math.isfinite(p[k]) for k in ('x','y'))
                    and (p.get('visibility') is None or type(p['visibility']) in (int,float) and p['visibility']>=.2)):
                xy.append([p['x']*width,p['y']*height])
        if points:
            boxes.append(_expanded_box(xy,width,height) if len(xy)>=4 else [0,0,width,height])
    # Common mapped coordinates also work for explicit SDK test adapters.
    for person in row.get('mapped_people') or []:
        xy=[[j['x_px'],j['y_px']] for j in person['joints'] if j.get('availability')=='predicted'
            and all(type(j.get(k)) in (int,float) and math.isfinite(j[k]) for k in ('x_px','y_px'))]
        if xy:boxes.append(_expanded_box(xy,width,height))
        elif person.get('joints'):boxes.append([0,0,width,height])
    return boxes


def _expanded_box(points, width, height):
    points=np.asarray(points,float);lo=points.min(axis=0);hi=points.max(axis=0)
    # Whole bounding rectangle with conservative margin, not thin landmark lines.
    pad=np.maximum((hi-lo)*.3,[width*.08,height*.12])
    return [max(0,int(lo[0]-pad[0])),max(0,int(lo[1]-pad[1])),
            min(width,int(hi[0]+pad[0]+1)),min(height,int(hi[1]+pad[1]+1))]


def background_masks(samples):
    """Union of pose/HOG people and temporal motion; independent of CLIP/posture rules."""
    import cv2
    if len(samples)<3:raise CalibrationFailure('정적 배경 확인에는 시간 간격이 있는 세 프레임 쌍이 필요합니다.')
    masks=[];diagnostics=[]
    hog=cv2.HOGDescriptor();hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    for slot in (0,1):
        packets=[p[slot] for p in samples];size=packets[0]['rgb'].shape
        if any(p['rgb'].shape!=size for p in packets):raise CalibrationFailure('수집 중 해상도가 변경되었습니다.')
        h,w=size[:2];mask=np.full((h,w),255,np.uint8);pose_count=hog_count=0
        grays=[]
        for p in packets:
            if p.get('processing_ok') is not True:raise CalibrationFailure('관절/사람 관측 처리가 실패했습니다. 현재 추론 상태를 확인하세요.')
            gray=cv2.cvtColor(p['rgb'],cv2.COLOR_RGB2GRAY);grays.append(gray)
            for x0,y0,x1,y1 in p.get('person_boxes',[]):
                mask[y0:y1,x0:x1]=0;pose_count+=1
            scale=min(1.,640./w)
            reduced=cv2.resize(p['rgb'],None,fx=scale,fy=scale) if scale<1 else p['rgb']
            if reduced.shape[0]>=128 and reduced.shape[1]>=64:
                found,_=hog.detectMultiScale(reduced,winStride=(8,8),padding=(8,8),scale=1.08)
                for x,y,bw,bh in found:
                    x0,y0,x1,y1=_expanded_box([[x/scale,y/scale],[(x+bw)/scale,(y+bh)/scale]],w,h)
                    mask[y0:y1,x0:x1]=0;hog_count+=1
        motion=np.zeros((h,w),np.uint8)
        for gray in grays[1:]:
            a=cv2.GaussianBlur(grays[0],(5,5),0);b=cv2.GaussianBlur(gray,(5,5),0)
            # Remove uniform exposure offset while retaining local changes.
            delta=b.astype(np.float32)-a;delta-=np.median(delta)
            motion[np.abs(delta)>18]=255
        motion=cv2.dilate(motion,np.ones((21,21),np.uint8))
        motion_fraction=float(np.mean(motion!=0));mask[motion!=0]=0
        available=float(np.mean(mask!=0))
        diagnostic=dict(pose_boxes=pose_count,hog_boxes=hog_count,motion_fraction=motion_fraction,
                        usable_background_fraction=available,person_detection_complete=False,
                        sources=['mediapipe_all_returned_candidates','opencv_hog','temporal_motion'])
        diagnostics.append(diagnostic)
        if motion_fraction>.35:raise CalibrationFailure('배경이 너무 많이 움직이거나 카메라가 움직였습니다. 빈 방에서 고정 후 다시 시작하세요.',dict(masking=diagnostics))
        if available<.2:raise CalibrationFailure('사람/움직임 제외 후 정적 배경이 너무 적습니다. 사람이 비킨 뒤 다시 시작하세요.',dict(masking=diagnostics))
        masks.append(mask)
    return masks,diagnostics


def match_features(images, masks, detector='SIFT'):
    import cv2
    task=cv2.SIFT_create(nfeatures=5000) if detector=='SIFT' else cv2.ORB_create(nfeatures=6000)
    gray=[cv2.cvtColor(v,cv2.COLOR_RGB2GRAY) for v in images]
    features=[task.detectAndCompute(g,m) for g,m in zip(gray,masks)]
    metrics=dict(detector=detector,keypoints=[len(f[0]) for f in features])
    if any(f[1] is None for f in features):raise CalibrationFailure('정적 배경 특징점이 부족합니다. 겹치는 시야·밝기·초점을 확인하세요.',metrics)
    matcher=cv2.BFMatcher(cv2.NORM_L2 if detector=='SIFT' else cv2.NORM_HAMMING)
    def ratio(a,b):
        return {m.queryIdx:m for pair in matcher.knnMatch(a,b,k=2) if len(pair)==2
                for m,n in [pair] if m.distance<.72*n.distance}
    forward=ratio(features[0][1],features[1][1]);back=ratio(features[1][1],features[0][1])
    good=[m for m in forward.values() if m.trainIdx in back and back[m.trainIdx].trainIdx==m.queryIdx]
    metrics['matched_features']=len(good)
    if len(good)<POLICY['min_matches']:raise CalibrationFailure('상호 검증된 배경 대응점이 40개 미만입니다. 공통 시야/배경 질감을 확보하세요.',metrics)
    points=[np.array([features[i][0][m.queryIdx if i==0 else m.trainIdx].pt for m in good],float) for i in (0,1)]
    return points,metrics


def _coverage(points,size):
    import cv2
    w,h=size
    hull=cv2.convexHull(np.asarray(points,np.float32))
    return float(cv2.contourArea(hull)/(w*h))


def estimate_geometry(points, sizes, cameras=None, baseline_m=None):
    """Numeric estimator separated for meaningful known-geometry/outlier tests."""
    import cv2
    metrics=dict(matched_features=len(points[0]))
    known=[camera_intrinsics(c) for c in cameras] if cameras is not None else None
    if known:
        corrected=[cv2.undistortPoints(p.reshape(-1,1,2),k,d,P=k).reshape(-1,2) for p,(k,d,_) in zip(points,known)]
    else:corrected=points
    f,fmask=cv2.findFundamentalMat(corrected[0],corrected[1],cv2.FM_RANSAC,1.5,.999,5000)
    if f is None or f.shape!=(3,3) or fmask is None:raise CalibrationFailure('Fundamental Matrix RANSAC 실패 · 공통 배경 대응을 확인하세요.',metrics)
    selected=fmask.ravel()!=0
    count=int(selected.sum());fraction=float(np.mean(selected))
    metrics.update(fundamental_inliers=count,fundamental_inlier_ratio=fraction)
    if count<POLICY['min_inliers'] or fraction<POLICY['min_inlier_ratio']:
        raise CalibrationFailure('RANSAC 인라이어 수/비율이 부족합니다.',metrics)
    x=[p[selected] for p in corrected]
    _,hmask=cv2.findHomography(x[0],x[1],cv2.RANSAC,2.)
    planar=float(np.mean(hmask)) if hmask is not None else 0.
    coverage=[_coverage(p,size) for p,size in zip(x,sizes)]
    metrics.update(homography_inlier_ratio=planar,coverage_fraction=coverage)
    if planar>POLICY['max_homography_ratio']:raise CalibrationFailure('한 평면/순수 회전이 우세해 상대 깊이가 불안정합니다. 여러 깊이의 배경과 시차가 필요합니다.',metrics)
    if min(coverage)<POLICY['min_coverage']:raise CalibrationFailure('대응점이 좁은 영역에 집중되어 있습니다. 더 넓은 정적 배경이 필요합니다.',metrics)
    base=dict(method='automatic_background',calibration_timestamp=datetime.now(timezone.utc).isoformat(),
              quality_policy=POLICY,metrics=metrics,fundamental_matrix=f.tolist(),
              triangulation_eligible=False,room_alignment_verified=False)
    if not known:
        xh=np.column_stack((x[0],np.ones(len(x[0]))));yh=np.column_stack((x[1],np.ones(len(x[1]))))
        fx=xh@f.T;fty=yh@f
        residual=np.abs(np.sum(yh*fx,axis=1))/np.sqrt(np.maximum(1e-15,np.sum(fx[:,:2]**2,axis=1)))
        metrics.update(epipolar_distance_median_px=float(np.median(residual)),reprojection_rms_px=None)
        return dict(base,status='needs_input',coordinate_system=None,unit=None,calibration_validity='fundamental_only',
                    reason='내부값이 없어 F와 영상 대응 품질만 추정했습니다. A/B의 K(초점거리·주점), 왜곡 계수와 그 해상도가 최소 입력입니다. 실제 거리에는 렌즈 중심 사이 실측 거리도 필요합니다.')
    normalized=[cv2.undistortPoints(p.reshape(-1,1,2),k,np.zeros(5)).reshape(-1,2) for p,(k,_,_) in zip(x,known)]
    focal=min(min(k[0,0],k[1,1]) for k,_,_ in known)
    e,emask=cv2.findEssentialMat(*normalized,np.eye(3),method=cv2.RANSAC,prob=.999,threshold=1.5/focal)
    if e is None or e.shape!=(3,3):raise CalibrationFailure('Essential Matrix 추정이 불안정합니다.',metrics)
    count,r,t,pmask=cv2.recoverPose(e,*normalized,np.eye(3),mask=emask)
    good=pmask.ravel()!=0
    metrics.update(pose_inliers=int(count),pose_inlier_ratio=float(count/len(points[0])))
    if count<POLICY['min_inliers'] or count/len(points[0])<POLICY['min_inlier_ratio']:
        raise CalibrationFailure('recoverPose의 앞쪽 관측/인라이어 비율이 부족합니다.',metrics)
    norm=[p[good] for p in normalized]
    homogeneous=cv2.triangulatePoints(np.column_stack((np.eye(3),np.zeros(3))),np.column_stack((r,t)),norm[0].T,norm[1].T)
    if np.any(np.abs(homogeneous[3])<1e-12):raise CalibrationFailure('배경 삼각측량이 무한대에 가까워 불안정합니다.',metrics)
    xyz=(homogeneous[:3]/homogeneous[3]).T
    center=(-r.T@t).ravel();a=xyz/np.linalg.norm(xyz,axis=1)[:,None];b=xyz-center;b/=np.linalg.norm(b,axis=1)[:,None]
    angles=np.degrees(np.arccos(np.clip(np.sum(a*b,axis=1),-1,1)))
    angles=np.minimum(angles,180-angles)
    errors=[]
    for i,(k,_,_) in enumerate(known):
        projected=(xyz if i==0 else xyz@r.T+t.ravel())@k.T
        errors.extend(np.linalg.norm(projected[:,:2]/projected[:,2,None]-x[i][good],axis=1))
    rms=float(np.sqrt(np.mean(np.square(errors))));p95=float(np.percentile(errors,95))
    metrics.update(reprojection_rms_px=rms,reprojection_p95_px=p95,median_ray_angle_degrees=float(np.median(angles)))
    if not np.isfinite(xyz).all() or np.median(angles)<POLICY['min_ray_angle_degrees']:
        raise CalibrationFailure('시차가 너무 작아 깊이 복원이 불안정합니다.',metrics)
    if rms>POLICY['max_reprojection_rms_px'] or p95>POLICY['max_reprojection_p95_px']:
        raise CalibrationFailure('재투영 오차가 품질 한계를 초과했습니다.',metrics)
    length=baseline_m if baseline_m is not None else 1.
    camera_rows=[]
    for i,(k,d,size) in enumerate(known):
        camera_rows.append(dict(camera_id=['camera_a','camera_b'][i],image_resolution=size,intrinsic_matrix=k.tolist(),
            distortion_coefficients=d.tolist(),rotation=(np.eye(3) if i==0 else r).tolist(),
            translation=([0.,0.,0.] if i==0 else (t.ravel()*length).tolist()),reprojection_error=rms))
    return dict(base,status='estimated',coordinate_system='camera_a',unit='m' if baseline_m is not None else 'baseline_units',
                calibration_validity='relative_metric_candidate' if baseline_m is not None else 'relative_pose_unscaled',
                baseline_m=baseline_m,rotation_camera_a_to_b=r.tolist(),translation_direction=t.ravel().tolist(),
                essential_matrix=e.tolist(),cameras=camera_rows,
                reason='카메라 A 기준 상대 자세입니다. 방 원점/위쪽 방향은 아직 측정하지 않았습니다.' if baseline_m is not None else '상대 자세만 추정했습니다. 실제 렌즈 중심 간 거리(m)를 입력해야 미터 척도가 생깁니다.')


def solve_background(samples, value):
    settings=validate_inputs(value)
    masks,mask_info=background_masks(samples)
    sizes=[[p['width'],p['height']] for p in samples[0]]
    if settings['cameras'] is not None:
        for size,c in zip(sizes,settings['cameras']):
            if size!=c['image_resolution']:raise CalibrationFailure('알려진 내부값의 해상도가 현재 원본 영상과 다릅니다.')
    results=[]
    for sample in samples:
        points,feature_metrics=match_features([v['rgb'] for v in sample],masks,settings['detector'])
        result=estimate_geometry(points,sizes,settings['cameras'],settings['baseline_m'])
        result['metrics'].update(feature_metrics)
        results.append(result)
    result=results[-1];metric=result['metrics'];metric['masking']=mask_info
    result['sample_metrics']=[dict(v['metrics']) for v in results[:-1]]+[dict(metric)]
    if settings['cameras'] is not None:
        r0=np.asarray(result['rotation_camera_a_to_b']);t0=np.asarray(result['translation_direction'])
        rotation_spread=max(float(np.degrees(np.arccos(np.clip((np.trace(np.asarray(v['rotation_camera_a_to_b'])@r0.T)-1)/2,-1,1)))) for v in results)
        translation_spread=max(float(np.degrees(np.arccos(np.clip(np.dot(np.asarray(v['translation_direction']),t0),-1,1)))) for v in results)
        metric.update(rotation_spread_degrees=rotation_spread,translation_spread_degrees=translation_spread)
        if rotation_spread>POLICY['max_rotation_spread_degrees'] or translation_spread>POLICY['max_translation_spread_degrees']:
            raise CalibrationFailure('프레임 간 상대 자세가 일관되지 않습니다. 카메라/사람 움직임·배경 대응을 확인하세요.',metric)
    result.update(sample_count=len(samples),source_frame_ids=[[p['frame_id'] for p in pair] for pair in samples],
                  source_receive_ns=[[str(p['time_ns']) for p in pair] for pair in samples],
                  intrinsics_sha256=object_hash(settings['cameras']),person_detection_complete=False)
    if settings['cameras'] is not None and settings['baseline_m'] is not None and settings['room_transform'] is not None:
        rw,cw=rigid(settings['room_transform']['rotation'],settings['room_transform']['translation'])
        for c in result['cameras']:
            r=np.asarray(c['rotation']);t=np.asarray(c['translation']);new_r=r@rw.T
            c.update(rotation=new_r.tolist(),translation=(t-new_r@cw).tolist())
        result.update(version='room-stereo-1',coordinate_system='room_world',unit='m',calibration_validity='measured',
            triangulation_eligible=True,room_alignment_verified=True,room_transform=settings['room_transform'],
            stereo_reprojection_error=metric['reprojection_rms_px'],anchor_reprojection_error=metric['reprojection_rms_px'],
            quality=dict(max_reprojection_error_px=POLICY['max_reprojection_rms_px'],min_ray_angle_degrees=POLICY['min_ray_angle_degrees'],min_visibility=.5),
            reason='측정한 내부값·기준선·방 변환과 정적 배경 품질 검사를 통과한 후보입니다. 실제 3D 정확도 인증은 아닙니다.')
    return result
