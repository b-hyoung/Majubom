import unittest
from bed_pose.posture_diagnostics import PostureDiagnostics
from bed_pose.automatic_posture import AutomaticClassifier,DEFAULTS,OLD_DEFAULTS,LEGACY_METHOD
from bed_pose.posture import PostureInput
from test_automatic_posture import observation

class PartialJointTests(unittest.TestCase):
    def test_lying_without_lower_limbs_but_torso_still_required(self):
        model=AutomaticClassifier()
        for label in ('supine','side'):
            obs=observation(label);p=list(obs.points)
            for i in (13,14,15,16):p[i]=None
            partial=PostureInput(tuple(p),640,360,obs.bed_rectangle)
            self.assertEqual(model.predict(partial),label)
            self.assertEqual(AutomaticClassifier(dict(OLD_DEFAULTS,version=LEGACY_METHOD)).evaluate(partial)[1],'automatic_joints_missing')
            p[11]=None
            self.assertEqual(model.evaluate(PostureInput(tuple(p),640,360,obs.bed_rectangle))[1],'automatic_torso_joints_missing')

    def test_missing_lower_body_does_not_turn_upright_into_lying(self):
        for label in ('standing','edge_sitting'):
            obs=observation(label);p=list(obs.points);p[15]=None
            self.assertEqual(AutomaticClassifier().evaluate(PostureInput(tuple(p),640,360,obs.bed_rectangle))[1],'automatic_lower_body_needed')

class DiagnosticsTests(unittest.TestCase):
    def assessment(self,status,frame=1,reason='test',model='model'):
        return {'classifier_id':model,'stream_frame_id':frame,'status':status,'reasons':[reason] if status=='held' else []}

    def test_counts_and_eviction(self):
        d=PostureDiagnostics(window=3)
        for i,status in enumerate(('held','held','predicted','unavailable')):result=d.observe(self.assessment(status,i),1)
        self.assertEqual((result['processed_frames'],result['held'],result['predicted'],result['unavailable']),(3,1,1,1))
        self.assertEqual(result['hold_reason_counts'],{'test':1});self.assertAlmostEqual(result['hold_fraction'],1/3)
        self.assertEqual(result['last_stream_frame_id'],3)

    def test_scope_resets_on_model_and_region(self):
        d=PostureDiagnostics();d.observe(self.assessment('held'),1)
        result=d.observe(self.assessment('predicted'),2)
        self.assertEqual(result['processed_frames'],1);self.assertEqual(result['hold_reason_counts'],{})
        result=d.observe(self.assessment('held',model='new'),2)
        self.assertEqual(result['processed_frames'],1);self.assertEqual(result,d.latest)
