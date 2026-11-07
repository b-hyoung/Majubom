import math
import unittest
from copy import deepcopy
from bed_pose.native_pose_cues import assess


def sample():
    pts=[{'x':.5,'y':.5,'visibility':.9,'presence':.9} for _ in range(33)]
    for i,x,y in [(0,.52,.2),(7,.4,.2),(8,.6,.2),(11,.3,.35),(12,.7,.35),
                  (25,.4,.55),(27,.4,.75),(29,.4,.77),(31,.5,.77),
                  (26,.6,.55),(28,.6,.75),(30,.6,.77),(32,.7,.77)]:pts[i].update(x=x,y=y)
    return {'stream_frame_id':4,'processing_status':'ok','posture_assessment':{'candidate_id':'p1'},
            'raw_candidates':[{'candidate_id':'p1','sdk_landmarks':{'image':pts}}]}


class NativeCuesTests(unittest.TestCase):
    def test_geometry_rotation_scale_and_no_mutation(self):
        row=sample();original=deepcopy(row)
        a=assess(row,500,500)
        self.assertEqual(row,original)
        self.assertFalse(a['affects_prediction'])
        self.assertAlmostEqual(a['face']['nose_lateral_offset_ear_span'],.1)
        self.assertAlmostEqual(a['feet']['left']['foot_shin_angle_degrees'],90)
        for p in row['raw_candidates'][0]['sdk_landmarks']['image']:
            p['x'],p['y']=1-p['y'],p['x']
        b=assess(row,1000,1000)
        for field in ('face','feet'): self.assertEqual(a[field]['status'] if field=='face' else a[field]['left']['status'],'available')
        self.assertAlmostEqual(a['face']['nose_lateral_offset_ear_span'],b['face']['nose_lateral_offset_ear_span'])
        self.assertAlmostEqual(a['feet']['left']['foot_shin_angle_degrees'],b['feet']['left']['foot_shin_angle_degrees'])

    def test_missing_low_confidence_degenerate_and_wrong_target(self):
        row=sample();pts=row['raw_candidates'][0]['sdk_landmarks']['image']
        pts[7]['visibility']=.1
        self.assertEqual(assess(row,500,500)['face']['status'],'unavailable')
        self.assertEqual(assess(row,500,500)['feet']['left']['status'],'available')
        pts[31]['x']={'nonfinite':'nan'}
        self.assertEqual(assess(row,500,500)['feet']['left']['status'],'unavailable')
        row['target_tracking']={'status':'held'}
        self.assertEqual(assess(row,500,500)['feet']['right']['status'],'unavailable')
        row.pop('target_tracking');row['posture_assessment']['candidate_id']='other'
        self.assertEqual(assess(row,500,500)['face']['reason'],'target_unavailable')
        row=sample();pts=row['raw_candidates'][0]['sdk_landmarks']['image'];pts[8]=dict(pts[7])
        self.assertEqual(assess(row,500,500)['face']['reason'],'degenerate_face_or_shoulders')
        row['raw_candidates'][0].pop('sdk_landmarks')
        self.assertEqual(assess(row,500,500)['face']['reason'],'native_landmarks_unavailable')

    def test_record_recompute_rejects_rehashed_tampering(self):
        import json
        from test_live import LiveTests
        from bed_pose.live_recording import verify_live
        from bed_pose.io_utils import load_json, atomic_json, file_hash
        from bed_pose.contracts import IntegrityError
        fixture=LiveTests();fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        engine=fixture.ready(fixture.engine())
        self.addCleanup(engine.close)
        directory=engine.start_measurement();engine.step();engine.stop_measurement()
        self.assertTrue(verify_live(directory)['ledger_complete'])
        path=directory/'live_frames.jsonl'
        row=json.loads(path.read_text(encoding='utf-8'))
        self.assertFalse(row['native_pose_cues']['affects_prediction'])
        row['native_pose_cues']['affects_prediction']=True
        path.write_text(json.dumps(row)+'\n',encoding='utf-8')
        manifest=load_json(directory/'manifest.json')
        manifest['artifact_sha256']['live_frames.jsonl']=file_hash(path)
        atomic_json(directory/'manifest.json',manifest)
        with self.assertRaisesRegex(IntegrityError,'Native pose cues'):
            verify_live(directory)
