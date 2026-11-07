"""Camera negotiation uses requests, driver reports and actual frames separately."""
import unittest
import time
import json
from pathlib import Path
from bed_pose.live_recording import verify_live
from unittest.mock import patch
import numpy as np
import cv2
from bed_pose.live_sources import CameraSource
from bed_pose.processing_rate import ProcessingRate
import test_web_workspace as web

class CameraTests(unittest.TestCase):
    def test_default_does_not_request_properties(self):
        with patch('cv2.VideoCapture') as constructor:
            cap=constructor.return_value;cap.isOpened.return_value=True;cap.get.return_value=0
            source=CameraSource();profile=source.open()
            cap.set.assert_not_called()
            self.assertEqual(profile['requested_capture'],{'resolution':None,'fps':None})
            self.assertIsNone(profile['driver_reported_fps']);source.close();cap.release.assert_called_once()

    def test_unsupported_requests_preserve_actual_rgb_and_reports(self):
        with patch('cv2.VideoCapture') as constructor:
            cap=constructor.return_value;cap.isOpened.return_value=True
            cap.set.side_effect=[False,RuntimeError('not supported'),False]
            cap.get.side_effect=lambda prop:{cv2.CAP_PROP_FPS:29.97,cv2.CAP_PROP_FRAME_WIDTH:640.,cv2.CAP_PROP_FRAME_HEIGHT:480.}[prop]
            cap.read.return_value=(True,np.full((480,640,3),(10,20,30),dtype=np.uint8))
            source=CameraSource(1401,resolution=[1920,1080],fps=60);profile=source.open()
            self.assertEqual(constructor.call_args.args,(1401,cv2.CAP_ANY))
            self.assertEqual(profile['capture_set_results'],{'width':False,'height':False,'fps':False})
            self.assertEqual(profile['driver_reported_fps'],29.97)
            self.assertEqual(profile['requested_capture']['resolution'],[1920,1080])
            rgb=source.read();self.assertEqual(rgb.shape,(480,640,3));np.testing.assert_array_equal(rgb[0,0],[30,20,10]);source.close()

    def test_invalid_requests_rejected_before_opening(self):
        for kwargs in ({'resolution':[True,480]},{'resolution':[3840,2160]},{'resolution':'HD'},{'fps':float('nan')},{'fps':True},{'fps':0}):
            with self.assertRaises(ValueError):CameraSource(**kwargs)

    def test_recent_completed_rate_window_and_reset(self):
        rate=ProcessingRate();self.assertIsNone(rate.observe(0))
        for n in range(1,45):self.assertAlmostEqual(rate.observe(n*100_000_000),10.)
        self.assertEqual(len(rate.samples),30)
        rate.reset();self.assertIsNone(rate.observe(8_000_000_000))
        self.assertIsNone(rate.observe(8_000_000_000))

class CameraWebTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    control=web.WebTests.control

    def test_api_rejects_invalid_settings_and_preserves_source_request(self):
        for value in (None,{'fps':31},{'resolution':[1,2]},{'unknown':30}):
            self.assertEqual(self.request('/api/inputs',{'kind':'camera','camera':0,'camera_settings':value})[0],400)
        def receive(self):
            time.sleep(.01)
            return np.zeros((480,640,3),dtype=np.uint8)
        with patch.object(CameraSource,'open',return_value={'kind':'camera','driver_reported_fps':25.,'requested_capture':{'resolution':[1920,1080],'fps':60}}),patch.object(CameraSource,'read',receive),patch.object(CameraSource,'close'):
            status,data=self.request('/api/inputs',{'kind':'camera','camera':0,'camera_settings':{'resolution':[1920,1080],'fps':60}})
            self.assertEqual(status,200)
            # Processing completes before the asynchronous preview publishes its
            # frame identity/geometry. Wait for both observations, not a race.
            state=self.wait(lambda x:x['sessions'][0].get('processing_fps') is not None
                            and x['sessions'][0]['frame_id'] is not None)['sessions'][0]
            self.assertEqual((state['width'],state['height']),(640,480))
            self.assertEqual(state['source_profile']['driver_reported_fps'],25.)
            self.assertGreater(state['processing_fps'],0)
            sid=data['id']
            view=self.request('/api/view/'+sid)[1]
            self.assertEqual(self.control(sid,'region',box=[10,10,500,400],selected_frame_id=view['frame_id'])[0],200)
            self.wait(lambda x:x['sessions'][0]['region'] is not None)
            self.control(sid,'start');self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
            self.control(sid,'stop');state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
            root=Path(state['directory']);self.assertEqual(verify_live(root)['integrity'],'verified')
            manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['source_profile']['requested_capture']['fps'],60)
            rows=[json.loads(line) for line in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(all('processing_fps' not in row for row in rows))
