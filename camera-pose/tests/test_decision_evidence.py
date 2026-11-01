"""Behavior preservation and evidence provenance, not recognition accuracy."""
from copy import deepcopy
import math
import random
import unittest
from bed_pose.decision_evidence import PROFILE, camera, single, group
from bed_pose.posture_decision import choose, finalize
from test_multiview_posture import views, evaluate


class DecisionEvidenceTests(unittest.TestCase):
    def test_unique_maximum_and_threshold_keep_previous_semantics(self):
        rng=random.Random(42)
        for _ in range(400):
            values={k:rng.random() for k in ('a','b','c')};threshold=rng.random()
            best=max(values,key=values.get)
            old=best if values[best]>=threshold and sum(abs(v-values[best])<=1e-8 for v in values.values())==1 else None
            self.assertEqual(choose(values,threshold),old)
        self.assertIsNone(choose({'a':.5,'b':.5},.5))
        self.assertEqual(choose({'a':.5001,'b':.4999},.5),'a')
        self.assertIsNone(choose({},0))

    def test_single_evidence_preserves_source_and_does_not_read_goal_or_sensor(self):
        a=views()[0]['assessment'];a.update(stream_frame_id=17,clock_id='clock',received_ns='100',candidate_id='p0')
        body=a['rule_features']['body_state'];body.update(status='predicted',state='lying',lying_direction='side')
        row={'posture_assessment':a};before=deepcopy(row);actual=single(row)
        self.assertEqual(row,before)
        self.assertEqual(actual['views'][0]['stream_frame_id'],17)
        self.assertEqual(actual['views'][0]['candidate_id'],'p0')
        self.assertTrue(actual['views'][0]['direction_used'])
        row.update(target_posture='supine',sensor_results={'label':'supine'},verified_gt='supine')
        self.assertEqual(actual,single(row))

    def test_conflict_is_visible_without_changing_prediction(self):
        v=views()
        for x,d in zip(v,({'supine':.95,'side':.03,'prone':.02},{'supine':.3,'side':.65,'prone':.05})):
            x['assessment']['rule_features']['body_state']['details']['direction_scores']=d
        result=evaluate(v);before=deepcopy(result);e=group(v,result)
        self.assertEqual(result,before);self.assertEqual(result['label'],'supine')
        self.assertEqual(e['conflicts'],['direction_winner_disagreement'])
        self.assertEqual([x['direction_weight'] for x in e['views']],[.5,.5])

    def test_fallback_excludes_bad_view_and_does_not_report_its_conflict(self):
        v=views();v[0]['assessment']['rule_features']['body_state']['input_quality']['status']='held'
        e=group(v,evaluate(v));self.assertEqual(e['mode'],'single_view_fallback')
        self.assertEqual([x['state_weight'] for x in e['views']],[0.,1.])
        self.assertEqual(e['conflicts'],[])
        self.assertTrue(e['views'][0]['exclusion_reasons'])

    def test_gated_views_keep_scores_but_have_no_contribution(self):
        v=views();result=evaluate(v,now=9999);e=group(v,result)
        self.assertTrue(e['views'][0]['state_scores'])
        self.assertTrue(all(x['state_weight']==0 for x in e['views']))
        self.assertIn('stale_observation',e['views'][0]['exclusion_reasons'])
        self.assertEqual(e['conflicts'],[])

    def test_tied_state_keeps_original_direction_pooling_and_missing_scores_are_not_zero(self):
        v=views();v[0]['assessment']['rule_features']['body_state']['scores']={'lying':.4,'sitting':.4,'standing':.1,'other':.1}
        e=group(v,evaluate(v));self.assertIsNone(e['views'][0]['state_candidate'])
        self.assertTrue(e['views'][0]['direction_used'])
        missing=camera(None);self.assertEqual(missing['state_scores'],{})
        self.assertEqual(missing['quality']['status'],'not_assessed')
        self.assertFalse(missing['fusion_usable'])
        bad=deepcopy(v[0]['assessment']);bad['rule_features']['body_state']['scores']['lying']=math.nan
        self.assertFalse(camera(bad)['fusion_usable'])

    def test_bed_is_separate_and_scope_is_unchanged(self):
        state={'status':'predicted','state':'lying','lying_direction':'side','reasons':[]}
        self.assertEqual(finalize(state,{'status':'unknown','relation':None}),('side',None))
        state.update(state='sitting',lying_direction='unknown')
        self.assertEqual(finalize(state,{'status':'unknown','relation':None})[1],'bed_edge_relation_unconfirmed')
        self.assertEqual(finalize(state,{'status':'observed','relation':'edge'})[0],'edge_sitting')


class DecisionRecordTests(unittest.TestCase):
    from test_multiview_posture import MultiviewWebTests
    setUp=MultiviewWebTests.setUp
    cleanup=MultiviewWebTests.cleanup
    start_pair=MultiviewWebTests.start_pair

    def test_new_record_diagnostics_verified_and_legacy_records_still_work(self):
        import json
        from pathlib import Path
        from bed_pose.fusion_inputs import inspect_group
        from bed_pose.io_utils import load_json,atomic_json,file_hash
        from bed_pose.contracts import IntegrityError
        from bed_pose.live_recording import verify_live
        gid=self.start_pair()
        self.wait(lambda x:x['groups'][0]['posture_assessment'].get('decision_evidence',{}).get('mode')=='two_view_fusion' and all(s['recorded_frames']>=3 for s in x['sessions']))
        self.request('/api/group',{'action':'stop','group_id':gid})
        g=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0];root=Path(g['directory'])
        self.assertEqual(inspect_group(root)['integrity'],'verified')
        manifest=load_json(root/'manifest.json');path=root/'posture_predictions.jsonl'
        original=path.read_text(encoding='utf-8');rows=[json.loads(x) for x in original.splitlines()]
        item=next(x for x in rows if x['decision_evidence']['mode']=='two_view_fusion')
        item['decision_evidence']['views'][0]['state_weight']=.123
        path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
        manifest['posture_predictions']['sha256']=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):inspect_group(root)
        # Simulate the exact previous format: original decisions, no new diagnostic profile/field.
        rows=[json.loads(x) for x in original.splitlines()]
        for row in rows:row.pop('decision_evidence')
        manifest.pop('decision_evidence_profile')
        path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
        manifest['posture_predictions']['sha256']=file_hash(path);atomic_json(root/'manifest.json',manifest)
        self.assertEqual(inspect_group(root)['integrity'],'verified')
        run=Path(manifest['members'][0]['run_directory']);m=load_json(run/'manifest.json');p=run/'live_frames.jsonl'
        rows=[json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all('decision_evidence' in x for x in rows))
        rows[0]['decision_evidence']['views'][0]['candidate_id']='tampered'
        p.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
        m['artifact_sha256']['live_frames.jsonl']=file_hash(p);atomic_json(run/'manifest.json',m)
        # Rehashing cannot hide a diagnostic/source mismatch.
        with self.assertRaises(IntegrityError):verify_live(run)
        m.pop('decision_evidence_profile')
        for row in rows:row.pop('decision_evidence')
        p.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
        m['artifact_sha256']['live_frames.jsonl']=file_hash(p);atomic_json(run/'manifest.json',m)
        self.assertEqual(verify_live(run)['integrity'],'verified')
