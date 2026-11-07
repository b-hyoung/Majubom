"""Projected synthetic geometry checks, not human posture accuracy tests."""
from dataclasses import replace
from copy import deepcopy
import unittest
import numpy as np
import cv2
from bed_pose.bed_leg_geometry import inspect, hanging_reason
from bed_pose.camera_posture import LivePostureEstimator
from test_viewpoint_posture import scene


def inspect_scene(obs):
    return inspect(obs.points, obs.world_points, obs.bed_rectangle, obs.width, obs.height)


def folded_legs():
    obs = scene('edge_sitting', tilt=25, yaw=30)
    q = cv2.Rodrigues(np.array([.2, -.4, .3]))[0]
    xyz = (np.asarray(obs.world_points)-[.3, -.1, .2]) @ q
    xyz[13] = [.16, 1.4, -.12];xyz[14] = [-.16, 1.4, -.12]
    xyz[15] = [-.1, 1.3, -.12];xyz[16] = [.1, 1.3, -.12]
    r = cv2.Rodrigues(np.array([0., np.radians(30), 0.]))[0] @ cv2.Rodrigues(np.array([np.radians(25), 0., 0.]))[0]
    k = np.array([[1000., 0, 500], [0, 1000., 500], [0, 0, 1]])
    pixels = cv2.projectPoints(xyz, cv2.Rodrigues(r)[0], np.array([0., -.2, 5.]), k, None)[0].reshape(-1, 2)
    world = xyz @ q.T + [.3, -.1, .2]
    return replace(obs, points=tuple(map(tuple, pixels)), world_points=tuple(map(tuple, world)))


class BedLegGeometryTests(unittest.TestCase):
    def test_observed_hanging_legs_with_inward_hip_are_not_rejected(self):
        features=dict(torso_up_cos=.9854,hip_edge_fraction=.3981,
            legs=[dict(knee_degrees=111.64,thigh_normal_cos=.0769,calf_down_cos=.9542,ankle_drop_torso=.9045),
                  dict(knee_degrees=122.35,thigh_normal_cos=.1401,calf_down_cos=.9094,ankle_drop_torso=.9537)])
        self.assertIsNone(hanging_reason(features))
        # Moving the entire seated shape off the bed still does not qualify.
        features['hip_edge_fraction']=-.4
        self.assertEqual(hanging_reason(features),'hip_outside_bed')
    def test_hanging_legs_across_camera_rotations(self):
        for roll, tilt, yaw in ((0, 25, 30), (90, 25, 30), (170, 55, 30), (0, 0, 0)):
            with self.subTest(roll=roll, tilt=tilt):
                r = inspect_scene(scene('edge_sitting', roll, tilt, yaw=yaw))
                self.assertTrue(r['supported'], r)
                self.assertFalse(r['physical_height_measured'])
                self.assertFalse(r['eligible_for_recording'])
                self.assertGreater(r['features']['legs'][0]['ankle_drop_torso'], .25)

    def test_lying_standing_and_folded_legs_are_not_hanging(self):
        for obs in (scene('supine', tilt=25), scene('standing', tilt=25), folded_legs()):
            r = inspect_scene(obs)
            self.assertFalse(r['supported'], r)
            self.assertEqual(r['status'], 'observed')

    def test_missing_bed_world_or_ankles_never_invents_geometry(self):
        obs = scene('edge_sitting', tilt=25)
        variants = [replace(obs, bed_rectangle=None), replace(obs, world_points=None)]
        points = list(obs.points); points[15] = None
        variants.append(replace(obs, points=tuple(points)))
        for variant in variants:
            r = inspect_scene(variant)
            self.assertFalse(r['supported'])
            self.assertEqual(r['status'], 'held')

    def test_missing_head_does_not_block_observed_torso_and_legs(self):
        obs=scene('edge_sitting',tilt=25,yaw=30)
        points=list(obs.points)
        for i in range(5):points[i]=None
        r=inspect_scene(replace(obs,points=tuple(points)))
        self.assertTrue(r['supported'],r)

    def test_bad_image_model_correspondence_is_rejected(self):
        obs = scene('edge_sitting', tilt=25)
        points = list(obs.points); points[7] = (50., 60.); points[8] = (900., 900.)
        r = inspect_scene(replace(obs, points=tuple(points)))
        self.assertEqual(r['reason'], 'model_registration_uncertain')

    def test_live_display_uses_leg_geometry_and_preserves_other_states(self):
        obs = scene('edge_sitting', tilt=25, yaw=30)
        packet = dict(time_ns=1_000_000_000, frame_id=1, input_id='A', source_camera='A',
            track_id='one', processing_ok=True, people_count=1, width=obs.width, height=obs.height,
            joints=[[*point, .9] for point in obs.points], world_joints=obs.world_points,
            bed_region=obs.bed_rectangle, image_body_state=dict(status='predicted',
                scores=dict(lying=.02, sitting=.94, standing=.02, other=.02)))
        r = LivePostureEstimator().update([packet, None], None, None, 1_010_000_000)
        self.assertEqual(r['label'], 'edge_sitting')
        self.assertTrue(r['views'][0]['leg_geometry']['supported'])
        self.assertFalse(r['eligible_for_recording'])
        for state in ('standing', 'other'):
            p = deepcopy(packet)
            p['image_body_state']['scores'] = {key: .94 if key == state else .02
                                              for key in ('lying', 'sitting', 'standing', 'other')}
            self.assertEqual(LivePostureEstimator().update([p, None], None, None, 1_010_000_000)['label'], state)
        folded = folded_legs()
        p = deepcopy(packet)
        p.update(joints=[[*point, .9] for point in folded.points], world_joints=folded.world_points,
                 bed_region=folded.bed_rectangle)
        from bed_pose.separated_posture import bed_relation
        from bed_pose.automatic_posture import FORCED_DEFAULTS
        self.assertEqual(bed_relation(folded.points, folded.bed_rectangle, FORCED_DEFAULTS,
                                     folded.width, folded.height)['relation'], 'edge')
        r = LivePostureEstimator().update([p, None], None, None, 1_010_000_000)
        self.assertEqual(r['label'], 'sitting')
        self.assertFalse(r['views'][0]['leg_geometry']['supported'])


if __name__ == '__main__':
    unittest.main()
