"""Stored-data summaries, time semantics and navigation, not posture accuracy."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from bed_pose.record_timeline import summarize
from bed_pose.record_library import RecordLibrary
from bed_pose.io_utils import file_hash, load_json, atomic_json
from bed_pose.live_engine import LiveEngine
from bed_pose.live_sources import SyntheticSource
from bed_pose.pose.fake import FakeAdapter
import test_record_library as records
import test_web_workspace as web


def saved_row(frame, stamp, label='side', scores=True, held=False, visual='consistent'):
    row={'frame_id':frame,'stream_frame_id':frame+5,'received_ns':str(stamp+1000),
         'source_media':{'relative_time_ns':str(stamp*10+5000)},'processing_status':'ok',
         'posture_assessment':{'status':'held' if held else 'predicted','label':None if held else label,
                              'reasons':['body_outside_bed_region']*2 if held else [],'rule_features':{}}}
    if scores:
        row['posture_assessment']['rule_features']['score_decision']={
            'scores':{'supine':.7+frame*.001,'side':.8,'edge_sitting':None}}
    if visual:row['silhouette_assessment']={'status':visual}
    return row


MANIFEST={'started_ns':'1000','source_profile':{'kind':'video_file'}}


class TimelineTests(unittest.TestCase):
    def test_binning_keeps_single_hold_conflict_and_exact_score_ranges(self):
        rows=[saved_row(i,i*100,held=i==2,visual='conflict' if i==3 else 'consistent') for i in range(6)]
        original=copy.deepcopy(rows);summary=summarize(lambda:iter(rows),MANIFEST,bins=1)
        bucket=summary['buckets'][0]
        self.assertEqual(rows,original)
        self.assertEqual(bucket['frame_count'],6)
        self.assertEqual(bucket['postures']['held'],1);self.assertEqual(bucket['silhouette']['conflict'],1)
        self.assertEqual(bucket['first_held_frame_id'],2);self.assertEqual(bucket['first_conflict_frame_id'],3)
        self.assertEqual(bucket['focus_frame_id'],2)
        self.assertEqual(bucket['hold_reasons'],[{'reason':'body_outside_bed_region','frames':1}])
        self.assertAlmostEqual(bucket['scores']['supine']['mean'],.7025)
        self.assertAlmostEqual(bucket['scores']['supine']['min'],.7)
        self.assertAlmostEqual(bucket['scores']['supine']['max'],.705)
        self.assertEqual(bucket['scores']['edge_sitting']['ineligible_count'],6)
        self.assertEqual(sum(b['frame_count'] for b in summary['buckets']),6)
        self.assertEqual(summary['end_ns'],'500')

    def test_gaps_are_empty_not_filled_and_last_frame_is_included(self):
        rows=[saved_row(i, i if i<5 else 10000+i) for i in range(10)]
        summary=summarize(lambda:iter(rows),MANIFEST,bins=10)
        self.assertEqual(sum(b['frame_count'] for b in summary['buckets']),10)
        empty=[b for b in summary['buckets'] if not b['frame_count']]
        self.assertTrue(empty)
        self.assertTrue(all(b['focus_frame_id'] is None and b['scores']['side']['mean'] is None for b in empty))
        self.assertEqual(summary['buckets'][-1]['last_frame_id'],9)
        self.assertNotIn('duration',summary['totals'])

    def test_receive_media_time_and_single_zero_or_equal_time_frames(self):
        rows=[saved_row(0,100),saved_row(1,200)]
        receive=summarize(lambda:iter(rows),MANIFEST,'receive')
        media=summarize(lambda:iter(rows),MANIFEST,'media')
        self.assertEqual((receive['start_ns'],receive['end_ns']),('100','200'))
        self.assertEqual((media['start_ns'],media['end_ns']),('6000','7000'))
        empty=summarize(lambda:iter([]),MANIFEST)
        self.assertEqual(empty['buckets'],[]);self.assertIsNone(empty['start_ns'])
        self.assertIsNone(empty['totals']['focus_frame_id'])
        same=summarize(lambda:iter([saved_row(i,10) for i in range(3)]),MANIFEST)
        self.assertEqual(len(same['buckets']),1);self.assertEqual(same['buckets'][0]['frame_count'],3)

    def test_missing_historical_scores_unknown_states_and_reason_limits(self):
        rows=[saved_row(0,0,scores=False,visual=None),saved_row(1,1),saved_row(2,2)]
        rows[1].pop('posture_assessment');rows[2]['processing_status']='failed'
        summary=summarize(lambda:iter(rows),MANIFEST)
        self.assertEqual(summary['totals']['postures']['unrecorded'],1)
        self.assertEqual(summary['totals']['postures']['failed'],1)
        self.assertEqual(summary['totals']['scores']['side']['missing_count'],2)
        self.assertEqual(summary['totals']['silhouette']['unrecorded'],1)
        rows=[saved_row(i,i,held=True) for i in range(300)]
        for i,row in enumerate(rows):row['posture_assessment']['reasons']=['reason_'+str(i)]
        bounded=summarize(lambda:iter(rows),MANIFEST,bins=1)
        self.assertLessEqual(len(bounded['totals']['hold_reasons']),8)
        self.assertIn('__other__',[r['reason'] for r in bounded['totals']['hold_reasons']])
        for bins in (0,601,-1,True,1.5):
            with self.assertRaises(ValueError):summarize(lambda:iter(rows),MANIFEST,bins=bins)
        with self.assertRaises(ValueError):summarize(lambda:iter(rows),MANIFEST,'utc')
        with self.assertRaises(ValueError):summarize(lambda:iter(rows),dict(MANIFEST,source_profile={'kind':'camera'}),'media')


class TimelineWebTests(unittest.TestCase):
    setUp=records.RecordWebTests.setUp
    cleanup=records.RecordWebTests.cleanup
    request=records.RecordWebTests.request
    wait=records.RecordWebTests.wait
    demo=records.RecordWebTests.demo
    control=records.RecordWebTests.control
    world_input=records.RecordWebTests.world_input
    finish=records.RecordWebTests.finish
    weighted_input=records.RecordWebTests.weighted_input
    measured=records.RecordWebTests.measured

    def test_timeline_auth_open_requirement_bounds_source_mutation_and_no_writes(self):
        sid,root=self.measured(7);url='/api/records/timeline?record_id='+root.name
        self.assertEqual(self.request(url,auth=False)[0],403)
        self.assertEqual(self.request(url)[0],400)
        self.assertEqual(self.request('/api/records/timeline?record_id=..%2Foutside')[0],400)
        self.request('/api/records/open',{'record_id':root.name})
        before={p.name:file_hash(p) for p in root.iterdir() if p.is_file()}
        status,summary=self.request(url+'&bins=2')
        self.assertEqual(status,200);self.assertEqual(summary['record_id'],root.name)
        self.assertEqual(summary['total_frames'],len((root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()))
        self.assertEqual(summary['totals']['postures']['side'],summary['total_frames'])
        self.assertAlmostEqual(summary['totals']['scores']['side']['mean'],7/9)
        self.assertEqual(summary['time_basis'],'receive')
        self.assertEqual(self.request(url+'&bins=2')[1],summary)
        self.assertEqual(before,{p.name:file_hash(p) for p in root.iterdir() if p.is_file()})
        for query in ('&bins=0','&bins=601','&bins=no','&basis=media','&basis=utc'):
            self.assertEqual(self.request(url+query)[0],400)
        path=root/'live_frames.jsonl';path.write_text(path.read_text(encoding='utf-8')+'\n',encoding='utf-8')
        self.assertEqual(self.request(url+'&bins=2')[0],400) # Even cached data checks original hashes.
        first=self.workspace.snapshot()['sessions'][0]['frame_id']
        self.wait(lambda x:x['sessions'][0]['frame_id']>first)

    def test_actual_file_record_has_separate_media_and_receive_axes(self):
        video=self.root/'timeline.mkv';web.make_video(video)
        asset=self.workspace.register_asset(video,'video','timeline.mkv')
        status,value=self.request('/api/inputs',{'kind':'video_file','video_asset':asset['asset_id']})
        self.assertEqual(status,200);sid=value['id']
        self.wait(lambda x:x['sessions'][0]['frame_id'] is not None)
        self.control(sid,'region',box=[1,1,150,80],selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'start')
        state=self.wait(lambda x:x['sessions'][0]['closed'])['sessions'][0]
        root=Path(state['directory']);self.request('/api/records/open',{'record_id':root.name})
        url='/api/records/timeline?record_id='+root.name
        default=self.request(url)[1];received=self.request(url+'&basis=receive')[1]
        self.assertEqual(default['time_basis'],'media');self.assertEqual(received['time_basis'],'receive')
        original=[json.loads(line) for line in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(default['start_ns'],original[0]['source_media']['relative_time_ns'])
        self.assertEqual(default['end_ns'],original[-1]['source_media']['relative_time_ns'])


class EmptyTimelineTests(unittest.TestCase):
    def test_empty_completed_record_has_no_fake_observation_or_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine=LiveEngine(SyntheticSource(interval=0),FakeAdapter(),{'execution_mode':'ordered_sequence'},Path(temporary),execution_kind='synthetic')
            engine.open()
            try:
                engine.step();engine.set_region([1,1,630,350],640,360,selected_frame_id=0)
                directory=engine.start_measurement();engine.stop_measurement()
            finally:engine.close()
            library=RecordLibrary(temporary);library.open(directory.name)
            summary=library.timeline(directory.name)
            self.assertEqual(summary['total_frames'],0);self.assertEqual(summary['buckets'],[])
