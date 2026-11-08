"""Posture evidence gates and regression coverage, not accuracy measurements."""
import unittest
from dataclasses import replace
from bed_pose.automatic_posture import AutomaticClassifier,EVIDENCE_DEFAULTS,FOOT_DEFAULTS
from bed_pose.separated_posture import bed_relation
from bed_pose.multiview_posture import EVIDENCE_POLICY,fuse
from test_automatic_posture import observation
from test_multiview_posture import views


class EvidencePostureTests(unittest.TestCase):
    def setUp(self):
        self.region=(100,100,500,300)
        self.points=list(observation('supine').points)
        self.points[5],self.points[6]=(370,120),(420,120)
        self.points[11],self.points[12]=(390,175),(410,225)
        self.points[13],self.points[14]=(465,175),(470,225)
        self.points[15],self.points[16]=(530,175),(540,225)
        self.state={'status':'predicted','state':'sitting','lying_direction':'unknown','reasons':[]}

    def classify(self,points=None,state=None,region='default',cfg=EVIDENCE_DEFAULTS):
        sample=replace(observation('supine'),points=tuple(self.points if points is None else points),
                       bed_rectangle=self.region if region=='default' else region,
                       body_state=state or self.state)
        return AutomaticClassifier(cfg).evaluate(sample)

    def test_edge_requires_hips_knees_and_ankles(self):
        label,reason,e=self.classify()
        self.assertEqual((label,reason),('edge_sitting',None))
        self.assertTrue(e['bed_relation']['edge_posture_evidence']['supported'])
        for index in (13,14,15,16):
            points=self.points.copy();points[index]=None
            self.assertIsNone(self.classify(points)[0])
        for index in (15,16):
            points=self.points.copy();points[index]=(450,180)
            self.assertEqual(self.classify(points)[1],'edge_leg_evidence_unconfirmed')

    def test_hip_near_edge_alone_cannot_trigger_and_old_rule_unchanged(self):
        points=self.points.copy();points[11],points[12]=(480,170),(480,230)
        points[13]=points[14]=points[15]=points[16]=None
        self.assertIsNone(self.classify(points)[0])
        self.assertEqual(self.classify(points,cfg=FOOT_DEFAULTS)[0],'edge_sitting')

    def test_lying_needs_bed_and_torso_and_prone_is_supported(self):
        state={'status':'predicted','state':'lying','lying_direction':'supine','reasons':[]}
        self.assertEqual(self.classify(state=state)[0],'supine')
        self.assertEqual(self.classify(state=state,region=None)[1],'bed_relation_unconfirmed')
        points=self.points.copy();points[11],points[12]=(550,180),(560,200)
        self.assertEqual(self.classify(points,state)[1],'bed_relation_unconfirmed')
        points=self.points.copy();points[5]=None
        self.assertEqual(self.classify(points,state)[1],'automatic_torso_joints_missing')
        state['lying_direction']='prone'
        self.assertEqual(self.classify(state=state)[0],'prone')

    def test_sitting_on_bed_needs_four_legs_inside_and_hip_away_from_edge(self):
        points=self.points.copy()
        points[11],points[12]=(280,170),(320,180)
        points[13],points[14]=(300,190),(350,190)
        points[15],points[16]=(280,220),(350,220)
        self.assertEqual(self.classify(points)[0],'sitting')
        points[15]=(530,180)
        self.assertIsNone(self.classify(points)[0])

    def test_two_view_cannot_bypass_edge_or_bed_gate(self):
        v=views()
        for x in v:
            b=x['assessment']['rule_features']['body_state']
            b['scores']={'lying':.05,'sitting':.85,'standing':.05,'other':.05}
        r=bed_relation(tuple(self.points),self.region,EVIDENCE_DEFAULTS,640,360)
        v[0]['assessment']['rule_features']['bed_relation']=r
        self.assertEqual(fuse(v,EVIDENCE_POLICY)['label'],'edge_sitting')
        r['edge_posture_evidence']['supported']=False
        self.assertIsNone(fuse(v,EVIDENCE_POLICY)['label'])
        r['edge_posture_evidence']['supported']=True
        v[1]['assessment']['rule_features']['bed_relation']={'status':'observed','relation':'outside'}
        self.assertIsNone(fuse(v,EVIDENCE_POLICY)['label'])

    def test_low_visibility_is_excluded_but_missing_presence_is_allowed(self):
        from bed_pose.posture import assess_frame
        from bed_pose.pose.mediapipe import COCO_INDICES
        from test_posture_evidence import sample
        row=sample();landmarks=row['raw_candidates'][0]['sdk_landmarks']['image']
        for joint in landmarks:joint['presence']=None
        classifier=AutomaticClassifier(EVIDENCE_DEFAULTS)
        valid=assess_frame(row,640,360,self.region,classifier)['usable_joints']
        landmarks[COCO_INDICES[15]]['visibility']=.1
        self.assertEqual(assess_frame(row,640,360,self.region,classifier)['usable_joints'],valid-1)

    def test_gpu_missing_presence_crop_uses_visibility_and_preserves_old_policy(self):
        from bed_pose.image_quality import inspect_crop
        from test_posture_evidence import sample
        row=sample()
        for joint in row['raw_candidates'][0]['sdk_landmarks']['image']:joint['presence']=None
        candidate=row['mapped_people'][0]['candidate_id']
        _,old=inspect_crop(row,candidate,640,360)
        _,new=inspect_crop(row,candidate,640,360,allow_missing_presence=True)
        self.assertEqual(old['status'],'held')
        self.assertEqual(new['status'],'usable')
        self.assertEqual(new['version'],'joint-crop-quality-0.2')

    def test_new_labels_survive_timeline_stability_and_accuracy(self):
        from bed_pose.record_timeline import posture_state
        from bed_pose.posture_stability import Stabilizer
        from bed_pose.posture_accuracy import summarize,EVIDENCE_LABELS
        for name in ('sitting','prone'):
            assessment={'status':'predicted','label':name,'reasons':[]}
            row={'processing_status':'ok','stream_frame_id':1,'posture_assessment':assessment}
            self.assertEqual(posture_state(row),name)
            model=Stabilizer()
            for i in range(3):result=model.observe(assessment,i*250_000_000,i,{})
            self.assertEqual(result['label'],name)
            metrics=summarize([row],{1:{'label':name}},EVIDENCE_LABELS)
            self.assertEqual(metrics['confusion'][name][name],1)


if __name__=='__main__':unittest.main()
