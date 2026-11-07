"""Unverified preview stays separate from metric room calibration and storage."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
import numpy as np
from test_room_monitor import fixture, projected_pair
from bed_pose.experimental_preview import prepare, reconstruct_preview
from bed_pose.room_monitor import RoomMonitor
from bed_pose.io_utils import atomic_json


class PreviewTests(unittest.TestCase):
    def test_observed_geometry_and_missing_joints_without_measured_claim(self):
        cal=fixture();cal['coordinate_system']='target_world';cal['room_alignment_verified']=False
        candidate=prepare(cal)
        xyz=np.array([[.1+i*.006,.2+i*.003,3+i*.01] for i in range(17)])
        pair=projected_pair(cal,xyz);pair[1]['joints'][3]=None
        pose=reconstruct_preview(pair,candidate)
        actual=[[j['x'],j['y'],j['z']] for j in pose['joints'] if j['valid']]
        np.testing.assert_allclose(actual,np.delete(xyz,3,axis=0)*[1,-1,-1],atol=1e-6)
        self.assertFalse(pose['joints'][3]['valid'])
        self.assertEqual(pose['source'],'live_two_camera_triangulation')
        self.assertEqual(pose['coordinateSystem'],'camera_a_preview')
        self.assertFalse(pose['eligible_for_recording']);self.assertFalse(pose['accuracy_verified'])
        self.assertNotEqual(pose['unit'],'m')

    def test_reprojection_rejection_and_malformed_candidates(self):
        cal=fixture();candidate=prepare(cal)
        xyz=np.array([[.1,.2,3.]]*17);pair=projected_pair(cal,xyz)
        pair[1]['joints'][0][1]+=150
        self.assertFalse(reconstruct_preview(pair,candidate)['joints'][0]['valid'])
        for mutation in [lambda c:c['cameras'][0].update(intrinsic_matrix=[[float('nan')]*3]*3),
                         lambda c:c['cameras'][0].update(rotation=[[1]*3]*3),
                         lambda c:c['cameras'][1].update(translation=[0,0,0])]:
            bad=deepcopy(cal);mutation(bad)
            with self.assertRaises(ValueError):prepare(bad)

    def test_preview_does_not_install_and_stale_or_disconnected_input_clears_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);atomic_json(root/'guided-calibration-candidate-20.json',fixture())
            monitor=RoomMonitor(root);monitor.config['bindings']=['a','b']
            monitor.set_preview({'enabled':True})
            self.assertIsNone(monitor.calibration)
            self.assertFalse((root/'calibration.json').exists())
            sessions={x:SimpleNamespace(state={'closed':False,'phase':'preview'}) for x in ['a','b']}
            monitor.latest_pair=[dict(time_ns=time.perf_counter_ns(),frame_id=1) for _ in range(2)]
            monitor.preview_reason=None;monitor.preview_skeleton={'source':'live_two_camera_triangulation','joints':[]}
            monitor.preview_stamp=time.perf_counter_ns()
            self.assertIsNotNone(monitor.status(sessions)['experimental_preview']['skeleton'])
            self.assertIsNone(monitor.status(sessions)['skeleton'])
            sessions['b'].state['closed']=True
            self.assertIsNone(monitor.status(sessions)['experimental_preview']['skeleton'])
            sessions['b'].state['closed']=False
            for p in monitor.latest_pair:p['time_ns']-=int(2e9)
            monitor.preview_stamp-=int(2e9)
            self.assertIsNone(monitor.status(sessions)['experimental_preview']['skeleton'])
            monitor.set_preview({'enabled':False})
            self.assertFalse(monitor.status(sessions)['experimental_preview']['enabled'])

    def test_preview_requires_explicit_enable_and_valid_saved_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            monitor=RoomMonitor(Path(temp))
            self.assertFalse(monitor.preview_enabled)
            with self.assertRaises(ValueError):monitor.set_preview({'enabled':1})
            with self.assertRaises(ValueError):monitor.set_preview({'enabled':True})
