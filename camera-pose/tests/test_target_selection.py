import unittest
from pathlib import Path
import json
import test_web_workspace as fixtures
import test_group_session as groups
from bed_pose.io_utils import load_json


class TargetTests(unittest.TestCase):
    setUp=fixtures.WebTests.setUp
    cleanup=fixtures.WebTests.cleanup
    request=fixtures.WebTests.request
    wait=fixtures.WebTests.wait
    demo=fixtures.WebTests.demo
    control=fixtures.WebTests.control
    prepare_pair=groups.GroupTests.prepare_pair

    def test_manual_pair_is_auditable_but_not_automatic_tracking(self):
        ids=self.prepare_pair();captures=[]
        for sid in ids:
            status,capture=self.request('/api/target-capture',{'id':sid})
            self.assertEqual(status,200);captures.append(capture)
        selections=[{'ticket':c['ticket'],'candidate_id':c['people'][0]['candidate_id']} for c in captures]
        status,value=self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':30,
                                              'target_selections':selections,'same_person_confirmed':True})
        self.assertEqual(status,200)
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        self.request('/api/group',{'action':'stop','group_id':value['group_id']})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        manifest=load_json(Path(group['directory'])/'manifest.json')
        self.assertEqual(manifest['same_person_assertion'],'operator_report_not_verified')
        for target,capture in zip(manifest['target_selections'],captures):
            self.assertEqual(target['selected_frame_id'],capture['frame_id'])
            self.assertEqual(target['candidate_snapshot'],capture['people'][0])
            self.assertNotIn('image',target)
        rows=[json.loads(line) for line in (Path(group['directory'])/'alignment.jsonl').read_text(encoding='utf-8').splitlines()]
        for row in rows:
            for side in ('left','right'):
                if row[side]:
                    self.assertEqual(row[side]['target_association']['status'],'continuity_unverified')
                    self.assertIsNone(row[side]['target_association']['candidate_id'])
        self.assertEqual(manifest['posture_prediction']['status'],'not_implemented')

    def test_wrong_input_missing_confirmation_and_candidate_rejected(self):
        ids=self.prepare_pair()
        _,capture=self.request('/api/target-capture',{'id':ids[0]})
        selection={'ticket':capture['ticket'],'candidate_id':capture['people'][0]['candidate_id']}
        body={'action':'start','inputs':ids,'target_selections':[selection,selection]}
        self.assertEqual(self.request('/api/group',body)[0],400)
        body['same_person_confirmed']=True
        self.assertEqual(self.request('/api/group',body)[0],400)
        _,other=self.request('/api/target-capture',{'id':ids[1]})
        body['target_selections'][1]={'ticket':other['ticket'],'candidate_id':'invented'}
        self.assertEqual(self.request('/api/group',body)[0],400)
        self.assertEqual(self.workspace.groups,{})

    def test_capture_missing_candidates_and_bounded_tickets(self):
        sid=self.demo()
        for _ in range(8):self.workspace.capture_target(sid)
        self.assertLessEqual(len(self.workspace.target_tickets),4)
        with self.workspace.lock:
            self.workspace.sessions[sid].view=None
            with self.assertRaises(ValueError):self.workspace.capture_target(sid)

    def test_missing_ambiguous_failed_and_single_frame_are_not_id_proofs(self):
        from bed_pose.time_alignment import references
        path=self.root/'reference_case';path.mkdir()
        rows=[]
        for index,(status,people) in enumerate([('ok',[]),('ok',[{},{}]),('ok',[{}]),('failed',None)]):
            rows.append({'frame_id':index,'stream_frame_id':index,'clock_id':'clock','received_ns':str(index+1),
                         'processing_status':status,'mapped_people':people})
        (path/'live_frames.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        result=list(references(path,{'clock_origin_perf_counter_ns':'100','measurement_id':'m'},selected={'candidate_id':'p0'}))
        self.assertEqual([r['target_association']['status'] for r in result],
                         ['target_missing','target_ambiguous','continuity_unverified','observation_failed'])
        self.assertTrue(all(r['target_association']['candidate_id'] is None for r in result))
