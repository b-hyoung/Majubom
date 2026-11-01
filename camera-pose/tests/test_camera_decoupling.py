import threading
import tempfile
import unittest
import numpy as np
from bed_pose.latest_camera import LatestCameraSource
from bed_pose.replay_compare import equivalent
from bed_pose.live_engine import LiveEngine
from bed_pose.pose.fake import FakeAdapter


class Camera:
    index=0
    def __init__(self):self.requests=threading.Semaphore(0);self.n=0;self.closed=False
    def open(self):return {'kind':'camera'}
    def read(self):
        if not self.requests.acquire(timeout=2):raise RuntimeError('test source timeout')
        self.n+=1
        return np.full((60,80,3),self.n,dtype=np.uint8)
    def close(self):self.closed=True


class CameraDecouplingTests(unittest.TestCase):
    def test_recomputed_floats_tolerate_rounding_but_not_labels_types_or_meaningful_changes(self):
        self.assertTrue(equivalent({'a':[169.53718383151116]}, {'a':[169.53718383151113]}))
        for a,b in ((1,True),(1,1.),('side','supine'),(.5,.50001),([1],[1,2]),(float('nan'),float('nan'))):
            self.assertFalse(equivalent(a,b))

    def test_capture_advances_during_blocked_analysis_and_keeps_latest_only(self):
        c=Camera();s=LatestCameraSource(c);s.open()
        try:
            c.requests.release();s.read();self.assertEqual(s.analysis_capture['capture_frame_id'],0)
            for _ in range(4):c.requests.release()
            with s.condition:self.assertTrue(s.condition.wait_for(lambda:s.sequence>=4,timeout=1))
            packet=s.latest();self.assertEqual(packet['capture_frame_id'],4)
            self.assertFalse(packet['rgb'].flags.writeable)
            image=s.read();self.assertEqual(int(image[0,0,0]),5)
            self.assertEqual(s.analysis_capture['skipped_before'],3)
        finally:
            s.stop.set();c.requests.release();s.close()
        self.assertTrue(c.closed)
        self.assertFalse(s.thread.is_alive())

    def test_source_errors_propagate_and_do_not_reuse_stale_frames(self):
        class Broken(Camera):
            def read(self):raise RuntimeError('broken')
        s=LatestCameraSource(Broken())
        try:
            try:s.open()
            except Exception:pass
            with s.condition:s.condition.wait_for(lambda:s.error is not None,timeout=1)
            self.assertIsNone(s.latest())
            with self.assertRaises(Exception):s.read()
        finally:
            try:s.close()
            except Exception:pass

    def test_camera_frame_times_and_skip_metadata_reach_analysis(self):
        c=Camera();s=LatestCameraSource(c)
        with tempfile.TemporaryDirectory() as d:
            class Adapter(FakeAdapter):
                def open(self,settings):return dict(super().open(settings),execution_kind='model_inference')
            e=LiveEngine(s,Adapter(),{'execution_mode':'ordered_sequence'},d,execution_kind='model_inference')
            e.open()
            try:
                c.requests.release();r=e.step()['row']
                self.assertEqual(r['camera_capture']['capture_frame_id'],'0')
                self.assertEqual(int(r['received_ns']),s.analysis_capture['received_ns'])
                path=e.start_measurement()
                c.requests.release()
                e.step()
                e.stop_measurement()
                from bed_pose.live_recording import verify_live
                self.assertEqual(verify_live(path)['integrity'],'verified')

            finally:
                s.stop.set();c.requests.release();e.close()

    def test_overlapping_capture_finalizes_and_invalid_timing_is_still_rejected(self):
        import itertools
        import json
        from pathlib import Path
        from bed_pose.live_recording import verify_live,_verify
        from bed_pose.contracts import IntegrityError
        c=Camera();s=LatestCameraSource(c)
        class Adapter(FakeAdapter):
            pump=False
            def open(self,settings):return dict(super().open(settings),execution_kind='model_inference')
            def estimate(self,value):
                if self.pump:
                    self.pump=False
                    expected=s.sequence+1;c.requests.release()
                    with s.condition:
                        if not s.condition.wait_for(lambda:s.sequence>=expected,timeout=1):
                            raise RuntimeError('Concurrent capture did not complete')
                return super().estimate(value)
        adapter=Adapter();ticks=itertools.count(0,10_000_000)
        with tempfile.TemporaryDirectory() as d:
            e=LiveEngine(s,adapter,{'execution_mode':'ordered_sequence'},d,clock=lambda:next(ticks))
            e.open()
            try:
                c.requests.release();e.step()
                path=e.start_measurement()
                adapter.pump=True;c.requests.release();e.step();e.step()
                e.stop_measurement()
                self.assertEqual(verify_live(path)['integrity'],'verified')
                path=Path(path);m=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
                file=path/'live_frames.jsonl';original=file.read_bytes();rows=[json.loads(l) for l in original.decode('utf-8').splitlines()]
                self.assertLess(int(rows[1]['received_ns']),int(rows[0]['record_started_ns']))
                changes=[('read_started_ns',int(rows[1]['received_ns'])+1),
                         ('inference_started_ns',int(rows[1]['received_ns'])),
                         ('received_ns',int(rows[0]['received_ns'])-1)]
                for field,value in changes:
                    mutated=json.loads(json.dumps(rows));mutated[1][field]=str(value)
                    file.write_text(''.join(json.dumps(x)+'\n' for x in mutated),encoding='utf-8')
                    with self.assertRaises(IntegrityError):_verify(path,m,hashes=False)
                file.write_bytes(original)
                self.assertEqual(verify_live(path)['integrity'],'verified')
            finally:
                s.stop.set();c.requests.release();e.close()
