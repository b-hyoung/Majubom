import json
from pathlib import Path
import unittest
import test_web_workspace as fixtures
import test_group_session as groups
from bed_pose.fusion_inputs import inspect_group,build_inputs,view_features
from bed_pose.io_utils import load_json,atomic_json,file_hash
from bed_pose.contracts import IntegrityError


class FusionTests(unittest.TestCase):
    setUp=fixtures.WebTests.setUp
    cleanup=fixtures.WebTests.cleanup
    request=fixtures.WebTests.request
    wait=fixtures.WebTests.wait
    demo=fixtures.WebTests.demo
    control=fixtures.WebTests.control
    prepare_pair=groups.GroupTests.prepare_pair

    def start_pair(self):
        ids=self.prepare_pair();selections=[]
        for sid in ids:
            capture=self.workspace.capture_target(sid)
            candidate=capture['people'][0]['candidate_id']
            result=self.workspace.select_target({'ticket':capture['ticket'],'candidate_id':candidate,
                                                 'tracking_config':{'max_gap_ms':10000}})
            self.wait(lambda x:any(s['id']==sid and s.get('target_selection_applied')==result['selection_id'] for s in x['sessions']))
            selections.append({'ticket':capture['ticket'],'candidate_id':candidate})
        status,result=self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':1000,
                        'target_selections':selections,'same_person_confirmed':True,'tracking_required':True})
        self.assertEqual(status,200)
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        return ids,result['group_id']

    def finish(self,group_id):
        self.request('/api/group',{'action':'stop','group_id':group_id})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        root=Path(group['directory'])
        rows=[json.loads(line) for line in (root/'fusion_inputs.jsonl').read_text(encoding='utf-8').splitlines()]
        return root,group,rows

    def test_two_views_are_separate_with_masks_and_no_predictions(self):
        ids,gid=self.start_pair();root,group,rows=self.finish(gid)
        result=inspect_group(root)
        self.assertEqual(result['integrity'],'verified')
        import contextlib,io
        from bed_pose.cli import main
        output=io.StringIO()
        with contextlib.redirect_stdout(output):self.assertEqual(main(['group-inspect','--run',str(root)]),0)
        self.assertEqual(json.loads(output.getvalue())['integrity'],'verified')
        self.assertGreater(result['input_counts']['candidate_input'],0)
        pair=next(r for r in rows if r['status']=='candidate_input')
        self.assertEqual([v['input_id'] for v in pair['views']],ids)
        self.assertTrue(all(len(v['joints'])==17 for v in pair['views']))
        self.assertTrue(all(r['verified_gt'] is None and r['target_posture'] is None and r['sensor_results'] is None for r in rows))
        self.assertTrue(all(r['posture_prediction']=={'status':'not_implemented','label':None} for r in rows))
        self.assertFalse(pair['identity_verified']);self.assertFalse(pair['exposure_synchronized'])
        self.assertEqual(sum(group['fusion_inputs']['counts'].values()),len(rows))

    def test_reselection_requires_time_scoped_pair_reconfirmation(self):
        ids,gid=self.start_pair()
        capture=self.workspace.capture_target(ids[0])
        result=self.workspace.select_target({'ticket':capture['ticket'],'candidate_id':capture['people'][0]['candidate_id'],
                                               'tracking_config':{'max_gap_ms':10000}})
        self.wait(lambda x:x['groups'][0]['target_pair_status']=='reconfirmation_required')
        count=self.workspace.sessions[ids[0]].state['recorded_frames']
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=count+3)
        self.request('/api/group',{'action':'confirm_targets','group_id':gid,'same_person_confirmed':True})
        count=self.workspace.sessions[ids[0]].state['recorded_frames']
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=count+3)
        root,group,rows=self.finish(gid)
        self.assertTrue(any('operator_pair_confirmation_missing' in r['hold_reasons'] for r in rows))
        self.assertTrue(any(r['status']=='candidate_input' for r in rows))
        stamp=int(group['target_pair_confirmations'][0]['requested_perf_counter_ns'])
        for r in rows:
            if r['status']=='candidate_input' and any(v['tracking']['track_id']==group['target_pair_confirmations'][0]['members'][0]['track_id'] for v in r['views']):
                self.assertTrue(all(int(v['time_ns'])>=stamp for v in r['views']))
        self.assertEqual(inspect_group(root)['integrity'],'verified')

    def test_semantic_feature_corruption_rejected_even_after_rehash(self):
        _,gid=self.start_pair();root,group,rows=self.finish(gid)
        row=next(r for r in rows if r['status']=='candidate_input')
        row['views'][0]['joints'][0]['image_xy'][0]+=1
        path=root/'fusion_inputs.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json');manifest['fusion_inputs']['sha256']=file_hash(path)
        atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):inspect_group(root)

    def test_reference_gap_fails_without_overwriting_existing_features(self):
        _,gid=self.start_pair();root,group,rows=self.finish(gid)
        path=root/'alignment.jsonl';lines=path.read_text(encoding='utf-8').splitlines();path.write_text('\n'.join(lines[1:])+'\n',encoding='utf-8')
        group['alignment']['sha256']=file_hash(path)
        original=(root/'fusion_inputs.jsonl').read_bytes()
        with self.assertRaises(IntegrityError):build_inputs(group,root)
        self.assertEqual((root/'fusion_inputs.jsonl').read_bytes(),original)
        self.assertFalse((root/'fusion_inputs.jsonl.tmp').exists())

    def test_missing_zero_outside_coordinates_and_missing_bed_stay_distinct(self):
        _,gid=self.start_pair();root,group,_=self.finish(gid)
        source=Path(group['members'][0]['run_directory']);manifest=load_json(source/'manifest.json')
        row=json.loads((source/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()[0])
        person=next(p for p in row['mapped_people'] if p['candidate_id']==row['target_tracking']['candidate_id'])
        person['joints'][0].update(availability='missing',x_px=None,y_px=None,reason='not_output')
        person['joints'][1].update(x_px=-5,y_px=10,flags=['outside_image'])
        person['joints'][2].update(x_px=0,y_px=0,flags=[])
        result=view_features(row,manifest,{row['bed_region_revision']:None},'test')
        joints=result['joints']
        self.assertEqual(joints[0]['image_xy'],[None,None]);self.assertFalse(joints[0]['image_mask'])
        self.assertLess(joints[1]['image_xy'][0],0);self.assertFalse(joints[1]['image_mask'])
        self.assertEqual(joints[2]['image_xy'],[0,0]);self.assertTrue(joints[2]['image_mask'])
        self.assertTrue(all(j['bed_xy']==[None,None] and not j['bed_mask'] for j in joints))
        self.assertIn('bed_region_missing',result['hold_reasons'])

    def test_background_finalization_keeps_other_previews_responsive(self):
        import threading
        from unittest.mock import patch
        ids,gid=self.start_pair()
        other=self.workspace.add({'kind':'demo'})['id']
        self.wait(lambda x:any(s['id']==other and s['frame_id'] is not None for s in x['sessions']))
        before=self.workspace.sessions[other].state['sequence']
        release=threading.Event();entered=threading.Event()
        def delayed(group,root):
            entered.set();release.wait(timeout=3)
            return build_inputs(group,root)
        with patch('bed_pose.fusion_inputs.build_inputs',side_effect=delayed):
            self.request('/api/group',{'action':'stop','group_id':gid})
            try:
                self.assertTrue(entered.wait(timeout=3))
                state=self.wait(lambda x:any(s['id']==other and s['sequence']>=before+2 for s in x['sessions']))
                self.assertEqual(state['groups'][0]['phase'],'finalizing')
                self.assertEqual(self.request('/api/state')[0],200)
            finally:release.set()
            group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        self.assertEqual(inspect_group(group['directory'])['integrity'],'verified')

    def test_remove_during_finalization_closes_inputs_and_preserves_artifacts(self):
        import threading
        from unittest.mock import patch
        ids,gid=self.start_pair()
        entered=threading.Event();release=threading.Event()
        def delayed(group,root):
            entered.set();release.wait(timeout=5)
            return build_inputs(group,root)
        with patch('bed_pose.fusion_inputs.build_inputs',side_effect=delayed):
            self.request('/api/group',{'action':'stop','group_id':gid})
            try:
                self.assertTrue(entered.wait(timeout=3))
                self.assertEqual(self.control(ids[0],'remove')[0],200)
                state=self.wait(lambda x:len(x['sessions'])==1 and x['sessions'][0]['closed'])
                self.assertEqual(state['groups'][0]['phase'],'finalizing')
            finally:release.set()
            group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        self.assertEqual(inspect_group(group['directory'])['integrity'],'verified')

    def test_group_region_change_clear_and_restore_recorded(self):
        ids,gid=self.start_pair();sid=ids[0]
        def change(box):
            selected=self.workspace.sessions[sid].state['frame_id']
            self.assertEqual(self.control(sid,'region',box=box,selected_frame_id=selected)[0],200)
            self.wait(lambda x:x['sessions'][0]['pending'] is None)
            count=self.workspace.sessions[sid].state['recorded_frames']
            self.wait(lambda x:x['sessions'][0]['recorded_frames']>=count+2)
        change([10,10,150,150]);change(None);change([20,20,180,180])
        root,group,rows=self.finish(gid)
        self.assertTrue(any('left:bed_region_missing' in r['hold_reasons'] for r in rows))
        revisions={r['views'][0]['bed_region_revision'] for r in rows if r['views'][0]}
        self.assertGreaterEqual(len(revisions),3)
        self.assertEqual(inspect_group(root)['integrity'],'verified')

    def test_bed_coordinate_availability_and_membership_are_independent(self):
        _,gid=self.start_pair();_,group,_=self.finish(gid)
        source=Path(group['members'][0]['run_directory']);manifest=load_json(source/'manifest.json')
        row=json.loads((source/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()[0])
        person=next(p for p in row['mapped_people'] if p['candidate_id']==row['target_tracking']['candidate_id'])
        points=[(50,50),(250,250),(10,10),(100,100),(-5,50)]
        for joint,(x,y) in zip(person['joints'],points):joint.update(availability='predicted',x_px=x,y_px=y,flags=[])
        person['joints'][5].update(availability='missing',x_px=None,y_px=None,reason='not_output')
        region={'x1':10,'y1':10,'x2':100,'y2':100}
        joints=view_features(row,manifest,{row['bed_region_revision']:region},'test')['joints']
        self.assertEqual([j['inside_bed'] for j in joints[:6]],[True,False,True,True,False,None])
        self.assertTrue(joints[1]['bed_mask']);self.assertGreater(joints[1]['bed_xy'][0],1)
        self.assertFalse(joints[4]['bed_mask'])
        missing=view_features(row,manifest,{row['bed_region_revision']:None},'test')['joints']
        self.assertTrue(all(j['inside_bed'] is None and not j['bed_mask'] for j in missing))

    def test_legacy_two_view_records_still_verify(self):
        from bed_pose.fusion_inputs import LEGACY_SCHEMA,LEGACY_POLICY
        from bed_pose.io_utils import object_hash
        _,gid=self.start_pair();root,group,rows=self.finish(gid)
        for row in rows:
            row['schema_version']=LEGACY_SCHEMA
            for view in row['views']:
                if view and view['joints']:
                    for joint in view['joints']:joint.pop('inside_bed')
        path=root/'fusion_inputs.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json')
        manifest['fusion_inputs'].update(schema_version=LEGACY_SCHEMA,policy=LEGACY_POLICY,
            policy_sha256=object_hash(LEGACY_POLICY),sha256=file_hash(path))
        atomic_json(root/'manifest.json',manifest)
        self.assertEqual(inspect_group(root)['integrity'],'verified')
