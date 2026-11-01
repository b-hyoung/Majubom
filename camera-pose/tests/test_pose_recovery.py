"""Recovery geometry, quality and transport contracts, not pose accuracy tests."""
from copy import deepcopy
import json,unittest
from unittest.mock import patch
import numpy as np
from bed_pose.pose.browser_gpu import BrowserGPUAdapter
from bed_pose.pose.recovery import quality,improves,proposals,restore
from bed_pose.contracts import FramePacket,ModelInput,AdapterCallError
from bed_pose.pose.mediapipe import COCO_INDICES
from bed_pose.pose.person_guard import supports_pose


def pose(visible=range(17),x=.4):
    image=[dict(x=x,y=.4,z=-.2,visibility=.1) for _ in range(33)]
    for i in visible:image[COCO_INDICES[i]]['visibility']=.9
    return dict(landmarks=[image],worldLandmarks=[[dict(x=.1,y=.2,z=.3,visibility=.9) for _ in range(33)]])


class RecoveryTests(unittest.TestCase):
    def frame(self,stamp=10,spec=None):
        return ModelInput(FramePacket('camera',7,np.zeros((480,640,3),np.uint8),spec or {}),stamp)

    def test_quality_ignores_missing_nonfinite_outside_and_low_visibility(self):
        p=pose();self.assertTrue(quality(p)['healthy'])
        p['landmarks'][0][11]['x']=float('nan');p['landmarks'][0][12]['x']=2
        p['landmarks'][0][23]['presence']=.1;p['landmarks'][0][24]['visibility']=.2
        q=quality(p);self.assertEqual(q['visible'],13);self.assertEqual(q['torso'],0);self.assertFalse(q['healthy'])
        self.assertEqual(quality({})['visible'],0)

    def test_alternative_must_improve_and_cannot_jump_to_distant_bystander(self):
        partial=quality(pose(range(5,13)))
        self.assertTrue(improves(quality(pose()),partial))
        self.assertFalse(improves(quality(pose(x=.8)),partial))
        self.assertFalse(improves(quality(pose(range(5))),quality({})))
        self.assertFalse(improves(partial,partial))

    def test_crop_rotation_restores_xy_width_depth_and_world_without_left_right_swap(self):
        original=pose();r=restore(original,1,(100,50,420,210),640,480)
        p=r['landmarks'][0][0];w=r['worldLandmarks'][0][0]
        np.testing.assert_allclose([p['x'],p['y'],p['z']],[(100+.6*320)/640,(50+.4*160)/480,-.05])
        np.testing.assert_allclose([w['x'],w['y'],w['z']],[-.2,.1,.3])
        self.assertEqual(original,pose())

    def test_bad_hint_and_tiny_crop_are_not_used_and_multi_person_keeps_full_frame(self):
        p=proposals(640,480,0,[0,0,float('nan'),100],[1,1,2,2])
        self.assertTrue(all(b is None or min(b[2]-b[0],b[3]-b[1])>=64 for _,b,_ in p))
        self.assertTrue(all(b is None for _,b,_ in proposals(640,480,0,allow_crops=False)))

    def test_acquisition_is_confirmed_then_healthy_tracking_has_no_extra_probe(self):
        a=BrowserGPUAdapter()
        with patch.object(a,'_rpc',return_value=pose()) as rpc:
            a.estimate(self.frame());self.assertEqual(rpc.call_count,2)
            a.estimate(self.frame(stamp=50))
        self.assertEqual(rpc.call_count,3);self.assertFalse(a.diagnostics['recovery_attempted'])
        a=BrowserGPUAdapter();partial=pose(range(5,13))
        with patch.object(a,'_rpc',side_effect=[partial,pose()]) as rpc:out=a.estimate(self.frame())
        self.assertEqual([c.kwargs.get('mode') for c in rpc.call_args_list[:2]],['video','probe'])
        self.assertEqual(out.model_timestamp_ms,10);self.assertEqual(out.candidates[0]['inference_recovery']['source_frame_id'],7)
        self.assertEqual(a.diagnostics['visible_joints'],17)

    def test_probe_failure_does_not_reset_tracking_or_invent_a_pose(self):
        a=BrowserGPUAdapter();empty=dict(landmarks=[],worldLandmarks=[])
        with patch.object(a,'_rpc',return_value=empty) as rpc:
            self.assertFalse(a.estimate(self.frame()).candidates)
        self.assertEqual([c.args[0] for c in rpc.call_args_list],['estimate','estimate'])

    def test_expired_crop_does_not_replay_old_coordinates(self):
        a=BrowserGPUAdapter();a.active_crop=(0,96,640,480);a.crop_until=0
        with patch.object(a,'_rpc',return_value=dict(landmarks=[],worldLandmarks=[])):
            out=a.estimate(self.frame(stamp=2000))
        self.assertFalse(out.candidates);self.assertEqual(a.diagnostics['visible_joints'],0)

    def test_changed_input_geometry_discards_old_crop_and_tracking(self):
        a=BrowserGPUAdapter();a.frame_context=('previous',320,240);a.active_crop=(0,0,200,100);a.rotation_turns=3
        with patch.object(a,'_rpc',side_effect=[None,pose(),pose()]) as rpc:a.estimate(self.frame())
        self.assertEqual(rpc.call_args_list[0].args[0],'reset');self.assertTrue(rpc.call_args_list[0].kwargs['all_tasks'])
        self.assertIsNone(a.active_crop);self.assertEqual(a.rotation_turns,0)

    def test_no_person_cannot_be_created_by_a_crop_only_false_positive(self):
        a=BrowserGPUAdapter();empty=dict(landmarks=[],worldLandmarks=[]);sizes=[]
        def rpc(op,**kw):
            if op=='reset':return None
            sizes.append((kw['width'],kw['height']))
            # Simulate a printed cartoon only producing a pose when cropped.
            return empty if kw['width']*kw['height']==640*480 else pose()
        with patch.object(a,'_rpc',side_effect=rpc):
            for i in range(24):self.assertFalse(a.estimate(self.frame(stamp=i*600,spec={'pose_region_hint':[100,50,500,400]})).candidates)
        self.assertTrue(all(w*h==640*480 for w,h in sizes));self.assertIsNone(a.active_crop)

    def test_crop_is_stateless_and_rejected_when_full_frame_evidence_disappears(self):
        a=BrowserGPUAdapter();a.active_crop=(0,96,640,480);a.crop_until=0
        with patch.object(a,'_rpc',side_effect=[pose(),dict(landmarks=[],worldLandmarks=[]),None]) as rpc:
            out=a.estimate(self.frame())
        self.assertEqual(rpc.call_args_list[0].kwargs['mode'],'probe')
        self.assertFalse(out.candidates);self.assertIsNone(a.active_crop)
        self.assertEqual(a.diagnostics['strategy'],'crop_rejected_no_full_frame_person')

    def test_video_only_false_positive_never_becomes_an_accepted_person(self):
        a=BrowserGPUAdapter();empty=dict(landmarks=[],worldLandmarks=[])
        def rpc(op,**kw):return pose() if kw.get('mode')=='video' else empty
        with patch.object(a,'_rpc',side_effect=rpc):
            for i in range(80):self.assertFalse(a.estimate(self.frame(stamp=i*40)).candidates)
        self.assertIsNone(a.support_stamp)

    def test_stale_video_tracking_expires_without_independent_image_support(self):
        a=BrowserGPUAdapter();a.support_stamp=0;a.support_center=[.4,.4]
        with patch.object(a,'_rpc',side_effect=[pose(),dict(landmarks=[],worldLandmarks=[]),None]) as rpc:
            out=a.estimate(self.frame(stamp=1000))
        self.assertFalse(out.candidates);self.assertIsNone(a.support_stamp)
        self.assertEqual(a.diagnostics['strategy'],'full_rejected_no_image_person')
        self.assertEqual(rpc.call_args_list[1].kwargs['mode'],'probe')

    def test_configured_person_guard_rejects_high_scoring_unconfirmed_joints(self):
        class Guard:
            diagnostics={'confirmed':False}
            def confirm(self,*args):return False
        a=BrowserGPUAdapter();a.person_guard=Guard()
        with patch.object(a,'_rpc',return_value=pose()) as rpc:out=a.estimate(self.frame())
        self.assertFalse(out.candidates);self.assertEqual(a.diagnostics['visible_joints'],0)
        self.assertFalse(a.diagnostics['person_evidence']['confirmed']);self.assertEqual(rpc.call_count,2)

    def test_person_box_support_requires_torso_center_and_half_of_pose_bounds(self):
        q=quality(pose());q['box']=[.2,.2,.6,.6]
        self.assertFalse(supports_pose([],q,640,480))
        self.assertTrue(supports_pose([[100,50,450,350]],q,640,480))
        self.assertFalse(supports_pose([[250,180,260,200]],q,640,480))
        self.assertFalse(supports_pose([[400,300,600,450]],q,640,480))


class Reply:
    status=200
    def read(self):return json.dumps(pose()).encode()


class Connection:
    def __init__(self,fail=False):self.fail=fail;self.calls=[];self.closed=False
    def request(self,*args,**kwargs):self.calls.append((args,kwargs))
    def getresponse(self):
        if self.fail:raise ConnectionAbortedError('injected dropped keep-alive')
        return Reply()
    def close(self):self.closed=True


class TransportRecoveryTests(unittest.TestCase):
    def adapter(self):
        a=BrowserGPUAdapter();a.inference_url='http://127.0.0.1:12345/private/infer';return a
    def frame(self,t=10):return ModelInput(FramePacket('camera',1,np.zeros((40,80,3),np.uint8),{}),t)

    def test_dropped_http_reconnects_once_with_new_sdk_time_same_external_frame(self):
        a=self.adapter();first=Connection(True);second=Connection();a.inference_connection=first
        with patch('bed_pose.pose.browser_gpu.http.client.HTTPConnection',return_value=second),patch.object(a,'_rpc') as rpc:
            out=a.estimate(self.frame())
        self.assertTrue(first.closed);self.assertEqual(a.transport_errors,1);rpc.assert_not_called()
        self.assertEqual(out.model_timestamp_ms,10)
        self.assertGreater(int(second.calls[0][1]['headers']['X-Timestamp']),int(first.calls[0][1]['headers']['X-Timestamp']))

    def test_two_http_failures_use_same_gpu_private_pipe_and_bound_retries(self):
        a=self.adapter();first=Connection(True);second=Connection(True);a.inference_connection=first
        with patch('bed_pose.pose.browser_gpu.http.client.HTTPConnection',return_value=second) as constructor,patch.object(a,'_rpc',return_value=pose()) as rpc:
            first_out=a.estimate(self.frame());second_out=a.estimate(self.frame(11))
        self.assertEqual(constructor.call_count,1);self.assertEqual(a.transport_errors,2);self.assertEqual(a.transport_fallbacks,1)
        self.assertEqual(rpc.call_count,3);self.assertGreater(rpc.call_args_list[1].kwargs['timestamp'],rpc.call_args_list[0].kwargs['timestamp'])
        self.assertEqual([first_out.model_timestamp_ms,second_out.model_timestamp_ms],[10,11])

    def test_actual_gpu_error_is_not_hidden_by_transport_fallback(self):
        class BadReply:
            status=500
            def read(self):return b'actual GPU unavailable'
        a=self.adapter();a.inference_connection=Connection()
        with patch.object(a.inference_connection,'getresponse',return_value=BadReply()),patch.object(a,'_rpc') as rpc:
            with self.assertRaisesRegex(AdapterCallError,'actual GPU unavailable'):a.estimate(self.frame())
        rpc.assert_not_called();self.assertEqual(a.transport_fallbacks,0)
