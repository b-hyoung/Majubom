"""Synthetic mono registration and axis invariants, never physical calibration evidence."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile,time,unittest
import numpy as np
from test_room_monitor import fixture,projected_pair
from bed_pose.experimental_preview import prepare,monocular_preview,select_live_pose
from bed_pose.preview_alignment import reference,rotation,pose_in_display,cameras_in_display,tilt_reference
from bed_pose.room_monitor import RoomMonitor


def body():
    p=np.random.default_rng(42).normal(0,.15,(17,3))
    p[5]=[.2,-.5,.02];p[6]=[-.2,-.5,-.02];p[11]=[.13,0,0];p[12]=[-.13,0,0]
    p[13]=[.13,.4,.1];p[14]=[-.13,.4,.08];p[15]=[.13,.8,.08];p[16]=[-.13,.8,.05]
    return p


def packet(slot=0):
    cal=fixture();p=body();pair=projected_pair(cal,p+[0,0,3])
    return dict(joints=pair[slot]['joints'],world_joints=p.tolist(),people_count=1,processing_ok=True,
                frame_id=7,time_ns=time.perf_counter_ns(),source_kind='camera',width=640,height=480)


class SinglePreviewTests(unittest.TestCase):
    def test_manual_pitch_uses_common_rotation_and_reports_camera_disagreement(self):
        cameras=prepare(fixture())['display_cameras']
        up,info=tilt_reference([37,27],cameras)
        np.testing.assert_allclose(up,[0,np.cos(np.radians(37)),np.sin(np.radians(37))],atol=1e-8)
        np.testing.assert_allclose(rotation(up)@up,[0,1,0],atol=1e-8)
        self.assertAlmostEqual(info['up_disagreement_degrees'],10)
        self.assertFalse(info['accuracy_verified'])
        for invalid in ([90,27],[37],['37',27],[float('nan'),27]):
            with self.assertRaises(ValueError):tilt_reference(invalid,cameras)

    def test_mono_a_and_b_recover_same_display_frame_from_model_shape(self):
        c=prepare(fixture());expected=(body()+[0,0,3])*[1,-1,-1]
        for slot in (0,1):
            pose=monocular_preview(packet(slot),c,slot)
            self.assertIsNotNone(pose)
            actual=np.array([[j['x'],j['y'],j['z']] for j in pose['joints']])
            np.testing.assert_allclose(actual,expected,atol=1e-4)
            self.assertEqual(pose['source_camera'],'AB'[slot]);self.assertTrue(pose['depth_is_model_estimate'])
            self.assertFalse(pose['eligible_for_recording'])

    def test_missing_body_model_or_insufficient_observations_do_not_invent_3d(self):
        c=prepare(fixture())
        for change in [dict(world_joints=None),dict(people_count=2),dict(processing_ok=False),dict(joints=[None]*17)]:
            p=packet();p.update(change);self.assertIsNone(monocular_preview(p,c,0))
        p=packet();p['joints'][5]=None;self.assertIsNone(monocular_preview(p,c,0))

    def test_better_coverage_mono_vs_stereo_selection(self):
        mono=monocular_preview(packet(),prepare(fixture()),0)
        stereo=deepcopy(mono);stereo.update(source='live_two_camera_triangulation',source_camera=None)
        self.assertEqual(select_live_pose(stereo,[mono])['source'],'live_two_camera_triangulation')
        for j in stereo['joints'][5:]:j['valid']=False
        self.assertEqual(select_live_pose(stereo,[mono])['source'],'single_camera_mediapipe_pnp')
        self.assertIsNone(select_live_pose(None,[None,None]))

    def test_one_active_camera_without_synced_pair_stale_disconnect_and_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            m=RoomMonitor(Path(temp));m.config['bindings']=['a','b'];m.preview_bindings=['a','b'];m.preview_enabled=True
            m.preview_candidate=prepare(fixture());p=packet();p['mono_preview']=monocular_preview(p,m.preview_candidate,0)
            m.latest_observations=[p,None];m.refresh_preview()
            sessions={'a':SimpleNamespace(state={'closed':False,'phase':'preview'}),'b':SimpleNamespace(state={'closed':True,'phase':'closed'})}
            self.assertIsNone(m.latest_pair)
            self.assertEqual(m.status(sessions)['experimental_preview']['skeleton']['source_camera'],'A')
            self.assertIsNone(m.status(sessions)['skeleton'])
            sessions['a'].state['closed']=True
            self.assertIsNone(m.status(sessions)['experimental_preview']['skeleton'])
            sessions['a'].state['closed']=False;m.preview_stamp-=int(1e9)
            self.assertIsNone(m.status(sessions)['experimental_preview']['skeleton'])
            p['time_ns']-=int(1e9);m.refresh_preview();self.assertIsNone(m.preview_skeleton)

    def test_user_axis_references_rotate_all_geometry_consistently(self):
        up=np.array([.2,.95,.1]);up/=np.linalg.norm(up)
        base=monocular_preview(packet(),prepare(fixture()),0)
        history=[]
        for i in range(10):
            pose=deepcopy(base)
            for j in pose['joints']:
                if j['id'] in (5,6):j.update(x=float(up[0]),y=float(up[1]),z=float(up[2]))
                if j['id'] in (11,12):j.update(x=0.,y=0.,z=0.)
            history.append((10+i*.05,pose))
        actual=reference(history,'standing',10.5);r=rotation(actual)
        np.testing.assert_allclose(r@up,[0,1,0],atol=1e-8);self.assertAlmostEqual(np.linalg.det(r),1)
        aligned=pose_in_display(base,r);self.assertEqual(aligned['coordinateSystem'],'camera_a_preview_aligned')
        cameras=prepare(fixture())['display_cameras'];changed=cameras_in_display(cameras,r)
        point=np.array([.1,.2,-3.]);old=cameras[1];new=changed[1]
        np.testing.assert_allclose(np.array(old['rotation'])@point+old['translation'],np.array(new['rotation'])@(r@point)+new['translation'],atol=1e-8)
        with self.assertRaises(ValueError):reference(history,'lying',10.5,actual)
        with self.assertRaises(ValueError):reference(history,'standing',20.)
        with self.assertRaises(ValueError):reference(history[:2],'standing',10.5)

    def test_lying_reference_fixes_heading_after_upright_reference(self):
        up=np.array([.2,.95,.1]);up/=np.linalg.norm(up)
        long=np.array([.6,0,.8]);long-=np.dot(long,up)*up;long/=np.linalg.norm(long)
        base=monocular_preview(packet(),prepare(fixture()),0);history=[]
        for i in range(10):
            p=deepcopy(base)
            for j in p['joints']:
                if j['id'] in (5,6):j.update(zip(('x','y','z'),(-long).tolist()))
                if j['id'] in (11,12):j.update(x=0.,y=0.,z=0.)
            history.append((10+i*.05,p))
        heading=reference(history,'lying',10.5,up);r=rotation(up,heading)
        np.testing.assert_allclose(r@up,[0,1,0],atol=1e-8)
        np.testing.assert_allclose(r@long,[0,0,1],atol=1e-8)
        with self.assertRaises(ValueError):reference(history,'lying',10.5)
