"""CLIP contracts/decisions with explicit doubles; no human accuracy claim."""
import copy
import contextlib
import io
import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys
import shutil
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from bed_pose.body_state import ImageBodyInput,validate_profile,validate_observation
from bed_pose.clip_assets import FILES,MANIFEST,MODEL_ID,setup_assets,read_assets
from bed_pose.clip_posture import ClipBodyStateProvider,STATE_NAMES,DIRECTION_NAMES,policy,decide,square_image
from bed_pose.io_utils import atomic_json,load_json,file_hash
from bed_pose.live_engine import LiveEngine
from bed_pose.live_recording import verify_live
from bed_pose.live_sources import VideoFileSource
from bed_pose.pose.mediapipe import mapping_profile
from bed_pose.contracts import PoseOutput,IntegrityError
from test_input_workspace import FileTestAdapter,make_video
from test_posture_evidence import sample


def source_assets(root):
    root.mkdir()
    for name in FILES:(root/name).write_bytes(b'not actual weights or tokenizer; test fixture')
    atomic_json(root/'config.json',{'model_type':'clip','projection_dim':512,
        'vision_config':{'image_size':224,'patch_size':16,'hidden_size':768,'num_hidden_layers':12},
        'text_config':{'hidden_size':512,'num_hidden_layers':12,'vocab_size':49408}})
    return root


def configuration():
    return dict(policy(),assets_manifest={'version':'clip-assets-0.1','model_id':MODEL_ID,'origin':'local_import',
                'revision':None,'files':dict.fromkeys(FILES,'0'*64)},
                runtime_versions=dict.fromkeys(('torch','transformers','Pillow'),'test-double'))


class RuntimeDouble:
    """Prescribed similarities. This object is not an image classifier."""
    closed=False
    def __init__(self,*args):self.calls=[];self.state='lying';self.direction='supine'
    def similarities(self,image):
        self.calls.append(image.copy())
        scores={name:0. for name in STATE_NAMES+DIRECTION_NAMES}
        scores[self.state]=.6;scores[self.direction]=.6
        return scores,10.
    def close(self):self.closed=True


class PoseAdapterDouble(FileTestAdapter):
    def estimate(self,m):
        candidates=copy.deepcopy(sample()['raw_candidates'])
        for person in candidates:
            for block in person['pose_blocks']:
                if block['block_id']=='image':
                    block['transform_to_canonical']['matrix']=[[m.frame.width,0,0],[0,m.frame.height,0],[0,0,1]]
        return PoseOutput(candidates,m.model_timestamp_ms,True)


class ClipTests(unittest.TestCase):
    def test_low_confidence_score_is_selected_and_record_replays(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);setup_assets(root/'model',source=source_assets(root/'src'));make_video(root/'input.mkv')
            with patch('bed_pose.clip_posture.version',return_value='test-double'),patch('bed_pose.clip_posture.ClipRuntime',RuntimeDouble):
                provider=ClipBodyStateProvider(root/'model')
                engine=LiveEngine(VideoFileSource(root/'input.mkv'),PoseAdapterDouble(),{'execution_mode':'ordered_sequence'},
                                  root/'records',profile=mapping_profile(),body_state_provider=provider)
                try:
                    engine.open()
                    provider.runtime.similarities=lambda image:({name:(.02 if name=='sitting' else 0.) for name in STATE_NAMES+DIRECTION_NAMES},10.)
                    engine.step();directory=engine.start_measurement();frame=engine.step()
                    self.assertEqual(frame['row']['body_state_observation']['status'],'held')
                    assessment=frame['row']['posture_assessment']
                    self.assertEqual(assessment['label'],'sitting')
                    self.assertIn('below_state_threshold_or_tied',assessment['rule_features']['forced_decision']['warnings'])
                finally:engine.close()
            self.assertEqual(verify_live(directory)['integrity'],'verified')

    def test_missing_presence_gpu_style_crop_record_replay(self):
        class MissingPresence(PoseAdapterDouble):
            def estimate(self,m):
                result=super().estimate(m)
                for person in result.candidates:
                    for point in person['sdk_landmarks']['image']:point['presence']=None
                return result
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);setup_assets(root/'model',source=source_assets(root/'src'));make_video(root/'input.mkv')
            with patch('bed_pose.clip_posture.version',return_value='test-double'),patch('bed_pose.clip_posture.ClipRuntime',RuntimeDouble):
                provider=ClipBodyStateProvider(root/'model')
                engine=LiveEngine(VideoFileSource(root/'input.mkv'),MissingPresence(),{'execution_mode':'ordered_sequence'},
                                  root/'records',profile=mapping_profile(),body_state_provider=provider)
                try:
                    engine.open();engine.step();directory=engine.start_measurement();frame=engine.step()
                    state=frame['row']['body_state_observation']
                    self.assertEqual(state['status'],'predicted')
                    self.assertEqual(state['input_quality']['version'],'joint-crop-quality-0.2')
                finally:engine.close()
            self.assertEqual(verify_live(directory)['integrity'],'verified')

    def test_asset_import_hashes_and_existing_output_preservation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=source_assets(root/'source');out=root/'prepared'
            _,manifest=setup_assets(out,source=source)
            self.assertEqual(manifest['origin'],'local_import');self.assertIsNone(manifest['revision'])
            self.assertEqual(read_assets(out,verify=True),manifest)
            from bed_pose.cli import main
            with contextlib.redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(main(['clip-setup','--source',str(source),'--output',str(root/'cli-model')]),0)
            self.assertFalse(json.loads(capture.getvalue())['accuracy_validated'])
            with self.assertRaises(ValueError):setup_assets(out,source=source)
            (out/'pytorch_model.bin').write_bytes(b'changed')
            with self.assertRaises(ValueError):read_assets(out,verify=True)
            (source/'config.json').write_text('{}',encoding='utf-8')
            with self.assertRaises(ValueError):setup_assets(root/'invalid',source=source)
            self.assertFalse((root/'invalid').exists())

    def test_download_resolves_revision_and_cleans_partial_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=source_assets(root/'source');calls=[]
            def download(repo,**kwargs):
                calls.append((repo,kwargs));shutil.copytree(source,kwargs['local_dir'])
            sdk=SimpleNamespace(HfApi=lambda:SimpleNamespace(model_info=lambda *a,**k:SimpleNamespace(sha='a'*40)),
                                snapshot_download=download)
            with patch.dict(sys.modules,{'huggingface_hub':sdk}):
                _,manifest=setup_assets(root/'download')
                self.assertEqual(manifest['revision'],'a'*40)
                self.assertEqual(calls[0][1]['revision'],'a'*40);self.assertEqual(calls[0][1]['allow_patterns'],list(FILES))
                sdk.snapshot_download=lambda *a,**k:(_ for _ in ()).throw(OSError('simulated network error'))
                with self.assertRaises(OSError):setup_assets(root/'failed')
                self.assertFalse((root/'failed').exists())

    def test_hierarchy_thresholds_ties_and_close_second_are_explicit(self):
        cfg=configuration();scores=dict.fromkeys(STATE_NAMES+DIRECTION_NAMES,0.)
        scores.update(lying=.6,supine=.6)
        answer=decide(scores,10.,cfg)
        self.assertEqual((answer['state'],answer['lying_direction']),('lying','supine'))
        scores.update(lying=0.,standing=.6)
        self.assertEqual(decide(scores,10.,cfg)['lying_direction'],'unknown')
        scores.update(standing=0.,lying=.6,supine=0.,side=0.,prone=0.)
        answer=decide(scores,10.,cfg)
        self.assertEqual(answer['state'],'lying');self.assertEqual(answer['lying_direction'],'unknown')
        scores.update(lying=0.)
        self.assertEqual(decide(scores,10.,cfg)['state'],'unknown')
        scores.update(lying=.6,supine=.6,side=.599,prone=-.6)
        self.assertEqual(decide(scores,10.,cfg)['lying_direction'],'supine')
        scores['lying']=float('nan')
        with self.assertRaises(ValueError):decide(scores,10.,cfg)
        with self.assertRaises(ValueError):policy(float('inf'))

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'Optional Pillow dependency missing')
    def test_padding_retains_both_ends_and_does_not_modify_rgb(self):
        rgb=np.full((224,448,3),255,np.uint8);rgb[:,:40]=[255,0,0];rgb[:,-40:]=[0,0,255]
        before=rgb.copy();image=np.asarray(square_image(rgb))
        self.assertEqual(image.shape,(224,224,3));np.testing.assert_array_equal(rgb,before)
        self.assertTrue((image[112,0]==[255,0,0]).all());self.assertTrue((image[112,-1]==[0,0,255]).all())
        self.assertTrue((image[0,0]==[123,117,104]).all())

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'Optional Pillow dependency missing')
    def test_provider_open_failure_and_frame_bound_preview(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);setup_assets(root/'model',source=source_assets(root/'src'))
            with patch('bed_pose.clip_posture.version',return_value='test-double'),patch('bed_pose.clip_posture.ClipRuntime',RuntimeDouble):
                provider=ClipBodyStateProvider(root/'model');validate_profile(provider.profile,'model_inference');provider.open()
                inp=ImageBodyInput(np.zeros((50,100,3),np.uint8),7,'clock','101','person',(),(0,0,100,50))
                result=provider.estimate(inp)
                self.assertEqual(result['state'],'lying');self.assertEqual(provider.last_input_preview['stream_frame_id'],7)
                self.assertTrue(provider.last_input_preview['image'].startswith('data:image/png;base64,'))
                with patch('PIL.Image.Image.save',side_effect=OSError('test preview error')):
                    self.assertEqual(provider.estimate(inp),result)
                    self.assertIsNone(provider.last_input_preview)
                row={'stream_frame_id':7,'clock_id':'clock','received_ns':'101'}
                value=dict(version='camera-body-state-0.1',**row,candidate_id='person',provider=provider.profile,
                           status='predicted',reasons=[],decision=result,verified_gt=False,
                           input_quality={'version':'joint-crop-quality-0.1','status':'usable','reasons':[],
                                          'bounds':[0,0,4,3],'usable_joints':17,'full_body_verified':False})
                validate_observation(value,row,'person',provider.profile)
                bad=copy.deepcopy(value);bad['decision']['lying_direction']='side'
                with self.assertRaises(ValueError):validate_observation(bad,row,'person',provider.profile)
                runtime=provider.runtime;provider.close();self.assertTrue(runtime.closed)
                with self.assertRaises(ValueError):provider.estimate(inp)
                (root/'model'/'pytorch_model.bin').write_bytes(b'changed asset')
                with self.assertRaises(ValueError):provider.open()

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'Optional Pillow dependency missing')
    def test_record_replay_csv_and_rehashed_score_tampering(self):
        from bed_pose.evidence_report import export_evidence
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);setup_assets(root/'model',source=source_assets(root/'src'));make_video(root/'input.mkv')
            with patch('bed_pose.clip_posture.version',return_value='test-double'),patch('bed_pose.clip_posture.ClipRuntime',RuntimeDouble):
                provider=ClipBodyStateProvider(root/'model')
                engine=LiveEngine(VideoFileSource(root/'input.mkv'),PoseAdapterDouble(),{'execution_mode':'ordered_sequence'},
                                  root/'records',profile=mapping_profile(),body_state_provider=provider)
                try:
                    engine.open();engine.step();directory=engine.start_measurement()
                    frame=engine.step();self.assertEqual(frame['row']['posture_assessment']['label'],'supine')
                    self.assertIsNotNone(frame['body_state_input_preview'])
                    provider.runtime.state='standing';frame=engine.step()
                    self.assertEqual(frame['row']['posture_assessment']['label'],'standing')
                    provider.runtime.similarities=lambda image:(_ for _ in ()).throw(RuntimeError('test failure'))
                    frame=engine.step();self.assertIsNone(frame['body_state_input_preview'])
                finally:engine.close()
            self.assertEqual(verify_live(directory)['integrity'],'verified')
            export_evidence(directory,root/'report')
            csv=(root/'report'/'features.csv').read_text(encoding='utf-8-sig')
            self.assertIn('image_direction_supine',csv)
            path=directory/'live_frames.jsonl';rows=[json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
            self.assertNotIn('data:image',path.read_text(encoding='utf-8'))
            rows[0]['body_state_observation']['decision']['scores']['lying']=.2
            path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
            manifest=load_json(directory/'manifest.json');manifest['artifact_sha256'][path.name]=file_hash(path)
            atomic_json(directory/'manifest.json',manifest)
            with self.assertRaises(IntegrityError):verify_live(directory)


class ClipWebTests(unittest.TestCase):
    def setUp(self):
        import test_web_workspace as web
        web.WebTests.setUp(self)
        for attribute in ('request','wait','control'):
            setattr(self,attribute,getattr(web.WebTests,attribute).__get__(self))
        model,_=setup_assets(self.root/'clip',source=source_assets(self.root/'clip-source'))
        self.version_patch=patch('bed_pose.clip_posture.version',return_value='test-double');self.version_patch.start()
        self.runtime_patch=patch('bed_pose.clip_posture.ClipRuntime',RuntimeDouble);self.runtime_patch.start()
        self.addCleanup(self.version_patch.stop);self.addCleanup(self.runtime_patch.stop)
        self.workspace.clip_options={'clip_model':model};self.workspace.clip_status={'available':True,'reason':None}
        def factory(source,model,output,options,*,demo=False,clip_model=None,**kwargs):
            provider=ClipBodyStateProvider(clip_model) if clip_model else None
            return LiveEngine(source,PoseAdapterDouble(),{'execution_mode':'ordered_sequence'},output,
                              profile=mapping_profile(),body_state_provider=provider)
        self.workspace.factory=factory

    def cleanup(self):
        import test_web_workspace as web
        web.WebTests.cleanup(self)

    def add_video(self,use_clip=True):
        path=self.root/'clip-input.mkv';make_video(path)
        _,asset=self.request('/api/upload?kind=video&name=clip-input.mkv',raw=path.read_bytes())
        status,value=self.request('/api/inputs',{'kind':'video_file','video_asset':asset['asset_id'],'use_clip':use_clip})
        self.assertEqual(status,200)
        self.wait(lambda s:any(x['id']==value['id'] and x['frame_id'] is not None for x in s['sessions']))
        return value['id']

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'Optional Pillow dependency missing')
    def test_api_clip_selection_input_preview_recording_and_opt_out(self):
        sid=self.add_video();_,view=self.request('/api/view/'+sid)
        self.assertEqual(view['posture_assessment']['label'],'supine')
        self.assertEqual(view['body_state_input_preview']['stream_frame_id'],view['frame_id'])
        self.assertGreaterEqual(view['body_state_ms'],0)
        self.assertEqual(self.control(sid,'start')[0],200)
        state=self.wait(lambda s:s['sessions'][0]['closed'])['sessions'][0]
        self.assertEqual(state['integrity'],'verified')
        sid2=self.add_video(False)
        second=next(x for x in self.workspace.snapshot()['sessions'] if x['id']==sid2)
        self.assertIsNone(second['body_state_provider_profile'])
        self.assertEqual(second['automatic_posture_profile']['version'],'automatic-posture-rules-0.7')
        self.assertEqual(self.request('/api/inputs',{'kind':'demo','use_clip':True})[0],400)


if __name__=='__main__':unittest.main()
