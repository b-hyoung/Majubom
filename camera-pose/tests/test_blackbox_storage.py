"""Storage isolation; these tests do not imply rolling retention is implemented."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from bed_pose.io_utils import load_json
from bed_pose.live_recording import verify_live
from bed_pose.live_web import WebWorkspace
from bed_pose.live_engine import LiveEngine
from bed_pose.live_sources import SyntheticSource
from bed_pose.pose.fake import FakeAdapter
from test_web_workspace import factory


class BlackboxStorageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        self.workspace=WebWorkspace(output_root=self.root/'records',engine_factory=factory,model_store=self.root/'models')
        self.addCleanup(self.workspace.close)

    def wait(self,predicate,timeout=10):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=self.workspace.snapshot()
            if predicate(state):return state
            time.sleep(.03)
        self.fail('Workspace timeout: '+str(self.workspace.snapshot()))

    def prepare(self,count=1):
        ids=[self.workspace.add({'kind':'demo'})['id'] for _ in range(count)]
        self.wait(lambda x:len(x['sessions'])==count and all(s['frame_id'] is not None for s in x['sessions']))
        for sid in ids:
            self.workspace.command(sid,'region',{'box':[1,1,200,200],'selected_frame_id':0})
        self.wait(lambda x:all(s['region'] is not None for s in x['sessions']))
        return ids

    def test_separate_single_with_video_and_catalog(self):
        sid=self.prepare()[0]
        self.workspace.command(sid,'start',{'blackbox':True,'record_video':True})
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.workspace.command(sid,'stop',{})
        state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        directory=Path(state['directory'])
        self.assertEqual(directory.parent,self.root/'records'/'blackbox')
        self.assertEqual(load_json(directory/'manifest.json')['storage_class'],'blackbox')
        self.assertEqual(verify_live(directory)['integrity'],'verified')
        self.assertEqual(self.workspace.record_library('normal').catalog()['total'],0)
        catalog=self.workspace.record_library('blackbox').catalog()
        self.assertEqual(catalog['total'],1)
        self.assertEqual(self.workspace.record_library('blackbox').open(directory.name)['integrity'],'verified')
        with self.assertRaises(ValueError):self.workspace.record_library('normal').open(directory.name)

    def test_two_input_group_stays_in_separate_storage(self):
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'record_video':True,'max_skew_ms':30})
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        record=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        root=self.root/'records'/'blackbox'
        self.assertEqual(Path(record['directory']).parent,root/'groups')
        for member in record['members']:
            path=Path(member['run_directory'])
            self.assertEqual(path.parent,root)
            self.assertEqual(verify_live(path)['integrity'],'verified')
        self.assertEqual(self.workspace.record_library('blackbox').catalog()['total'],2)
        self.assertEqual(self.workspace.record_library('normal').catalog()['total'],0)

    def test_two_input_segments_preserve_each_video_and_alignment(self):
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'record_video':True,
                                            'segment_seconds':1,'max_skew_ms':100})
        self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        result=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        segment=result['segments'][0]
        directory=Path(segment['directory'])
        first_group=load_json(directory/'manifest.json')
        self.assertEqual(first_group['group_segment_index'],0)
        self.assertEqual(first_group['alignment']['status'],'receive_time_approximation')
        import json
        rows=[json.loads(line) for line in (directory/'alignment.jsonl').read_text().splitlines()]
        for side,member in zip(('left','right'),first_group['members']):
            path=Path(member['run_directory']);self.assertEqual(verify_live(path)['integrity'],'verified')
            manifest=load_json(path/'manifest.json')
            self.assertEqual(manifest['blackbox_segment']['group_id'],group['group_id'])
            self.assertEqual(sorted(row[side]['frame_id'] for row in rows if row[side] is not None),list(range(manifest['recorded_frames'])))
        for member in result['members']:
            path=Path(member['run_directory']);self.assertEqual(verify_live(path)['integrity'],'verified')
            self.assertGreaterEqual(load_json(path/'manifest.json')['blackbox_segment']['index'],1)

    def test_group_retention_deletes_both_members_and_alignment_together(self):
        from bed_pose.blackbox_retention import reclaim
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'record_video':True,
                                            'segment_seconds':1,'max_skew_ms':100})
        state=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')['groups'][0]
        old=state['segments'][0]
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            active={Path(s.state['directory']).name for s in self.workspace.sessions.values() if s.state.get('recording')}
            with library.lock:result=reclaim(self.workspace.output_root,1,active|set(library.opened),self.workspace.groups)
        self.assertIn(group['group_id']+':0',result['deleted'])
        self.assertFalse(Path(old['directory']).exists())
        self.assertTrue(all(not Path(p).exists() for p in old['members']))
        self.assertTrue(all((self.workspace.output_root/'blackbox'/p).exists() for p in active))
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        done=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        self.assertEqual(done['segments'][0]['status'],'deleted')

    def test_group_quota_rotates_old_segments_while_recording(self):
        from bed_pose.blackbox_retention import _size
        ids=self.prepare(2)
        created=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'record_video':True,
                                              'segment_seconds':1,'max_skew_ms':100})
        state=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')['groups'][0]
        old=state['segments'][0];root=self.workspace.output_root/'blackbox'
        with self.workspace.lock:
            total=_size(root)
            old_size=_size(Path(old['directory']))+sum(_size(Path(p)) for p in old['members'])
            self.workspace.groups[created['group_id']].data['retention_max_bytes']=max(1,total-old_size//2)
            self.workspace._tick_retention()
        state=self.workspace.snapshot()['groups'][0]
        self.assertEqual(state['phase'],'recording')
        self.assertEqual(state['segments'][0]['status'],'deleted')
        self.assertTrue(all(not Path(p).exists() for p in old['members']))
        self.assertIsNone(state['retention_status']['reason'])
        with self.workspace.lock:self.workspace.groups[created['group_id']].data.pop('retention_max_bytes')
        self.workspace.group_command({'action':'stop','group_id':created['group_id']})
        self.wait(lambda x:x['groups'][0]['phase']=='completed')

    def test_group_rotation_failure_stops_both_without_deleting_records(self):
        ids=self.prepare(2)
        engine=self.workspace.sessions[ids[0]].worker.engine
        def broken():raise OSError('injected rotation failure')
        engine.rotate_blackbox_segment=broken
        created=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,
                                              'segment_seconds':1,'max_skew_ms':100})
        state=self.wait(lambda x:x['groups'][0]['phase']=='failed',timeout=15)['groups'][0]
        self.assertFalse(state['ledger_complete'])
        self.assertTrue(state['errors'])
        self.assertTrue(any((self.root/'records'/'blackbox').glob('*/manifest.json')))
        self.assertTrue(all(self.workspace.sessions[sid].worker.stop_requested.is_set() for sid in ids))

    def test_group_retention_keeps_both_if_one_member_is_pinned(self):
        from bed_pose.blackbox_retention import reclaim
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':1,'max_skew_ms':100})
        state=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')['groups'][0]
        old=state['segments'][0]
        self.workspace.record_library('blackbox').protect(Path(old['members'][0]).name,True)
        catalog=self.workspace.record_library('blackbox').catalog()
        old_ids={Path(p).name for p in old['members']}
        self.assertTrue(all(item['protected'] for item in catalog['items'] if item['record_id'] in old_ids))
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            with library.lock:reclaim(self.workspace.output_root,1,set(library.opened),self.workspace.groups)
        self.assertTrue(Path(old['directory']).exists())
        self.assertTrue(all(Path(p).exists() for p in old['members']))
        self.workspace.record_library('blackbox').protect(Path(old['members'][1]).name,False)
        self.assertTrue(all(not (Path(p)/'.keep').exists() for p in old['members']))
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        self.wait(lambda x:x['groups'][0]['phase']=='completed')

    def test_completed_group_final_segment_is_reclaimed_as_one_unit(self):
        from bed_pose.blackbox_retention import reclaim
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':60,'max_skew_ms':100})
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        done=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        paths=[Path(m['run_directory']) for m in done['members']];directory=Path(done['directory'])
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            with library.lock:result=reclaim(self.workspace.output_root,1,set(library.opened),self.workspace.groups)
        self.assertIn(group['group_id']+':None',result['deleted'])
        self.assertFalse(directory.exists());self.assertTrue(all(not p.exists() for p in paths))

    def test_completed_group_cleanup_keeps_unknown_segment_files(self):
        from bed_pose.blackbox_retention import reclaim
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':60,'max_skew_ms':100})
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        done=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        directory=Path(done['directory']);unknown=directory/'segments'/'unknown';unknown.mkdir(parents=True)
        (unknown/'notes.txt').write_text('preserve')
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            with library.lock:result=reclaim(self.workspace.output_root,1,set(library.opened),self.workspace.groups)
        self.assertIn(group['group_id']+':-1',result['deleted'])
        self.assertEqual((unknown/'notes.txt').read_text(),'preserve')

    def test_complete_group_reclaims_old_and_final_segments_in_one_pass(self):
        from bed_pose.blackbox_retention import reclaim
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':1,'max_skew_ms':100})
        self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        done=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        old=done['segments'][0];latest=[Path(m['run_directory']) for m in done['members']]
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            with library.lock:result=reclaim(self.workspace.output_root,1,set(library.opened),self.workspace.groups)
        self.assertTrue(result['within_limit'])
        self.assertIn(group['group_id']+':0',result['deleted'])
        self.assertIn(group['group_id']+':None',result['deleted'])
        self.assertFalse(Path(done['directory']).exists())
        self.assertTrue(all(not Path(p).exists() for p in old['members']))
        self.assertTrue(all(not p.exists() for p in latest))

    def test_pinned_old_group_segment_does_not_keep_newer_final_segment(self):
        from bed_pose.blackbox_retention import reclaim
        ids=self.prepare(2)
        created=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':1,'max_skew_ms':100})
        state=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')['groups'][0]
        old=state['segments'][0];library=self.workspace.record_library('blackbox')
        library.protect(Path(old['members'][0]).name,True)
        self.workspace.group_command({'action':'stop','group_id':created['group_id']})
        done=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        final=[Path(m['run_directory']) for m in done['members']];root=Path(done['directory'])
        with self.workspace.lock:
            with library.lock:result=reclaim(self.workspace.output_root,1,set(library.opened),self.workspace.groups)
        self.assertIn(created['group_id']+':-1',result['deleted'])
        self.assertTrue(root.exists());self.assertTrue(all(Path(p).exists() for p in old['members']))
        self.assertTrue(all(not p.exists() for p in final))
        self.assertEqual(load_json(root/'manifest.json')['final_segment_status'],'deleted')
        library.protect(Path(old['members'][1]).name,False)
        with self.workspace.lock:
            with library.lock:result=reclaim(self.workspace.output_root,1,set(library.opened),self.workspace.groups)
        self.assertTrue(result['within_limit']);self.assertFalse(root.exists())

    def test_group_cleanup_rolls_back_when_move_fails(self):
        from bed_pose.blackbox_retention import reclaim
        from unittest.mock import patch
        import os
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':1,'max_skew_ms':100})
        state=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')['groups'][0]
        old=state['segments'][0];original=os.replace
        def fail_one(source,target):
            if str(source)==old['members'][0]:raise OSError('injected rename failure')
            return original(source,target)
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            with library.lock,patch('bed_pose.blackbox_retention.os.replace',side_effect=fail_one):
                with self.assertRaises(OSError):reclaim(self.workspace.output_root,1,set(library.opened),self.workspace.groups)
        self.assertTrue(Path(old['directory']).exists())
        self.assertTrue(all(Path(p).exists() for p in old['members']))
        self.assertEqual(self.workspace.groups[group['group_id']].data['segments'][0]['status'],'completed')
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        self.wait(lambda x:x['groups'][0]['phase']=='completed')

    def test_interrupted_group_cleanup_restores_moved_files(self):
        from bed_pose.blackbox_retention import reclaim
        from bed_pose.io_utils import atomic_json
        import os
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':1,'max_skew_ms':100})
        state=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')['groups'][0]
        old=state['segments'][0];root=self.workspace.output_root/'blackbox';members=[Path(p).name for p in old['members']]
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            with library.lock:
                staging=root/'.delete-staging'/('c'*32);staging.mkdir(parents=True)
                atomic_json(staging/'intent.json',{'group_id':group['group_id'],'index':0,'members':members})
                os.replace(old['directory'],staging/'group');os.replace(old['members'][0],staging/members[0])
                reclaim(self.workspace.output_root,1024**4,set(library.opened),self.workspace.groups)
        self.assertTrue(Path(old['directory']).exists())
        self.assertTrue(all(Path(p).exists() for p in old['members']))
        self.assertFalse(staging.exists())
        self.workspace.group_command({'action':'stop','group_id':group['group_id']})
        self.wait(lambda x:x['groups'][0]['phase']=='completed')

    def test_interrupted_final_segment_cleanup_restores_members_and_alignment(self):
        from bed_pose.blackbox_retention import reclaim
        from bed_pose.io_utils import atomic_json
        import os
        ids=self.prepare(2)
        created=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':1,'max_skew_ms':100})
        state=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')['groups'][0]
        self.workspace.record_library('blackbox').protect(Path(state['segments'][0]['members'][0]).name,True)
        self.workspace.group_command({'action':'stop','group_id':created['group_id']})
        done=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        root=self.workspace.output_root/'blackbox';groupdir=Path(done['directory']);members=[Path(m['run_directory']) for m in done['members']]
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            with library.lock:
                staging=root/'.delete-staging'/('d'*32);staging.mkdir(parents=True)
                atomic_json(staging/'intent.json',{'mode':'tail_segment','group_id':created['group_id'],'index':-1,
                    'members':[p.name for p in members],'artifacts':['alignment.jsonl']})
                os.replace(members[0],staging/members[0]);os.replace(groupdir/'alignment.jsonl',staging/'alignment.jsonl')
                reclaim(self.workspace.output_root,1024**4,set(library.opened),self.workspace.groups)
        self.assertTrue(all(p.exists() for p in members));self.assertTrue((groupdir/'alignment.jsonl').exists())
        self.assertFalse(staging.exists())

    def test_completed_group_cleanup_resumes_after_commit(self):
        from bed_pose.blackbox_retention import reclaim
        from bed_pose.io_utils import atomic_json
        import os
        ids=self.prepare(2)
        created=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,'segment_seconds':60,'max_skew_ms':100})
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        self.workspace.group_command({'action':'stop','group_id':created['group_id']})
        done=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        root=self.workspace.output_root/'blackbox';groupdir=Path(done['directory']);members=[Path(m['run_directory']) for m in done['members']]
        with self.workspace.lock:
            library=self.workspace.record_library('blackbox')
            with library.lock:
                staging=root/'.delete-staging'/('e'*32);staging.mkdir(parents=True)
                atomic_json(staging/'intent.json',{'mode':'complete_group','group_id':created['group_id'],'index':None,'members':[p.name for p in members]})
                os.replace(groupdir,staging/'group')
                for member in members:os.replace(member,staging/member.name)
                atomic_json(staging/'committed.json',{'committed':True})
                reclaim(self.workspace.output_root,1024**4,set(library.opened),{})
        self.assertFalse(staging.exists());self.assertFalse(groupdir.exists());self.assertTrue(all(not p.exists() for p in members))

    def test_symlink_storage_is_rejected(self):
        sid=self.prepare()[0]
        records=self.root/'records';records.mkdir(exist_ok=True)
        outside=self.root/'elsewhere';outside.mkdir()
        try:os.symlink(outside,records/'blackbox',target_is_directory=True)
        except (OSError,NotImplementedError):self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):self.workspace.record_library('blackbox')
        with self.assertRaises(ValueError):self.workspace.command(sid,'start',{'blackbox':True})
        self.assertFalse(list(outside.iterdir()))

    def test_single_segment_rotation_continues_recording(self):
        sid=self.prepare()[0]
        self.workspace.command(sid,'start',{'blackbox':True,'record_video':True,'segment_seconds':1})
        state=self.wait(lambda x:x['sessions'][0].get('segment_index')==1)['sessions'][0]
        self.assertTrue(state['recording'])
        second=Path(state['directory'])
        self.workspace.command(sid,'stop',{})
        self.wait(lambda x:x['sessions'][0]['integrity']=='verified')
        second_manifest=load_json(second/'manifest.json')
        first=second.parent/second_manifest['blackbox_segment']['previous_measurement_id']
        self.assertEqual(verify_live(first)['integrity'],'verified')
        self.assertEqual(verify_live(second)['integrity'],'verified')
        self.assertEqual(load_json(first/'manifest.json')['blackbox_segment']['index'],0)
        self.assertEqual(second_manifest['blackbox_segment']['index'],1)

    def large_completed_segment(self):
        engine=LiveEngine(SyntheticSource(interval=0),FakeAdapter(),{'execution_mode':'ordered_sequence'},
                          self.root/'records',execution_kind='synthetic')
        engine.open();engine.step();engine.set_region([1,1,200,200],640,360)
        path=engine.start_measurement(blackbox=True,segment={'series_id':'a'*32,'index':0,'previous_measurement_id':None})
        while (path/'live_frames.jsonl').stat().st_size<1100000:engine.step()
        engine.stop_measurement();engine.close()
        self.assertEqual(verify_live(path)['integrity'],'verified')
        return path

    def test_quota_removes_old_segment_and_keeps_active(self):
        old=self.large_completed_segment()
        sid=self.prepare()[0]
        self.workspace.command(sid,'start',{'blackbox':True,'segment_seconds':100,'retention_max_bytes':1024**2})
        state=self.wait(lambda x:x['sessions'][0]['recording'])['sessions'][0]
        current=Path(state['directory'])
        with self.workspace.lock:self.workspace._tick_retention()
        self.assertFalse(old.exists());self.assertTrue(current.exists())
        self.assertTrue(self.workspace.snapshot()['sessions'][0]['recording'])
        self.workspace.command(sid,'stop',{})
        self.wait(lambda x:x['sessions'][0]['integrity']=='verified')

    def test_pinned_segment_is_kept_and_measurement_stops_on_limit(self):
        old=self.large_completed_segment()
        library=self.workspace.record_library('blackbox')
        self.assertTrue(library.protect(old.name,True)['protected'])
        sid=self.prepare()[0]
        self.workspace.command(sid,'start',{'blackbox':True,'segment_seconds':100,'retention_max_bytes':1024**2})
        self.wait(lambda x:x['sessions'][0]['recording'])
        with self.workspace.lock:self.workspace._tick_retention()
        state=self.wait(lambda x:not x['sessions'][0]['recording'] and x['sessions'][0]['integrity']=='verified')['sessions'][0]
        self.assertTrue(old.exists());self.assertIn('보호',state['retention_status']['reason'])
        self.assertFalse(library.protect(old.name,False)['protected'])

    def test_group_stops_at_limit_without_deleting_group_or_pinned_records(self):
        old=self.large_completed_segment()
        self.workspace.record_library('blackbox').protect(old.name,True)
        ids=self.prepare(2)
        group=self.workspace.group_command({'action':'start','inputs':ids,'blackbox':True,
                                            'retention_max_bytes':1024**2})
        self.wait(lambda x:x['groups'][0]['phase']=='recording')
        with self.workspace.lock:self.workspace._tick_retention()
        state=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        self.assertIn('보호',state['retention_stop_reason'])
        self.assertTrue(old.exists())
        self.assertTrue(all(Path(m['run_directory']).exists() for m in state['members']))
