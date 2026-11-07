"""Measured checkerboard stereo calibration. Room axes: X width, Y up, Z length (m)."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import math
import numpy as np
from .io_utils import atomic_json, load_json

VERSION = 'room-stereo-1'


def rigid(rotation, translation):
    r, t = np.asarray(rotation, float), np.asarray(translation, float)
    if (r.shape != (3, 3) or t.shape != (3,) or not np.isfinite(r).all()
            or not np.isfinite(t).all() or not np.allclose(r.T @ r, np.eye(3), atol=1e-5)
            or not np.isclose(np.linalg.det(r), 1, atol=1e-5)):
        raise ValueError('Invalid rigid transform')
    return r, t


def validate(data):
    try:
        return _validate(data)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError('Malformed calibration file') from exc


def _validate(data):
    if isinstance(data,dict) and (data.get('room_alignment_verified') is False or
            data.get('method') in ('manual','manual_visualization','natural_features')):
        raise ValueError('Unverified/manual/unscaled parameters cannot be applied as room calibration')
    if not isinstance(data, dict) or data.get('version') != VERSION or data.get('unit') != 'm' or data.get('coordinate_system') != 'room_world':
        raise ValueError('Expected room-stereo-1 calibration in room_world meters')
    if data.get('calibration_validity') != 'measured' or not isinstance(data.get('calibration_timestamp'), str):
        raise ValueError('Measured calibration and timestamp required')
    datetime.fromisoformat(data['calibration_timestamp'])
    if data.get('method')=='automatic_background':
        from .automatic_calibration import POLICY
        m=data['metrics']
        if (data.get('triangulation_eligible') is not True or data.get('sample_count',0)<3
                or not isinstance(data.get('baseline_m'),(int,float)) or data['baseline_m']<=0
                or data.get('room_transform',{}).get('measured') is not True):
            raise ValueError('Automatic calibration requires scale, measured room alignment and multi-frame quality checks')
        for name,limit,minimum in [('pose_inliers',POLICY['min_inliers'],True),
            ('pose_inlier_ratio',POLICY['min_inlier_ratio'],True),('median_ray_angle_degrees',POLICY['min_ray_angle_degrees'],True),
            ('reprojection_rms_px',POLICY['max_reprojection_rms_px'],False),('reprojection_p95_px',POLICY['max_reprojection_p95_px'],False),
            ('rotation_spread_degrees',POLICY['max_rotation_spread_degrees'],False),
            ('translation_spread_degrees',POLICY['max_translation_spread_degrees'],False)]:
            v=m[name]
            if type(v) not in (int,float) or not math.isfinite(v) or v<0 or (v<limit if minimum else v>limit):
                raise ValueError('Automatic calibration quality gate failed: '+name)
        if (type(m['homography_inlier_ratio']) not in (int,float) or not math.isfinite(m['homography_inlier_ratio'])
                or m['homography_inlier_ratio']>POLICY['max_homography_ratio']
                or len(m['coverage_fraction'])!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or v<POLICY['min_coverage'] for v in m['coverage_fraction'])):
            raise ValueError('Automatic calibration coverage/planarity gate failed')
    for field in ('stereo_reprojection_error', 'anchor_reprojection_error'):
        value = data[field]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid calibration error metrics')
    cameras = data.get('cameras')
    if not isinstance(cameras, list) or [c.get('camera_id') for c in cameras] != ['camera_a', 'camera_b']:
        raise ValueError('Expected camera_a and camera_b in order')
    centers = []
    for c in cameras:
        size = c['image_resolution']
        k, d = np.asarray(c['intrinsic_matrix'], float), np.asarray(c['distortion_coefficients'], float)
        if (len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
                or k.shape != (3, 3) or not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0
                or not np.allclose(k[2], [0, 0, 1]) or abs(np.linalg.det(k)) < 1e-9
                or d.ndim != 1 or d.size not in (4, 5, 8, 12, 14) or not np.isfinite(d).all()):
            raise ValueError('Invalid camera intrinsics, distortion or resolution')
        r, t = rigid(c['rotation'], c['translation'])
        centers.append(-r.T @ t)
        if type(c['reprojection_error']) not in (int, float) or not math.isfinite(c['reprojection_error']) or c['reprojection_error'] < 0:
            raise ValueError('Invalid reprojection error')
    if np.linalg.norm(centers[0] - centers[1]) < 1e-6:
        raise ValueError('Stereo baseline is zero')
    if data.get('method')=='automatic_background' and not np.isclose(np.linalg.norm(centers[0]-centers[1]),data['baseline_m'],rtol=1e-4):
        raise ValueError('Automatic calibration scale differs from measured baseline')
    q = data['quality']
    for key, maximum in [('max_reprojection_error_px', 1000), ('min_ray_angle_degrees', 90), ('min_visibility', 1.000001)]:
        if type(q[key]) not in (int, float) or not math.isfinite(q[key]) or not 0 < q[key] < maximum:
            raise ValueError('Invalid quality thresholds')
    return data


def board_points(columns, rows, square_m):
    if type(columns) is not int or type(rows) is not int or min(columns, rows) < 3 or not math.isfinite(square_m) or square_m <= 0:
        raise ValueError('Specify inner corners >=3 and measured positive square size in meters')
    points = np.zeros((columns * rows, 3), np.float32)
    points[:, :2] = np.mgrid[:columns, :rows].T.reshape(-1, 2) * square_m
    return points


def corners(rgb, pattern):
    import cv2
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    ok, found = cv2.findChessboardCornersSB(gray, pattern, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        ok, found = cv2.findChessboardCornersSB(
            gray, pattern, flags=cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    if not ok:
        raise ValueError('체커보드 내부 코너를 찾지 못했습니다. 양쪽 시야·초점·가림을 확인하세요.')
    return found.astype(np.float32)


def solve_samples(objects, views_a, views_b, size, anchor_index, world_points):
    """Public numeric entry point also used by synthetic projection tests."""
    import cv2
    solved = [cv2.calibrateCamera(objects, views, size, None, None) for views in (views_a, views_b)]
    (ea, ka, da, _, _), (eb, kb, db, _, _) = solved
    error, ka, da, kb, db, rab, tab, essential, fundamental = cv2.stereoCalibrate(
        objects, views_a, views_b, ka, da, kb, db, size, flags=cv2.CALIB_FIX_INTRINSIC)
    ok, rv, ta = cv2.solvePnP(world_points, views_a[anchor_index], ka, da)
    if not ok:
        raise ValueError('Room anchor pose could not be estimated')
    ra = cv2.Rodrigues(rv)[0]
    rb, tb = rab @ ra, rab @ ta + tab
    if any(np.any((world_points @ r.T + t.ravel())[:, 2] <= 0) for r, t in ((ra, ta), (rb, tb))):
        raise ValueError('Room anchor lies behind a camera; check board correspondence')
    projected, _ = cv2.projectPoints(world_points, rv, ta, ka, da)
    projected_b, _ = cv2.projectPoints(world_points, cv2.Rodrigues(rb)[0], tb, kb, db)
    anchor_error = max(float(np.sqrt(np.mean(np.sum((p-v) ** 2, axis=2))))
                       for p,v in ((projected, views_a[anchor_index]), (projected_b, views_b[anchor_index])))
    rect = cv2.stereoRectify(ka, da, kb, db, size, rab, tab)
    result = dict(version=VERSION, unit='m', coordinate_system='room_world',
                  calibration_validity='measured', calibration_timestamp=datetime.now(timezone.utc).isoformat(),
                  stereo_reprojection_error=float(error), anchor_reprojection_error=anchor_error,
                  quality=dict(max_reprojection_error_px=3., min_ray_angle_degrees=2., min_visibility=.5),
                  cameras=[dict(camera_id=cid, image_resolution=list(size), intrinsic_matrix=k.tolist(),
                                distortion_coefficients=d.ravel().tolist(), rotation=r.tolist(),
                                translation=t.ravel().tolist(), reprojection_error=float(e))
                           for cid, k, d, r, t, e in [('camera_a', ka, da, ra, ta, ea), ('camera_b', kb, db, rb, tb, eb)]],
                  stereo=dict(rotation=rab.tolist(), translation=tab.ravel().tolist(),
                              essential=essential.tolist(), fundamental=fundamental.tolist(),
                              rectification=[v.tolist() for v in rect[:5]]))
    return validate(result)


def solve(directory, columns, rows, square_m, anchor, origin=(0, 0, 0), yaw_degrees=0):
    import cv2
    obj = board_points(columns, rows, square_m)
    pairs = sorted(Path(directory).glob('*/pair.json'))
    objects, a, b, names, rejected = [], [], [], [], []
    size = None
    bindings = None
    for manifest in pairs:
        metadata = load_json(manifest)
        current = metadata.get('bindings')
        if not isinstance(current, list) or len(current) != 2 or not all(isinstance(v, str) and v for v in current) or current[0] == current[1]:
            raise ValueError('Capture manifest must identify two distinct inputs')
        if bindings is not None and bindings != current:
            raise ValueError('Camera bindings changed between captures; use a separate capture directory per installation')
        bindings = current
        images = [cv2.imdecode(np.frombuffer((manifest.parent / (cid + '.png')).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR) for cid in ('camera_a', 'camera_b')]
        if any(v is None for v in images):
            raise ValueError('Missing calibration image: ' + str(manifest.parent))
        shapes = [tuple(v.shape[1::-1]) for v in images]
        if shapes[0] != shapes[1] or (size is not None and size != shapes[0]):
            raise ValueError('All calibration images must have identical resolutions')
        size = shapes[0]
        try:
            found = [corners(cv2.cvtColor(v, cv2.COLOR_BGR2RGB), (columns, rows)) for v in images]
        except ValueError:
            rejected.append(manifest.parent.name)
            continue
        objects.append(obj.copy()); a.append(found[0]); b.append(found[1]); names.append(manifest.parent.name)
    if len(objects) < 10 or anchor not in names:
        raise ValueError(f'Need >=10 detected pairs and a detected floor anchor {anchor}; accepted={names}, rejected={rejected}')
    # First inner corner is the measured room position; board columns +X, rows +Z at yaw=0.
    theta = math.radians(yaw_degrees)
    rotation = np.array([[math.cos(theta), 0, math.sin(theta)], [0, 1, 0], [-math.sin(theta), 0, math.cos(theta)]])
    floor = np.column_stack((obj[:, 0], np.zeros(len(obj)), obj[:, 1]))
    world = (floor @ rotation.T + np.asarray(origin)).astype(np.float32)
    result = solve_samples(objects, a, b, size, names.index(anchor), world)
    result['capture'] = dict(bindings=bindings, accepted_pairs=names, rejected_pairs=rejected, anchor_pair=anchor,
                             inner_corners=[columns, rows], square_size_m=square_m,
                             anchor_origin_m=list(origin), anchor_yaw_degrees=yaw_degrees)
    return result


def main():
    p = argparse.ArgumentParser(description='Calibrate captured checkerboard pairs; anchor board must lie on room floor')
    p.add_argument('--images', type=Path, required=True)
    p.add_argument('--columns', type=int, required=True)
    p.add_argument('--rows', type=int, required=True)
    p.add_argument('--square-m', type=float, required=True)
    p.add_argument('--anchor', required=True, help='Captured pair directory name; first inner corner defines measured origin')
    p.add_argument('--origin', type=float, nargs=3, default=[0, 0, 0])
    p.add_argument('--yaw-degrees', type=float, default=0)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    try:
        result = solve(args.images, args.columns, args.rows, args.square_m, args.anchor, args.origin, args.yaw_degrees)
        if args.output.exists():
            raise ValueError('Output exists; choose a new filename to preserve calibration history')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.output, result)
        print(f'Saved {args.output}; stereo RMS={result["stereo_reprojection_error"]:.3f}px')
    except (ValueError, KeyError, OSError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    main()
