"""Protocol checks and injected values must never certify camera accuracy or empty-bed calibration."""
import unittest
from datetime import datetime,timedelta
from bed_pose.tof_learning.check import compare,stability,canonical
from test_tof_learning import camera,sensor


def pi():
    stamp=datetime(2026,11,1,20)
    latest=dict(posture_raw='supine',posture_conf=.9,**{s:dict(received_at=stamp.isoformat()) for s in ('tof1','tof2')})
    return latest,dict(server_now=(stamp+timedelta(seconds=.1)).isoformat(),in_bed=True)


class ConnectionCheckTests(unittest.TestCase):
    def test_aliases_preserve_empty_and_ambiguous_lying_is_not_supine(self):
        self.assertEqual(canonical('side_left'),'left_side');self.assertEqual(canonical('empty'),'empty')
        self.assertIsNone(canonical('lying'))

    def test_temporary_input_is_separate_from_camera_prediction_and_training(self):
        latest,presence=pi();c=camera();c['posture_3d']['status']='held'
        result=compare(c,latest,presence,'supine')
        self.assertIsNone(result['independent_comparison']['match'])
        self.assertTrue(result['temporary_input_check']['match'])
        self.assertFalse(result['temporary_input_check']['accuracy_verified'])
        self.assertFalse(result['temporary_input_check']['training_sample_saved'])
        self.assertEqual(c['posture_3d']['status'],'held')

    def test_stale_sensor_or_missing_pi_clock_never_counts_as_agreement(self):
        latest,presence=pi();presence['server_now']='2026-11-01T20:00:10'
        self.assertIsNone(compare(camera(),latest,presence,'supine')['temporary_input_check']['match'])
        self.assertIsNone(compare(camera(),latest,{},'supine')['independent_comparison']['match'])

    def test_independent_matching_predictions_are_not_ground_truth(self):
        latest,presence=pi();r=compare(camera(),latest,presence)
        self.assertTrue(r['independent_comparison']['match']);self.assertFalse(r['independent_comparison']['accuracy_verified'])
        latest['posture_raw']='side_right'
        self.assertFalse(compare(camera(),latest,presence)['independent_comparison']['match'])

    def test_stationary_person_cannot_become_empty_baseline(self):
        frames=[sensor('tof1',100+i*.5,i) for i in range(20)]
        result=stability(frames)
        self.assertTrue(result['stationary']);self.assertFalse(result['empty_bed_verified']);self.assertFalse(result['baseline_apply_allowed'])
        self.assertFalse(stability([])['stationary'])
