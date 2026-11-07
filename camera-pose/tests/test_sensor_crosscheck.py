import unittest
from bed_pose.sensor_crosscheck import compare


def sample(conf=.9):
    frame=dict(distances_mm=[1000]*64,received_at='2026-11-02T18:00:00.000')
    return dict(tof1=dict(frame),tof2=dict(frame),posture_raw='side_left',posture='empty',posture_conf=conf),dict(server_now='2026-11-02T18:00:00.100')


class CrosscheckTests(unittest.TestCase):
    def test_match_is_not_ground_truth(self):
        a,b=sample();r=compare(dict(label='left_side'),a,b)
        self.assertEqual(r['status'],'match');self.assertFalse(r['eligible_for_recording'])
        self.assertEqual(r['tof_filtered_label'],'empty')
    def test_conflict_preserves_camera(self):
        a,b=sample();r=compare(dict(label='right_side'),a,b)
        self.assertEqual(r['status'],'conflict');self.assertEqual(r['assisted_label'],'right_side')
    def test_low_confidence_not_used_as_fallback(self):
        a,b=sample(.4);r=compare(dict(label=None),a,b)
        self.assertEqual(r['status'],'low_confidence');self.assertIsNone(r['assisted_label'])
    def test_fallback_is_explicit(self):
        a,b=sample();r=compare(dict(label=None),a,b)
        self.assertIsNone(r['assisted_source']);self.assertIsNone(r['assisted_label']);self.assertFalse(r['accuracy_verified'])
    def test_stale_and_malformed_frames_do_not_validate(self):
        a,b=sample();b['server_now']='2026-11-02T18:00:10.000'
        self.assertEqual(compare(dict(label='left_side'),a,b)['status'],'unavailable')
        a,b=sample();a['tof1']['distances_mm']=[1]*16
        self.assertEqual(compare(dict(label='left_side'),a,b)['status'],'unavailable')


if __name__=='__main__':unittest.main()
