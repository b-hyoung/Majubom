"""Fusion behavior with prescribed scores, not posture recognition accuracy."""
from copy import deepcopy
import unittest
from bed_pose.multiview_posture import POLICY
from bed_pose.reference_posture import combine_views
from test_clip_posture import configuration


def views():
    profile={'model_id':'openai/clip-vit-base-patch16','model_sha256':'0'*64,'preprocess_id':'test','decision_policy':configuration()}
    result=[]
    for i in range(2):
        body={'status':'held','state':'lying','score_semantics':'uncalibrated_probability',
              'input_quality':{'status':'usable'},'scores':{'lying':.8,'sitting':.1,'standing':.05,'other':.05},
              'details':{'direction_scores':{'supine':.45,'side':.4,'prone':.15}}}
        result.append({'input_id':str(i),'closed':False,'time_ns':str(1000+i*10),'max_age_ns':500,
                       'tracking_provisional':True,'image_profile':deepcopy(profile),
                       'reference_profile':{'version':'automatic-posture-rules-0.8'},
                       'assessment':{'status':'held','label':None,'track_id':'t'+str(i),'rule_features':{
                            'body_state':body,'bed_relation':{'status':'unknown','relation':None}}}})
    return result


def evaluate(v,**kw):
    return combine_views(v,kw.get('skew',50),kw.get('confirmations',[(0,{'0':'t0','1':'t1'})]),True,kw.get('now',1050),fusion_policy=POLICY)


class MultiviewTests(unittest.TestCase):
    def test_mixed_precision_views_are_not_fused(self):
        v=views();v[1]['image_profile']['decision_policy']['dtype']='cuda_amp_fp16_float32_scores'
        self.assertIn('fusion_provider_mismatch',evaluate(v)['reasons'])

    def test_direction_complements_held_local_prediction_without_bed_or_pixel_averaging(self):
        v=views();v[1]['assessment']['rule_features']['body_state']['details']['direction_scores']={'supine':.9,'side':.05,'prone':.05}
        a=evaluate(v);self.assertEqual(a['label'],'supine');self.assertEqual(a['fusion_evidence']['mode'],'two_view_fusion')
        self.assertAlmostEqual(a['fusion_evidence']['direction_scores']['supine'],.675)

    def test_unusable_image_uses_one_view_but_reports_fallback(self):
        v=views();v[0]['assessment']['rule_features']['body_state']={'input_quality':{'status':'held'},'reasons':['body_state_crop_quality_insufficient']}
        v[1]['assessment']['rule_features']['body_state']['details']['direction_scores']={'supine':.1,'side':.8,'prone':.1}
        a=evaluate(v);self.assertEqual(a['label'],'side');self.assertEqual(a['fusion_evidence']['mode'],'single_view_fallback')
        self.assertEqual([x['used'] for x in a['fusion_evidence']['contributors']],[False,True])

    def test_disagreement_and_no_evidence_hold(self):
        v=views()
        for i,d in enumerate(({'supine':.8,'side':.1,'prone':.1},{'supine':.1,'side':.8,'prone':.1})):
            v[i]['assessment']['rule_features']['body_state']['details']['direction_scores']=d
        self.assertIsNone(evaluate(v)['label'])
        for x in v:x['assessment']['rule_features']['body_state']={}
        self.assertIn('fusion_no_usable_view',evaluate(v)['reasons'])

    def test_gates_are_not_relaxed_by_fusion_or_fallback(self):
        for mutation,expected in ((lambda v:v[0].update(time_ns='1'),'pair_time_mismatch'),
             (lambda v:v[0]['assessment'].update(track_id='new'),'operator_pair_confirmation_missing'),
             (lambda v:v[0].update(tracking_provisional=False),'view_features_unavailable'),
             (lambda v:v[0].update(closed=True),'view_unavailable')):
            v=views();mutation(v);self.assertIn(expected,evaluate(v)['reasons'])
        self.assertIn('stale_observation',evaluate(views(),now=5000)['reasons'])

    def test_execution_device_does_not_change_fusion_and_models_must_match(self):
        v=views();a=evaluate(v)
        v[0]['image_profile']['decision_policy']['device']='cpu';v[1]['image_profile']['decision_policy']['device']='cuda'
        self.assertEqual(a['fusion_evidence'],evaluate(v)['fusion_evidence'])
        v[1]['image_profile']['decision_policy']['device']='mps'
        self.assertEqual(a['fusion_evidence'],evaluate(v)['fusion_evidence'])
        v[1]['image_profile']['model_sha256']='1'*64;self.assertIn('fusion_provider_mismatch',evaluate(v)['reasons'])

    def test_sitting_requires_edge_context_and_conflicts_hold(self):
        v=views()
        for x in v:x['assessment']['rule_features']['body_state']['scores']={'lying':.05,'sitting':.85,'standing':.05,'other':.05}
        self.assertIsNone(evaluate(v)['label'])
        v[0]['assessment']['rule_features']['bed_relation']={'status':'observed','relation':'edge'}
        self.assertEqual(evaluate(v)['label'],'edge_sitting')
        v[1]['assessment']['rule_features']['bed_relation']={'status':'observed','relation':'outside'}
        self.assertIsNone(evaluate(v)['label'])

    def test_live_closest_candidate_requires_gated_unique_supported_evidence(self):
        from bed_pose.multiview_posture import closest_candidate
        v=views()
        held=evaluate(v)
        self.assertEqual(held['status'],'held')
        self.assertEqual(closest_candidate(held)['label'],'supine')
        self.assertIsNone(held['label'])
        v[0]['time_ns']='1'
        self.assertIsNone(closest_candidate(evaluate(v)))
        v=views()
        for x in v:
            x['assessment']['rule_features']['body_state']['details']['direction_scores']={'supine':.4,'side':.4,'prone':.2}
        self.assertIsNone(closest_candidate(evaluate(v)))
        for x in v:
            body=x['assessment']['rule_features']['body_state']
            body['scores']={'lying':.05,'sitting':.85,'standing':.05,'other':.05}
        self.assertEqual(closest_candidate(evaluate(v))['label'],'edge_sitting')
        for x in v:
            x['assessment']['rule_features']['body_state']['scores']={'lying':.05,'sitting':.05,'standing':.85,'other':.05}
        self.assertIsNone(closest_candidate(evaluate(v)))

    def test_goal_sensor_and_gt_do_not_change_predictions(self):
        v=views();a=evaluate(v)
        for x in v:x.update(target_posture='side',sensor_results={'label':'side'},verified_gt='side')
        self.assertEqual(a['fusion_evidence'],evaluate(v)['fusion_evidence'])


class MultiviewWebTests(unittest.TestCase):
    from test_clip_posture import ClipWebTests
    setUp=ClipWebTests.setUp
    cleanup=ClipWebTests.cleanup

    def start_pair(self,with_regions=False,blackbox=False):
        from bed_pose.live_sources import SyntheticSource
        original=self.workspace.factory
        class Camera(SyntheticSource):
            def __init__(self,index):super().__init__(interval=.02);self.index=index
            def open(self):return dict(super().open(),kind='camera')
        def factory(source,*args,**kwargs):return original(Camera(source.index),*args,**kwargs)
        self.workspace.factory=factory
        ids=[];selections=[]
        for index in (0,1):
            status,result=self.request('/api/inputs',{'kind':'camera','camera':index,'use_clip':True})
            self.assertEqual(status,200,result);sid=result['id'];ids.append(sid)
            self.wait(lambda x:any(s['id']==sid and s['frame_id'] is not None for s in x['sessions']))
            if with_regions:
                _,view=self.request('/api/view/'+sid)
                self.assertEqual(self.control(sid,'region',box=[1,1,639,359],selected_frame_id=view['frame_id'])[0],200)
                self.wait(lambda x:any(s['id']==sid and s['region'] is not None for s in x['sessions']))
            capture=self.workspace.capture_target(sid);candidate=capture['people'][0]['candidate_id']
            selected=self.workspace.select_target({'ticket':capture['ticket'],'candidate_id':candidate,
                'tracking_config':{'max_gap_ms':10000,'max_speed_diagonals_per_second':100}})
            self.wait(lambda x:any(s['id']==sid and s.get('target_selection_applied')==selected['selection_id'] for s in x['sessions']))
            selections.append({'ticket':capture['ticket'],'candidate_id':candidate})
        status,result=self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':1000,'blackbox':blackbox,'segment_seconds':1 if blackbox else None,
            'target_selections':selections,'same_person_confirmed':True,'tracking_required':True})
        self.assertEqual(status,200,result)
        return result['group_id']

    def test_group_segment_saved_stability_continues(self):
        from pathlib import Path
        from bed_pose.io_utils import load_json
        from bed_pose.fusion_inputs import inspect_group
        gid=self.start_pair(blackbox=True)
        self.wait(lambda x:len(x['groups'][0].get('segments',[]))>=2 and all(v['status']=='completed' for v in x['groups'][0]['segments'][:2]),timeout=15)
        self.request('/api/group',{'action':'stop','group_id':gid})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed',timeout=15)['groups'][0]
        first,second=(Path(v['directory']) for v in group['segments'][:2])
        a,b=load_json(first/'manifest.json'),load_json(second/'manifest.json')
        self.assertEqual(inspect_group(first)['integrity'],'verified')
        self.assertEqual(inspect_group(second)['integrity'],'verified')
        self.assertEqual(b['initial_group_stability_checkpoint'],a['posture_predictions']['final_stability_checkpoint'])
        self.assertEqual(b['group_stability_sequence_offset'],a['posture_predictions']['next_stability_sequence'])

    def test_group_stability_continues_after_older_segment_is_deleted(self):
        from pathlib import Path
        from bed_pose.io_utils import load_json
        from bed_pose.blackbox_retention import _size
        from bed_pose.fusion_inputs import inspect_group
        gid=self.start_pair(blackbox=True)
        group=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed',timeout=15)['groups'][0]
        old=group['segments'][0];first=load_json(Path(old['directory'])/'manifest.json')
        with self.workspace.lock:
            root=self.workspace.output_root/'blackbox';total=_size(root)
            size=_size(Path(old['directory']))+sum(_size(Path(p)) for p in old['members'])
            self.workspace.groups[gid].data['retention_max_bytes']=max(1,total-size//2)
            self.workspace._tick_retention()
            self.workspace.groups[gid].data['retention_max_bytes']=1024**4
        state=self.wait(lambda x:x['groups'][0]['segments'][0]['status']=='deleted' and len(x['groups'][0]['segments'])>=2 and x['groups'][0]['segments'][1]['status']=='completed',timeout=15)
        self.assertEqual(state['groups'][0]['phase'],'recording')
        second=Path(state['groups'][0]['segments'][1]['directory']);manifest=load_json(second/'manifest.json')
        self.assertEqual(manifest['initial_group_stability_checkpoint'],first['posture_predictions']['final_stability_checkpoint'])
        self.assertEqual(inspect_group(second)['integrity'],'verified')
        self.request('/api/group',{'action':'stop','group_id':gid})
        self.wait(lambda x:x['groups'][0]['phase']=='completed',timeout=15)

    def test_saved_group_depth_export_preserves_posture(self):
        import json
        from pathlib import Path
        from bed_pose.io_utils import load_json, atomic_json
        from bed_pose.stereo_depth import export
        gid=self.start_pair()
        self.wait(lambda x:all(s['recorded_frames']>=4 for s in x['sessions']))
        self.request('/api/group',{'action':'stop','group_id':gid})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        root=Path(group['directory']); manifest=load_json(root/'manifest.json')
        cameras=[]
        for i, member in enumerate(manifest['members']):
            run=load_json(Path(member['run_directory'])/'manifest.json')
            cameras.append({'input_id':member['input_id'],'image_size':[run['width'],run['height']],
                'intrinsic':[[500,0,320],[0,500,240],[0,0,1]],
                'rotation_world_to_camera':[[1,0,0],[0,1,0],[0,0,1]],
                'translation_world_to_camera':[-i,0,0]})
        calibration={'version':'stereo-pinhole-0.1','image_coordinates':'undistorted_pinhole_pixels',
            'unit':'m','quality':{'max_reprojection_error_px':3,'min_ray_angle_degrees':2},'cameras':cameras}
        cal=self.root/'cal.json';atomic_json(cal,calibration)
        out=self.root/'stereo';report=export(root,cal,out)
        actual=[json.loads(line) for line in (out/'posture_with_depth.jsonl').read_text().splitlines()]
        original=[json.loads(line) for line in (root/'posture_predictions.jsonl').read_text().splitlines()]
        self.assertGreater(len(actual),0)
        self.assertEqual([row['posture'] for row in actual],original)
        self.assertEqual(report['rows'],len(original))
        self.assertFalse(report['depth_used_in_classifier'])
        self.assertTrue(all(row['stereo_depth']['time_basis']=='receive_time_approximation' for row in actual))
        calibration['bed_normal_up_world']=[0,0,-1];atomic_json(cal,calibration)
        checked=self.root/'checked';checked_report=export(root,cal,checked,depth_check=True)
        checked_rows=[json.loads(line) for line in (checked/'posture_with_depth.jsonl').read_text().splitlines()]
        self.assertTrue(checked_report['depth_used_in_classifier'])
        self.assertEqual([row['posture'] for row in checked_rows],original)
        self.assertTrue(all(row['depth_checked_posture']['status']=='held' for row in checked_rows))

    def test_bed_person_preferred_over_passer_in_both_views_and_replayed(self):
        from bed_pose.live_sources import SyntheticSource
        from bed_pose.contracts import PoseOutput
        from bed_pose.fusion_inputs import inspect_group
        from bed_pose.io_utils import load_json
        from pathlib import Path
        import copy,json
        original=self.workspace.factory
        class Camera(SyntheticSource):
            def __init__(self,index):super().__init__(interval=.02);self.index=index
            def open(self):return dict(super().open(),kind='camera')
        def factory(source,*args,**kwargs):return original(Camera(source.index),*args,**kwargs)
        self.workspace.factory=factory
        ids=[]
        for i in (0,1):
            status,value=self.request('/api/inputs',{'kind':'camera','camera':i,'use_clip':True})
            self.assertEqual(status,200,value);sid=value['id'];ids.append(sid)
            self.wait(lambda x:any(s['id']==sid and s['frame_id'] is not None for s in x['sessions']))
            status,_=self.request('/api/control',{'id':sid,'action':'region','box':[0,100,350,260],'selected_frame_id':0})
            self.assertEqual(status,200)
            self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['region'] is not None)
            adapter=self.workspace.sessions[sid].worker.engine.adapter
            base=adapter.estimate
            def two_people(m,orig=base):
                result=orig(m);bed=copy.deepcopy(result.candidates[0]);passer=copy.deepcopy(bed)
                passer['candidate_id']='passer'
                for block in passer['pose_blocks']:
                    if block['block_id']=='image':
                        for xy in block['values']:xy[0]+=.4
                for point in passer['sdk_landmarks']['image']:point['x']+=.4
                return PoseOutput([passer,bed],m.model_timestamp_ms,True)
            adapter.estimate=two_people
        self.wait(lambda x:all(s['candidate_count']==2 for s in x['sessions']))
        for s in self.workspace.snapshot()['sessions']:
            self.assertEqual(s['bed_target_selection']['status'],'selected')
            self.assertNotEqual(s['bed_target_selection']['candidate_id'],'passer')
        payload={'action':'start','inputs':ids,'max_skew_ms':1000,
                 'association_policy':'bed-priority-v2','bed_regions_confirmed':True}
        status,result=self.request('/api/group',payload);self.assertEqual(status,200,result)
        state=self.wait(lambda x:x['groups'][0]['posture_assessment']['label']=='supine' and all(s['recorded_frames']>=3 for s in x['sessions']))
        self.assertFalse(state['groups'][0]['posture_assessment']['identity_verified'])
        self.request('/api/group',{'action':'stop','group_id':result['group_id']})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed',timeout=15)['groups'][0]
        root=Path(group['directory']);self.assertEqual(inspect_group(root)['integrity'],'verified')
        rows=[json.loads(line) for line in (root/'fusion_inputs.jsonl').read_text(encoding='utf-8').splitlines()]
        pair=next(r for r in rows if r['status']=='candidate_input')
        self.assertTrue(all(v['unique_bed_candidate'] and v['posture_assessment']['candidate_id']!='passer' for v in pair['views']))
        for member in group['members']:
            manifest=load_json(Path(member['run_directory'])/'manifest.json')
            self.assertEqual(manifest['bed_target_selection_profile']['version'],'bed-target-selection-0.1')

    def test_empty_bed_starts_then_auto_fuses_when_person_appears(self):
        from bed_pose.live_sources import SyntheticSource
        from bed_pose.contracts import PoseOutput
        from bed_pose.fusion_inputs import inspect_group
        from pathlib import Path
        import json
        original=self.workspace.factory
        class Camera(SyntheticSource):
            def __init__(self,index):super().__init__(interval=.02);self.index=index
            def open(self):return dict(super().open(),kind='camera')
        def factory(source,*args,**kwargs):return original(Camera(source.index),*args,**kwargs)
        self.workspace.factory=factory
        ids=[]
        for i in (0,1):
            status,value=self.request('/api/inputs',{'kind':'camera','camera':i,'use_clip':True})
            self.assertEqual(status,200,value);sid=value['id'];ids.append(sid)
            self.wait(lambda x:any(s['id']==sid and s['frame_id'] is not None for s in x['sessions']))
            status,_=self.request('/api/control',{'id':sid,'action':'region','box':[0,0,640,360],'selected_frame_id':0})
            self.assertEqual(status,200)
            self.wait(lambda x:next(s for s in x['sessions'] if s['id']==sid)['region'] is not None)
        switches=[]
        from bed_pose.posture_stability import Stabilizer
        for sid in ids:
            self.workspace.sessions[sid].worker.engine.stability=Stabilizer({'confirm_ms':0,'min_samples':2})
            adapter=self.workspace.sessions[sid].worker.engine.adapter
            original_estimate=adapter.estimate
            switch={'empty':True};switches.append(switch)
            def estimate(m,orig=original_estimate,flag=switch):
                return PoseOutput([],m.model_timestamp_ms,True) if flag['empty'] else orig(m)
            adapter.estimate=estimate
        self.wait(lambda x:all(s['candidate_count']==0 for s in x['sessions']))
        payload={'action':'start','inputs':ids,'max_skew_ms':1000,
                 'association_policy':'unique-person-in-bed-v1','bed_regions_confirmed':True}
        status,result=self.request('/api/group',payload);self.assertEqual(status,200,result)
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        self.assertIsNone(self.workspace.snapshot()['groups'][0]['posture_assessment']['label'])
        for switch in switches:switch['empty']=False
        state=self.wait(lambda x:x['groups'][0]['posture_assessment']['label']=='supine' and all(s['recorded_frames']>=5 for s in x['sessions']))
        self.assertFalse(state['groups'][0]['posture_assessment']['identity_verified'])
        self.wait(lambda x:(x['groups'][0]['posture_assessment'].get('stable_posture') or {}).get('status')=='stable')
        for switch in switches:switch['empty']=True
        state=self.wait(lambda x:all(s['candidate_count']==0 for s in x['sessions']) and x['groups'][0]['posture_assessment']['status']=='held')
        self.assertIsNone((state['groups'][0]['posture_assessment'].get('stable_posture') or {}).get('label'))
        self.request('/api/group',{'action':'stop','group_id':result['group_id']})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed',timeout=15)['groups'][0]
        root=Path(group['directory']);self.assertEqual(inspect_group(root)['integrity'],'verified')
        rows=[json.loads(line) for line in (root/'posture_predictions.jsonl').read_text().splitlines()]
        self.assertTrue(any(r['status']=='held' for r in rows))
        self.assertTrue(any(r['label']=='supine' for r in rows))
        self.assertTrue(all(not r['identity_verified'] for r in rows))

    def test_live_fusion_no_bed_record_replay_and_tampering(self):
        import json
        from pathlib import Path
        from bed_pose.fusion_inputs import inspect_group
        from bed_pose.io_utils import load_json,atomic_json,file_hash
        from bed_pose.contracts import IntegrityError
        gid=self.start_pair()
        result=self.wait(lambda x:x['groups'][0]['posture_assessment'].get('fusion_evidence',{}).get('mode')=='two_view_fusion' and all(s['recorded_frames']>=4 for s in x['sessions']))
        self.assertEqual(result['groups'][0]['posture_assessment']['label'],'supine')
        self.assertEqual(result['groups'][0]['posture_assessment']['fusion_evidence']['mode'],'two_view_fusion')
        self.request('/api/group',{'action':'stop','group_id':gid})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0];root=Path(group['directory'])
        self.assertEqual(inspect_group(root)['integrity'],'verified')
        manifest=load_json(root/'manifest.json');self.assertEqual(manifest['posture_predictions']['method'],'gated_image_score_fusion_v1')
        path=root/'posture_predictions.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        row=next(r for r in rows if r.get('fusion_evidence',{}).get('state_scores'));self.assertEqual(row['version'],'group-posture-0.3')
        row['fusion_evidence']['state_scores']['lying']=0.
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['posture_predictions']['sha256']=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):inspect_group(root)
