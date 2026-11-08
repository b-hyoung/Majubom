"""Original record browsing, boundaries and immutable stored decisions."""
import json
from pathlib import Path
import tempfile
import unittest

from bed_pose.record_library import RecordLibrary
from bed_pose.io_utils import file_hash,load_json,atomic_json
from bed_pose.live_recording import verify_live
from bed_pose.live_engine import LiveEngine
from bed_pose.live_sources import SyntheticSource
from bed_pose.pose.fake import FakeAdapter
import test_weighted_posture as weighted_tests


class RecordWebTests(unittest.TestCase):
    setUp=weighted_tests.WeightedWebTests.setUp
    cleanup=weighted_tests.WeightedWebTests.cleanup
    request=weighted_tests.WeightedWebTests.request
    wait=weighted_tests.WeightedWebTests.wait
    demo=weighted_tests.WeightedWebTests.demo
    control=weighted_tests.WeightedWebTests.control
    world_input=weighted_tests.WeightedWebTests.world_input
    finish=weighted_tests.WeightedWebTests.finish
    weighted_input=weighted_tests.WeightedWebTests.weighted_input

    def measured(self,frames=3):
        sid=self.weighted_input();self.workspace.sessions[sid].worker.engine.source.interval=.005
        self.assertEqual(self.control(sid,'start')[0],200)
        self.wait(lambda x:x['sessions'][0]['recording'] and x['sessions'][0]['recorded_frames']>=frames)
        self.control(sid,'stop')
        state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        return sid,Path(state['directory'])

    def test_empty_and_authenticated_root_boundaries(self):
        self.assertEqual(self.request('/api/records')[1]['total'],0)
        self.assertEqual(self.request('/api/records',auth=False)[0],403)
        self.assertEqual(self.request('/api/records/open',{'record_id':'a'*32},auth=False)[0],403)
        for path in ('/api/records?offset=-1','/api/records?limit=0','/api/records?limit=101','/api/records?offset=no'):
            self.assertEqual(self.request(path)[0],400)
        for record_id in ('../outside','/etc','A'*32,'a'*32):
            self.assertEqual(self.request('/api/records/open',{'record_id':record_id})[0],400)
        self.assertEqual(self.request('/api/records/frames?record_id=..%2Foutside')[0],400)

    def test_finished_records_page_original_scores_without_mutating_files(self):
        sid,root=self.measured();before={p.name:file_hash(p) for p in root.iterdir() if p.is_file()}
        record_id=root.name;status,catalog=self.request('/api/records')
        self.assertEqual(status,200);self.assertEqual(catalog['total'],1)
        self.assertEqual(catalog['items'][0]['availability'],'ready')
        self.assertEqual(catalog['items'][0]['integrity'],'not_checked')
        status,detail=self.request('/api/records/open',{'record_id':record_id})
        self.assertEqual(status,200);self.assertEqual(detail['integrity'],'verified')
        self.assertEqual(detail['execution_kind'],'synthetic')
        original=[json.loads(l) for l in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
        _,page=self.request('/api/records/frames?record_id='+record_id+'&limit=1')
        self.assertEqual(page['total'],len(original));self.assertEqual(len(page['frames']),1)
        saved=original[0];row=page['frames'][0]
        self.assertEqual(row['assessment']['label'],saved['posture_assessment']['label'])
        self.assertEqual(row['score_decision'],saved['posture_assessment']['rule_features']['score_decision'])
        self.assertEqual(row['received_elapsed_ns'],str(int(saved['received_ns'])-int(load_json(root/'manifest.json')['started_ns'])))
        self.assertEqual(row['assessment']['label'],'side');self.assertTrue(row['score_decision']['close_candidates'])
        _,second=self.request('/api/records/frames?record_id='+record_id+'&limit=1&offset=1')
        self.assertEqual(second['frames'][0]['frame_id'],1)
        self.assertEqual(before,{p.name:file_hash(p) for p in root.iterdir() if p.is_file()})
        self.assertEqual(verify_live(root)['integrity'],'verified')
        library=RecordLibrary(self.workspace.output_root)
        self.assertEqual(library.catalog()['total'],1);self.assertEqual(library.open(record_id)['integrity'],'verified')

    def test_in_progress_is_not_openable_and_broken_entry_does_not_hide_others(self):
        sid=self.weighted_input();self.control(sid,'start')
        state=self.wait(lambda x:x['sessions'][0]['recording'] and x['sessions'][0]['recorded_frames']>=1)['sessions'][0]
        root=Path(state['directory']);bad=root.parent/('b'*32);bad.mkdir();(bad/'manifest.json').write_text('{broken',encoding='utf-8')
        _,catalog=self.request('/api/records');items={i['record_id']:i for i in catalog['items']}
        self.assertEqual(items[root.name]['availability'],'incomplete');self.assertEqual(items[bad.name]['availability'],'invalid')
        self.assertEqual(self.request('/api/records/open',{'record_id':root.name})[0],400)
        self.control(sid,'stop');self.wait(lambda x:x['sessions'][0]['integrity']=='verified')
        self.assertEqual(self.request('/api/records/open',{'record_id':root.name})[0],200)

    def test_source_change_after_open_is_rejected_and_live_input_is_untouched(self):
        sid,root=self.measured();self.request('/api/records/open',{'record_id':root.name})
        first=self.workspace.snapshot()['sessions'][0]['frame_id']
        path=root/'live_frames.jsonl';path.write_text(path.read_text(encoding='utf-8')+'\n',encoding='utf-8')
        self.assertEqual(self.request('/api/records/frames?record_id='+root.name)[0],400)
        self.assertEqual(self.request('/api/records/open',{'record_id':root.name})[0],400)
        self.wait(lambda x:x['sessions'][0]['frame_id']>first)
        self.assertFalse(self.workspace.snapshot()['sessions'][0]['closed'])

    def test_legacy_missing_assessment_and_scores_are_not_filled_in(self):
        sid=self.demo();self.control(sid,'region',box=[1,1,630,350],selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None);root=self.finish(sid)
        manifest=load_json(root/'manifest.json');manifest.pop('posture_assessment_profile');manifest.pop('posture_evidence_profile')
        manifest.pop('silhouette_profile')
        manifest.pop('native_pose_cues_profile')
        manifest.pop('decision_evidence_profile')
        manifest.pop('stability_profile')
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        for row in rows:
            row.pop('posture_assessment');row.pop('posture_evidence')
            row.pop('silhouette_observation');row.pop('silhouette_assessment')
            row.pop('native_pose_cues')
            row.pop('decision_evidence')
            row.pop('stable_posture')
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        self.assertEqual(self.request('/api/records/open',{'record_id':root.name})[0],200)
        _,page=self.request('/api/records/frames?record_id='+root.name)
        self.assertTrue(all(r['assessment'] is None and r['score_decision'] is None for r in page['frames']))
        _,timeline=self.request('/api/records/timeline?record_id='+root.name)
        self.assertEqual(timeline['totals']['postures']['unrecorded'],timeline['total_frames'])
        self.assertEqual(timeline['totals']['scores']['side']['missing_count'],timeline['total_frames'])

    def test_failed_run_with_complete_records_is_readable_and_missing_file_rejected(self):
        sid,root=self.measured();manifest=load_json(root/'manifest.json');manifest.update(run_status='failed',errors=[{'code':'TEST_FAILURE'}])
        atomic_json(root/'manifest.json',manifest)
        status,item=self.request('/api/records/open',{'record_id':root.name})
        self.assertEqual(status,200);self.assertEqual(item['run_status'],'failed');self.assertEqual(item['integrity'],'verified')
        (root/'control_events.jsonl').unlink()
        self.assertEqual(self.request('/api/records/open',{'record_id':root.name})[0],400)

    def test_directory_and_artifact_symlinks_cannot_escape_root(self):
        sid,root=self.measured()
        with tempfile.TemporaryDirectory() as outer:
            outside=Path(outer);(outside/'manifest.json').write_text('{}',encoding='utf-8')
            link=root.parent/('c'*32)
            try:link.symlink_to(outside,target_is_directory=True)
            except (OSError,NotImplementedError):self.skipTest('Symlink creation unavailable')
            self.assertEqual(self.request('/api/records/open',{'record_id':link.name})[0],400)
            artifact=root/'control_events.jsonl';artifact.unlink();artifact.symlink_to(outside/'manifest.json')
            self.assertEqual(self.request('/api/records/open',{'record_id':root.name})[0],400)


class EmptyRecordTests(unittest.TestCase):
    def test_zero_frame_measurement_is_visible_and_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            engine=LiveEngine(SyntheticSource(interval=0),FakeAdapter(),{'execution_mode':'ordered_sequence'},root,execution_kind='synthetic')
            engine.open()
            try:
                engine.step();engine.set_region([1,1,630,350],640,360,selected_frame_id=0)
                directory=engine.start_measurement();engine.stop_measurement()
            finally:engine.close()
            library=RecordLibrary(root);self.assertEqual(library.catalog()['total'],1)
            self.assertEqual(library.open(directory.name)['recorded_frames'],0)
            self.assertEqual(library.frames(directory.name)['frames'],[])
