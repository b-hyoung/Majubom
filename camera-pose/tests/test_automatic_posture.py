"""Synthetic rule boundary tests, never evidence of human classification accuracy."""
import copy
import json
import unittest
from pathlib import Path
from bed_pose.automatic_posture import AutomaticClassifier,DEFAULTS,OLD_DEFAULTS
from bed_pose.posture import PostureInput,assess_frame
from bed_pose.contracts import PoseOutput,IntegrityError
from bed_pose.live_recording import verify_live
from bed_pose.io_utils import file_hash,load_json,atomic_json
from bed_pose.reference_posture import combine_views
import test_web_workspace as web

BOX=[100,100,550,250]

def points(label):
    p=[(175.,180.)]*17;p[1]=(172.,175.);p[2]=(172.,185.)
    p[5],p[6],p[11],p[12]=(200.,155.),(200.,205.),(300.,155.),(300.,205.)
    p[13],p[14],p[15],p[16]=(400.,155.),(400.,205.),(500.,155.),(500.,205.)
    if label=='side':
        for i in (5,11,13,15):p[i]=(p[i][0],175.)
        for i in (6,12,14,16):p[i]=(p[i][0],185.)
    if label=='edge_sitting':
        for i,v in {5:(330.,140.),6:(270.,140.),11:(320.,240.),12:(280.,240.),13:(420.,240.),14:(380.,240.),15:(420.,330.),16:(380.,330.)}.items():p[i]=v
    if label=='standing':
        for i,v in {5:(330.,70.),6:(270.,70.),11:(320.,170.),12:(280.,170.),13:(320.,250.),14:(280.,250.),15:(320.,330.),16:(280.,330.)}.items():p[i]=v
    return p

def observation(label,view=None):return PostureInput(tuple(points(label)),640,360,tuple(BOX))

class AutomaticTests(unittest.TestCase):
    def test_real_engine_defaults_to_automatic(self):
        from bed_pose.live_engine import LiveEngine
        engine=LiveEngine(object(),object(),{'execution_mode':'ordered_sequence'},'/tmp/not-opened')
        self.assertIsInstance(engine.posture_classifier,AutomaticClassifier)

    def test_three_poses_without_references(self):
        model=AutomaticClassifier()
        for label in ('supine','side','edge_sitting'):self.assertEqual(model.predict(observation(label)),label)
        self.assertNotIn('references',model.profile)

    def test_standing_face_and_missing_and_outside_hold(self):
        model=AutomaticClassifier()
        self.assertEqual(model.evaluate(observation('standing'))[1],'standing_or_upright_out_of_scope')
        for index,reason in ((0,'front_facing_evidence_missing'),(5,'automatic_torso_joints_missing')):
            obs=observation('supine');p=list(obs.points);p[index]=None
            self.assertEqual(model.evaluate(PostureInput(tuple(p),640,360,obs.bed_rectangle))[1],reason)
        obs=observation('supine');p=list(obs.points);p[5],p[6]=p[6],p[5]
        self.assertEqual(model.evaluate(PostureInput(tuple(p),640,360,obs.bed_rectangle))[1],'front_back_ambiguous')
        self.assertEqual(model.evaluate(PostureInput(obs.points,640,360,(1,1,50,50)))[1],'body_outside_bed_region')
        self.assertEqual(model.evaluate(PostureInput(obs.points,640,360,None))[1],'bed_region_required')

    def test_overhead_holds_sitting_and_ambiguous_spans(self):
        model=AutomaticClassifier(dict(DEFAULTS,view='overhead'))
        self.assertEqual(model.evaluate(observation('edge_sitting'))[1],'overhead_sitting_ambiguous')
        obs=observation('supine');p=list(obs.points);p[11]=(300.,175.);p[12]=(300.,185.)
        self.assertEqual(model.evaluate(PostureInput(tuple(p),640,360,obs.bed_rectangle))[1],'shoulder_hip_orientation_conflict')

    def test_bad_profile_and_no_goal_leak(self):
        with self.assertRaises(ValueError):AutomaticClassifier(dict(DEFAULTS,lying_cos=True))
        row={'processing_status':'ok','stream_frame_id':1,'clock_id':'c','received_ns':'1',
             'mapped_people':[{'candidate_id':'p','joints':[{'x_px':x,'y_px':y,'availability':'predicted'} for x,y in points('side')]}],
             'target_posture':'supine','verified_gt':'supine','sensor_results':{'label':'supine'}}
        self.assertEqual(assess_frame(row,640,360,dict(zip(('x1','y1','x2','y2'),BOX)),AutomaticClassifier())['label'],'side')

    def test_two_views_agree_conflict_and_time_gate(self):
        views=[{'input_id':sid,'closed':False,'time_ns':'1000','max_age_ns':1000,'tracking_provisional':True,
          'reference_profile':DEFAULTS,'assessment':{'track_id':sid,'status':'predicted','label':'side'}} for sid in ('a','b')]
        confirms=[(900,{'a':'a','b':'b'})]
        self.assertEqual(combine_views(views,50,confirms,True,1100)['label'],'side')
        views[1]['assessment']['label']='supine'
        self.assertIn('view_posture_conflict',combine_views(views,50,confirms,True,1100)['reasons'])
        views[1]['time_ns']='1100'
        self.assertIn('pair_time_mismatch',combine_views(views,50,confirms,True,1100)['reasons'])

class AutomaticWebTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    demo=web.WebTests.demo
    control=web.WebTests.control

    def automatic(self):
        sid=self.demo();engine=self.workspace.sessions[sid].worker.engine;original=engine.adapter.estimate
        self.pose='supine'
        def controlled(model_input):
            output=original(model_input);candidates=copy.deepcopy(output.candidates)
            candidates[0]['pose_blocks'][0]['values']=[list(p) for p in points(self.pose)]
            return PoseOutput(candidates,output.model_timestamp_ms,output.sdk_call_started)
        engine.adapter.estimate=controlled
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['frame_id'] is not None)
        self.control(sid,'region',box=BOX,selected_frame_id=0)
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['region'] is not None)
        self.control(sid,'posture_mode',mode='automatic',view='oblique')
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['label']=='supine')
        return sid

    def test_automatic_no_registration_recording_and_off(self):
        sid=self.automatic();self.pose='side'
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['label']=='side')
        self.control(sid,'start');self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['recorded_frames']>=2)
        self.assertEqual(self.control(sid,'posture_mode',mode='off')[0],400)
        self.control(sid,'stop');state=self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);self.assertEqual(verify_live(root)['integrity'],'verified')
        manifest=load_json(root/'manifest.json');self.assertIsNone(manifest['reference_posture_profile'])
        self.assertEqual(manifest['automatic_posture_profile'],DEFAULTS)
        self.control(sid,'posture_mode',mode='off')
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['classifier_id'] is None)

    def test_group_auto_predictions_recompute_and_tampering_is_rejected(self):
        from bed_pose.fusion_inputs import inspect_group
        ids=[self.automatic(),self.automatic()];selections=[]
        for sid in ids:
            capture=self.workspace.capture_target(sid)
            result=self.workspace.select_target({'ticket':capture['ticket'],'candidate_id':capture['people'][0]['candidate_id'],
              'tracking_config':{'max_gap_ms':10000,'max_speed_diagonals_per_second':100}})
            self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid).get('target_selection_applied')==result['selection_id'])
            selections.append({'ticket':capture['ticket'],'candidate_id':capture['people'][0]['candidate_id']})
        status,value=self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':1000,'target_selections':selections,
                            'same_person_confirmed':True,'tracking_required':True})
        self.assertEqual(status,200)
        self.wait(lambda x:x['groups'][0]['posture_assessment']['label']=='supine' and all(s['recorded_frames']>=4 for s in x['sessions']))
        self.request('/api/group',{'action':'stop','group_id':value['group_id']})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0];root=Path(group['directory'])
        self.assertEqual(inspect_group(root)['integrity'],'verified')
        path=root/'posture_predictions.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(r['label']=='supine' for r in rows))
        rows[0]['label']='side';path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json');manifest['posture_predictions']['sha256']=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):inspect_group(root)

    def test_partial_leg_prediction_record_and_diagnostics(self):
        sid=self.automatic();engine=self.workspace.sessions[sid].worker.engine
        original=engine.adapter.estimate
        def partial(model_input):
            out=original(model_input);candidates=copy.deepcopy(out.candidates)
            for i in (13,14,15,16):candidates[0]['pose_blocks'][0]['values'][i]=[None,None]
            return PoseOutput(candidates,out.model_timestamp_ms,out.sdk_call_started)
        engine.adapter.estimate=partial
        before=self.workspace.sessions[sid].state['frame_id']
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['frame_id']>=before+3 and next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['label']=='supine')
        self.control(sid,'start');self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['recorded_frames']>=2)
        self.control(sid,'stop');state=self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['integrity']=='verified')['sessions'][0]
        self.assertGreater(state['posture_diagnostics']['predicted'],0)
        self.assertEqual(verify_live(state['directory'])['integrity'],'verified')

    def test_previous_rule_version_records_still_verify(self):
        from bed_pose.automatic_posture import LEGACY_METHOD
        sid=self.automatic();engine=self.workspace.sessions[sid].worker.engine
        engine.posture_classifier=AutomaticClassifier(dict(OLD_DEFAULTS,version=LEGACY_METHOD))
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['classifier_id'].startswith(LEGACY_METHOD+':'))
        self.control(sid,'start');self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['recorded_frames']>=2)
        self.control(sid,'stop');state=self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['integrity']=='verified')['sessions'][0]
        self.assertEqual(verify_live(state['directory'])['integrity'],'verified')

    def test_lower_geometry_review_records_replay_by_rule_version(self):
        from bed_pose.automatic_posture import METHOD,PREVIOUS_METHOD,TORSO_METHOD,WORLD_METHOD,V4_DEFAULTS
        sid=self.automatic();engine=self.workspace.sessions[sid].worker.engine
        original=engine.adapter.estimate
        def overlapping(model_input):
            out=original(model_input);candidates=copy.deepcopy(out.candidates)
            values=candidates[0]['pose_blocks'][0]['values'];values[15]=list(values[13])
            return PoseOutput(candidates,out.model_timestamp_ms,out.sdk_call_started)
        engine.adapter.estimate=overlapping
        for version,label in ((PREVIOUS_METHOD,None),(TORSO_METHOD,'supine'),(WORLD_METHOD,'supine'),(METHOD,'supine')):
            engine.posture_classifier=AutomaticClassifier(dict(DEFAULTS if version==METHOD else V4_DEFAULTS if version==WORLD_METHOD else OLD_DEFAULTS,version=version))
            self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['classifier_id'].startswith(version+':')
                      and next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['label']==label)
            previous_directory=self.workspace.sessions[sid].state.get('directory')
            self.assertEqual(self.control(sid,'start')[0],200)
            self.wait(lambda x:any(s['id']==sid and s['recording'] and not s.get('pending')
                      and s.get('directory')!=previous_directory and s['recorded_frames']>=2 for s in x['sessions']))
            self.assertEqual(self.control(sid,'stop')[0],200)
            state=self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['integrity']=='verified')['sessions'][0]
            root=Path(state['directory']);self.assertEqual(verify_live(root)['integrity'],'verified')
            manifest=load_json(root/'manifest.json')
            self.assertEqual(manifest['automatic_posture_profile']['version'],version)
            rows=[json.loads(line) for line in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(all(row['posture_assessment']['label']==label for row in rows))
            if label:
                self.assertTrue(all(row['posture_assessment']['rule_features']['invalid_knee_geometry']==[13] for row in rows))
                rows[0]['posture_assessment']['rule_features']['invalid_knee_geometry']=[]
                path=root/'live_frames.jsonl'
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
                manifest['artifact_sha256']['live_frames.jsonl']=file_hash(path)
                atomic_json(root/'manifest.json',manifest)
                with self.assertRaisesRegex(IntegrityError,'Posture assessment differs'):
                    verify_live(root)
