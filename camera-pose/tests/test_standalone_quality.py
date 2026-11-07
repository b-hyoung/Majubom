"""Regression cases for partial crops, slow processing and explicit failures."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

from bed_pose.body_state import observe, validate_observation
from bed_pose.clip_posture import decide, validate_policy
from bed_pose.image_quality import inspect_crop
from bed_pose.target_tracking import TargetTracker
from bed_pose.live_factory import make_engine
from bed_pose.live_sources import CameraSource
from test_posture_evidence import sample
from test_clip_posture import configuration
from test_target_tracking import person,row


class QualityTests(unittest.TestCase):
    def test_partial_body_is_rejected_without_requiring_both_sides(self):
        r=sample();p=r['mapped_people'][0];cid=p['candidate_id']
        points,q=inspect_crop(r,cid,640,360)
        self.assertEqual(q['status'],'usable')
        for i in (6,12,14,16):p['joints'][i]['availability']='missing'
        self.assertEqual(inspect_crop(r,cid,640,360)[1]['status'],'usable')
        for i in (13,15):p['joints'][i]['availability']='missing'
        self.assertIn('crop_lower_body_missing',inspect_crop(r,cid,640,360)[1]['reasons'])
        for j in p['joints']:j['availability']='missing'
        for i in (0,1):p['joints'][i]['availability']='predicted'
        self.assertEqual(inspect_crop(r,cid,640,360)[1]['status'],'held')

    def test_low_confidence_and_invalid_scores_are_not_crop_evidence(self):
        for value in (0.1,None,float('nan'),True):
            r=sample()
            for i in (11,12):r['raw_candidates'][0]['sdk_landmarks']['image'][i]['visibility']=value
            self.assertIn('crop_shoulder_missing',inspect_crop(r,'p0',640,360)[1]['reasons'])

    def test_provider_error_is_separate_and_does_not_leak_message(self):
        class Provider:
            profile={'model_id':'test','decision_policy':{'crop_quality':True}}
            calls=0
            def estimate(self,inp):
                self.calls+=1
                raise RuntimeError('/private/path secret message')
        provider=Provider();r=sample()
        result=observe(r,np.zeros((360,640,3),np.uint8),{'candidate_id':'p0'},provider,provider.profile)
        self.assertEqual(result['error'],{'code':'IMAGE_PROVIDER_ERROR','exception_type':'RuntimeError'})
        self.assertNotIn('private',str(result));self.assertEqual(r['processing_status'],'ok')
        validate_observation(result,r,'p0',provider.profile)
        for j in r['mapped_people'][0]['joints'][13:]:j['availability']='missing'
        result=observe(r,np.zeros((360,640,3),np.uint8),{'candidate_id':'p0'},provider,provider.profile)
        self.assertEqual(provider.calls,1)
        self.assertEqual(result['reasons'],['body_state_crop_quality_insufficient'])

    def test_legacy_clip_scores_remain_replayable(self):
        cfg=configuration();cfg.pop('crop_quality')
        cfg.update(version='clip-posture-0.1',preprocess='selected_joint_crop_10pct_then_aspect_preserving_square_pad_224_clip_mean_v1')
        validate_policy(cfg)
        result=decide(dict.fromkeys(('lying','sitting','standing','other','supine','side','prone'),0.),10.,cfg)
        self.assertEqual(result['details']['version'],'clip-posture-0.1')

    def test_silhouette_factory_defaults_off_and_opt_in_enables_sdk(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);model=root/'model.task';model.write_bytes(b'fixture')
            for enabled in (False,True):
                engine=make_engine(CameraSource(),model,root,root,**({'silhouette':True} if enabled else {}))
                self.assertEqual(engine.adapter.silhouette,enabled)


class SlowTrackingTests(unittest.TestCase):
    def test_engine_measures_work_but_does_not_discount_idle_time(self):
        from bed_pose.live_engine import LiveEngine
        from bed_pose.live_sources import SyntheticSource
        from bed_pose.pose.fake import FakeAdapter
        from test_body_state import ImageModel
        from test_live import Clock
        from bed_pose.live_recording import verify_live
        clock=Clock()
        class SlowModel(ImageModel):
            def estimate(self,observation):
                clock.value+=600_000_000
                return super().estimate(observation)
        with tempfile.TemporaryDirectory() as d:
            engine=LiveEngine(SyntheticSource(interval=0),FakeAdapter(),{'execution_mode':'ordered_sequence'},
                              d,execution_kind='synthetic',clock=clock,body_state_provider=SlowModel())
            try:
                engine.open();first=engine.step()
                candidate=first['row']['mapped_people'][0]['candidate_id']
                engine.target_tracker.select(0,candidate)
                directory=engine.start_measurement();second=engine.step()['row']
                self.assertGreaterEqual(int(second['previous_processing_ns']),600_000_000)
                self.assertEqual(second['target_tracking']['status'],'tracking_provisional')
                clock.value+=600_000_000
                self.assertEqual(engine.step()['row']['target_tracking']['reason'],'observation_gap')
            finally:engine.close()
            self.assertEqual(verify_live(directory)['integrity'],'verified')

    def tracker(self):
        t=TargetTracker();t.observe(row(0,[person('p0',10)]),100,100);t.select(0,'p0');return t

    def test_measured_work_gap_does_not_latch_but_missing_does(self):
        t=self.tracker();r=row(6,[person('p0',10)]);r['previous_processing_ns']='550000000'
        a=t.observe(r,100,100)
        self.assertEqual(a['status'],'tracking_provisional')
        self.assertEqual(a['reason'],'unique_motion_candidate_after_processing_delay')
        r=row(7,[]);self.assertEqual(t.observe(r,100,100)['reason'],'target_missing')
        self.assertEqual(t.observe(row(8,[person('p0',10)]),100,100)['status'],'held')

    def test_unmeasured_idle_and_file_pts_keep_gap_policy(self):
        t=self.tracker();self.assertEqual(t.observe(row(6,[person('p0',10)]),100,100)['reason'],'observation_gap')
        t=self.tracker();r=row(6,[person('p0',10)]);r.update(previous_processing_ns='550000000',source_media={'relative_time_ns':r['received_ns']})
        self.assertEqual(t.observe(r,100,100)['reason'],'observation_gap')

    def test_slow_work_cannot_expand_motion_gate_or_resolve_ambiguity(self):
        t=self.tracker();r=row(20,[person('p0',90)]);r['previous_processing_ns']='1900000000'
        self.assertEqual(t.observe(r,100,100)['reason'],'motion_or_visibility_gate_failed')
        t=self.tracker();r=row(6,[person('a',10),person('b',11)]);r['previous_processing_ns']='550000000'
        self.assertEqual(t.observe(r,100,100)['reason'],'target_ambiguous')
