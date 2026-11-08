"""Bed-normal leg evidence from model 3D; no metric room calibration is implied."""
import math
import numpy as np
from .automatic_posture import AUTO_DEFAULTS
from .bed_geometry import relative_xy, contains
from .viewpoint_posture import bed_camera

METHOD = 'bed-leg-model-0.3'
POLICY = dict(torso_up_min=.65, thigh_normal_max=.55, calf_down_min=.65,
              ankle_drop_torso_min=.25, knee_min=65., knee_max=135.,
              hip_outside_max=.15, reprojection_torso_max=.08)


def hanging_reason(features):
    """An inward hip is valid: sitting with thighs extending toward the edge."""
    legs = features['legs']
    if features['torso_up_cos'] < POLICY['torso_up_min']:
        return 'torso_not_upright'
    if features['hip_edge_fraction'] < -POLICY['hip_outside_max']:
        return 'hip_outside_bed'
    if not all(POLICY['knee_min'] <= leg['knee_degrees'] <= POLICY['knee_max'] for leg in legs):
        return 'knees_not_bent_for_sitting'
    if not all(leg['thigh_normal_cos'] <= POLICY['thigh_normal_max'] for leg in legs):
        return 'thighs_not_along_bed'
    if not all(leg['calf_down_cos'] >= POLICY['calf_down_min']
               and leg['ankle_drop_torso'] >= POLICY['ankle_drop_torso_min'] for leg in legs):
        return 'calves_not_hanging_down'
    return None


def inspect(points, world, region, width, height):
    result = dict(method=METHOD, status='held', supported=False, on_bed_supported=False, reason=None,
                  accuracy_verified=False, eligible_for_recording=False,
                  physical_height_measured=False, features={}, policy=dict(POLICY))
    def held(reason):
        result['reason'] = reason
        return result
    if region is None:
        return held('bed_region_required')
    if world is None:
        return held('model_3d_unavailable')
    if len(points) != 17 or len(world) != 17:
        return held('model_3d_unavailable')
    core = (5, 6, 11, 12, 13, 14, 15, 16)
    if any(points[i] is None for i in core):
        return held('lower_body_joints_missing')
    try:
        import cv2
        xyz = np.asarray(world, dtype=np.float64)
        if xyz.shape != (17, 3) or not np.isfinite(xyz).all():
            return held('model_3d_unavailable')
        k, normal, registration = bed_camera(region, width, height, AUTO_DEFAULTS)
        indices = [i for i, point in enumerate(points) if point is not None]
        pixels = np.asarray([points[i] for i in indices], dtype=np.float64)
        if pixels.shape != (len(indices), 2) or not np.isfinite(pixels).all():
            return held('invalid_image_joints')
        shoulder_px = np.mean([points[5], points[6]], axis=0)
        hip_px = np.mean([points[11], points[12]], axis=0)
        torso_px = float(np.linalg.norm(shoulder_px - hip_px))
        if torso_px < 4:
            return held('torso_projection_too_small')
        ok, rv, tv = cv2.solvePnP(xyz[indices], pixels, k, None, flags=cv2.SOLVEPNP_SQPNP)
        if not ok:
            return held('model_registration_uncertain')
        rv, tv = cv2.solvePnPRefineLM(xyz[indices], pixels, k, None, rv, tv)
        camera = xyz @ cv2.Rodrigues(rv)[0].T + tv.reshape(3)
        projected = cv2.projectPoints(xyz[indices], rv, tv, k, None)[0].reshape(-1, 2)
        error = float(np.sqrt(np.mean(np.sum((projected - pixels)**2, axis=1))) / torso_px)
        result['registration'] = dict(registration, reprojection_torso_fraction=error)
        if (not np.isfinite(camera).all() or np.min(camera[:, 2]) <= 0
                or error > POLICY['reprojection_torso_max']):
            return held('model_registration_uncertain')
        shoulders = (camera[5] + camera[6]) / 2
        hips = (camera[11] + camera[12]) / 2
        torso = shoulders - hips
        length = float(np.linalg.norm(torso))
        if not .08 < length < 2:
            return held('degenerate_torso')
        uv = relative_xy(region, hip_px)
        if uv is None:
            return held('bed_coordinate_unavailable')
        edge = float(min(*uv, 1-uv[0], 1-uv[1]))
        legs = []
        for hip, knee, ankle, side in ((11, 13, 15, 'left'), (12, 14, 16, 'right')):
            thigh = camera[knee] - camera[hip]
            calf = camera[ankle] - camera[knee]
            thigh_length, calf_length = np.linalg.norm(thigh), np.linalg.norm(calf)
            if min(thigh_length, calf_length) < length * .15:
                return held('degenerate_legs')
            angle = math.degrees(math.acos(float(np.clip(
                np.dot(-thigh, calf)/(thigh_length*calf_length), -1, 1))))
            legs.append(dict(side=side, knee_degrees=angle,
                thigh_normal_cos=float(abs(np.dot(thigh, normal))/thigh_length),
                calf_down_cos=float(-np.dot(calf, normal)/calf_length),
                ankle_drop_torso=float(np.dot(camera[hip]-camera[ankle], normal)/length)))
        upright = float(np.dot(torso, normal)/length)
        result['features'] = dict(torso_up_cos=upright, hip_edge_fraction=edge, legs=legs,
            drop_reference='hip_relative_along_estimated_bed_normal', distance_unit='torso_ratio')
        result['status'] = 'observed'
        result['on_bed_supported'] = bool(upright >= POLICY['torso_up_min']
            and edge >= -POLICY['hip_outside_max']
            and all(contains(region, points[i]) for i in (13,14,15,16))
            and all(abs(leg['ankle_drop_torso']) <= POLICY['ankle_drop_torso_min'] for leg in legs))
        reason = hanging_reason(result['features'])
        if reason:
            return held(reason)
        result.update(supported=True, reason=None)
        return result
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, np.linalg.LinAlgError):
        return held('bed_model_geometry_uncertain')
    except cv2.error:
        return held('model_registration_uncertain')
