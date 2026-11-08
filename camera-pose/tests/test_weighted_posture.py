"""Weighted rule-fit behavior and provenance, not calibrated human accuracy."""
import copy
import csv
import json
from dataclasses import replace
from pathlib import Path
import unittest

from bed_pose.automatic_posture import AutomaticClassifier,DEFAULTS,V4_DEFAULTS
from bed_pose.weighted_posture import select_scores
from bed_pose.contracts import PoseOutput,IntegrityError
from bed_pose.live_recording import verify_live
from bed_pose.io_utils import load_json,file_hash,atomic_json
from bed_pose.evidence_report import export_evidence
from test_automatic_posture import observation,BOX
from test_lying_orientation import gap,world,sdk_gap
import test_lying_orientation as orientation_tests


class WeightedTests(unittest.TestCase):
    def test_close_runner_up_never_vetoes_a_qualified_winner(self):
        scores={'supine':.74,'side':.72,'edge_sitting':.1}
        result=select_scores(scores,.65,.1)
        self.assertEqual(result['label'],'supine');self.assertTrue(result['close_candidates'])
        self.assertFalse(result['margin_required']);self.assertEqual(result['scores'],scores)
        result=select_scores({'supine':.5,'side':.6,'edge_sitting':.1},.55,.2)
        self.assertEqual(result['label'],'side')

    def test_threshold_tie_missing_and_invalid_scores(self):
        for scores,reason in (({'supine':.5,'side':.6,'edge_sitting':.1},'posture_score_below_threshold'),
                             ({'supine':.8,'side':.8,'edge_sitting':None},'posture_scores_tied'),
                             (dict.fromkeys(('supine','side','edge_sitting')),'posture_scores_unavailable')):
            result=select_scores(scores,.65,.1);self.assertIsNone(result['label']);self.assertEqual(result['reason'],reason)
        self.assertEqual(select_scores({'supine':None,'side':.65,'edge_sitting':None},.65,.1)['label'],'side')
        for value in (True,float('nan'),float('inf'),-1,1.1):
            with self.assertRaises(ValueError):select_scores({'supine':value,'side':0,'edge_sitting':None},.65,.1)

    def test_independent_overlapping_scores_choose_close_lying_poses(self):
        model=AutomaticClassifier()
        self.assertIsNone(AutomaticClassifier(V4_DEFAULTS).predict(gap()))
        label,reason,e=model.evaluate(gap());d=e['score_decision']
        self.assertEqual((label,reason),('side',None));self.assertTrue(d['close_candidates'])
        self.assertGreater(d['scores']['supine'],.65);self.assertGreater(d['scores']['side'],.65)
        self.assertGreater(sum(v for v in d['scores'].values() if v is not None),1.)
        self.assertIsNone(d['scores']['edge_sitting'])
        p=list(gap().points)
        for i,y in ((5,164.),(6,196.),(11,164.),(12,196.)):p[i]=(p[i][0],y)
        label,_,e=model.evaluate(replace(gap(),points=tuple(p)))
        self.assertEqual(label,'supine');self.assertTrue(e['score_decision']['close_candidates'])

    def test_real_geometry_tie_and_high_threshold_hold(self):
        p=list(gap().points)
        for i,y in ((5,164.5),(6,195.5),(11,164.5),(12,195.5)):p[i]=(p[i][0],y)
        self.assertEqual(AutomaticClassifier().evaluate(replace(gap(),points=tuple(p)))[1],'posture_scores_tied')
        self.assertEqual(AutomaticClassifier(dict(DEFAULTS,score_min=.9)).evaluate(gap())[1],'posture_score_below_threshold')

    def test_missing_3d_is_omitted_not_zero_filled_or_required(self):
        label,_,e=AutomaticClassifier().evaluate(gap());d=e['score_decision']
        for name in ('supine','side'):
            self.assertEqual(d['scores'][name],d['components'][name]['body_2d'])
            self.assertNotIn('world_3d',d['components'][name])
        self.assertEqual(label,'side')

    def test_weights_and_world_features_change_final_scores(self):
        model=AutomaticClassifier()
        for depth,label in ((0,'supine'),(.9,'side')):
            actual=model.evaluate(replace(gap(),world_body=world(depth)))
            self.assertEqual(actual[0],label);self.assertIn('world_3d',actual[2]['score_decision']['components'][label])
        p=list(gap().points);p[5],p[6]=(200.,166.),(200.,194.);p[11],p[12]=(300.,163.),(300.,197.)
        obs=replace(gap(),points=tuple(p))
        for a,b,label in ((.8,.2,'side'),(.2,.8,'supine')):
            self.assertEqual(AutomaticClassifier(dict(DEFAULTS,score_shoulder_weight=a,score_hip_weight=b)).predict(obs),label)

    def test_existing_geometry_face_and_out_of_scope_gates_remain_separate(self):
        model=AutomaticClassifier()
        self.assertEqual(model.evaluate(observation('standing'))[1],'standing_or_upright_out_of_scope')
        self.assertTrue(all(v is None for v in model.evaluate(observation('standing'))[2]['score_decision']['scores'].values()))
        p=list(observation('supine').points);p[0]=None
        self.assertEqual(model.evaluate(replace(observation('supine'),points=tuple(p)))[1],'front_facing_evidence_missing')
        self.assertEqual(model.evaluate(replace(gap(),bed_rectangle=None))[1],'bed_region_required')
        actual=model.evaluate(observation('edge_sitting'))
        self.assertEqual(actual[0],'edge_sitting');self.assertIsNone(actual[2]['score_decision']['scores']['side'])
        self.assertIn('knees',actual[2]['score_decision']['components']['edge_sitting'])

    def test_new_parameters_are_validated_and_tie_does_not_reuse_label(self):
        for key,value in (('score_min',True),('score_min',1.1),('score_close_gap',2),('score_2d_weight',0),('score_overlap_scale',.5),('score_knee_peak',150)):
            with self.assertRaises(ValueError):AutomaticClassifier(dict(DEFAULTS,**{key:value}))
        model=AutomaticClassifier();self.assertEqual(model.predict(gap()),'side')
        self.assertEqual(model.predict(observation('standing')),None)
        self.assertNotIn('score_min',AutomaticClassifier(V4_DEFAULTS).profile)
        large=AutomaticClassifier(dict(DEFAULTS,score_shoulder_weight=1e308,score_hip_weight=1e308))
        self.assertEqual(large.predict(gap()),'side')


class WeightedWebTests(unittest.TestCase):
    setUp=orientation_tests.LyingOrientationWebTests.setUp
    cleanup=orientation_tests.LyingOrientationWebTests.cleanup
    request=orientation_tests.LyingOrientationWebTests.request
    wait=orientation_tests.LyingOrientationWebTests.wait
    demo=orientation_tests.LyingOrientationWebTests.demo
    control=orientation_tests.LyingOrientationWebTests.control
    world_input=orientation_tests.LyingOrientationWebTests.world_input
    finish=orientation_tests.LyingOrientationWebTests.finish

    def weighted_input(self):
        sid=self.world_input();engine=self.workspace.sessions[sid].worker.engine
        self.raw=sdk_gap(.9);del self.raw[0]['sdk_landmarks']['world']
        engine.adapter.estimate=lambda m:PoseOutput(copy.deepcopy(self.raw),m.model_timestamp_ms,True)
        self.control(sid,'region',box=BOX,selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'posture_mode',mode='automatic',view='oblique')
        self.wait(lambda x:x['sessions'][0]['posture_assessment']['label']=='side')
        return sid

    def test_close_scores_final_label_record_csv_and_rehashed_tamper(self):
        sid=self.weighted_input();_,view=self.request('/api/view/'+sid)
        assessment=view['posture_assessment'];d=assessment['rule_features']['score_decision']
        self.assertEqual(assessment['label'],'side');self.assertTrue(d['close_candidates'])
        self.assertEqual(assessment['stream_frame_id'],view['frame_id'])
        root=self.finish(sid);self.assertEqual(verify_live(root)['integrity'],'verified')
        self.assertEqual(load_json(root/'manifest.json')['automatic_posture_profile'],DEFAULTS)
        output,_=export_evidence(root,self.root/'weighted-report')
        with (output/'features.csv').open(encoding='utf-8-sig',newline='') as stream:report=list(csv.DictReader(stream))
        self.assertTrue(all(r['baseline_label']=='side' and r['score_close_candidates']=='True' and r['score_edge_sitting']=='' for r in report))
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        rows[0]['posture_assessment']['rule_features']['score_decision']['scores']['supine']=1.
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json');manifest['artifact_sha256'][path.name]=file_hash(path)
        atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(root)
