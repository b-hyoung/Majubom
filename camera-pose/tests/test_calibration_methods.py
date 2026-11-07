"""Screen target and alternative geometry tests use generated data, never measured calibration."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from bed_pose.calibration import board_points,validate
from bed_pose.calibration_methods import pattern_png,detect,pattern_config,screen_solve,measured_points_solve,natural_pose
from bed_pose.room_monitor import RoomMonitor
from bed_pose.screen_pattern import page
from test_room_monitor import fixture


class PatternTests(unittest.TestCase):
    def test_screen_patterns_detect_metric_coordinates(self):
        for kind in ('checkerboard','charuco','aruco'):
            with self.subTest(kind=kind):
                config=dict(kind=kind,columns=9,rows=6,width_m=.25)
                img=cv2.imdecode(np.frombuffer(pattern_png(config),np.uint8),0)
                rgb=cv2.cvtColor(cv2.copyMakeBorder(img,50,50,50,50,cv2.BORDER_CONSTANT,value=255),cv2.COLOR_GRAY2RGB)
                obj,pixels,ids=detect(rgb,config)
                self.assertGreaterEqual(len(ids),54);self.assertEqual(pixels.shape,(len(ids),1,2))
                self.assertLessEqual(float(obj[:,0].max()),.250001)
                self.assertEqual(len(set(ids.tolist())),len(ids))
                with self.assertRaises(ValueError):detect(rgb,dict(config,width_m=None))

    def test_pattern_invalid_dimensions_and_portable_page(self):
        with self.assertRaises(ValueError):pattern_config(dict(width_m=-1))
        with self.assertRaises(ValueError):pattern_config(dict(columns=100))
        html=page({'kind':['charuco']})
        self.assertIn('data:image/png;base64,',html)
        self.assertIn('휴대폰용 HTML 저장',html)
        self.assertNotIn('src="http',html)

    def test_screen_candidate_not_silently_aligned_to_room(self):
        config=dict(kind='checkerboard',columns=9,rows=6,width_m=.35)
        obj=board_points(9,6,.035);k=np.array([[800.,0,320],[0,790,240],[0,0,1]])
        rab=cv2.Rodrigues(np.array([0.,.15,.02]))[0];tab=np.array([-.6,.01,.02])
        found=[]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for i in range(12):
                folder=root/f'pair-{i:02}';folder.mkdir()
                (folder/'pair.json').write_text(json.dumps(dict(bindings=['a','b'],pattern=config)),encoding='utf-8')
                r=cv2.Rodrigues(np.array([.1*(i%5)-.2,.13*(i%4)-.15,.04*i]))[0]
                t=np.array([-.25+.035*(i%6),-.15+.025*(i%5),1.1+.08*(i%7)])
                for cid,rc,tc in [('camera_a',r,t),('camera_b',rab@r,rab@t+tab)]:
                    cv2.imwrite(str(folder/(cid+'.png')),np.zeros((480,640,3),np.uint8))
                    pixels=cv2.projectPoints(obj,cv2.Rodrigues(rc)[0],tc,k,np.zeros(5))[0]
                    found.append((obj.copy(),pixels,np.arange(54)))
            with patch('bed_pose.calibration_methods.detect',side_effect=found):
                result=screen_solve(root,config,'pair-00')
            self.assertEqual(result['coordinate_system'],'target_world')
            self.assertFalse(result['room_alignment_verified'])
            self.assertLess(result['stereo_reprojection_error'],.001)
            with self.assertRaises(ValueError):validate(result)
            with patch('bed_pose.calibration_methods.detect',side_effect=found):
                aligned=screen_solve(root,config,'pair-00',dict(rotation=np.eye(3).tolist(),translation=[0,0,0],measured=True))
            self.assertEqual(validate(aligned)['coordinate_system'],'room_world')


class AlternativeTests(unittest.TestCase):
    def test_measured_reference_points_recover_camera_pose_and_reject_wrong_camera_position(self):
        cal=fixture();world=np.array([[x,y,z] for x in (-.5,.1,.6) for y in (-.4,.3) for z in (3.,4.)])
        cameras=[]
        for c in cal['cameras']:
            pixels=cv2.projectPoints(world,np.zeros(3),np.array(c['translation'],float),np.array(c['intrinsic_matrix'],float),np.array(c['distortion_coefficients'],float))[0]
            cameras.append(dict(c,world_points_m=world.tolist(),image_points_px=pixels.reshape(-1,2).tolist(),
                                measured_camera_position_m=(-np.array(c['translation'])).tolist()))
        result=measured_points_solve(dict(cameras=cameras))
        self.assertLess(result['stereo_reprojection_error'],1e-4)
        np.testing.assert_allclose(result['cameras'][1]['translation'],[-.7,0,0],atol=1e-4)
        cameras[0]['measured_camera_position_m']=[10,10,10]
        with self.assertRaises(ValueError):measured_points_solve(dict(cameras=cameras))

    def test_natural_features_empty_scene_rejected(self):
        img=np.zeros((480,640,3),np.uint8)
        with self.assertRaisesRegex(ValueError,'특징점'):natural_pose([img,img],fixture()['cameras'])

    def test_natural_relative_pose_has_no_metric_unit_or_room_alignment(self):
        rng=np.random.default_rng(42);world=np.column_stack((rng.uniform(-1,1,80),rng.uniform(-.6,.6,80),rng.uniform(3,7,80)))
        cams=fixture()['cameras'];points=[]
        for c in cams:
            pix=cv2.projectPoints(world,np.zeros(3),np.array(c['translation'],float),np.array(c['intrinsic_matrix'],float),np.array(c['distortion_coefficients'],float))[0].reshape(-1,2)
            points.append([cv2.KeyPoint(float(x),float(y),1) for x,y in pix])
        descriptors=rng.normal(size=(80,128)).astype(np.float32)
        fake=unittest.mock.Mock();fake.detectAndCompute.side_effect=[(points[0],descriptors),(points[1],descriptors)]
        with patch('cv2.SIFT_create',return_value=fake):
            result=natural_pose([np.zeros((480,640,3),np.uint8)]*2,cams)
        self.assertGreater(result['inliers'],60);self.assertIsNone(result['unit'])
        self.assertFalse(result['room_alignment_verified'])
        with self.assertRaises(ValueError):validate(result)

    def test_manual_pose_is_visualization_only_and_calibration_errors_are_reported(self):
        with self.assertRaises(ValueError):validate(dict(fixture(),method='manual'))
        with self.assertRaises(ValueError):validate(dict(fixture(),room_alignment_verified=False))
        with tempfile.TemporaryDirectory() as d:
            m=RoomMonitor(d)
            setup=dict(m.setup,method='manual',manual_cameras=[dict(position=[0,1,0],look_at=[2,0,2]),dict(position=[4,1,4],look_at=[2,0,2])])
            m.set_setup(setup);self.assertIsNone(m.calibration)
            m.start_calibration({})
            import time
            deadline=time.monotonic()+2
            while m.calibration_job['state']=='running' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(m.calibration_job['state'],'failed');self.assertIn('시각화',m.calibration_job['error'])
            self.assertIsNone(m.calibration)


if __name__=='__main__':unittest.main()
