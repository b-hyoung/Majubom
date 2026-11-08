"""Reproduce known decision limits; no claim of posture accuracy."""
import unittest

from bed_pose.automatic_posture import FOOT_DEFAULTS
from bed_pose.image_quality import inspect_crop
from bed_pose.posture_decision import finalize
from bed_pose.separated_posture import bed_relation
from test_posture_evidence import sample


class PostureAuditTests(unittest.TestCase):
    def test_hip_only_edge_can_trigger_sitting_without_visible_legs(self):
        points=[None]*17
        points[11],points[12]=(480,170),(480,230)
        relation=bed_relation(points,(100,100,500,300),FOOT_DEFAULTS,640,360)
        state={'status':'predicted','state':'sitting','lying_direction':'unknown','reasons':[]}
        self.assertEqual(relation['relation'],'edge')
        self.assertEqual(finalize(state,relation)[0],'edge_sitting')

    def test_lying_label_does_not_require_bed_relation(self):
        state={'status':'predicted','state':'lying','lying_direction':'supine','reasons':[]}
        self.assertEqual(finalize(state,{'status':'observed','relation':'outside'}),('supine',None))

    def test_absent_presence_values_hold_clip_crop(self):
        row=sample()
        for point in row['raw_candidates'][0]['sdk_landmarks']['image']:
            point['presence']=None
        _,quality=inspect_crop(row,row['mapped_people'][0]['candidate_id'],640,360)
        self.assertEqual(quality['status'],'held')
        self.assertEqual(quality['usable_joints'],0)


if __name__ == '__main__': unittest.main()
