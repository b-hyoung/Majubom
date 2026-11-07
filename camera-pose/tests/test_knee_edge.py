"""Knee-supported image relation; no claim of physical or clinical accuracy."""
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from bed_pose.automatic_posture import BODY_STATE_DEFAULTS, KNEE_DEFAULTS, FOOT_DEFAULTS, AutomaticClassifier
from bed_pose.posture_decision import finalize
from bed_pose.separated_posture import bed_relation
from test_automatic_posture import observation
class KneeEdgeTests(unittest.TestCase):
    def setUp(self):
        self.region = (100, 100, 500, 300)
        self.points = [None] * 17
        self.points[11], self.points[12] = (390, 175), (410, 225)
        self.points[13], self.points[14] = (530, 175), (540, 225)

    def relation(self, points=None, config=KNEE_DEFAULTS):
        return bed_relation(points or self.points, self.region, config, 640, 360)

    def test_crossing_knees_support_edge_only_in_new_version(self):
        old = self.relation(config=BODY_STATE_DEFAULTS)
        new = self.relation()
        self.assertEqual(old['relation'], 'inside')
        self.assertEqual(new['relation'], 'edge')
        self.assertTrue(new['knee_edge_evidence']['supported'])
        state = {'status':'predicted','state':'sitting','lying_direction':'unknown','reasons':[]}
        self.assertEqual(finalize(state, old)[0], None)
        self.assertEqual(finalize(state, new)[0], 'edge_sitting')

    def test_missing_opposite_or_inside_knees_do_not_create_edge(self):
        for knees in ((None, (540,225)), ((530,175), (480,225)), ((530,175), (80,225)), ((530,175), (540,80))):
            points = list(self.points); points[13], points[14] = knees
            result = self.relation(points)
            self.assertEqual(result['relation'], 'inside')
            self.assertFalse(result['knee_edge_evidence']['supported'])

    def test_hips_deep_inside_and_outside_remain_conservative(self):
        points = list(self.points); points[11], points[12] = (290,175), (310,225)
        self.assertEqual(self.relation(points)['relation'], 'inside')
        points[11], points[12] = (530,175), (540,225)
        self.assertEqual(self.relation(points)['relation'], 'outside')

    def test_new_profile_validates_and_old_profile_stays_usable(self):
        self.assertNotEqual(AutomaticClassifier(KNEE_DEFAULTS).model_id,
                            AutomaticClassifier(BODY_STATE_DEFAULTS).model_id)
        bad = dict(KNEE_DEFAULTS, knee_edge_hip_limit=.6)
        with self.assertRaises(ValueError): AutomaticClassifier(bad)

    def test_sitting_state_and_knee_evidence_produce_edge_prediction(self):
        original = observation('supine')
        points = list(original.points)
        for index in (11, 12, 13, 14): points[index] = self.points[index]
        state = {'status':'predicted','state':'sitting','lying_direction':'unknown','reasons':[]}
        sample = replace(original, points=tuple(points), bed_rectangle=self.region, body_state=state)
        self.assertEqual(AutomaticClassifier(KNEE_DEFAULTS).evaluate(sample)[0], 'edge_sitting')
        self.assertIsNone(AutomaticClassifier(BODY_STATE_DEFAULTS).evaluate(sample)[0])

    def test_group_accepts_new_version_for_image_fusion(self):
        from bed_pose.group_session import MeasurementGroup
        from bed_pose.posture_stability import profile
        with TemporaryDirectory() as directory:
            members=[]
            for index in range(2):
                engine=SimpleNamespace(body_state_profile={'model_id':'openai/clip-vit-base-patch16'},
                    posture_classifier=SimpleNamespace(profile=KNEE_DEFAULTS),
                    stability=SimpleNamespace(profile=profile()))
                members.append(SimpleNamespace(id=str(index),title=str(index),worker=SimpleNamespace(engine=engine)))
            group=MeasurementGroup(members,Path(directory))
            self.assertIn('posture_fusion_policy',group.data)

    def test_ankles_support_edge_when_knees_are_inside_near_edge(self):
        points=list(self.points)
        points[13],points[14]=(465,175),(470,225)
        points[15],points[16]=(530,175),(540,225)
        old=self.relation(points,KNEE_DEFAULTS);new=self.relation(points,FOOT_DEFAULTS)
        self.assertEqual(old['relation'],'inside')
        self.assertEqual(new['relation'],'edge')
        self.assertTrue(new['foot_edge_evidence']['supported'])
        self.assertEqual(finalize({'status':'predicted','state':'sitting','lying_direction':'unknown','reasons':[]},new)[0],'edge_sitting')

    def test_ankles_alone_or_missing_mismatched_evidence_do_not_create_edge(self):
        for knees,ankles in [(((465,175),(470,225)),(None,(540,225))),
                              (((465,175),(470,225)),((530,175),(450,225))),
                              (((465,175),(470,225)),((530,175),(80,225))),
                              (((250,175),(260,225)),((530,175),(540,225)))]:
            points=list(self.points);points[13],points[14]=knees;points[15],points[16]=ankles
            result=self.relation(points,FOOT_DEFAULTS)
            self.assertEqual(result['relation'],'inside')
            self.assertFalse(result['foot_edge_evidence']['supported'])


if __name__ == '__main__': unittest.main()
