"""Edge-case matrix for provisional geometry; no real accuracy certification."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile,time,unittest
import numpy as np
from test_bed_posture_3d import bed,pose
from test_single_camera_preview import body,packet
from test_room_monitor import fixture
from bed_pose.experimental_preview import prepare,sanitize_pose,select_live_pose,monocular_preview
from bed_pose.preview_alignment import bed_transform,pose_in_display,cameras_in_display
from bed_pose.bed_posture_3d import bed_frame,classify,PostureFilter
from bed_pose.room_monitor import RoomMonitor
from bed_pose.io_utils import atomic_json,object_hash


class BedAlignmentTests(unittest.TestCase):
    def tilted_bed(self):
        a=.4;b=.6
        rx=np.array([[1,0,0],[0,np.cos(a),-np.sin(a)],[0,np.sin(a),np.cos(a)]])
        rz=np.array([[np.cos(b),-np.sin(b),0],[np.sin(b),np.cos(b),0],[0,0,1]])
        return bed_frame(np.array(bed()['corners'])@(rz@rx).T+[2,-1,-3],'camera_a_preview')

    def test_bed_plane_is_horizontal_centered_heading_and_metric_height_only_offset(self):
        b=self.tilted_bed();r,t=bed_transform(b,.65)
        p=np.array(b['corners'])@r.T+t
        np.testing.assert_allclose(p[:,1],.65,atol=1e-10)
        np.testing.assert_allclose(r@b['long'],[0,0,1],atol=1e-10)
        np.testing.assert_allclose(p.mean(0),[0,.65,0],atol=1e-10)
        self.assertAlmostEqual(np.linalg.norm(p[1]-p[0]),np.linalg.norm(np.array(b['corners'])[1]-np.array(b['corners'])[0]))
        self.assertAlmostEqual(np.linalg.det(r),1)

    def test_camera_projection_is_unchanged_by_common_rigid_transform(self):
        r,t=bed_transform(self.tilted_bed());c=prepare(fixture())['display_cameras']
        new=cameras_in_display(c,r,t);p=np.array([.3,-.2,-3])
        for old,changed in zip(c,new):
            np.testing.assert_allclose(np.array(old['rotation'])@p+old['translation'],np.array(changed['rotation'])@(r@p+t)+changed['translation'],atol=1e-10)

    def test_posture_relative_geometry_invariant_under_bed_transform(self):
        b=bed();r,t=bed_transform(b)
        displayed=bed_frame(np.array(b['corners'])@r.T+t,'bed_preview')
        for name in ('supine','prone','left_side','right_side','sitting','edge_sitting'):
            self.assertEqual(classify(pose_in_display(pose(name),r,t),displayed)['label'],name)

    def test_invalid_height_rejected(self):
        for height in (None,float('nan'),-1,0,9,True):
            with self.assertRaises(ValueError):bed_transform(bed(),height)

    def test_saved_bed_restores_and_status_levels_it_without_person(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);candidate=fixture();atomic_json(root/'guided-calibration-candidate-20.json',candidate)
            b=self.tilted_bed();atomic_json(root/'preview-bed.json',dict(candidate_hash=object_hash(candidate['cameras']),bed=b))
            atomic_json(root/'physical-measurements.json',{'bed_surface_height_m':.65})
            m=RoomMonitor(root);m.config['bindings']=['a','b'];m.set_preview({'enabled':True})
            sessions={i:SimpleNamespace(state={'closed':False,'phase':'preview'}) for i in ['a','b']}
            status=m.status(sessions);display=status['experimental_preview']
            self.assertEqual(display['alignment']['source'],'bed_plane')
            np.testing.assert_allclose(np.array(display['bed']['corners'])[:,1],.65)
            self.assertIsNone(display['skeleton']);self.assertIsNone(m.calibration)
            np.testing.assert_allclose(m.preview_bed['corners'],b['corners'])
            self.assertFalse((root/'calibration.json').exists())

    def test_corrupt_bed_file_does_not_disable_remaining_live_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);atomic_json(root/'guided-calibration-candidate-20.json',fixture())
            (root/'preview-bed.json').write_text('{broken',encoding='utf-8')
            m=RoomMonitor(root);m.set_preview({'enabled':True});self.assertTrue(m.preview_enabled);self.assertIsNone(m.preview_bed)


class PartialPostureTests(unittest.TestCase):
    def test_one_knee_all_lying_orientations_without_fabrication(self):
        for label in ('supine','prone','left_side','right_side'):
            p=pose(label);p['joints'][14]['valid']=False
            r=classify(p,bed());self.assertEqual(r['label'],label);self.assertEqual(r['evidence_level'],'partial')
            self.assertGreaterEqual(r['required_stable_seconds'],1.2)
            self.assertIn('one_knee_occluded',r['warnings']);self.assertFalse(p['joints'][14]['valid'])

    def test_both_knees_missing_requires_strong_torso_and_bed_support(self):
        p=pose('right_side')
        for i in (13,14):p['joints'][i]['valid']=False
        self.assertEqual(classify(p,bed())['label'],'right_side')
        for j in p['joints']:j['y']+=.5
        self.assertIsNone(classify(p,bed())['label'])

    def test_partial_sitting_keeps_foot_evidence_for_edge(self):
        for label in ('sitting','edge_sitting'):
            p=pose(label);p['joints'][14]['valid']=False
            self.assertEqual(classify(p,bed())['label'],label)
        for i in (15,16):p['joints'][i]['valid']=False
        self.assertIsNone(classify(p,bed())['label'])

    def test_invalid_float_duplicate_definition_and_low_confidence(self):
        for value in (float('nan'),float('inf'),None,True):
            p=pose('supine');p['joints'][5]['confidence']=value
            self.assertEqual(classify(p,bed())['status'],'held')
        p=pose('supine');p['joints'].append(deepcopy(p['joints'][5]))
        self.assertEqual(classify(p,bed())['reason'],'duplicate_joint_id')
        p=pose('supine');p['jointDefinition']='unknown'
        self.assertEqual(classify(p,bed())['reason'],'joint_definition_mismatch')

    def test_partial_candidate_needs_longer_dwell_and_missing_resets(self):
        p=pose('left_side');p['joints'][14]['valid']=False;r=classify(p,bed());f=PostureFilter()
        for stamp in (0,.3,.6,.9):self.assertEqual(f.update(r,stamp)['status'],'candidate')
        self.assertEqual(f.update(r,1.3)['status'],'stable')
        self.assertEqual(f.update(classify(None,bed()),1.4)['status'],'held')
        self.assertEqual(f.update(r,1.5)['status'],'candidate')

    def test_face_fallback_requires_consistent_visible_eyes(self):
        p=pose('prone');p['joints'][0]['valid']=False
        for i in (1,2):
            for k in ('x','y','z'):p['joints'][i][k]=pose('prone')['joints'][0][k]
        r=classify(p,bed());self.assertEqual(r['label'],'prone');self.assertIn('nose_occluded_using_eyes',r['warnings'])


class ObservationGuardTests(unittest.TestCase):
    def test_limb_spike_and_descendants_excluded(self):
        p=pose('supine');p['joints'][13]['x']=9
        r=sanitize_pose(p);self.assertFalse(r['joints'][13]['valid']);self.assertFalse(r['joints'][15]['valid'])
        self.assertTrue(p['joints'][13]['valid'])

    def test_finite_confidence_and_coordinate_checks(self):
        for key,value in [('x',float('nan')),('z',float('inf')),('confidence',1.3),('y',None)]:
            p=pose('supine');p['joints'][9][key]=value
            self.assertFalse(sanitize_pose(p)['joints'][9]['valid'])

    def test_duplicate_and_incomplete_packet_rejected(self):
        p=pose('supine');p['joints'][4]['id']=3;self.assertIsNone(sanitize_pose(p))
        p=pose('supine');p['joints'].pop();self.assertIsNone(sanitize_pose(p))
        p=packet();p['world_joints']=[];self.assertIsNone(monocular_preview(p,prepare(fixture()),0))

    def test_missing_stereo_torso_selects_complete_monocular(self):
        mono=monocular_preview(packet(),prepare(fixture()),0);stereo=deepcopy(mono)
        stereo.update(source='live_two_camera_triangulation',source_camera=None);stereo['joints'][11]['valid']=False
        self.assertEqual(select_live_pose(stereo,[mono])['source'],'single_camera_mediapipe_pnp')

    def test_future_time_multiple_people_and_bindings_change_clear_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            m=RoomMonitor(Path(temp));m.config['bindings']=['a','b'];m.preview_bindings=['a','b'];m.preview_enabled=True;m.preview_candidate=prepare(fixture())
            p=packet();p['mono_preview']=monocular_preview(p,m.preview_candidate,0)
            m.latest_observations=[p,None];m.refresh_preview();self.assertIsNotNone(m.preview_skeleton)
            p['people_count']=2;m.refresh_preview();self.assertIsNone(m.preview_skeleton);self.assertEqual(m.preview_reason,'multiple_people')
            p['people_count']=1;p['time_ns']=time.perf_counter_ns()+int(1e9);m.refresh_preview();self.assertIsNone(m.preview_skeleton)
            p['time_ns']=time.perf_counter_ns();m.config['bindings']=['new','b'];m.refresh_preview();self.assertIsNone(m.preview_skeleton)
