import unittest
from bed_pose.bed_association import eligible, POLICY
from bed_pose.reference_posture import combine_views
from test_multiview_posture import views


class BedAssociationTests(unittest.TestCase):
    def test_empty_multiple_outside_and_missing_hips_are_not_associated(self):
        assessment={'candidate_id':'p0','rule_features':{'bed_relation':{'status':'observed','relation':'inside'}}}
        self.assertTrue(eligible(1,assessment,{'method':'bed'}))
        self.assertFalse(eligible(0,assessment,{'method':'bed'}))
        self.assertFalse(eligible(2,assessment,{'method':'bed'}))
        self.assertFalse(eligible(1,assessment,None))
        assessment['rule_features']['bed_relation']['relation']='outside'
        self.assertFalse(eligible(1,assessment,{'method':'bed'}))
        assessment['rule_features']['bed_relation']={'status':'unknown','relation':None}
        self.assertFalse(eligible(1,assessment,{'method':'bed'}))

    def test_time_gate_and_bed_gate_both_required(self):
        from bed_pose.multiview_posture import POLICY as FUSION
        v=views()
        for x in v:
            x['unique_bed_candidate']=True
            x['assessment']['rule_features']['body_state']['details']['direction_scores']={'supine':.8,'side':.1,'prone':.1}
        a=combine_views(v,50,[],True,1050,fusion_policy=FUSION,association_policy=POLICY)
        self.assertEqual(a['label'],'supine');self.assertFalse(a['identity_verified'])
        v[0]['unique_bed_candidate']=False
        self.assertIn('bed_person_unavailable_or_ambiguous',combine_views(v,50,[],True,1050,fusion_policy=FUSION,association_policy=POLICY)['reasons'])
        v[0]['unique_bed_candidate']=True;v[0]['time_ns']='1'
        self.assertIn('pair_time_mismatch',combine_views(v,50,[],True,1050,fusion_policy=FUSION,association_policy=POLICY)['reasons'])

class BedPriorityTests(unittest.TestCase):
    def person(self, candidate, hip_x, hip_y=50):
        joints=[{'availability':'predicted','x_px':hip_x,'y_px':hip_y,'source_index':i} for i in range(17)]
        return {'candidate_id':candidate,'joints':joints}

    def test_priority_ignores_outside_people_and_refuses_bed_ambiguity(self):
        from bed_pose.bed_association import select,eligible,PRIORITY_POLICY
        from bed_pose.posture import assess_frame
        bed={'x1':10,'y1':10,'x2':60,'y2':90}
        in_bed=self.person('bed',35);passer=self.person('passer',80)
        row={'processing_status':'ok','stream_frame_id':1,'clock_id':'t','received_ns':'100',
             'mapped_people':[passer,in_bed],'target_tracking':{'status':'not_configured'}}
        result=select(row,bed,100,100)
        self.assertEqual(result['candidate_id'],'bed')
        row['bed_target_selection']=result
        assessment=assess_frame(row,100,100,None)
        self.assertEqual(assessment['candidate_id'],'bed')
        self.assertTrue(eligible(2,assessment,bed,selection=result,policy=PRIORITY_POLICY))
        row['mapped_people'].reverse()
        self.assertEqual(select(row,bed,100,100)['candidate_id'],'bed')
        passer['joints'][11]['x_px']=passer['joints'][12]['x_px']=40
        self.assertEqual(select(row,bed,100,100)['reason'],'multiple_bed_candidates')
        row['bed_target_selection']=select(row,bed,100,100)
        self.assertIsNone(assess_frame(row,100,100,None)['candidate_id'])
        in_bed['joints'][11]['availability']='missing'
        passer['joints'][11]['availability']='missing'
        self.assertEqual(select(row,bed,100,100)['reason'],'no_bed_candidate')
        self.assertEqual(select(row,None,100,100)['reason'],'bed_region_missing')

    def test_single_outside_person_keeps_existing_individual_diagnostics(self):
        from bed_pose.bed_association import select,eligible,PRIORITY_POLICY
        from bed_pose.posture import assess_frame
        bed={'x1':0,'y1':0,'x2':60,'y2':99};person=self.person('outside',80)
        row={'processing_status':'ok','stream_frame_id':1,'clock_id':'t','received_ns':'100',
             'mapped_people':[person],'target_tracking':{'status':'not_configured'}}
        row['bed_target_selection']=select(row,bed,100,100)
        assessment=assess_frame(row,100,100,None)
        self.assertEqual(assessment['candidate_id'],'outside')
        self.assertFalse(eligible(1,assessment,bed,selection=row['bed_target_selection'],policy=PRIORITY_POLICY))

    def test_low_visibility_or_hidden_hip_does_not_establish_bed_membership(self):
        from bed_pose.bed_association import select
        bed={'x1':0,'y1':0,'x2':60,'y2':99};person=self.person('bed',35)
        scores=[{'visibility':.9,'presence':.9} for _ in range(17)]
        row={'processing_status':'ok','mapped_people':[person],
             'raw_candidates':[{'candidate_id':'bed','sdk_landmarks':{'image':scores}}]}
        self.assertEqual(select(row,bed,100,100)['status'],'selected')
        scores[11]['visibility']=.1
        self.assertEqual(select(row,bed,100,100)['reason'],'no_bed_candidate')
        scores[11]['visibility']=.9;scores[12]['presence']=None
        self.assertEqual(select(row,bed,100,100)['status'],'selected')

class DetectorConfigurationTests(unittest.TestCase):
    def test_new_live_engines_request_up_to_three_poses(self):
        import tempfile
        from pathlib import Path
        from bed_pose.live_factory import make_engine
        from bed_pose.live_sources import VideoFileSource
        from bed_pose.io_utils import load_json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);model=root/'model.task';model.write_bytes(b'fixture')
            source=VideoFileSource(root/'unused.mp4')
            engine=make_engine(source,model,root/'runs',root)
            self.assertEqual(load_json(engine.settings['adapter_options_path'])['num_poses'],3)

    def test_group_stability_context_resets_when_bed_candidate_changes(self):
        from bed_pose.posture_stability import group_context
        views=[{'input_id':'a','assessment':{'candidate_id':'p0','track_id':None,'classifier_id':'c'}},
               {'input_id':'b','assessment':{'candidate_id':'p0','track_id':None,'classifier_id':'c'}}]
        result={'association_policy':'bed-priority-v2','method':'test','fusion_evidence':{}}
        first=group_context(views,result,[1,1])
        views[0]['assessment']['candidate_id']='p1'
        self.assertNotEqual(first,group_context(views,result,[1,1]))

    def test_changed_bed_area_does_not_reuse_old_candidate(self):
        from bed_pose.bed_association import select,eligible,PRIORITY_POLICY
        person=BedPriorityTests().person('bed',35)
        original={'x1':0,'y1':0,'x2':60,'y2':99}
        selection=select({'processing_status':'ok','mapped_people':[person]},original,100,100)
        assessment={'candidate_id':'bed'}
        self.assertTrue(eligible(1,assessment,original,selection=selection,policy=PRIORITY_POLICY))
        changed={'x1':0,'y1':0,'x2':40,'y2':99}
        self.assertFalse(eligible(1,assessment,changed,selection=selection,policy=PRIORITY_POLICY))

class BedPriorityRecordTests(unittest.TestCase):
    def test_recorded_selection_is_recomputed_and_tampering_rejected(self):
        import json
        import tempfile
        from pathlib import Path
        from bed_pose.contracts import IntegrityError
        from bed_pose.io_utils import load_json,atomic_json,file_hash
        from bed_pose.live_engine import LiveEngine
        from bed_pose.live_recording import verify_live
        from bed_pose.live_sources import SyntheticSource
        from bed_pose.pose.fake import FakeAdapter
        with tempfile.TemporaryDirectory() as tmp:
            engine=LiveEngine(SyntheticSource(interval=.01),FakeAdapter(),{'execution_mode':'ordered_sequence'},Path(tmp),execution_kind='synthetic')
            engine.open();engine.step();engine.set_region([0,0,640,360],640,360)
            root=engine.start_measurement();engine.step();engine.stop_measurement();engine.close()
            self.assertEqual(verify_live(root)['integrity'],'verified')
            path=root/'live_frames.jsonl';rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[0]['bed_target_selection']['status'],'selected')
            rows[0]['bed_target_selection']['candidate_id']='wrong_person'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            manifest=load_json(root/'manifest.json');manifest['artifact_sha256']['live_frames.jsonl']=file_hash(path);atomic_json(root/'manifest.json',manifest)
            with self.assertRaises(IntegrityError):verify_live(root)
