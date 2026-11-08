"""GPU rotation recovery contracts and coordinate inversion, not accuracy scores."""
from copy import deepcopy
from unittest.mock import patch
import unittest
import numpy as np
from bed_pose.pose.rotation_rescue import restore_result
from bed_pose.pose.browser_gpu import BrowserGPUAdapter
from bed_pose.contracts import FramePacket,ModelInput


def result():
    return dict(landmarks=[[dict(x=.2,y=.3,z=-.1,visibility=.9) for _ in range(33)]],
                worldLandmarks=[[dict(x=.1,y=.2,z=.3,visibility=.9) for _ in range(33)]])


class RotationRescueTests(unittest.TestCase):
    def test_inverse_all_quarter_turns_and_width_normalized_depth(self):
        original=result()
        expected={1:(.7,.2,-.075,-.2,.1),2:(.8,.7,-.1,-.1,-.2),3:(.3,.8,-.075,.2,-.1)}
        for turn,values in expected.items():
            r=restore_result(original,turn,640,480);p=r['landmarks'][0][0];w=r['worldLandmarks'][0][0]
            np.testing.assert_allclose([p['x'],p['y'],p['z'],w['x'],w['y']],values)
            self.assertEqual(w['z'],.3);self.assertEqual(p['visibility'],.9)
        self.assertEqual(original,result())

    def test_four_inverse_rotations_return_original_without_identity_swap(self):
        r=result()
        for i in range(4):r=restore_result(r,1,640 if i%2==0 else 480,480 if i%2==0 else 640)
        for key in ('landmarks','worldLandmarks'):
            for k in ('x','y','z'):self.assertAlmostEqual(r[key][0][0][k],result()[key][0][0][k])

    def test_failed_frame_retries_once_and_latches_then_restores_original_resolution(self):
        a=BrowserGPUAdapter();calls=[]
        def rpc(op,**kw):
            calls.append((op,kw))
            if op=='reset':return None
            return {'landmarks':[],'worldLandmarks':[]} if len([c for c in calls if c[0]=='estimate'])==1 else result()
        frame=FramePacket('B',0,np.zeros((480,640,3),np.uint8),{})
        with patch.object(a,'_rpc',side_effect=rpc):
            out=a.estimate(ModelInput(frame,10))
            self.assertEqual(a.rotation_turns,1)
            estimates=[kw for op,kw in calls if op=='estimate'];self.assertEqual(len(estimates),2)
            self.assertEqual((estimates[1]['width'],estimates[1]['height']),(480,640))
            self.assertGreater(estimates[1]['timestamp'],estimates[0]['timestamp'])
            point=out.candidates[0]['sdk_landmarks']['image'][0]
            self.assertAlmostEqual(point['x'],.7);self.assertAlmostEqual(point['y'],.2)
            self.assertEqual(out.model_timestamp_ms,10)
            self.assertEqual(out.candidates[0]['inference_rotation_ccw_degrees'],90)
            before=len(calls);a.estimate(ModelInput(frame,43))
            self.assertEqual(len(calls),before+1)

    def test_no_success_does_not_invent_people_and_limits_retry_frequency(self):
        a=BrowserGPUAdapter();calls=[]
        def rpc(op,**kw):
            calls.append(op);return None if op=='reset' else {'landmarks':[],'worldLandmarks':[]}
        frame=FramePacket('A',0,np.zeros((4,8,3),np.uint8),{})
        with patch.object(a,'_rpc',side_effect=rpc):
            self.assertFalse(a.estimate(ModelInput(frame,0)).candidates)
            self.assertEqual(calls.count('estimate'),2)
            self.assertFalse(a.estimate(ModelInput(frame,33)).candidates)
            self.assertEqual(calls.count('estimate'),3)
            self.assertFalse(a.estimate(ModelInput(frame,600)).candidates)
            self.assertEqual(calls.count('estimate'),5)

    def test_tracking_reset_clears_rotated_state(self):
        a=BrowserGPUAdapter();a.rotation_turns=3;a.last_rotation_probe=5;a.bridge_timestamp=99
        with patch.object(a,'_rpc',return_value=None):a.reset_for_video()
        self.assertEqual(a.rotation_turns,0);self.assertIsNone(a.last_rotation_probe);self.assertEqual(a.bridge_timestamp,-1)
