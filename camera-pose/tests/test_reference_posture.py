"""Controlled geometry checks; these are not human accuracy measurements."""
import copy
import json
import unittest
from pathlib import Path
from bed_pose.posture import PostureInput,assess_frame,LABELS
from bed_pose.reference_posture import ReferenceClassifier,features,combine_views,METHOD
from bed_pose.contracts import PoseOutput,IntegrityError
from bed_pose.io_utils import load_json,atomic_json,file_hash
from bed_pose.live_recording import verify_live
import test_web_workspace as web


def points(mode):
    p=[(200.,100.)]*17
    p[5],p[6],p[11],p[12]=(180.,120.),(220.,120.),(185.,200.),(215.,200.)
    p[13],p[14],p[15],p[16]=(185.,260.),(215.,260.),(185.,320.),(215.,320.)
    if mode=='side':p[6]=(185.,120.);p[12]=(190.,200.);p[14]=(190.,260.);p[16]=(190.,320.)
    if mode=='edge_sitting':p[13],p[14],p[15],p[16]=(260.,200.),(290.,200.),(260.,290.),(290.,290.)
    return p

BOX=[1,1,600,350]
def observation(mode):return PostureInput(tuple(points(mode)),640,360,tuple(BOX))
def profile():
    return {'version':METHOD,'label_source':'operator_reference_not_verified','width':640,'height':360,
            'bed_rectangle':BOX,'max_distance':.2,'min_margin':.05,
            'references':{label:{'features':features(observation(label)),'source':{'test':True}} for label in LABELS}}

class ReferenceTests(unittest.TestCase):
    def test_three_labels_and_no_supervisory_inputs(self):
        model=ReferenceClassifier(profile())
        for label in LABELS:self.assertEqual(model.predict(observation(label)),label)
        self.assertFalse(hasattr(observation('side'),'goal_posture'))

    def test_missing_geometry_and_incomplete(self):
        model=ReferenceClassifier(profile());obs=observation('side');p=list(obs.points);p[15]=None
        self.assertEqual(model.evaluate(PostureInput(tuple(p),640,360,tuple(BOX)))[1],'reference_joints_missing')
        self.assertEqual(model.evaluate(PostureInput(obs.points,640,360,(0,0,640,360)))[1],'reference_geometry_changed')
        config=profile();config['references'].pop('side')
        self.assertEqual(ReferenceClassifier(config).evaluate(obs)[1],'references_incomplete')

    def test_reject_ambiguous_and_far(self):
        config=profile();config['references']['side']=copy.deepcopy(config['references']['supine'])
        self.assertEqual(ReferenceClassifier(config).evaluate(observation('supine'))[1],'reference_ambiguous')
        model=ReferenceClassifier(profile());p=list(observation('supine').points);p[15]=(600.,350.)
        self.assertEqual(model.evaluate(PostureInput(tuple(p),640,360,tuple(BOX)))[1],'reference_too_far')

    def test_bad_config_rejected(self):
        for key,value in [('max_distance',float('nan')),('min_margin',True),('bed_rectangle',[1,1,1,1])]:
            config=profile();config[key]=value
            with self.assertRaises(ValueError):ReferenceClassifier(config)

    def test_two_view_fusion_time_identity_staleness_and_conflict(self):
        views=[]
        for sid in ('a','b'):
            views.append({'input_id':sid,'closed':False,'time_ns':'1000','max_age_ns':1000,'tracking_provisional':True,
                'reference_profile':profile(),'assessment':{'track_id':sid,'reference_distances':{'supine':0.,'side':.3,'edge_sitting':.4}}})
        confirms=[(900,{'a':'a','b':'b'})]
        self.assertEqual(combine_views(views,50,confirms,True,1100)['label'],'supine')
        self.assertIn('operator_pair_confirmation_missing',combine_views(views,50,[],True,1100)['reasons'])
        self.assertIn('stale_observation',combine_views(views,50,confirms,True,3000)['reasons'])
        views[1]['time_ns']='1100'
        self.assertIn('pair_time_mismatch',combine_views(views,50,confirms,True,1100)['reasons'])
        views[1]['time_ns']='1000';views[1]['assessment']['reference_distances']={'supine':.3,'side':0.,'edge_sitting':.4}
        self.assertIn('reference_ambiguous',combine_views(views,50,confirms,True,1100)['reasons'])

class ReferenceWebTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    demo=web.WebTests.demo
    control=web.WebTests.control

    def configured(self):
        sid=self.demo();engine=self.workspace.sessions[sid].worker.engine
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['frame_id'] is not None)
        self.control(sid,'region',box=BOX,selected_frame_id=0)
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['region'] is not None)
        original=engine.adapter.estimate
        self.mode='supine'
        def controlled(model_input):
            output=original(model_input);candidates=copy.deepcopy(output.candidates)
            candidates[0]['pose_blocks'][0]['values']=[list(p) for p in points(self.mode)]
            return PoseOutput(candidates,output.model_timestamp_ms,output.sdk_call_started)
        engine.adapter.estimate=controlled
        for label in LABELS:
            self.mode=label
            before=self.workspace.sessions[sid].state['frame_id']
            self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['frame_id']>=before+3)
            frame=self.workspace.sessions[sid].state['frame_id']
            self.assertEqual(self.control(sid,'posture_reference',label=label,selected_frame_id=frame,confirmed=True,max_distance=.2,min_margin=.05)[0],200)
            self.wait(lambda x:label in (next(s for s in x['sessions'] if s['id']==sid).get('posture_reference_profile') or {}).get('references',{}))
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['label']=='edge_sitting')
        return sid

    def test_live_predictions_record_and_tamper_verification(self):
        sid=self.configured();self.mode='supine'
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['label']=='supine')
        self.control(sid,'start');self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['recorded_frames']>=2)
        self.assertEqual(self.control(sid,'posture_reference',clear=True)[0],400)
        self.control(sid,'stop');state=self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);self.assertEqual(verify_live(root)['integrity'],'verified')
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(r['posture_prediction']=={'status':'experimental','label':'supine'} for r in rows))
        self.assertTrue(all(r['verified_gt'] is None and r['target_posture'] is None for r in rows))
        rows[0]['posture_prediction']['label']='side'
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json');manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(root)

    def test_region_change_holds_and_clear_disables(self):
        sid=self.configured();frame=self.workspace.sessions[sid].state['frame_id']
        self.control(sid,'region',box=[1,1,500,350],selected_frame_id=frame)
        self.wait(lambda x:'reference_geometry_changed' in next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['reasons'])
        self.control(sid,'posture_reference',clear=True)
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid).get('posture_reference_profile') is None and next(s for s in x['sessions'] if s['id']==sid)['posture_assessment']['classifier_id'] is None)

    def test_two_cameras_live_and_saved_prediction_and_tampering(self):
        from bed_pose.fusion_inputs import inspect_group
        ids=[self.configured(),self.configured()];selections=[]
        for sid in ids:
            capture=self.workspace.capture_target(sid)
            result=self.workspace.select_target({'ticket':capture['ticket'],'candidate_id':capture['people'][0]['candidate_id'],
                'tracking_config':{'max_gap_ms':10000,'max_speed_diagonals_per_second':100}})
            self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid).get('target_selection_applied')==result['selection_id'])
            selections.append({'ticket':capture['ticket'],'candidate_id':capture['people'][0]['candidate_id']})
        status,value=self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':1000,
            'target_selections':selections,'same_person_confirmed':True,'tracking_required':True})
        self.assertEqual(status,200)
        self.wait(lambda x:x['groups'][0]['posture_assessment']['label']=='edge_sitting' and all(s['recorded_frames']>=4 for s in x['sessions']))
        self.request('/api/group',{'action':'stop','group_id':value['group_id']})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0];root=Path(group['directory'])
        self.assertEqual(inspect_group(root)['integrity'],'verified')
        rows=[json.loads(l) for l in (root/'posture_predictions.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(r['label']=='edge_sitting' for r in rows))
        rows[0]['label']='supine';path=root/'posture_predictions.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json');manifest['posture_predictions']['sha256']=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):inspect_group(root)

    def test_bad_registration_preserves_previous_model_and_live_input(self):
        sid=self.configured();before=self.workspace.sessions[sid].worker.engine.posture_classifier.model_id
        frame=self.workspace.sessions[sid].state['frame_id']
        self.assertEqual(self.control(sid,'posture_reference',label='side',selected_frame_id=frame,confirmed=False)[0],400)
        self.assertEqual(self.control(sid,'posture_reference',label='side',selected_frame_id=frame,confirmed=True,max_distance=True)[0],200)
        self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['error'] is not None)
        session=self.workspace.sessions[sid]
        self.assertFalse(session.state['closed']);self.assertEqual(session.worker.engine.posture_classifier.model_id,before)
