"""Synthetic video/log synchronization; does not emulate camera exposure timing."""
import json
import unittest
from pathlib import Path
from test_live import LiveTests
from bed_pose.live_recording import verify_live
from bed_pose.contracts import IntegrityError
from bed_pose.io_utils import load_json, atomic_json, file_hash


class CameraVideoTests(unittest.TestCase):
    setUp=LiveTests.setUp
    engine=LiveTests.engine
    ready=LiveTests.ready
    lines=LiveTests.lines

    def test_recorded_frames_and_log_have_matching_timestamps(self):
        engine=self.ready(self.engine())
        root=engine.start_measurement(record_video=True)
        frames=[engine.step()['rgb'].copy() for _ in range(4)]
        _,manifest=engine.stop_measurement();engine.close()
        import av
        import numpy as np
        with av.open(str(root/'analysis_video.mkv')) as video:
            decoded=[f.to_ndarray(format='rgb24') for f in video.decode(video=0)]
        for actual,expected in zip(decoded,frames,strict=True):np.testing.assert_array_equal(actual,expected)
        self.assertTrue(manifest['video_recording'])
        self.assertEqual(manifest['video_artifact']['frame_count'],4)
        self.assertEqual(verify_live(root)['integrity'],'verified')
        rows=self.lines(root,'live_frames.jsonl');index=self.lines(root,'video_frames.jsonl')
        self.assertEqual([r['stream_frame_id'] for r in rows],[r['stream_frame_id'] for r in index])
        self.assertEqual([r['received_ns'] for r in rows],[r['received_ns'] for r in index])
        index[0]['received_ns']='1'
        p=root/'video_frames.jsonl';p.write_text(''.join(json.dumps(x)+'\n' for x in index))
        data=load_json(root/'manifest.json');data['video_artifact']['index_sha256']=file_hash(p);atomic_json(root/'manifest.json',data)
        with self.assertRaises(IntegrityError):verify_live(root)

    def test_empty_recording_remains_valid(self):
        engine=self.ready(self.engine());root=engine.start_measurement(record_video=True)
        _,manifest=engine.stop_measurement();engine.close()
        self.assertEqual(manifest['video_artifact']['status'],'empty')
        self.assertEqual(verify_live(root)['integrity'],'verified')

    def test_record_library_opens_matching_camera_frame(self):
        from bed_pose.record_library import RecordLibrary
        engine=self.ready(self.engine());root=engine.start_measurement(record_video=True)
        for _ in range(2):engine.step()
        engine.stop_measurement();engine.close()
        library=RecordLibrary(root.parent)
        self.assertTrue(library.open(root.name)['video_recording'])
        result=library.image(root.name,1)
        self.assertEqual(result['frame_id'],1)
        self.assertEqual(result['stream_frame_id'],2)
        self.assertEqual(result['verification'],'recorded_video_frame_timestamp_verified')
        self.assertTrue(result['image'].startswith('data:image/'))

    def test_video_off_does_not_create_artifacts(self):
        engine=self.ready(self.engine());root=engine.start_measurement();engine.step();engine.stop_measurement();engine.close()
        self.assertFalse((root/'analysis_video.mkv').exists())
        self.assertFalse((root/'video_frames.jsonl').exists())
        self.assertEqual(verify_live(root)['integrity'],'verified')

class TwoCameraVideoTests(unittest.TestCase):
    import test_group_session as group_tests
    setUp=group_tests.GroupTests.setUp
    cleanup=group_tests.GroupTests.cleanup
    request=group_tests.GroupTests.request
    wait=group_tests.GroupTests.wait
    demo=group_tests.GroupTests.demo
    control=group_tests.GroupTests.control
    prepare_pair=group_tests.GroupTests.prepare_pair

    def test_group_videos_reference_each_member_and_alignment(self):
        ids=self.prepare_pair()
        status,result=self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':1000,'record_video':True})
        self.assertEqual(status,200,result)
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=3 for s in x['sessions']))
        self.request('/api/group',{'action':'stop','group_id':result['group_id']})
        group=self.wait(lambda x:x['groups'][0]['phase']=='completed',timeout=15)['groups'][0]
        self.assertTrue(group['video_recording_requested'])
        indices=[]
        for m in group['members']:
            root=Path(m['run_directory']);manifest=load_json(root/'manifest.json')
            self.assertTrue(manifest['video_recording'])
            self.assertEqual(verify_live(root)['integrity'],'verified')
            indices.append([json.loads(line) for line in (root/'video_frames.jsonl').read_text().splitlines()])
        aligned=[json.loads(line) for line in (Path(group['directory'])/'alignment.jsonl').read_text().splitlines()]
        match=next(x for x in aligned if x['status']=='matched')
        for side, data in zip(('left','right'),indices):
            ref=match[side];entry=data[ref['frame_id']]
            self.assertEqual(entry['received_ns'],ref['received_ns'])
            self.assertEqual(entry['stream_frame_id'],ref['stream_frame_id'])

    def test_invalid_video_choice_rejected(self):
        ids=self.prepare_pair()
        status,_=self.request('/api/group',{'action':'start','inputs':ids,'record_video':'yes'})
        self.assertEqual(status,400)
