"""Synthetic provider-boundary checks, never model or human accuracy evidence."""
import copy
import json
from pathlib import Path
import unittest
import numpy as np

from bed_pose.body_state import VERSION,observe,validate_profile,read_observation,compose
from bed_pose.automatic_posture import AutomaticClassifier,BODY_STATE_DEFAULTS
from bed_pose.posture import assess_frame
from bed_pose.contracts import PoseOutput,IntegrityError
from bed_pose.io_utils import load_json,atomic_json,file_hash
from bed_pose.live_recording import verify_live
from test_posture_evidence import sample
import test_web_workspace as web

PROFILE={'version':VERSION,'source':'camera_image','model_id':'synthetic-test-provider',
         'model_sha256':'0'*64,'preprocess_id':'synthetic-test-v1',
         'decision_policy':{'kind':'prescribed_test_labels_not_a_classifier'},'execution_kind':'synthetic'}


class ImageModel:
    """Reports prescribed labels for integration tests; does not recognize people."""
    def __init__(self):
        self.profile=copy.deepcopy(PROFILE);self.calls=[];self.closed=False
        self.decision={'state':'lying','lying_direction':'supine','scores':{'lying':2.,'standing':-1.},'score_semantics':'logits'}
    def estimate(self,inp):self.calls.append(inp);return copy.deepcopy(self.decision)
    def close(self):self.closed=True


def recorded_observation(row,provider=None):
    provider=provider or ImageModel();rgb=np.zeros((360,640,3),dtype=np.uint8);rgb.setflags(write=False)
    selection=assess_frame(row,640,360,None)
    return observe(row,rgb,selection,provider,provider.profile)


class BodyStateTests(unittest.TestCase):
    def test_provider_only_sees_selected_image_crop_and_current_visual_metadata(self):
        row=sample();before=copy.deepcopy(row);provider=ImageModel()
        value=recorded_observation(row,provider);inp=provider.calls[0]
        self.assertEqual(set(vars(inp)),{'rgb','points','image_bounds','candidate_id','stream_frame_id','clock_id','received_ns'})
        self.assertFalse(inp.rgb.flags.writeable);self.assertLess(inp.rgb.size,360*640*3)
        self.assertEqual(inp.candidate_id,row['mapped_people'][0]['candidate_id'])
        self.assertEqual(inp.stream_frame_id,row['stream_frame_id']);self.assertEqual(row,before)
        row.update(target_posture='standing',verified_gt='supine',sensor_results={'label':'other'})
        self.assertEqual(recorded_observation(row),value)

    def test_lying_prediction_does_not_require_region_and_is_not_gt(self):
        row=sample();row['body_state_observation']=recorded_observation(row)
        model=AutomaticClassifier(BODY_STATE_DEFAULTS,body_state_profile=PROFILE)
        for region in (None,(1,1,50,50),{'method':'invalid'}):
            a=assess_frame(row,640,360,region,model)
            self.assertEqual((a['status'],a['label']),('predicted','supine'))
            self.assertFalse(a['rule_features']['context_assessment']['physical_posture_verified'])
            self.assertEqual(a['rule_features']['bed_relation']['physical_occupancy'],'unknown')

    def test_body_state_gates_override_camera_facing_shape_and_keep_unknowns(self):
        provider=ImageModel();row=sample();model=AutomaticClassifier(BODY_STATE_DEFAULTS,body_state_profile=PROFILE)
        for state,direction,expected,reason in (('lying','side','side',None),('lying','unknown',None,'lying_direction_evidence_missing'),
                  ('lying','prone',None,'prone_out_of_scope'),('standing','unknown',None,'standing_or_other_out_of_scope'),
                  ('other','unknown',None,'standing_or_other_out_of_scope'),('unknown','unknown',None,'body_state_unknown')):
            provider.decision.update(state=state,lying_direction=direction)
            row['body_state_observation']=recorded_observation(row,provider)
            a=assess_frame(row,640,360,None,model)
            self.assertEqual(a['label'],expected)
            self.assertEqual(a['reasons'],[reason] if reason else [])
        provider.decision.update(state='sitting',lying_direction='unknown')
        row['body_state_observation']=recorded_observation(row,provider)
        self.assertEqual(assess_frame(row,640,360,(290,100,550,250),model)['label'],'edge_sitting')
        for region in (None,(1,1,50,50),(100,100,550,250)):
            a=assess_frame(row,640,360,region,model)
            self.assertEqual(a['reasons'],['bed_edge_relation_unconfirmed'])
            self.assertEqual(a['rule_features']['body_state']['state'],'sitting')

    def test_no_provider_stale_or_wrong_person_evidence_never_predicts(self):
        row=sample();candidate=row['mapped_people'][0]['candidate_id'];good=recorded_observation(row)
        for field,value in (('stream_frame_id',8),('clock_id','other'),('candidate_id','other'),('received_ns','101'),('verified_gt',True)):
            bad=copy.deepcopy(good);bad[field]=value
            self.assertEqual(read_observation(bad,row,candidate,PROFILE)['status'],'held')
        model=AutomaticClassifier(BODY_STATE_DEFAULTS)
        row['body_state_observation']=good
        self.assertIsNone(assess_frame(row,640,360,None,model)['label'])
        for field,value in (('processing_status','failed'),('target_tracking',{'status':'held'}),('mapped_people',[])):
            bad=copy.deepcopy(row);bad[field]=value
            self.assertIsNone(assess_frame(bad,640,360,None,AutomaticClassifier(BODY_STATE_DEFAULTS,body_state_profile=PROFILE))['label'])

    def test_provider_errors_bad_scores_or_profile_changes_do_not_reuse_prediction(self):
        row=sample();provider=ImageModel();self.assertEqual(recorded_observation(row,provider)['status'],'predicted')
        provider.decision['scores']={'lying':float('nan')}
        self.assertEqual(recorded_observation(row,provider)['status'],'held')
        provider.profile['source']='external_sensor'
        with self.assertRaises(ValueError):validate_profile(provider.profile,'synthetic')
        with self.assertRaises(ValueError):validate_profile(PROFILE,'model_inference')
        changed=copy.deepcopy(PROFILE);changed['model_sha256']='1'*64
        self.assertNotEqual(AutomaticClassifier(BODY_STATE_DEFAULTS,body_state_profile=PROFILE).model_id,
                            AutomaticClassifier(BODY_STATE_DEFAULTS,body_state_profile=changed).model_id)


class BodyStateWebTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    control=web.WebTests.control
    demo=web.WebTests.demo

    def state_input(self):
        from bed_pose.live_engine import LiveEngine
        from bed_pose.pose.fake import FakeAdapter
        from bed_pose.pose.mediapipe import mapping_profile
        self.provider=ImageModel()
        class Adapter(FakeAdapter):
            def estimate(self,m):return PoseOutput(copy.deepcopy(sample()['raw_candidates']),m.model_timestamp_ms,True)
        def factory(source,model,output_root,options_root,**kwargs):
            engine=LiveEngine(source,Adapter(),{'execution_mode':'ordered_sequence'},output_root,
                              profile=mapping_profile(),execution_kind='synthetic',body_state_provider=self.provider)
            # This fixture exercises the legacy 0.8 provider contract, not the CLIP-specific 0.12 scores.
            engine.posture_classifier=AutomaticClassifier(BODY_STATE_DEFAULTS,body_state_profile=PROFILE)
            return engine
        self.workspace.factory=factory
        sid=self.demo();self.wait(lambda s:s['sessions'][0]['posture_assessment']['label']=='supine')
        return sid

    def test_live_state_record_replay_evaluation_and_provenance_tamper(self):
        from bed_pose.posture_accuracy import evaluate
        sid=self.state_input();self.assertEqual(self.control(sid,'start')[0],200)
        self.wait(lambda s:s['sessions'][0]['recorded_frames']>=2)
        self.provider.decision.update(state='standing',lying_direction='unknown')
        self.wait(lambda s:(s['sessions'][0]['posture_assessment'] or {}).get('rule_features',{}).get('body_state',{}).get('state')=='standing')
        self.control(sid,'stop');state=self.wait(lambda s:s['sessions'][0]['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);manifest=load_json(root/'manifest.json')
        self.assertEqual(manifest['body_state_provider_profile'],PROFILE)
        self.assertEqual(verify_live(root)['integrity'],'verified')
        rows=[json.loads(l) for l in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(rows[0]['posture_assessment']['label'],'supine')
        self.assertIsNone(rows[-1]['posture_assessment']['label'])
        _,report=evaluate(root,self.root/'no-annotations')
        self.assertEqual(report['evaluation_status'],'not_evaluated_no_reviewed_labels');self.assertIsNone(report['metrics'])
        annotations={'version':'posture-annotations-0.1','measurement_id':manifest['measurement_id'],
                     'label_source':'reviewed_video','review_protocol_id':'synthetic-protocol-not-human-review',
                     'frames':[{'stream_frame_id':rows[0]['stream_frame_id'],'label':'supine','annotation_id':'synthetic-1','uncertainty_reason':None}]}
        path=self.root/'annotations.json';atomic_json(path,annotations)
        _,report=evaluate(root,self.root/'with-annotations',path)
        self.assertFalse(report['human_accuracy_validated']);self.assertEqual(report['execution_kind'],'synthetic')
        self.assertEqual(report['metrics']['coverage'],1.);self.assertIsNone(report['metrics']['full_three_class_macro_f1'])
        rows[0]['body_state_observation']['candidate_id']='other-person'
        path=root/'live_frames.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(root)


if __name__=='__main__':unittest.main()
