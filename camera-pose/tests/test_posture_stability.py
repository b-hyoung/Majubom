"""Causal state transitions and replay integrity, not posture accuracy."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from bed_pose.posture_stability import Stabilizer,profile,project,frame


def raw(label='supine',status='predicted'):
    return {'status':status,'label':label,'reasons':[]}


class StabilityTests(unittest.TestCase):
    def test_time_and_distinct_samples_are_both_required(self):
        m=Stabilizer()
        self.assertEqual(m.observe(raw(),0,0,{} )['status'],'confirming')
        self.assertEqual(m.observe(raw(),250_000_000,1,{})['status'],'confirming')
        stable=m.observe(raw(),500_000_000,2,{})
        self.assertEqual(stable['label'],'lying');self.assertEqual(stable['confirmation_delay_ms'],500.)
        self.assertIsNone(m.observe(raw(),600_000_000,3,{})['confirmation_delay_ms'])

    def test_unified_lying_stays_stable_and_legacy_profile_remains_distinct(self):
        m=Stabilizer()
        for i,label in enumerate(('supine','lying','supine')):
            result=m.observe(raw(label),i*250_000_000,i,{})
        self.assertEqual((result['status'],result['label']),('stable','lying'))
        self.assertEqual(result['instantaneous_label'],'supine')
        legacy=Stabilizer({'version':'posture-stability-0.1'})
        for i in range(3):result=legacy.observe(raw('supine'),i*250_000_000,i,{})
        self.assertEqual(result['label'],'supine')
        self.assertEqual(legacy.observe(raw('lying'),750_000_000,3,{})['status'],'changing')

    def test_duplicate_polling_cannot_confirm_and_returns_detached_copy(self):
        m=Stabilizer();first=m.observe(raw(),0,0,{})
        for _ in range(100):self.assertEqual(m.observe(raw(),900_000_000,0,{}),first)
        first['candidate_samples']=999
        self.assertEqual(m.observe(raw(),900_000_000,0,{})['candidate_samples'],1)

    def stable(self):
        m=Stabilizer()
        for i in range(3):m.observe(raw(),i*250_000_000,i,{})
        return m

    def test_changed_candidate_never_emits_previous_label_as_current(self):
        m=self.stable();v=m.observe(raw('side'),600_000_000,3,{})
        self.assertEqual(v['status'],'changing');self.assertIsNone(v['label']);self.assertEqual(v['last_confirmed_label'],'lying')
        self.assertEqual(m.observe(raw(),700_000_000,4,{})['label'],'lying')
        for i,t in enumerate((800,1050,1300),5):v=m.observe(raw('side'),t*1_000_000,i,{})
        self.assertEqual(v['label'],'side')

    def test_held_missing_or_out_of_scope_clears_current_and_requires_reconfirmation(self):
        for a in (raw(None,'held'),raw(None,'unavailable'),raw('unsupported')):
            m=self.stable();v=m.observe(a,600_000_000,3,{})
            self.assertEqual(v['status'],'held');self.assertIsNone(v['last_confirmed_label'])
            self.assertEqual(m.observe(raw(),700_000_000,4,{})['status'],'confirming')

    def test_context_gap_and_non_increasing_time_reset(self):
        for timestamp,seq,context,reason in ((600_000_000,3,{'target':'new'},'context_changed'),
                (2_000_000_000,3,{},'observation_gap'),(100_000_000,3,{},'observation_not_increasing')):
            m=self.stable();v=m.observe(raw(),timestamp,seq,context)
            self.assertEqual(v['reset_reason'],reason);self.assertIsNone(v['label'])

    def test_expiry_is_a_read_only_projection_without_new_camera_frames(self):
        m=self.stable();original=deepcopy(m.result)
        self.assertEqual(project(original,2_000_000_001)['status'],'expired')
        self.assertIsNone(project(original,2_000_000_001)['label'])
        self.assertEqual(m.result,original)
        self.assertEqual(project(original,None)['label'],'lying') # paused stored video
        self.assertEqual(project(original,None,closed=True)['status'],'expired')

    def test_source_video_pts_and_label_independence(self):
        def sequence(goal):
            m=Stabilizer();result=[]
            for i in range(3):
                r={'posture_assessment':dict(raw(),track_id='t',candidate_id='p',classifier_id='c'),
                   'source_media':{'relative_time_ns':str(i*250_000_000)},'received_ns':str(i*10_000_000_000),
                   'stream_frame_id':i,'clock_id':'c','bed_region_revision':0,
                   'target_posture':goal,'verified_gt':goal,'sensor_results':{'label':goal}}
                result.append(frame(m,r))
            return result
        self.assertEqual(sequence('supine'),sequence('side'))
        self.assertEqual(sequence('side')[-1]['label'],'lying')
        self.assertEqual(sequence('side')[-1]['time_basis'],'media_relative')

    def test_unified_result_preserves_legacy_profile_validation(self):
        from bed_pose.posture_stability import LEGACY,merged
        self.assertEqual(profile(dict(LEGACY)),LEGACY)
        self.assertEqual(merged([dict(LEGACY),dict(LEGACY)])['version'],'posture-stability-0.1')
        self.assertEqual(merged([profile(),profile()])['version'],'posture-stability-0.2')
        self.assertEqual(merged([dict(LEGACY),profile()])['version'],'posture-stability-0.1')

    def test_settings_reject_hidden_threshold_changes_and_invalid_ranges(self):
        for value in ({'min_samples':True},{'min_samples':1},{'confirm_ms':.5},{'max_gap_ms':2000,'expire_ms':1000},{'future_frames':True}):
            with self.assertRaises(ValueError):profile(value)
        self.assertEqual(profile({'confirm_ms':200})['confirm_ms'],200)

    def test_group_requires_both_new_frames_and_does_not_count_http_polls(self):
        from bed_pose.group_session import MeasurementGroup
        from test_multiview_posture import views
        g=MeasurementGroup.__new__(MeasurementGroup);g.stability=Stabilizer();g._stable_ids=None;g._stable_gate=None;g._stable_sequence=0
        g.data={'phase':'recording'};g.members=[SimpleNamespace(state={'bed_region_revision':0}) for _ in range(2)]
        v=views()
        for x in v:x['assessment']['stream_frame_id']=0;x['time_ns']='0'
        with patch('bed_pose.group_session.time.perf_counter_ns',return_value=0):
            a=g.stable_public(v,raw())
            for _ in range(100):self.assertEqual(g.stable_public(v,raw()),a)
            v[0]['assessment']['stream_frame_id']=1;v[0]['time_ns']='250000000'
            self.assertEqual(g.stable_public(v,raw())['candidate_samples'],1)
            self.assertEqual(g.stable_public(v,raw('side'))['status'],'waiting_for_pair')
        for i in (1,2):
            for x in v:x['assessment']['stream_frame_id']=i;x['time_ns']=str(i*250_000_000)
            with patch('bed_pose.group_session.time.perf_counter_ns',return_value=i*250_000_000):a=g.stable_public(v,raw())
        self.assertEqual(a['label'],'lying')
        with patch('bed_pose.group_session.time.perf_counter_ns',return_value=2_000_000_001):self.assertEqual(g.stable_public(v,raw())['status'],'expired')


class StabilityRecordTests(unittest.TestCase):
    from test_multiview_posture import MultiviewWebTests
    setUp=MultiviewWebTests.setUp
    cleanup=MultiviewWebTests.cleanup
    start_pair=MultiviewWebTests.start_pair

    def test_single_and_group_replay_detect_rehashed_stability_tampering(self):
        import json
        from bed_pose.io_utils import load_json,atomic_json,file_hash
        from bed_pose.fusion_inputs import inspect_group
        from bed_pose.live_recording import verify_live
        from bed_pose.contracts import IntegrityError
        gid=self.start_pair(with_regions=True)
        self.wait(lambda x:all((s.get('stable_posture') or {}).get('status')=='stable' for s in x['sessions']) and (x['groups'][0]['posture_assessment'].get('stable_posture') or {}).get('status')=='stable')
        self.request('/api/group',{'action':'stop','group_id':gid})
        g=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0];root=Path(g['directory'])
        self.assertEqual(inspect_group(root)['integrity'],'verified')
        m=load_json(root/'manifest.json');path=root/'posture_predictions.jsonl';original=path.read_text(encoding='utf-8')
        rows=[json.loads(x) for x in original.splitlines()];rows[-1]['stable_posture']['label']='side'
        path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8');m['posture_predictions']['sha256']=file_hash(path);atomic_json(root/'manifest.json',m)
        with self.assertRaises(IntegrityError):inspect_group(root)
        rows=[json.loads(x) for x in original.splitlines()]
        for x in rows:x.pop('stable_posture')
        m.pop('stability_profile');path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8');m['posture_predictions']['sha256']=file_hash(path);atomic_json(root/'manifest.json',m)
        self.assertEqual(inspect_group(root)['integrity'],'verified')
        run=Path(m['members'][0]['run_directory']);manifest=load_json(run/'manifest.json');path=run/'live_frames.jsonl'
        original=path.read_text(encoding='utf-8');rows=[json.loads(x) for x in original.splitlines()];rows[-1]['stable_posture']['candidate_samples']=999
        path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8');manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(run/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(run)
        rows=[json.loads(x) for x in original.splitlines()]
        for x in rows:x.pop('stable_posture')
        manifest.pop('stability_profile');path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8');manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(run/'manifest.json',manifest)
        self.assertEqual(verify_live(run)['integrity'],'verified')
