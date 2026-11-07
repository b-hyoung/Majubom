"""Experimental bed-plane/pose registration. Assumed intrinsics are not calibration."""
import math
import numpy as np
from .bed_geometry import is_quad,contains,relative_xy,_homography
from .weighted_posture import fit,weighted,select_scores
from .posture_evidence import body_geometry


def world_points(row,person):
    """Same candidate, verified MP mapping only; never scores/targets as coordinates."""
    from .pose.mediapipe import COCO_INDICES
    from .posture_evidence import world_body_evidence
    if world_body_evidence(row,person)['status']!='available':return None
    raw=[p for p in (row.get('raw_candidates') or []) if p.get('candidate_id')==person['candidate_id']]
    if len(raw)!=1:return None
    if tuple(j.get('source_index') for j in person['joints'])!=COCO_INDICES:return None
    try:
        values=raw[0]['sdk_landmarks']['world']
        points=tuple(tuple(values[i][k] for k in ('x','y','z')) for i in COCO_INDICES)
        if any(type(v) not in (int,float) or not math.isfinite(v) for p in points for v in p):return None
        return points
    except (KeyError,TypeError,IndexError):return None


def bed_camera(region,width,height,cfg):
    if not is_quad(region):raise ValueError('auto_view_bed_corners_required')
    # Estimate a pinhole K from the rectangle's orthogonal vanishing directions.
    # Principal point=image centre, square pixels, no skew/distortion are assumptions.
    coefficients=_homography(tuple(tuple(p) for p in region['vertices']),width,height)
    image_to_unit=np.array([*coefficients,1.],dtype=np.float64).reshape(3,3)
    H=np.diag([width,height,1.])@np.linalg.inv(image_to_unit)
    H/=np.linalg.norm(H)
    if not np.isfinite(H).all():raise ValueError('auto_view_bed_geometry_uncertain')
    a,b=H[:,0].copy(),H[:,1].copy()
    a[:2]-=np.array([width/2,height/2])*a[2];b[:2]-=np.array([width/2,height/2])*b[2]
    product=a[2]*b[2];size=max(width,height)
    focal2=-float(np.dot(a[:2],b[:2]))/product if abs(product)>1e-10 else -1
    estimated=focal2>0 and .25*size<=math.sqrt(focal2)<=5*size
    focal=math.sqrt(focal2) if estimated else size*cfg['auto_focal_prior']
    K=np.array([[focal,0,width/2],[0,focal,height/2],[0,0,1]],dtype=np.float64)
    axes=np.linalg.solve(K,H[:,:2]);lengths=np.linalg.norm(axes,axis=0)
    if min(lengths)<=1e-12:raise ValueError('auto_view_bed_geometry_uncertain')
    axes/=lengths;orth=abs(float(np.dot(axes[:,0],axes[:,1])))
    if orth>cfg['auto_plane_orthogonal_max']:raise ValueError('auto_view_bed_geometry_uncertain')
    normal=np.cross(axes[:,0],axes[:,1]);normal/=np.linalg.norm(normal)
    if normal[2]>0:normal=-normal
    if abs(normal[2])<cfg['auto_plane_visible_min']:raise ValueError('auto_view_bed_geometry_uncertain')
    return K,normal,{'focal_px':float(focal),'intrinsics_source':'rectangle_orthogonality' if estimated else 'assumed_focal_prior',
        'principal_point':'image_center_assumed','square_pixels_assumed':True,'distortion':'zero_assumed',
        'plane_normal_camera':normal.tolist(),'orthogonality_error':orth,'calibration_verified':False}


def evaluate(obs,cfg):
    e={'view':'auto','information_path':'bed_registered_model_3d','camera_registration':None}
    def finish(scores=None,reason=None):
        d=select_scores(scores or dict.fromkeys(('supine','side','edge_sitting')),cfg['score_min'],cfg['score_close_gap'])
        d.update(warnings=['experimental_camera_and_monocular_geometry'],components=e.get('components',{}))
        if reason:d.update(label=None,status='held',reason=reason)
        e['score_decision']=d
        e['orientation_decision']={'source':'bed_registered_model_3d' if 'torso_plane_normal_cos' in e else 'registration_unavailable','status':d['status']}
        return d['label'],d['reason'],e
    if obs.bed_rectangle is None:return finish(reason='bed_region_required')
    if len(obs.points)!=17 or any(obs.points[i] is None for i in (5,6,11,12)):
        return finish(reason='automatic_torso_joints_missing')
    if obs.world_points is None:return finish(reason='auto_view_world_required')
    if (len(obs.world_points)!=17 or any(len(p)!=3 or any(type(v) not in (float,int) or not math.isfinite(v) for v in p) for p in obs.world_points)):
        return finish(reason='auto_view_world_required')
    import cv2
    try:K,normal,registration=bed_camera(obs.bed_rectangle,obs.width,obs.height,cfg)
    except ValueError as exc:return finish(reason=str(exc))
    except (cv2.error,np.linalg.LinAlgError,OverflowError):return finish(reason='auto_view_bed_geometry_uncertain')
    e['camera_registration']=registration
    indices=[i for i,p in enumerate(obs.points) if p is not None]
    if len(indices)<6:return finish(reason='auto_view_registration_uncertain')
    xyz=np.asarray([obs.world_points[i] for i in indices],dtype=np.float64)
    xy=np.asarray([obs.points[i] for i in indices],dtype=np.float64)
    torso2d=math.dist(tuple((obs.points[5][k]+obs.points[6][k])/2 for k in (0,1)),
                      tuple((obs.points[11][k]+obs.points[12][k])/2 for k in (0,1)))
    if torso2d<=1e-6:return finish(reason='degenerate_body_geometry')
    try:
        ok,r,t=cv2.solvePnP(xyz,xy,K,None,flags=cv2.SOLVEPNP_SQPNP)
        if not ok:return finish(reason='auto_view_registration_uncertain')
        r,t=cv2.solvePnPRefineLM(xyz,xy,K,None,r,t)
        R=cv2.Rodrigues(r)[0];camera=np.asarray(obs.world_points,dtype=np.float64)@R.T+t.reshape(3)
        projected=cv2.projectPoints(xyz,r,t,K,None)[0].reshape(-1,2)
        error=float(np.sqrt(np.mean(np.sum((projected-xy)**2,axis=1)))/torso2d)
        registration.update(reprojection_torso_fraction=error,point_count=len(indices),rotation=R.tolist(),translation=t.reshape(3).tolist())
        if not np.isfinite(camera).all() or np.min(camera[:,2])<=0 or error>cfg['auto_reprojection_max']:
            return finish(reason='auto_view_registration_uncertain')
    except (cv2.error,ValueError,np.linalg.LinAlgError):return finish(reason='auto_view_registration_uncertain')
    return classify_registered(obs,cfg,camera,normal,e,finish)


def classify_registered(obs,cfg,p,normal,e,finish):
    """Apply the existing 0.6 geometry rules to already registered coordinates."""
    s=(p[5]+p[6])/2;h=(p[11]+p[12])/2;torso=h-s;length=np.linalg.norm(torso)
    if length<=1e-9:return finish(reason='degenerate_body_geometry')
    f=body_geometry([p[i].tolist() for i in (5,6,11,12)],3)
    if max(f[n]['axial'] for n in ('shoulders','hips'))>cfg['body_axial_max'] or f['line_angle_degrees'] is None or f['line_angle_degrees']>cfg['body_line_angle_max']:
        return finish(reason='body_axis_inconsistent')
    upright=abs(float(np.dot(torso/length,normal)));e['torso_plane_normal_cos']=upright
    hips_inside=all(contains(obs.bed_rectangle,obs.points[i]) for i in (11,12))
    if not hips_inside:return finish(reason='body_outside_bed_region')
    scores=dict.fromkeys(('supine','side','edge_sitting'))
    if upright>=cfg['upright_cos']:
        if any(obs.points[i] is None for i in (13,14,15,16)):return finish(reason='automatic_lower_body_needed')
        def angle(a,b,c):
            u=a-b;v=c-b;d=np.linalg.norm(u)*np.linalg.norm(v)
            return math.degrees(math.acos(float(np.clip(np.dot(u,v)/d,-1,1)))) if d>1e-12 else None
        knees=[angle(p[i],p[k],p[a]) for i,k,a in ((11,13,15),(12,14,16))];e['knee_angles']=knees
        if any(a is None for a in knees):return finish(reason='degenerate_body_geometry')
        if min(knees)>=cfg['straight_knee_min']:return finish(reason='standing_or_upright_out_of_scope')
        if not all(cfg['bent_knee_min']<=a<=cfg['bent_knee_max'] for a in knees):return finish(reason='auto_view_posture_ambiguous')
        # Thigh along bed, calf away from its visible surface. No image-axis tests.
        for hip,knee,ankle in ((11,13,15),(12,14,16)):
            thigh=p[knee]-p[hip];calf=p[ankle]-p[knee]
            if (np.linalg.norm(thigh)<=1e-9 or np.linalg.norm(calf)<=1e-9
                or abs(np.dot(thigh,normal))/np.linalg.norm(thigh)>cfg['lying_cos']
                or np.dot(calf,normal)/np.linalg.norm(calf)>-cfg['upright_cos']):return finish(reason='auto_view_posture_ambiguous')
        uv=relative_xy(obs.bed_rectangle,tuple((obs.points[11][i]+obs.points[12][i])/2 for i in (0,1)))
        if uv is None:return finish(reason='bed_coordinate_unavailable')
        edge=max(0.,min(*uv,1-uv[0],1-uv[1]));e['hip_edge_fraction']=edge
        if edge>cfg['edge_fraction']:return finish(reason='auto_view_posture_ambiguous')
        peak=cfg['score_knee_peak'];components={'torso':min(1.,(upright-cfg['lying_cos'])/(cfg['upright_cos']-cfg['lying_cos'])),
            'knees':min(fit(a,peak,cfg['bent_knee_min'] if a<peak else cfg['bent_knee_max'],1.) for a in knees),
            'edge':max(0.,1-edge/cfg['edge_fraction'])}
        e['components']={'edge_sitting':components}
        scores['edge_sitting']=weighted([(components[k],cfg['score_sitting_'+name+'_weight']) for k,name in (('torso','torso'),('knees','knee'),('edge','edge'))])
    elif upright<=cfg['lying_cos']:
        if not all(contains(obs.bed_rectangle,obs.points[i]) for i in (5,6)):return finish(reason='body_outside_bed_region')
        pairs=[p[5]-p[6],p[11]-p[12]]
        if any(np.linalg.norm(v)<=1e-9 for v in pairs) or np.dot(*pairs)<=0:return finish(reason='body_axis_inconsistent')
        depths=[abs(float(np.dot(v,normal)/np.linalg.norm(v))) for v in pairs]
        e['bed_normal_fractions']=depths
        if abs(depths[0]-depths[1])>cfg['world_depth_difference_max']:return finish(reason='shoulder_hip_orientation_conflict')
        for label,proto,other in (('side',cfg['world_side_depth_min'],cfg['world_front_depth_max']),('supine',cfg['world_front_depth_max'],cfg['world_side_depth_min'])):
            scores[label]=weighted([(fit(v,proto,other,cfg['score_overlap_scale']),w) for v,w in zip(depths,(cfg['score_shoulder_weight'],cfg['score_hip_weight']))])
        # Anatomical anterior points opposite the shoulder-left/right x torso cross.
        anterior=-np.cross(pairs[0],torso);an=np.linalg.norm(anterior)
        front=an>1e-9 and np.dot(anterior/an,normal)>0
        if any(obs.points[i] is None for i in (0,1,2)) or not front:scores['supine']=None
        if max(depths)<=cfg['world_front_depth_max'] and not front:return finish(reason='front_back_ambiguous')
        e['components']={'bed_normal_3d':{'shoulders':depths[0],'hips':depths[1]}}
    else:return finish(reason='auto_view_posture_ambiguous')
    return finish(scores)
