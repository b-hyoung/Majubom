"""Exact saved-video frame comparison without invoking an inference adapter."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_preview_quality as quality
from test_input_workspace import make_video
from bed_pose.io_utils import file_hash,atomic_json,load_json

class ImageTests(unittest.TestCase):
    setUp=quality.QualityWebTests.setUp
    cleanup=quality.QualityWebTests.cleanup
    request=quality.QualityWebTests.request
    wait=quality.QualityWebTests.wait
    paused_file=quality.QualityWebTests.paused_file
    control=quality.web.WebTests.control

    def measured_file(self):
        sid=self.paused_file();session=self.workspace.sessions[sid]
        asset=next(k for k,v in self.workspace.assets.items() if v['kind']=='video')
        view=self.request('/api/view/'+sid)[1]
        self.control(sid,'region',box=[1,1,150,110],selected_frame_id=view['frame_id'])
        self.wait(lambda s:s['sessions'][0]['region'] is not None)
        self.control(sid,'start');state=self.wait(lambda s:s['sessions'][0]['closed'])['sessions'][0]
        root=Path(state['directory']);self.assertEqual(self.request('/api/records/open',{'record_id':root.name})[0],200)
        return root,asset,session

    def test_exact_source_frame_pts_pixels_saved_evidence_and_no_inference_or_record_mutation(self):
        root,asset,session=self.measured_file()
        saved=[json.loads(l) for l in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
        before={p.name:file_hash(p) for p in root.iterdir()};count=session.worker.engine.next_frame_id
        with patch.object(session.worker.engine.adapter,'estimate',side_effect=AssertionError('must not infer')):
            for index in (0,len(saved)-1):
                status,value=self.request('/api/records/image',{'record_id':root.name,'frame_id':index,'video_asset':asset})
                self.assertEqual(status,200,value)
                self.assertEqual(value['source_media'],saved[index]['source_media'])
                self.assertEqual(value['people'],saved[index]['mapped_people'])
                self.assertEqual(value['posture_assessment'],saved[index]['posture_assessment'])
                self.assertFalse(value['inference_performed']);self.assertTrue(value['image'].startswith('data:image/jpeg'))
                self.assertEqual(value['bed_region'],session.worker.engine.region)
        self.assertGreater(saved[0]['source_media']['source_frame_id'],0)
        self.assertEqual(session.worker.engine.next_frame_id,count)
        self.assertEqual(before,{p.name:file_hash(p) for p in root.iterdir()})

    def test_wrong_video_auth_unopened_missing_frames_and_changed_record_rejected(self):
        root,asset,_=self.measured_file();body={'record_id':root.name,'frame_id':0,'video_asset':asset}
        self.assertEqual(self.request('/api/records/image',body,auth=False)[0],403)
        other=self.root/'other.mkv';make_video(other,count=13)
        wrong=self.workspace.register_asset(other,'video','other.mkv')['asset_id']
        self.assertEqual(self.request('/api/records/image',dict(body,video_asset=wrong))[0],400)
        for changes in ({'frame_id':-1},{'frame_id':True},{'frame_id':99999},{'video_asset':'missing'},{'record_id':'../outside'}):
            self.assertEqual(self.request('/api/records/image',dict(body,**changes))[0],400)
        (root/'live_frames.jsonl').write_text('changed',encoding='utf-8')
        self.assertEqual(self.request('/api/records/image',body)[0],400)

    def test_camera_or_synthetic_record_without_saved_video_remains_browsable(self):
        _,data=self.request('/api/inputs',{'kind':'demo'});sid=data['id']
        self.wait(lambda x:x['sessions'][0]['sequence']>0)
        frame=self.request('/api/view/'+sid)[1]
        self.control(sid,'region',box=[1,1,300,300],selected_frame_id=frame['frame_id'])
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'start');self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.control(sid,'stop');state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);self.request('/api/records/open',{'record_id':root.name})
        video=self.root/'unused.mkv';make_video(video)
        asset=self.workspace.register_asset(video,'video','unused.mkv')['asset_id']
        self.assertEqual(self.request('/api/records/image',{'record_id':root.name,'frame_id':0,'video_asset':asset})[0],400)
        self.assertEqual(self.request('/api/records/frames?record_id='+root.name)[0],200)

    def test_pixel_identity_mismatch_is_rejected_even_with_consistent_record_hashes(self):
        root,asset,_=self.measured_file()
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        rows[0]['source_media']['pixel_sha256']='0'*64
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json');manifest['artifact_sha256'][path.name]=file_hash(path)
        atomic_json(root/'manifest.json',manifest)
        status,result=self.request('/api/records/open',{'record_id':root.name})
        self.assertEqual(status,200,result)
        status,result=self.request('/api/records/image',{'record_id':root.name,'frame_id':0,'video_asset':asset})
        self.assertEqual(status,400,result);self.assertIn('픽셀',result['error'])
