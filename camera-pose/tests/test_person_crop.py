"""Fallback region/identity/replay contracts; prescribed boxes are not accuracy evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import importlib.util
from bed_pose.person_crop import observe,validate,quality
from bed_pose.person_detector import MODEL_ID,CROP_POLICY
from test_posture_evidence import sample
from test_clip_posture import configuration
from bed_pose.body_state import observe as body_observe,validate_observation


def profile():
    return {'version':'person-detector-0.1','model_id':MODEL_ID,'device':'cpu','score_min':.7,'max_side':640,
            'validation':'experimental_not_bed_validated','crop_policy':dict(CROP_POLICY),'assets':{'version':'person-assets-0.1','model_id':MODEL_ID,
            'revision':None,'files':dict.fromkeys(('config.json','preprocessor_config.json','model.safetensors'),'0'*64)}}


class Detector:
    def __init__(self,boxes=None):
        self.profile=profile();self.calls=0;self.boxes=boxes if boxes is not None else [{'bounds':[10.,10.,630.,350.],'score':.9}]
    def detect(self,rgb):self.calls+=1;return deepcopy(self.boxes)
    def close(self):pass


def row():
    r=sample();candidate=r['mapped_people'][0]['candidate_id'];r['target_tracking']={'status':'tracking_provisional','candidate_id':candidate,'track_id':'t0'}
    from bed_pose.pose.mediapipe import COCO_INDICES
    for i in (13,14,15,16):r['raw_candidates'][0]['sdk_landmarks']['image'][COCO_INDICES[i]]['visibility']=0.
    return r,candidate


class CropTests(unittest.TestCase):
    def test_current_target_recovered_with_missing_legs_and_no_goal_input(self):
        import numpy as np
        r,c=row();rgb=np.zeros((360,640,3),dtype=np.uint8);d=Detector();v=observe(r,rgb,{'candidate_id':c},d)
        self.assertEqual(v['quality']['status'],'usable');self.assertEqual(d.calls,1)
        self.assertFalse(v['quality']['full_body_verified']);validate(v,r,640,360,d.profile)
        changed=deepcopy(v);changed['quality']['bounds'][0]+=1
        with self.assertRaises(ValueError):validate(changed,r,640,360,d.profile)
        r.update(target_posture='prone',verified_gt=None,sensor_results={})
        _,q=quality(r,c,640,360,v['boxes'],d.profile);self.assertEqual(q,v['quality'])
        r['stream_frame_id']+=1
        with self.assertRaises(ValueError):validate(v,r,640,360,d.profile)

    def test_no_extra_model_pass_when_joint_crop_works(self):
        import numpy as np
        r=sample();d=Detector();v=observe(r,np.zeros((360,640,3),dtype=np.uint8),{'candidate_id':r['mapped_people'][0]['candidate_id']},d)
        self.assertEqual(d.calls,0);self.assertEqual(v['status'],'not_needed');validate(v,r,640,360,d.profile)

    def test_missing_target_anchors_boundary_ambiguity_and_overlap_hold(self):
        import numpy as np
        r,c=row();rgb=np.zeros((360,640,3),dtype=np.uint8)
        r['target_tracking']['status']='held';d=Detector();v=observe(r,rgb,{'candidate_id':None},d)
        self.assertEqual(d.calls,0);self.assertEqual(v['status'],'held');validate(v,r,640,360,d.profile)
        r['target_tracking']['status']='tracking_provisional'
        missing=deepcopy(r)
        from bed_pose.pose.mediapipe import COCO_INDICES
        for i in (5,6,11,12):missing['raw_candidates'][0]['sdk_landmarks']['image'][COCO_INDICES[i]]['visibility']=0.
        d=Detector();v=observe(missing,rgb,{'candidate_id':c},d)
        self.assertEqual(d.calls,0);self.assertEqual(v['quality']['reasons'],['person_crop_torso_anchors_missing'])
        validate(v,missing,640,360,d.profile)
        other=deepcopy(r);person=deepcopy(other['mapped_people'][0]);person['candidate_id']='another';other['mapped_people'].append(person)
        v=observe(other,rgb,{'candidate_id':c},Detector())
        self.assertEqual(v['quality']['reasons'],['person_crop_multiple_people'])
        for boxes,reason in [([], 'person_crop_no_matching_box'),
                            ([{'bounds':[0.,0.,640.,360.],'score':.9}],'person_crop_boundary_contact'),
                            ([{'bounds':[10.,10.,630.,350.],'score':.9}]*2,'person_crop_target_ambiguous'),
                            ([{'bounds':[10.,10.,630.,350.],'score':.9},{'bounds':[1.,1.,20.,20.],'score':.9}],'person_crop_multiple_people')]:
            v=observe(r,rgb,{'candidate_id':c},Detector(boxes));self.assertEqual(v['quality']['reasons'],[reason]);validate(v,r,640,360,profile())

    def test_detector_error_does_not_escape_into_camera_pipeline(self):
        import numpy as np
        r,c=row();d=Detector();d.detect=lambda rgb:(_ for _ in ()).throw(RuntimeError('test'))
        v=observe(r,np.zeros((360,640,3),dtype=np.uint8),{'candidate_id':c},d)
        self.assertEqual(v['quality']['reasons'],['person_crop_detector_failed']);validate(v,r,640,360,d.profile)
        d=Detector([{'bounds':[1,2],'score':.9}]);v=observe(r,np.zeros((360,640,3),dtype=np.uint8),{'candidate_id':c},d)
        self.assertIsNotNone(v['error']);validate(v,r,640,360,d.profile)

    def test_fallback_reaches_unchanged_image_provider_and_validates(self):
        import numpy as np
        r,c=row();rgb=np.zeros((360,640,3),dtype=np.uint8);d=Detector()
        class Provider:
            profile={'model_id':'test','decision_policy':configuration()}
            def estimate(self,x):
                self.last=x
                return {'state':'lying','lying_direction':'side','scores':{},'score_semantics':'not_reported'}
        p=Provider();old=body_observe(r,rgb,{'candidate_id':c},p,p.profile)
        self.assertEqual(old['status'],'held')
        r['person_crop_observation']=observe(r,rgb,{'candidate_id':c},d)
        v=body_observe(r,rgb,{'candidate_id':c},p,p.profile)
        self.assertEqual(v['status'],'predicted');self.assertEqual(p.last.image_bounds,(10,10,630,350))
        self.assertEqual(v['input_quality']['source'],'person_detection');validate_observation(v,r,c,p.profile)

    @unittest.skipUnless(all(importlib.util.find_spec(x) for x in ("torch","transformers","PIL")),"Optional image model dependencies missing")
    def test_real_transformers_random_graph_and_offline_asset_import(self):
        import numpy as np
        from transformers import YolosConfig,YolosForObjectDetection,YolosImageProcessor
        from bed_pose.person_detector import setup,read_assets,PersonDetector
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source';source.mkdir()
            config=YolosConfig(hidden_size=32,num_hidden_layers=1,num_attention_heads=4,intermediate_size=64,
                image_size=[32,32],num_detection_tokens=2,use_mid_position_embeddings=False,
                num_labels=2,id2label={0:'person',1:'other'},label2id={'person':0,'other':1})
            YolosForObjectDetection(config).save_pretrained(source)
            YolosImageProcessor(size={'shortest_edge':32,'longest_edge':32}).save_pretrained(source)
            out=Path(temp)/'prepared';setup(out,source);read_assets(out,verify=True)
            detector=PersonDetector(out);boxes=detector.detect(np.zeros((32,32,3),dtype=np.uint8))
            self.assertIsInstance(boxes,list);detector.close()
            with self.assertRaises(ValueError):setup(out,source)
            (out/'config.json').write_text('{}',encoding='utf-8')
            with self.assertRaises(ValueError):read_assets(out,verify=True)


class CropRecordTests(unittest.TestCase):
    from test_multiview_posture import MultiviewWebTests
    setUp=MultiviewWebTests.setUp
    cleanup=MultiviewWebTests.cleanup
    start_pair=MultiviewWebTests.start_pair

    def enable_fallback(self):
        from bed_pose.pose.mediapipe import COCO_INDICES
        original=self.workspace.factory
        def factory(*args,**kwargs):
            engine=original(*args,**kwargs);engine.person_detector=Detector()
            infer=engine.adapter.estimate
            def missing_legs(value):
                output=infer(value)
                for person in output.candidates:
                    for i in (13,14,15,16):person['sdk_landmarks']['image'][COCO_INDICES[i]]['visibility']=0.
                return output
            engine.adapter.estimate=missing_legs
            return engine
        self.workspace.factory=factory

    def test_fallback_engine_group_record_and_rehashed_box_tampering(self):
        from bed_pose.pose.mediapipe import COCO_INDICES
        from bed_pose.io_utils import load_json,atomic_json,file_hash
        from bed_pose.live_recording import verify_live
        from bed_pose.fusion_inputs import inspect_group
        from bed_pose.contracts import IntegrityError
        import json
        self.enable_fallback()
        gid=self.start_pair()
        state=self.wait(lambda x:x['groups'][0]['posture_assessment']['label']=='supine' and all(s['recorded_frames']>=3 for s in x['sessions']))
        self.assertEqual(state['groups'][0]['posture_assessment']['fusion_evidence']['mode'],'two_view_fusion')
        for s in state['sessions']:
            self.assertEqual(s['posture_assessment']['rule_features']['body_state']['input_quality']['source'],'person_detection')
        self.request('/api/group',{'action':'stop','group_id':gid})
        g=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        self.assertEqual(inspect_group(Path(g['directory']))['integrity'],'verified')
        root=Path(g['members'][0]['run_directory']);manifest=load_json(root/'manifest.json')
        path=root/'live_frames.jsonl';rows=[json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
        changed=next(x for x in rows if x['person_crop_observation']['status']=='usable')
        changed['person_crop_observation']['quality']['bounds'][0]+=1
        path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(root)
