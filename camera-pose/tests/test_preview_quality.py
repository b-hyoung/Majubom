"""Display dimensions/compression choices; no camera or AI accuracy claims."""
import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import cv2
import numpy as np

from bed_pose.preview_quality import encode_preview
from bed_pose.io_utils import file_hash
from bed_pose.live_recording import verify_live
import test_web_workspace as web
import test_record_library as records


def decode(image):
    return cv2.imdecode(np.frombuffer(base64.b64decode(image.split(',')[1]),dtype=np.uint8),cv2.IMREAD_COLOR)


class QualityTests(unittest.TestCase):
    def test_4k_source_becomes_1080p_or_720p_and_preserves_source(self):
        source=np.empty((2160,3840,3),dtype=np.uint8);source[:]=(180,60,20)
        source.setflags(write=False)
        for quality,size,setting in [('high',(1920,1080),95),('standard',(1280,720),85)]:
            with patch('cv2.imencode',wraps=cv2.imencode) as encoder:
                image,metadata=encode_preview(source,quality)
            actual=decode(image)
            self.assertEqual(actual.shape[:2],size[::-1]);self.assertEqual(metadata['jpeg_quality'],setting)
            self.assertEqual((metadata['width'],metadata['height']),size)
            self.assertEqual(encoder.call_args.args[2],[cv2.IMWRITE_JPEG_QUALITY,setting])
            np.testing.assert_allclose(actual[0,0],[20,60,180],atol=2)
            self.assertFalse(metadata['upscaled'])
        self.assertTrue(np.all(source==np.array([180,60,20])))

    def test_low_resolution_not_upscaled_portrait_and_non_16_9_keep_aspect(self):
        for shape,expected in [((480,640,3),(640,480)),((3840,2160,3),(1080,1920)),((1440,1920,3),(1920,1440))]:
            image,metadata=encode_preview(np.zeros(shape,dtype=np.uint8))
            self.assertEqual((metadata['width'],metadata['height']),expected)
            self.assertEqual(decode(image).shape[:2],expected[::-1])

    def test_invalid_options_and_encoding_failure_are_explicit(self):
        rgb=np.zeros((20,30,3),dtype=np.uint8)
        for value in ('4k',None,True,{},95):
            with self.assertRaises(ValueError):encode_preview(rgb,value)
        with patch('cv2.imencode',return_value=(False,None)):
            with self.assertRaises(ValueError):encode_preview(rgb)


class QualityWebTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait

    def paused_file(self):
        video=self.root/('video'+str(len(self.workspace.sessions))+'.mkv');web.make_video(video)
        asset=self.workspace.register_asset(video,'video',video.name)
        _,result=self.request('/api/inputs',{'kind':'video_file','video_asset':asset['asset_id']});sid=result['id']
        self.wait(lambda x:any(s['id']==sid and s['frame_id'] is not None for s in x['sessions']))
        return sid

    def test_paused_failed_preview_can_reencode_without_new_inference(self):
        with patch('bed_pose.live_web.encode_preview',side_effect=ValueError('display only')):
            sid=self.paused_file()
            state=self.wait(lambda x:x['sessions'][0].get('preview_error'))['sessions'][0]
            self.assertFalse(state['closed']);self.assertTrue(state['paused'])
            before=self.request('/api/view/'+sid)[1]
            self.assertIsNone(before['image'])
        self.assertEqual(self.request('/api/preview-quality',{'quality':'standard'})[0],200)
        after=self.request('/api/view/'+sid)[1]
        self.assertEqual(before['frame_id'],after['frame_id'])
        self.assertEqual(before['received_ns'],after['received_ns'])
        self.assertEqual(before['posture_assessment'],after['posture_assessment'])
        self.assertTrue(after['image'])
        self.assertIsNone(self.request('/api/state')[1]['sessions'][0]['preview_error'])

    def test_authenticated_validated_global_default_and_paused_frame_reencode(self):
        self.assertEqual(self.request('/api/state')[1]['preview_quality'],'high')
        self.assertEqual(self.request('/api/preview-quality',{'quality':'standard'},auth=False)[0],403)
        for bad in ({}, {'quality':'4k'},{'quality':None}):
            self.assertEqual(self.request('/api/preview-quality',bad)[0],400)
        sid=self.paused_file();engine=self.workspace.sessions[sid].worker.engine
        _,before=self.request('/api/view/'+sid);next_frame=engine.next_frame_id
        self.assertEqual(before['preview']['jpeg_quality'],95)
        self.assertEqual(self.request('/api/preview-quality',{'quality':'standard'})[0],200)
        _,after=self.request('/api/view/'+sid)
        self.assertGreater(after['sequence'],before['sequence'])
        self.assertEqual(after['preview']['jpeg_quality'],85)
        self.assertEqual(engine.next_frame_id,next_frame)
        # Frame age is a response-time diagnostic and advances while paused.
        self.assertGreaterEqual(after['age_ms'],before['age_ms'])
        excluded=('image','preview','sequence','age_ms')
        self.assertEqual({k:v for k,v in before.items() if k not in excluded},
                         {k:v for k,v in after.items() if k not in excluded})
        self.assertTrue(self.workspace.sessions[sid].state['paused'])

    def test_all_paused_inputs_change_and_display_failure_is_atomic(self):
        ids=[self.paused_file(),self.paused_file()]
        self.request('/api/preview-quality',{'quality':'standard'})
        self.assertTrue(all(self.request('/api/view/'+sid)[1]['preview']['quality']=='standard' for sid in ids))
        with patch('bed_pose.live_web.encode_preview',side_effect=ValueError('display failure')):
            self.assertEqual(self.request('/api/preview-quality',{'quality':'high'})[0],400)
        self.assertEqual(self.request('/api/state')[1]['preview_quality'],'standard')
        self.assertTrue(all(self.request('/api/view/'+sid)[1]['preview']['quality']=='standard' for sid in ids))


class QualityRecordingTests(unittest.TestCase):
    setUp=records.RecordWebTests.setUp
    cleanup=records.RecordWebTests.cleanup
    request=records.RecordWebTests.request
    wait=records.RecordWebTests.wait
    demo=records.RecordWebTests.demo
    control=records.RecordWebTests.control
    world_input=records.RecordWebTests.world_input
    weighted_input=records.RecordWebTests.weighted_input

    def test_quality_switch_during_measurement_keeps_verdict_and_record_contract(self):
        sid=self.weighted_input();self.control(sid,'start')
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=1)
        self.request('/api/preview-quality',{'quality':'standard'})
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=3 and x['sessions'][0]['preview']['quality']=='standard')
        self.control(sid,'stop');state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);self.assertEqual(verify_live(root)['integrity'],'verified')
        rows=[json.loads(line) for line in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(r['posture_assessment']['label']=='side' for r in rows))
        self.assertTrue(all('preview' not in r and 'image' not in r for r in rows))
        before={p.name:file_hash(p) for p in root.iterdir() if p.is_file()}
        self.request('/api/preview-quality',{'quality':'high'})
        self.assertEqual(before,{p.name:file_hash(p) for p in root.iterdir() if p.is_file()})


    def test_preview_failure_keeps_recording_and_recovers_on_next_frame(self):
        sid=self.weighted_input();self.control(sid,'start')
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=1)
        with patch('bed_pose.live_web.encode_preview',side_effect=ValueError('display only')):
            state=self.wait(lambda x:x['sessions'][0].get('preview_error'))['sessions'][0]
            before=state['recorded_frames']
            state=self.wait(lambda x:x['sessions'][0]['recorded_frames']>=before+3)['sessions'][0]
            self.assertTrue(state['recording']);self.assertFalse(state['closed'])
            self.assertIsNone(state['error'])
            self.assertEqual(state['posture_assessment']['label'],'side')
            view=self.request('/api/view/'+sid)[1]
            self.assertIsNone(view['image']);self.assertIsNone(view['preview'])
            self.assertEqual(self.request('/api/target-capture',{'id':sid})[0],400)
        self.wait(lambda x:not x['sessions'][0].get('preview_error'))
        self.assertTrue(self.request('/api/view/'+sid)[1]['image'].startswith('data:image/jpeg'))
        self.control(sid,'stop')
        state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        self.assertEqual(verify_live(Path(state['directory']))['integrity'],'verified')
