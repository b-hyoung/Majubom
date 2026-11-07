"""Rule dependency regressions using synthetic coordinates, not accuracy tests."""
from dataclasses import replace
import unittest

from bed_pose.automatic_posture import AutomaticClassifier, DEFAULTS, OLD_DEFAULTS, LEGACY_METHOD, PREVIOUS_METHOD
from bed_pose.posture import assess_frame
from test_automatic_posture import observation


class RuleDependencyTests(unittest.TestCase):
    def test_lying_uses_torso_when_projected_lower_angle_is_undefined(self):
        for label in ('supine', 'side'):
            for joint, replacement in ((15, None), (15, 13), (13, 11), (16, 14)):
                with self.subTest(label=label, joint=joint, replacement=replacement):
                    obs=observation(label);points=list(obs.points)
                    points[joint]=points[replacement] if replacement is not None else None
                    result=AutomaticClassifier().evaluate(replace(obs,points=tuple(points)))
                    self.assertEqual(result[:2],(label,None))
                    self.assertEqual(result[2]['missing_lower_joints'],[joint] if replacement is None else [])
                    self.assertEqual(result[2]['invalid_knee_geometry'],[] if replacement is None else [14 if joint==16 else 13])

    def test_old_versions_retain_their_original_lower_body_gate(self):
        obs=observation('supine');points=list(obs.points);points[15]=points[13]
        for version in (LEGACY_METHOD,PREVIOUS_METHOD):
            model=AutomaticClassifier(dict(OLD_DEFAULTS,version=version))
            self.assertEqual(model.evaluate(replace(obs,points=tuple(points)))[:2],
                             (None,'degenerate_body_geometry'))

    def test_upright_undefined_lower_angle_never_becomes_sitting(self):
        for label in ('standing','edge_sitting'):
            obs=observation(label);points=list(obs.points);points[15]=points[13]
            result=AutomaticClassifier().evaluate(replace(obs,points=tuple(points)))
            self.assertEqual(result[:2],(None,'automatic_lower_body_needed'))

    def test_intermediate_torso_reason_does_not_promise_more_legs_will_help(self):
        obs=observation('supine');points=list(obs.points)
        # A 45 degree torso is between the existing lying/upright thresholds.
        for i in (11,12):points[i]=(points[i][0],points[i][1]+100)
        points[15]=None
        result=AutomaticClassifier().evaluate(replace(obs,points=tuple(points)))
        self.assertEqual(result[:2],(None,'view_or_body_orientation_ambiguous'))

    def test_partial_overhead_vertical_torso_stays_conservative(self):
        model=AutomaticClassifier(dict(DEFAULTS,view='overhead'))
        obs=observation('standing');points=list(obs.points);points[15]=None
        self.assertEqual(model.evaluate(replace(obs,points=tuple(points)))[:2],
                         (None,'automatic_lower_body_needed'))
        self.assertEqual(model.evaluate(observation('edge_sitting'))[:2],
                         (None,'overhead_sitting_ambiguous'))

    def test_missing_torso_face_and_bed_gates_still_apply_with_bad_lower_body(self):
        for joint,reason in ((5,'automatic_torso_joints_missing'),(0,'front_facing_evidence_missing')):
            obs=observation('supine');points=list(obs.points)
            points[15]=points[13];points[joint]=None
            self.assertEqual(AutomaticClassifier().evaluate(replace(obs,points=tuple(points)))[:2],(None,reason))
        obs=observation('side');points=list(obs.points);points[15]=points[13]
        for region,reason in ((None,'bed_region_required'),((1,1,50,50),'body_outside_bed_region')):
            self.assertEqual(AutomaticClassifier().evaluate(replace(obs,points=tuple(points),bed_rectangle=region))[:2],(None,reason))

    def test_intermediate_frame_is_held_instead_of_reusing_previous_label(self):
        model=AutomaticClassifier()
        for label,expected in (('supine','supine'),('standing',None),('side','side')):
            obs=observation(label)
            row={'processing_status':'ok','stream_frame_id':1,'clock_id':'test','received_ns':'1',
                 'mapped_people':[{'candidate_id':'test','joints':[
                     {'x_px':x,'y_px':y,'availability':'predicted'} for x,y in obs.points]}]}
            region=dict(zip(('x1','y1','x2','y2'),obs.bed_rectangle))
            result=assess_frame(row,obs.width,obs.height,region,model)
            self.assertEqual(result['label'],expected)
            self.assertEqual(result['status'],'predicted' if expected else 'held')
