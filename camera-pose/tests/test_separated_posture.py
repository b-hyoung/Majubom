"""Dependency boundaries and record replay, not human posture accuracy."""
import copy
import csv
import json
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from bed_pose.automatic_posture import AutomaticClassifier,SEPARATED_DEFAULTS
from bed_pose.posture import assess_frame
from bed_pose.separated_posture import BodyInput,body_shape,bed_relation
from bed_pose.bed_geometry import make_quadrilateral
from bed_pose.contracts import PoseOutput,IntegrityError
from bed_pose.io_utils import load_json,atomic_json,file_hash
from bed_pose.live_recording import verify_live
from bed_pose.evidence_report import export_evidence
from test_automatic_posture import observation
from test_posture_evidence import sample
import test_web_workspace as web


class SeparationTests(unittest.TestCase):
    def test_bed_edit_removal_and_failure_never_change_body(self):
        model=AutomaticClassifier(SEPARATED_DEFAULTS)
        for pose,shape in (('supine','front_shape'),('side','side_shape')):
            obs=observation(pose);expected=None
            quad=make_quadrilateral([[50,50],[600,70],[550,350],[80,300]],640,360)
            with patch('bed_pose.viewpoint_posture.bed_camera',side_effect=AssertionError('bed plane used')):
                for region in (obs.bed_rectangle,None,(1,1,50,50),quad,{'method':'broken'}):
                    label,reason,e=model.evaluate(replace(obs,bed_rectangle=region))
                    self.assertIsNone(label);self.assertEqual(reason,'physical_posture_unverified')
                    actual=e['body_assessment'];self.assertEqual(actual['label'],shape)
                    self.assertFalse(actual['bed_used']);self.assertFalse(actual['physical_posture_verified'])
                    if expected is None:expected=actual
                    self.assertEqual(actual,expected)
        self.assertNotIn('bed_rectangle',BodyInput.__dataclass_fields__)

    def test_region_membership_is_not_occupancy_or_exit(self):
        obs=observation('supine');cfg=SEPARATED_DEFAULTS
        for region,expected in (((100,100,550,250),'inside'),((290,100,550,250),'edge'),((1,1,50,50),'outside')):
            r=bed_relation(obs.points,region,cfg,640,360)
            self.assertEqual(r['relation'],expected);self.assertEqual(r['physical_occupancy'],'unknown')
        p=list(obs.points);p[11]=(90,150)
        self.assertEqual(bed_relation(p,(100,100,550,250),cfg,640,360)['relation'],'boundary')
        for region,p in ((None,obs.points),((100,100,550,250),(None,)*17),({'method':'broken'},obs.points)):
            r=bed_relation(p,region,cfg,640,360);self.assertEqual(r['status'],'unknown');self.assertIsNone(r['relation'])

    def test_seated_shape_requires_hip_and_knee_evidence_and_is_not_edge_sitting(self):
        obs=observation('supine');xyz=[(0.,0.,0.)]*17
        for s,h,k,a,x in ((5,11,13,15,.2),(6,12,14,16,-.2)):
            xyz[s]=(x,-1.,0.);xyz[h]=(x,0.,0.);xyz[k]=(x,0.,1.);xyz[a]=(x,1.,1.)
        obs=replace(obs,world_points=tuple(xyz),bed_rectangle=None)
        model=AutomaticClassifier(SEPARATED_DEFAULTS)
        _,_,e=model.evaluate(obs)
        self.assertEqual(e['body_assessment']['label'],'seated_shape')
        self.assertIsNone(e['context_assessment']['candidate_label'])
        # Straight hips with bent knees alone are not the seated-shape condition.
        xyz[13]=(.2,1.,0.);xyz[14]=(-.2,1.,0.)
        self.assertNotEqual(model.evaluate(replace(obs,world_points=tuple(xyz)))[2]['body_assessment']['label'],'seated_shape')

    def test_shape_and_overlap_do_not_confirm_lying_or_standing(self):
        model=AutomaticClassifier(SEPARATED_DEFAULTS)
        for pose in ('supine','side','standing','edge_sitting'):
            label,_,e=model.evaluate(observation(pose))
            self.assertIsNone(label);self.assertFalse(e['context_assessment']['physical_posture_verified'])
            self.assertEqual(e['bed_relation']['physical_occupancy'],'unknown')

    def test_failures_identity_and_nonvisual_labels_cannot_create_shape(self):
        row=sample();model=AutomaticClassifier(SEPARATED_DEFAULTS)
        a=assess_frame(row,640,360,None,model)
        self.assertEqual(a['rule_features']['body_assessment']['status'],'candidate')
        changed=copy.deepcopy(row);changed.update(target_posture='edge_sitting',verified_gt='supine',sensor_results={'label':'supine'})
        self.assertEqual(a,assess_frame(changed,640,360,None,model))
        for key,value,reason in (('processing_status','failed','processing_failed'),('mapped_people',[],'no_candidate'),
                                 ('target_tracking',{'status':'held'},'target_held')):
            changed=copy.deepcopy(row);changed[key]=value
            held=assess_frame(changed,640,360,None,model)
            self.assertIsNone(held['label']);self.assertIn(reason,held['reasons'])
            self.assertNotIn('rule_features',held)


class SeparationWebTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    control=web.WebTests.control
    demo=web.WebTests.demo

    def body_input(self):
        from bed_pose.live_engine import LiveEngine
        from bed_pose.pose.fake import FakeAdapter
        from bed_pose.pose.mediapipe import mapping_profile
        class Adapter(FakeAdapter):
            def estimate(self,m):return PoseOutput(copy.deepcopy(sample()['raw_candidates']),m.model_timestamp_ms,True)
        def factory(source,model,output_root,options_root,**kwargs):
            return LiveEngine(source,Adapter(),{'execution_mode':'ordered_sequence'},output_root,
                              profile=mapping_profile(),execution_kind='synthetic')
        self.workspace.factory=factory
        sid=self.demo()
        self.assertEqual(self.control(sid,'posture_mode',mode='automatic')[0],200)
        state=self.wait(lambda x:(x['sessions'][0]['posture_assessment'] or {}).get('rule_features',{}).get('body_assessment',{}).get('status')=='candidate')['sessions'][0]
        self.assertEqual(state['automatic_posture_profile'],SEPARATED_DEFAULTS)
        return sid

    def test_optional_region_frame_api_record_csv_and_rehashed_tamper(self):
        sid=self.body_input()
        status,view=self.request('/api/view/'+sid)
        self.assertEqual(status,200);e=view['posture_assessment']['rule_features']
        self.assertEqual(e['body_assessment']['label'],'front_shape');self.assertIsNone(e['bed_relation']['relation'])
        self.assertEqual(self.control(sid,'start')[0],200)
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.assertEqual(self.control(sid,'region',box=[1,1,50,50],selected_frame_id=view['frame_id'])[0],200)
        self.wait(lambda x:(x['sessions'][0]['posture_assessment'] or {}).get('rule_features',{}).get('bed_relation',{}).get('relation')=='outside')
        _,latest=self.request('/api/view/'+sid)
        self.assertEqual(self.control(sid,'region',box=None,selected_frame_id=latest['frame_id'])[0],200)
        self.wait(lambda x:x['sessions'][0]['region'] is None and x['sessions'][0]['pending'] is None)
        self.assertEqual(self.control(sid,'stop')[0],200)
        state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);self.assertEqual(verify_live(root)['integrity'],'verified')
        manifest=load_json(root/'manifest.json');self.assertIsNone(manifest['initial_bed_region'])
        self.assertEqual(manifest['schema_version'],'live-0.3-draft')
        rows=[json.loads(l) for l in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
        bodies=[r['posture_assessment']['rule_features']['body_assessment'] for r in rows]
        self.assertTrue(all(b==bodies[0] for b in bodies))
        output,_=export_evidence(root,self.root/'report')
        with (output/'features.csv').open(encoding='utf-8-sig',newline='') as stream:report=list(csv.DictReader(stream))
        self.assertTrue(all(r['body_shape_label']=='front_shape' and r['baseline_label']=='' and r['physical_occupancy']=='unknown' for r in report))
        status,opened=self.request('/api/records/open',{'record_id':manifest['measurement_id']});self.assertEqual(status,200)
        status,page=self.request('/api/records/frames?record_id='+manifest['measurement_id']+'&offset=0&limit=50')
        self.assertEqual(status,200);self.assertEqual(page['frames'][0]['body_assessment'],bodies[0])
        rows[0]['posture_assessment']['rule_features']['body_assessment']['label']='side_shape'
        path=root/'live_frames.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(root)


if __name__=='__main__':unittest.main()
