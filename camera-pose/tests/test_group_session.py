import time
import unittest
from pathlib import Path
import test_web_workspace as webtests
from bed_pose.io_utils import load_json
from bed_pose.live_recording import verify_live


class GroupTests(unittest.TestCase):
    setUp=webtests.WebTests.setUp
    cleanup=webtests.WebTests.cleanup
    request=webtests.WebTests.request
    wait=webtests.WebTests.wait
    demo=webtests.WebTests.demo
    control=webtests.WebTests.control
    def prepare_pair(self):
        ids=[self.demo(),self.demo()]
        self.wait(lambda x:len(x['sessions'])==2 and all(s['frame_id'] is not None for s in x['sessions']))
        for sid in ids:self.control(sid,'region',box=[1,1,200,200],selected_frame_id=0)
        self.wait(lambda x:all(s['region'] is not None for s in x['sessions']))
        return ids

    def test_group_start_stop_and_references(self):
        ids=self.prepare_pair()
        status,value=self.request('/api/group',{'action':'start','inputs':ids})
        self.assertEqual(status,200)
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        self.assertEqual(self.control(ids[0],'stop')[0],400)
        self.request('/api/group',{'action':'stop','group_id':value['group_id']})
        state=self.wait(lambda x:x['groups'][0]['phase']=='completed')
        group=state['groups'][0]
        self.assertTrue(group['ledger_complete'])
        for member in group['members']:
            self.assertEqual(verify_live(member['run_directory'])['integrity'],'verified')
            self.assertIn('clock_origin_perf_counter_ns',member['timing'])
        self.assertEqual(load_json(Path(group['directory'])/'manifest.json')['alignment_status'],'not_implemented')
        self.assertTrue(all(not s['closed'] for s in state['sessions']))

    def test_group_partial_start_failure_rolls_back(self):
        ids=self.prepare_pair()
        def broken(*args,**kwargs):raise ValueError('injected start failure')
        self.workspace.sessions[ids[1]].worker.engine.start_measurement=broken
        self.request('/api/group',{'action':'start','inputs':ids})
        state=self.wait(lambda x:x['groups'][0]['phase']=='failed')
        self.assertTrue(all(s['closed'] for s in state['sessions']))
        started=[m for m in state['groups'][0]['members'] if m['run_directory']]
        self.assertEqual(len(started),1)
        self.assertEqual(verify_live(started[0]['run_directory'])['integrity'],'verified')

    def test_group_remove_closes_both_and_preserves_records(self):
        ids=self.prepare_pair()
        self.request('/api/group',{'action':'start','inputs':ids})
        self.wait(lambda x:x['groups'][0]['phase']=='recording')
        self.control(ids[0],'remove')
        state=self.wait(lambda x:x['groups'][0]['phase']=='completed')
        self.assertEqual(len(state['sessions']),1)
        self.assertTrue(state['sessions'][0]['closed'])
        self.assertTrue(state['groups'][0]['ledger_complete'])

    def test_group_alignment_full_record_coverage(self):
        ids=self.prepare_pair()
        status,value=self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':30})
        self.assertEqual(status,200)
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=3 for s in x['sessions']))
        self.request('/api/group',{'action':'stop','group_id':value['group_id']})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        result=group['alignment']
        self.assertEqual(result['status'],'receive_time_approximation')
        self.assertFalse(result['exposure_synchronized'])
        rows=[__import__('json').loads(line) for line in (Path(group['directory'])/'alignment.jsonl').read_text(encoding='utf-8').splitlines()]
        for side,member in zip(('left','right'),group['members']):
            references=[row[side]['frame_id'] for row in rows if row[side] is not None]
            count=load_json(Path(member['run_directory'])/'manifest.json')['recorded_frames']
            self.assertEqual(sorted(references),list(range(count)))
        self.assertEqual(group['posture_prediction']['status'],'not_implemented')

    def test_file_group_requires_verified_media_offset(self):
        from test_input_workspace import make_video
        video=self.root/'pair.mkv';make_video(video)
        asset=self.workspace.register_asset(video,'video',video.name)
        ids=[self.workspace.add({'kind':'video_file','video_asset':asset['asset_id']})['id'] for _ in range(2)]
        self.wait(lambda x:all(s['frame_id'] is not None for s in x['sessions']))
        for sid in ids:self.control(sid,'region',box=[1,1,150,100],selected_frame_id=0)
        self.wait(lambda x:all(s['region'] is not None for s in x['sessions']))
        self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':30})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        self.assertEqual(group['alignment_status'],'blocked_media_offset_required')
        self.assertFalse((Path(group['directory'])/'alignment.jsonl').exists())
