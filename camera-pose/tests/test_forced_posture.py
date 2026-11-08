"""Best-score selection uses available evidence; it is not an accuracy test."""
import unittest
from bed_pose.posture_decision import finalize_forced
from bed_pose.multiview_posture import FORCED_POLICY,fuse
from bed_pose.automatic_posture import FORCED_DEFAULTS,AutomaticClassifier
from test_multiview_posture import views


def state(scores,directions=None,status='held'):
    return {'status':status,'state':'unknown','lying_direction':'unknown','reasons':['body_state_unknown'],
            'scores':scores,'details':{'direction_scores':directions or {'supine':.2,'side':.6,'prone':.2}}}


class ForcedPostureTests(unittest.TestCase):
    def test_low_state_score_still_selects_and_marks_warning(self):
        s=state({'lying':.25,'sitting':.40,'standing':.15,'other':.20})
        relation={'status':'observed','relation':'edge','edge_posture_evidence':{'supported':False}}
        label,reason,e=finalize_forced(s,relation)
        self.assertEqual((label,reason),('edge_sitting',None))
        self.assertIn('below_state_threshold_or_tied',e['warnings'])
        self.assertIn('edge_based_on_hip_only',e['warnings'])
        self.assertFalse(e['physical_posture_verified'])

    def test_standing_other_and_lying_are_not_forced_into_sitting(self):
        for values,expected in (([.1,.2,.6,.1],'standing'),([.1,.2,.1,.6],'other'),([.4,.3,.2,.1],'side')):
            scores=dict(zip(('lying','sitting','standing','other'),values))
            self.assertEqual(finalize_forced(state(scores),{})[0],expected)

    def test_no_scores_cannot_invent_a_posture_and_ties_are_deterministic(self):
        self.assertIsNone(finalize_forced({'reasons':['missing'],'scores':{}},{})[0])
        label,_,e=finalize_forced(state(dict.fromkeys(('lying','sitting','standing','other'),.25)),{})
        self.assertEqual(label,'side');self.assertIn('state_tie',e['warnings'])

    def test_two_camera_low_scores_are_used(self):
        v=views()
        for x in v:
            x['assessment']['rule_features']['body_state']['scores']={'lying':.3,'sitting':.4,'standing':.1,'other':.2}
        result=fuse(v,FORCED_POLICY)
        self.assertEqual(result['label'],'sitting')
        self.assertTrue(result['fusion_evidence']['forced_decision']['available'])

    def test_profile_keeps_legacy_classifier_separate(self):
        self.assertTrue(AutomaticClassifier(FORCED_DEFAULTS).model_id.startswith('automatic-posture-rules-0.12:'))


if __name__=='__main__':unittest.main()
