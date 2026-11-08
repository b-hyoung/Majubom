"""Synthetic geometry, background exclusion, and a fake monotonic clock; no live calibration."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
from bed_pose.automatic_calibration import (CalibrationFailure, background_masks, estimate_geometry,
    match_features, person_boxes, solve_background, validate_inputs)
from bed_pose.calibration import validate
from bed_pose.room_monitor import RoomMonitor, default_config
from test_room_monitor import fixture


def scene_points(seed=12, noise=0., outliers=0):
    rng=np.random.default_rng(seed);cal=fixture()
    for c in cal['cameras']:c['distortion_coefficients']=[0]*5
    world=np.column_stack((rng.uniform(-1.2,1.1,150),rng.uniform(-.85,.85,150),rng.uniform(3.,7.,150)))
    points=[]
    for c in cal['cameras']:
        pix=cv2.projectPoints(world,np.zeros(3),np.asarray(c['translation'],float),np.asarray(c['intrinsic_matrix'],float),np.zeros(5))[0].reshape(-1,2)
        pix+=rng.normal(0,noise,pix.shape);points.append(pix)
    if outliers:points[1][:outliers]=rng.uniform([30,30],[610,450],size=(outliers,2))
    return points,cal['cameras']


def packet(stamp=1, index=0, image=None):
    image=np.zeros((480,640,3),np.uint8) if image is None else image
    return dict(time_ns=stamp,frame_id=index,rgb=image,width=image.shape[1],height=image.shape[0],
                source_kind='camera',processing_ok=True,person_boxes=[])


class GeometryTests(unittest.TestCase):
    def test_metric_scale_outliers_pose_and_reprojection(self):
        p,c=scene_points(noise=.15,outliers=20)
        cv2.setRNGSeed(5)
        result=estimate_geometry(p,[[640,480]]*2,c,1.4)
        self.assertFalse(result['triangulation_eligible'])
        self.assertEqual(result['coordinate_system'],'camera_a');self.assertEqual(result['unit'],'m')
        np.testing.assert_allclose(result['rotation_camera_a_to_b'],np.eye(3),atol=.015)
        np.testing.assert_allclose(result['translation_direction'],[-1,0,0],atol=.025)
        self.assertAlmostEqual(np.linalg.norm(result['cameras'][1]['translation']),1.4,places=6)
        self.assertLess(result['metrics']['reprojection_rms_px'],1)
        self.assertGreater(result['metrics']['pose_inlier_ratio'],.6)
        with self.assertRaises(ValueError):validate(result)

    def test_missing_intrinsics_only_fundamental_and_no_fake_pose(self):
        p,_=scene_points()
        result=estimate_geometry(p,[[640,480]]*2)
        self.assertEqual(result['status'],'needs_input');self.assertEqual(result['calibration_validity'],'fundamental_only')
        self.assertNotIn('cameras',result);self.assertIsNone(result['metrics']['reprojection_rms_px'])
        self.assertIn('K',result['reason']);self.assertFalse(result['triangulation_eligible'])

    def test_baseline_missing_unscaled_and_planar_scene_rejected(self):
        p,c=scene_points()
        result=estimate_geometry(p,[[640,480]]*2,c)
        self.assertEqual(result['unit'],'baseline_units')
        rng=np.random.default_rng(2);a=rng.uniform([50,50],[580,420],size=(100,2));b=a+[35,3]
        with self.assertRaises(CalibrationFailure):estimate_geometry([a,b],[[640,480]]*2,c,.7)

    def test_multiframe_quality_alignment_and_validation_gate(self):
        points,cameras=scene_points(noise=.02)
        frames=[(packet(i*400000000,i),packet(i*400000000+10000,i)) for i in range(3)]
        settings=dict(cameras=cameras,baseline_m=.7,room_transform=dict(rotation=np.eye(3).tolist(),translation=[2,1,0],measured=True))
        with patch('bed_pose.automatic_calibration.background_masks',return_value=([None,None],[{},{}])),patch('bed_pose.automatic_calibration.match_features',return_value=(points,dict(matched_features=150))):
            result=solve_background(frames,settings)
        self.assertTrue(result['triangulation_eligible']);self.assertEqual(validate(result)['coordinate_system'],'room_world')
        np.testing.assert_allclose(result['cameras'][0]['translation'],[-2,-1,0],atol=1e-6)
        altered=deepcopy(result);altered['metrics']['pose_inliers']=1
        with self.assertRaises(ValueError):validate(altered)
        altered=deepcopy(result);altered['metrics']['reprojection_rms_px']=float('nan')
        with self.assertRaises(ValueError):validate(altered)

    def test_bad_intrinsics_and_invalid_scale_rejected(self):
        for value in (0,-1,float('nan'),True):
            with self.assertRaises(ValueError):validate_inputs(dict(baseline_m=value))
        with self.assertRaises((ValueError,TypeError,KeyError)):validate_inputs(dict(cameras=[{},{}]))


class MaskTests(unittest.TestCase):
    def test_all_people_expanded_and_motion_excluded(self):
        row=dict(mapped_people=[dict(joints=[dict(x_px=50,y_px=50,availability='predicted'),dict(x_px=110,y_px=200,availability='predicted')]),
                                dict(joints=[dict(x_px=400,y_px=50,availability='predicted'),dict(x_px=450,y_px=200,availability='predicted')])])
        boxes=person_boxes(row,640,480);self.assertEqual(len(boxes),2)
        images=[]
        for i in range(3):
            img=np.zeros((480,640,3),np.uint8);img[300:320,240+i*30:260+i*30]=255;images.append(img)
        samples=[]
        for i,img in enumerate(images):
            pair=[packet(i*400000000,i,img),packet(i*400000000,i,img)]
            for p in pair:p['person_boxes']=boxes
            samples.append(pair)
        hog=Mock();hog.detectMultiScale.return_value=(np.array([[500,200,70,150]]),[1.])
        with patch('cv2.HOGDescriptor',return_value=hog):masks,stats=background_masks(samples)
        for mask in masks:
            self.assertEqual(mask[100,70],0);self.assertEqual(mask[100,420],0)
            self.assertEqual(mask[270,530],0);self.assertEqual(mask[310,250],0)
            self.assertEqual(mask[430,100],255)
        self.assertGreater(stats[0]['pose_boxes'],0);self.assertGreater(stats[0]['hog_boxes'],0)
        self.assertFalse(stats[0]['person_detection_complete'])

    def test_failed_inference_and_no_background_fail_closed(self):
        samples=[(packet(i,i),packet(i,i)) for i in range(3)]
        samples[0][0]['processing_ok']=False
        with self.assertRaises(CalibrationFailure):background_masks(samples)
        samples[0][0]['processing_ok']=True
        for pair in samples:
            for p in pair:p['person_boxes']=[[0,0,640,480]]
        hog=Mock();hog.detectMultiScale.return_value=([],[])
        with patch('cv2.HOGDescriptor',return_value=hog):
            with self.assertRaises(CalibrationFailure):background_masks(samples)

    def test_real_sift_orb_masks_and_mutual_ratio_matching(self):
        rng=np.random.default_rng(4)
        img=rng.integers(0,256,(480,640,3),np.uint8)
        mask=np.full((480,640),255,np.uint8);mask[100:400,150:500]=0
        for detector in ('SIFT','ORB'):
            points,metrics=match_features([img,img],[mask,mask],detector)
            self.assertGreater(metrics['matched_features'],40)
            for p in points:
                self.assertFalse(np.any((p[:,0]>=150)&(p[:,0]<500)&(p[:,1]>=100)&(p[:,1]<400)))


class CountdownTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.m=RoomMonitor(self.temp.name)
        config=default_config();config['bindings']=['a','b'];self.m.configure(config)
        self.sessions={sid:SimpleNamespace(state=dict(closed=False,phase='preview')) for sid in ('a','b')}

    def test_no_start_until_requested_exact_10s_and_fresh_three_pairs_saved_restored(self):
        m=self.m;start=100_000_000_000
        self.assertEqual(m.setup['method'],'automatic_background')
        self.assertEqual(m.calibration_job['state'],'idle')
        with patch('bed_pose.automatic_calibration.solve_background',return_value=dict(status='needs_input',method='automatic_background',metrics={},reason='K required')) as solve:
            m.schedule_automatic({},now_ns=start)
            m.latest_pair=(packet(start-1,1),packet(start-1,1))
            m.tick_automatic(self.sessions,start+9_999_999_999)
            self.assertEqual(m.calibration_job['state'],'countdown');self.assertEqual(m.calibration_job['remaining_seconds'],1)
            m.tick_automatic(self.sessions,start+10_000_000_000);solve.assert_not_called()
            for i in range(3):
                now=start+10_010_000_000+i*400_000_000
                m.latest_pair=(packet(now,i+10),packet(now+1000,i+10))
                m.tick_automatic(self.sessions,now+2000)
            deadline=time.monotonic()+2
            while m.calibration_job['state']=='running' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(solve.call_count,1)
            used=solve.call_args.args[0]
            self.assertEqual(len(used),3);self.assertTrue(all(p['time_ns']>start+10_000_000_000 for pair in used for p in pair))
        self.assertEqual(m.calibration_job['state'],'needs_input');self.assertIsNone(m.calibration)
        restored=RoomMonitor(self.temp.name)
        self.assertEqual(restored.calibration_job['state'],'idle')
        self.assertIsNotNone(restored.auto_saved)
        self.assertEqual(restored.restore_automatic()['state'],'needs_input')
        self.assertFalse(restored.config['installation_confirmed'])

    def test_cancel_and_missing_fresh_camera_timeout_do_not_estimate(self):
        self.m.schedule_automatic({},now_ns=0);self.m.cancel_calibration()
        self.m.tick_automatic(self.sessions,11_000_000_000)
        self.assertEqual(self.m.calibration_job['state'],'cancelled')
        self.m.schedule_automatic({},now_ns=0)
        self.m.tick_automatic(self.sessions,19_000_000_000)
        self.assertEqual(self.m.calibration_job['state'],'failed');self.assertIsNone(self.m.calibration)
        self.assertIn('신선',self.m.calibration_job['error'])

    def test_cancel_during_solve_cannot_publish_late_result(self):
        entered=threading.Event();release=threading.Event()
        def solve(*args):entered.set();release.wait(2);return dict(status='estimated',method='automatic_background')
        with patch('bed_pose.automatic_calibration.solve_background',side_effect=solve):
            self.m.schedule_automatic({},now_ns=0)
            for i in range(3):
                now=10_001_000_000+i*400_000_000
                self.m.latest_pair=(packet(now,i),packet(now,i));self.m.tick_automatic(self.sessions,now)
            self.assertTrue(entered.wait(1));self.m.cancel_calibration();release.set();time.sleep(.05)
        self.assertEqual(self.m.calibration_job['state'],'cancelled')
        self.assertFalse((Path(self.temp.name)/'automatic-result.json').exists())


if __name__=='__main__':unittest.main()
