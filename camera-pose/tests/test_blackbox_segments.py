"""Segment boundary contracts using synthetic input; no physical cameras."""
import unittest
from bed_pose.posture_stability import Stabilizer
from bed_pose.io_utils import atomic_json, load_json
from bed_pose.live_recording import verify_live
import test_live as live_tests


class SegmentTests(unittest.TestCase):
    setUp=live_tests.LiveTests.setUp
    engine=live_tests.LiveTests.engine
    ready=live_tests.LiveTests.ready
    lines=live_tests.LiveTests.lines

    def test_segment_links_video_frames_and_stability(self):
        engine=self.ready(self.engine())
        segment={'series_id':'a'*32,'index':0,'previous_measurement_id':None}
        first=engine.start_measurement(blackbox=True,record_video=True,segment=segment)
        for _ in range(3):engine.step()
        checkpoint=engine.stability.checkpoint()
        first_dir,first_manifest,second=engine.rotate_blackbox_segment()
        self.assertEqual(first,first_dir)
        self.assertEqual(engine.stability.checkpoint(),checkpoint)
        self.assertEqual(engine.recorder.manifest['initial_stability_checkpoint'],checkpoint)
        for _ in range(3):engine.step()
        _,second_manifest=engine.stop_measurement();engine.close()
        self.assertEqual(verify_live(first)['integrity'],'verified')
        self.assertEqual(verify_live(second)['integrity'],'verified')
        self.assertEqual(second_manifest['blackbox_segment'],{'series_id':'a'*32,'index':1,'previous_measurement_id':first.name})
        first_rows=self.lines(first,'live_frames.jsonl')
        second_rows=self.lines(second,'live_frames.jsonl')
        self.assertEqual(first_rows[-1]['stream_frame_id']+1,second_rows[0]['stream_frame_id'])
        self.assertTrue(first_manifest['video_recording'] and second_manifest['video_recording'])
        self.assertEqual(len(self.lines(second,'video_frames.jsonl')),len(second_rows))
        tampered=load_json(second/'manifest.json')
        tampered['initial_stability_checkpoint']['sequence']=second_rows[0]['stream_frame_id']
        atomic_json(second/'manifest.json',tampered)
        with self.assertRaises(Exception):verify_live(second)

    def test_unsegmented_record_cannot_rotate(self):
        engine=self.ready(self.engine());engine.start_measurement(blackbox=True)
        with self.assertRaises(ValueError):engine.rotate_blackbox_segment()
        engine.stop_measurement();engine.close()

    def test_checkpoint_keeps_pending_and_confirmed_state(self):
        machine=Stabilizer({'confirm_ms':0,'min_samples':2})
        predicted={'status':'predicted','label':'side'}
        machine.observe(predicted,10,0,{'track':'one'})
        restored=Stabilizer(machine.profile);restored.restore(machine.checkpoint())
        self.assertEqual(machine.observe(predicted,20,1,{'track':'one'}),
                         restored.observe(predicted,20,1,{'track':'one'}))
        restored=Stabilizer(machine.profile);restored.restore(machine.checkpoint())
        self.assertEqual(machine.observe(predicted,30,2,{'track':'one'}),
                         restored.observe(predicted,30,2,{'track':'one'}))
