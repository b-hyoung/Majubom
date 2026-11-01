"""Calibrated two-view geometry as supplementary evidence; never changes posture rules."""
import math
from pathlib import Path
import numpy as np
from .io_utils import load_json, file_hash, json_text
from .fusion_inputs import inspect_group, iter_inputs
from .group_posture import predictions
from .contracts import COCO17


def validate_calibration(calibration, group):
    if calibration.get('version') != 'stereo-pinhole-0.1':
        raise ValueError('Unsupported stereo calibration version')
    if calibration.get('image_coordinates') != 'undistorted_pinhole_pixels':
        raise ValueError('Calibration requires undistorted input images')
    if not isinstance(calibration.get('unit'), str) or not calibration['unit'].strip():
        raise ValueError('Calibration coordinate unit is required')
    limits = calibration.get('quality', {})
    for name in ('max_reprojection_error_px', 'min_ray_angle_degrees'):
        v = limits.get(name)
        if type(v) not in (int, float) or not math.isfinite(v) or v <= 0:
            raise ValueError('Specify positive finite stereo quality limits')
    if limits['min_ray_angle_degrees'] >= 90:
        raise ValueError('Minimum ray angle must be below 90 degrees')
    cameras = calibration.get('cameras')
    if not isinstance(cameras, list) or len(cameras) != 2:
        raise ValueError('Exactly two calibrated cameras are required')
    if [c.get('input_id') for c in cameras] != [m['input_id'] for m in group['members']]:
        raise ValueError('Calibration camera order or input IDs differ from group')
    matrices = []
    for camera, member in zip(cameras, group['members']):
        manifest = load_json(Path(member['run_directory']) / 'manifest.json')
        if camera.get('image_size') != [manifest['width'], manifest['height']]:
            raise ValueError('Calibration image size differs from recording')
        try:
            k = np.asarray(camera['intrinsic'], dtype=float)
            r = np.asarray(camera['rotation_world_to_camera'], dtype=float)
            t = np.asarray(camera['translation_world_to_camera'], dtype=float)
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError('Invalid camera matrices') from exc
        if (k.shape != (3, 3) or r.shape != (3, 3) or t.shape != (3,)
                or not all(np.isfinite(x).all() for x in (k, r, t))
                or k[0, 0] <= 0 or k[1, 1] <= 0
                or not np.allclose(k[2], [0, 0, 1], atol=1e-8)
                or not np.allclose(r @ r.T, np.eye(3), atol=1e-5)
                or not np.isclose(np.linalg.det(r), 1, atol=1e-5)):
            raise ValueError('Invalid intrinsic or rigid camera transform')
        matrices.append((k @ np.column_stack((r, t)), r, t, -r.T @ t))
    if np.linalg.norm(matrices[0][3] - matrices[1][3]) < 1e-9:
        raise ValueError('Camera centers must differ')
    return matrices


def triangulate(points, matrices, quality):
    """DLT reconstruction with cheirality, parallax and pixel reprojection checks."""
    p0, p1 = (m[0] for m in matrices)
    a = np.stack([points[0][0]*p0[2]-p0[0], points[0][1]*p0[2]-p0[1],
                  points[1][0]*p1[2]-p1[0], points[1][1]*p1[2]-p1[1]])
    _, _, vh = np.linalg.svd(a)
    h = vh[-1]
    if abs(h[3]) < 1e-12:
        return {'status':'held', 'reason':'point_at_infinity'}
    xyz = h[:3] / h[3]
    if not np.isfinite(xyz).all():
        return {'status':'held', 'reason':'invalid_geometry'}
    if any((r @ xyz + t)[2] <= 0 for _, r, t, _ in matrices):
        return {'status':'held', 'reason':'behind_camera'}
    rays = [xyz - m[3] for m in matrices]
    norms = [np.linalg.norm(v) for v in rays]
    if min(norms) < 1e-12:
        return {'status':'held', 'reason':'invalid_geometry'}
    cosine = np.clip(np.dot(*rays) / (norms[0]*norms[1]), -1, 1)
    angle = math.degrees(math.acos(cosine))
    errors = []
    for point, (p, _, _, _) in zip(points, matrices):
        projected = p @ np.append(xyz, 1)
        errors.append(float(np.linalg.norm(projected[:2] / projected[2] - point)))
    diagnostic = {'ray_angle_degrees':angle, 'reprojection_error_px':errors}
    if min(angle, 180-angle) < quality['min_ray_angle_degrees']:
        return dict(status='held', reason='insufficient_parallax', **diagnostic)
    if max(errors) > quality['max_reprojection_error_px']:
        return dict(status='held', reason='reprojection_error', **diagnostic)
    return dict(status='estimated', xyz=xyz.tolist(), **diagnostic)


def depth_row(row, matrices, quality):
    # Bed-region availability is unrelated to stereo geometry. All other gates remain.
    reasons = [r for r in row['hold_reasons'] if not r.endswith(':bed_region_missing')]
    result = {'status':'held', 'reasons':reasons, 'joints':[], 'time_basis':row['time_basis'],
              'identity_verified':False, 'exposure_synchronized':False}
    if reasons or any(v is None or v['joints'] is None for v in row['views']):
        if not reasons: result['reasons'] = ['joint_observation_unavailable']
        return result
    for index, name in enumerate(COCO17):
        joints = [v['joints'][index] for v in row['views']]
        if any(not j['image_mask'] for j in joints):
            estimate = {'status':'held', 'reason':'joint_not_observed_in_both_views'}
        else:
            estimate = triangulate([[j['x_px'], j['y_px']] for j in joints], matrices, quality)
        result['joints'].append(dict(joint_index=index, name=name, **estimate))
    result['status'] = 'estimated' if any(j['status']=='estimated' for j in result['joints']) else 'held'
    if result['status']=='held': result['reasons']=['no_reconstructable_joints']
    return result


def export(group_dir, calibration_path, output, *, depth_check=False):
    group_dir, output = Path(group_dir), Path(output)
    inspect_group(group_dir)
    group = load_json(group_dir/'manifest.json')
    if group.get('posture_predictions', {}).get('status') != 'generated':
        raise ValueError('A group posture prediction record is required')
    calibration = load_json(calibration_path)
    matrices = validate_calibration(calibration, group)
    normal = validate_bed_normal(calibration) if depth_check else None
    config = decision_config(group) if depth_check else None
    output.mkdir(parents=True, exist_ok=False)
    count = estimated = 0
    with (output/'posture_with_depth.jsonl').open('x', encoding='utf-8') as stream:
        for row, posture in zip(iter_inputs(group, group_dir), predictions(group, group_dir), strict=True):
            if row['alignment_id'] != posture['alignment_id']:
                raise ValueError('Alignment mismatch')
            depth = depth_row(row, matrices, calibration['quality'])
            depth['unit'] = calibration['unit']
            combined = {'posture':posture, 'stereo_depth':depth}
            if depth_check:
                stereo = stereo_posture(row, depth, normal, config)
                combined.update(stereo_posture=stereo, depth_checked_posture=combine_depth(posture, stereo))
            stream.write(json_text(combined)+'\n')
            count += 1
            estimated += depth['status']=='estimated'
    report = {'version':'stereo-posture-evidence-0.1', 'group_id':group['group_id'],
              'group_manifest_sha256':file_hash(group_dir/'manifest.json'),
              'calibration':calibration, 'calibration_sha256':file_hash(calibration_path),
              'output_sha256':file_hash(output/'posture_with_depth.jsonl'), 'rows':count,
              'rows_with_estimated_joints':estimated, 'posture_rules_changed':False,
              'depth_used_in_classifier':depth_check, 'depth_rule_profile':config,
              'decision_policy':'image-and-stereo-agreement-0.1' if depth_check else None,
              'accuracy_validated':False}
    from .io_utils import atomic_json
    atomic_json(output/'manifest.json', report)
    return report


def decision_config(group):
    """Reuse the two members' existing geometry thresholds, without retuning them."""
    from .automatic_posture import AUTO_DEFAULTS, AutomaticClassifier
    from .group_posture import profiles
    configs = profiles(group)
    if any(not isinstance(p, dict) or p.get('version') not in ('automatic-posture-rules-0.8','automatic-posture-rules-0.9','automatic-posture-rules-0.10','automatic-posture-rules-0.11','automatic-posture-rules-0.12') for p in configs):
        raise ValueError('Depth checking requires compatible automatic body-state profiles on both views')
    geometry_keys = set(AUTO_DEFAULTS) - {'version', 'view', 'auto_focal_prior',
        'auto_plane_orthogonal_max', 'auto_plane_visible_min', 'auto_reprojection_max'}
    if any(configs[0].get(k) != configs[1].get(k) for k in geometry_keys):
        raise ValueError('Camera geometry rule thresholds differ')
    config = dict(AUTO_DEFAULTS)
    for key in geometry_keys:
        config[key] = configs[0][key]
    AutomaticClassifier(config)  # Validate the inherited rule settings.
    return config


def validate_bed_normal(calibration):
    try:
        normal = np.asarray(calibration['bed_normal_up_world'], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Specify bed_normal_up_world in calibration world coordinates') from exc
    if normal.shape != (3,) or not np.isfinite(normal).all() or not np.isclose(np.linalg.norm(normal), 1, atol=1e-5):
        raise ValueError('bed_normal_up_world must be a unit vector pointing away from the bed')
    return normal


def stereo_posture(row, depth, normal, config):
    """Apply the unchanged registered 3D rules to stereo coordinates in the bed frame."""
    from .viewpoint_posture import classify_registered
    from .posture import PostureInput
    from .weighted_posture import select_scores
    result = {'status':'held', 'label':None, 'reason':None,
              'method':'stereo-existing-geometry-0.6', 'features':{}}
    if depth['status'] != 'estimated':
        result['reason'] = 'stereo_depth_unavailable'
        return result
    view = row['views'][0]
    if view['bed_region'] is None:
        result['reason'] = 'bed_region_required'
        return result
    points = tuple((j['x_px'], j['y_px']) if j['image_mask'] else None for j in view['joints'])
    xyz = [np.asarray(j['xyz'], dtype=float) if j['status']=='estimated' else None for j in depth['joints']]
    if any(xyz[i] is None or points[i] is None for i in (5, 6, 11, 12)):
        result['reason'] = 'automatic_torso_joints_missing'
        return result
    torso = (xyz[11]+xyz[12]-xyz[5]-xyz[6])/2
    size = np.linalg.norm(torso)
    if size <= 1e-9:
        result['reason'] = 'degenerate_body_geometry'
        return result
    if abs(float(np.dot(torso/size, normal))) >= config['upright_cos'] and any(xyz[i] is None for i in (13,14,15,16)):
        result['reason'] = 'automatic_lower_body_needed'
        return result
    obs = PostureInput(points,view['width'],view['height'],view['bed_region'])
    features = {'view':'stereo', 'information_path':'calibrated_stereo_bed_normal'}
    def finish(scores=None,reason=None):
        decision=select_scores(scores or dict.fromkeys(('supine','side','edge_sitting')), config['score_min'],config['score_close_gap'])
        if reason: decision.update(label=None,status='held',reason=reason)
        features['score_decision']=decision
        return decision['label'],decision['reason'],features
    label, reason, features = classify_registered(obs,config,xyz,normal,features,finish)
    result.update(status='predicted' if label else 'held',label=label,reason=reason,features=features)
    return result


def combine_depth(posture, stereo):
    """Require agreement when depth checking is explicitly enabled; never invent a label."""
    result = {'status':'held','label':None,'reasons':[], 'method':'image-and-stereo-agreement-0.1',
              'experimental':True}
    if posture['status'] != 'predicted':
        result['reasons'] = ['original_posture_held'] + list(posture['reasons'])
    elif stereo['status'] != 'predicted':
        result['reasons'] = ['stereo_posture_held',stereo['reason']]
    elif posture['label'] != stereo['label']:
        result['reasons'] = ['image_stereo_posture_conflict']
    else:
        result.update(status='predicted',label=posture['label'])
    return result
