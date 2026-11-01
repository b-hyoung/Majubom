"""Loopback API and real generated-file decoding with explicit test SDKs."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from bed_pose.live_engine import LiveEngine
from bed_pose.live_web import WebWorkspace, WorkspaceHTTPServer
from bed_pose.live_sources import SyntheticSource, CameraSource
from bed_pose.pose.fake import FakeAdapter
from bed_pose.live_recording import verify_live
from test_input_workspace import make_video, FileTestAdapter


def factory(source, model, output_root, options_root, *, demo=False):
    return LiveEngine(source, FakeAdapter() if demo else FileTestAdapter(),
                      {'execution_mode': 'ordered_sequence'}, output_root,
                      execution_kind='synthetic' if demo else 'model_inference')


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=WebWorkspace(output_root=self.root/'records',engine_factory=factory,model_store=self.root/'model-store')
        self.server=WorkspaceHTTPServer(self.workspace)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.workspace.close();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def request(self,path,body=None,*,auth=True,origin=None,host=None,raw=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=5)
        headers={}
        if auth:headers['X-BedPose-Token']=self.server.token
        if origin:headers['Origin']=origin
        if host:headers['Host']=host
        data=raw if raw is not None else json.dumps(body).encode() if body is not None else None
        connection.request('POST' if data is not None else 'GET',path,body=data,headers=headers)
        response=connection.getresponse();content=response.read();status=response.status
        mime=response.getheader('Content-Type');connection.close()
        return status,json.loads(content) if mime.startswith('application/json') else content.decode()

    def wait(self,predicate,timeout=4):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=self.workspace.snapshot()
            if predicate(state):return state
            time.sleep(.03)
        self.fail('Workspace timeout: '+str(self.workspace.snapshot()))

    def demo(self):
        status,value=self.request('/api/inputs',{'kind':'demo'})
        self.assertEqual(status,200);sid=value['id']
        self.wait(lambda x:x['sessions'][0]['frame_id'] is not None)
        return sid

    def control(self,sid,action,**values):
        return self.request('/api/control',{'id':sid,'action':action,**values})

    def test_html_and_local_access_boundary(self):
        status,html=self.request('/',auth=False)
        self.assertEqual(status,200);self.assertIn('측정 작업실',html)
        self.assertEqual(self.request('/api/state',auth=False)[0],403)
        self.assertEqual(self.request('/api/inputs',{'kind':'demo'},origin='https://example.com')[0],403)
        self.assertEqual(self.request('/',host='example.com')[0],403)
        self.assertEqual(self.request('/../../pyproject.toml')[0],404)
        self.assertEqual(self.request('/api/state')[0],200)

    def test_browser_measurement_and_view_share_frame_identity(self):
        sid=self.demo()
        status,view=self.request('/api/view/'+sid)
        self.assertEqual(status,200);self.assertTrue(view['image'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(view['width'],640)
        self.assertEqual(self.control(sid,'start')[0],400)
        self.assertEqual(self.control(sid,'region',box=[10,20,200,300],selected_frame_id=view['frame_id'])[0],200)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.assertEqual(self.control(sid,'start')[0],200)
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.assertEqual(self.control(sid,'stop')[0],200)
        state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        self.assertFalse(state['recording']);self.assertEqual(state['posture'],'not_implemented')
        self.assertEqual(verify_live(state['directory'])['integrity'],'verified')

    def test_file_upload_first_pause_playback_and_normal_eof(self):
        video=self.root/'sample.mkv';make_video(video)
        status,asset=self.request('/api/upload?kind=video&name=%EC%98%81%EC%83%81.mkv',raw=video.read_bytes())
        self.assertEqual(status,200)
        status,result=self.request('/api/inputs',{'kind':'video_file','video_asset':asset['asset_id']})
        self.assertEqual(status,200);sid=result['id']
        state=self.wait(lambda x:x['sessions'][0]['frame_id'] is not None)['sessions'][0]
        self.assertEqual(state['frame_id'],0);self.assertTrue(state['paused'])
        self.control(sid,'region',box=[1,1,150,110],selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'start')
        state=self.wait(lambda x:x['sessions'][0]['closed'])['sessions'][0]
        self.assertEqual(state['phase'],'ended');self.assertEqual(state['recorded_frames'],11)
        self.assertEqual(verify_live(state['directory'])['counts']['ok'],11)

    def test_multiple_inputs_and_removal_are_independent(self):
        one=self.demo();status,result=self.request('/api/inputs',{'kind':'demo'});two=result['id']
        self.wait(lambda x:len(x['sessions'])==2 and all(s['frame_id'] is not None for s in x['sessions']))
        self.control(one,'region',box=[1,1,150,110],selected_frame_id=0)
        state=self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.assertIsNone(state['sessions'][1]['region'])
        self.control(one,'remove');state=self.wait(lambda x:len(x['sessions'])==1)
        self.assertEqual(state['sessions'][0]['id'],two)

    def test_error_clears_previous_view_and_rejects_commands(self):
        class Broken(SyntheticSource):
            fail=False
            def read(self):
                if self.fail:raise RuntimeError('source disconnected')
                return super().read()
        source=Broken(.03)
        engine=LiveEngine(source,FakeAdapter(),{'execution_mode':'ordered_sequence'},self.root/'records',execution_kind='synthetic')
        self.workspace.factory=lambda *args,**kwargs:engine
        sid=self.demo();source.fail=True
        state=self.wait(lambda x:x['sessions'][0]['closed'])['sessions'][0]
        self.assertEqual(state['phase'],'error')
        self.assertIsNone(self.request('/api/view/'+sid)[1])
        self.assertIsNone(self.workspace.sessions[sid].preview_rgb)
        self.assertEqual(self.control(sid,'start')[0],400)

    def test_upload_validation_and_no_client_paths(self):
        self.assertEqual(self.request('/api/upload?kind=video&name=../bad.exe',raw=b'abc')[0],400)
        self.assertEqual(self.request('/api/upload?kind=model&name=x.task',raw=b'')[0],400)
        self.assertEqual(self.request('/api/inputs',{'kind':'video_file','video_asset':'../../etc/passwd'})[0],400)
        status,asset=self.request('/api/upload?kind=model&name=x.task',raw=b'model bytes')
        self.assertEqual(status,200)
        self.assertEqual(self.workspace.assets[asset['asset_id']]['path'].parent, self.root/'model-store')

    def test_selected_model_survives_restart_and_failed_upload(self):
        store=self.root/'model-store'
        status,asset=self.request('/api/upload?kind=model&name=first.task',raw=b'first model')
        self.assertEqual(status,200)
        chosen=self.workspace.assets[asset['asset_id']]['path']
        self.assertEqual(self.workspace.snapshot()['model'],'first.task')
        self.workspace.close()
        restored=WebWorkspace(model=self.root/'explicit.task',output_root=self.root/'records2',engine_factory=factory,model_store=store)
        self.addCleanup(restored.close)
        self.assertEqual(restored.model,chosen)
        self.assertEqual(restored.snapshot()['model'],'first.task')
        self.assertTrue(restored.snapshot()['model_available'])
        self.assertEqual(restored.model.read_bytes(),b'first model')
        with self.assertRaises(ValueError):restored.register_asset(self.root/'missing.task','model','missing.task')
        self.assertEqual(restored.model,chosen)
        chosen.write_bytes(b'changed')
        again=WebWorkspace(output_root=self.root/'records3',engine_factory=factory,model_store=store)
        self.addCleanup(again.close)
        self.assertFalse(again.snapshot()['model_available'])
        self.assertIsNotNone(again.snapshot()['model_error'])

    def test_shutdown_rejects_new_inputs_and_cleans_temporary_assets(self):
        sid=self.demo();root=self.workspace.temp_root
        self.request('/api/shutdown',{})
        self.assertEqual(self.request('/api/inputs',{'kind':'demo'})[0],400)
        self.workspace.close();self.assertFalse(root.exists())
        self.assertFalse(self.workspace.sessions[sid].worker.is_alive())

    def test_posture_assessment_matches_display_and_record_and_rejects_tampering(self):
        from bed_pose.io_utils import load_json,atomic_json,file_hash
        from bed_pose.contracts import IntegrityError
        sid=self.demo();view=self.workspace.view(sid)
        self.assertEqual(view['posture_assessment']['stream_frame_id'],view['frame_id'])
        self.assertIsNone(view['posture_assessment']['label'])
        self.control(sid,'region',box=[10,10,200,200],selected_frame_id=view['frame_id'])
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'start');self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.control(sid,'stop');state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);path=root/'live_frames.jsonl'
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(r['posture_assessment']['stream_frame_id']==r['stream_frame_id'] and r['posture_assessment']['label'] is None for r in rows))
        rows[0]['posture_assessment']['label']='supine'
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json');manifest['artifact_sha256']['live_frames.jsonl']=file_hash(path)
        atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(root)
        # Legacy records without the optional extension remain readable.
        manifest.pop('posture_assessment_profile')
        manifest.pop('posture_evidence_profile')
        manifest.pop('silhouette_profile')
        manifest.pop('native_pose_cues_profile')
        manifest.pop('decision_evidence_profile')
        manifest.pop('stability_profile')
        for row in rows:
            row.pop('posture_assessment')
            row.pop('posture_evidence')
            row.pop('silhouette_observation');row.pop('silhouette_assessment')
            row.pop('native_pose_cues')
            row.pop('decision_evidence')
            row.pop('stable_posture')
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['artifact_sha256']['live_frames.jsonl']=file_hash(path)
        atomic_json(root/'manifest.json',manifest)
        self.assertEqual(verify_live(root)['integrity'],'verified')
