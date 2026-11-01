"""Deterministic source/adapter tests; not physical-camera or pose-accuracy tests."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from bed_pose.cli import main
from bed_pose.contracts import IntegrityError, SourceError
from bed_pose.io_utils import atomic_json, file_hash, load_json
from bed_pose.live_engine import LiveEngine, LiveFailure, LiveWorker
from bed_pose.live_gui import source_point, viewport
from bed_pose.live_recording import make_region, verify_live
from bed_pose.live_sources import CameraSource, SyntheticSource
from bed_pose.pose.fake import FakeAdapter, FakeOptions


class Clock:
    def __init__(self):
        self.value = 10**12
    def __call__(self):
        self.value += 2_000_000
        return self.value


class LiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def engine(self, source=None, adapter=None, clock=None):
        return LiveEngine(source or SyntheticSource(interval=0), adapter or FakeAdapter(),
            {'execution_mode':'ordered_sequence'}, self.root/'runs', execution_kind='synthetic',
            clock=clock or Clock(), utc_clock=lambda:1700000000000000000)

    def ready(self, engine):
        engine.open()
        engine.step()
        engine.set_region([10,20,200,300],640,360)
        return engine

    def lines(self, directory, name):
        return [json.loads(line) for line in (directory/name).read_text(encoding='utf-8').splitlines()]

    def test_requires_preview_and_region_before_measurement(self):
        engine=self.engine()
        with self.assertRaises(ValueError):engine.start_measurement()
        engine.open()
        with self.assertRaises(ValueError):engine.start_measurement()
        engine.step()
        with self.assertRaises(ValueError):engine.start_measurement()
        self.assertFalse((self.root/'runs').exists())
        engine.close()

    def test_measurement_region_revisions_and_unknown_predictions(self):
        engine=self.ready(self.engine())
        directory=engine.start_measurement()
        engine.step()
        engine.set_region([20,30,220,310],640,360)
        engine.step()
        engine.set_region(None,640,360)
        engine.step()
        _,manifest=engine.stop_measurement()
        engine.close()
        self.assertTrue(manifest['ledger_complete'])
        self.assertEqual(verify_live(directory)['counts'],{'ok':3,'failed':0})
        rows=self.lines(directory,'live_frames.jsonl')
        self.assertEqual([r['frame_id'] for r in rows],[0,1,2])
        self.assertEqual([r['stream_frame_id'] for r in rows],[1,2,3])
        self.assertEqual([r['bed_region_revision'] for r in rows],[1,2,3])
        self.assertTrue(all(r['camera_exposure_ns'] is None for r in rows))
        self.assertTrue(all(r['posture_prediction']=={'status':'not_implemented','label':None} for r in rows))
        self.assertFalse(manifest['video_recording'])
        self.assertEqual({p.name for p in directory.iterdir()}, {'manifest.json','live_frames.jsonl','control_events.jsonl'})
        self.assertIsNone(self.lines(directory,'control_events.jsonl')[2]['region'])

    def test_zero_candidate_success_and_distinct_measurements(self):
        adapter=FakeAdapter(FakeOptions(no_detection_at=(1,)))
        engine=self.ready(self.engine(adapter=adapter))
        first=engine.start_measurement();engine.step();engine.stop_measurement()
        second=engine.start_measurement();engine.step();engine.stop_measurement();engine.close()
        self.assertNotEqual(first,second)
        self.assertEqual(self.lines(first,'live_frames.jsonl')[0]['candidate_count'],0)
        self.assertEqual(verify_live(first)['counts'],{'ok':1,'failed':0})
        self.assertEqual(self.lines(second,'live_frames.jsonl')[0]['frame_id'],0)

    def test_inference_failure_is_complete_failed_recording(self):
        engine=self.ready(self.engine(adapter=FakeAdapter(FakeOptions(fail_at=1))))
        directory=engine.start_measurement()
        with self.assertRaises(LiveFailure):engine.step()
        engine.close('failed',{'code':'INFERENCE_ERROR'})
        self.assertEqual(verify_live(directory)['run_status'],'failed')
        self.assertEqual(verify_live(directory)['counts'],{'ok':0,'failed':1})
        row=self.lines(directory,'live_frames.jsonl')[0]
        self.assertIsNone(row['candidate_count'])
        self.assertEqual(row['error']['code'],'INFERENCE_ERROR')

    def test_source_disconnect_does_not_fabricate_a_frame(self):
        engine=self.ready(self.engine())
        directory=engine.start_measurement();engine.step()
        with patch.object(engine.source,'read',side_effect=SourceError('unplugged')):
            with self.assertRaises(LiveFailure):engine.step()
        engine.close('failed',{'code':'SOURCE_READ_FAILED'})
        self.assertEqual(verify_live(directory)['counts'],{'ok':1,'failed':0})
        self.assertEqual(verify_live(directory)['run_status'],'failed')

    def test_geometry_change_fails_without_reusing_old_region(self):
        engine=self.ready(self.engine());directory=engine.start_measurement()
        with patch.object(engine.source,'read',return_value=np.zeros((100,100,3),dtype=np.uint8)):
            with self.assertRaisesRegex(LiveFailure,'영상 크기'):engine.step()
        engine.close('failed',{'code':'CAMERA_GEOMETRY_CHANGED'})
        self.assertEqual(verify_live(directory)['counts'],{'ok':0,'failed':0})

    def test_bad_region_and_letterbox_coordinates(self):
        self.assertEqual(viewport(1000,500,640,320),(1.5625,0.0,0.0))
        self.assertIsNone(source_point(0,0,1000,500,640,480))
        point=source_point(500,250,1000,500,640,480)
        self.assertAlmostEqual(point[0],320);self.assertAlmostEqual(point[1],240)
        self.assertEqual(source_point(-10,-10,1000,500,640,480,clamp=True),(0,0))
        with self.assertRaises(ValueError):make_region([0,0,0,5],640,480)
        with self.assertRaises(ValueError):make_region([-1,0,40,40],640,480)
        engine=self.ready(self.engine())
        with self.assertRaises(ValueError):engine.set_region([0,0,20,20],100,100)
        engine.close()

    def test_cleanup_failure_cannot_be_success(self):
        class BadClose(FakeAdapter):
            def close(self):raise RuntimeError('close failed')
        engine=self.ready(self.engine(adapter=BadClose()))
        directory=engine.start_measurement();engine.step();engine.close()
        self.assertEqual(verify_live(directory)['run_status'],'failed')
        self.assertEqual(load_json(directory/'manifest.json')['errors'][0]['code'],'CLEANUP_ERROR')

    def test_record_corruption_and_fake_prediction_rejected(self):
        engine=self.ready(self.engine());directory=engine.start_measurement();engine.step();engine.stop_measurement();engine.close()
        path=directory/'live_frames.jsonl'
        row=self.lines(directory,'live_frames.jsonl')[0]
        row['posture_prediction']={'status':'predicted','label':'supine'}
        path.write_text(json.dumps(row)+'\n',encoding='utf-8')
        with self.assertRaises(IntegrityError):verify_live(directory)
        manifest=load_json(directory/'manifest.json');manifest['artifact_sha256']['live_frames.jsonl']=file_hash(path)
        atomic_json(directory/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(directory)

    def test_storage_failure_leaves_incomplete_manifest(self):
        engine=self.ready(self.engine());directory=engine.start_measurement()
        stream=engine.recorder.streams['live_frames.jsonl']
        class Broken:
            @property
            def closed(self):return stream.closed
            def write(self,value):raise OSError('disk full')
            def flush(self):return stream.flush()
            def fileno(self):return stream.fileno()
            def close(self):return stream.close()
        engine.recorder.streams['live_frames.jsonl']=Broken()
        with self.assertRaises(IntegrityError):engine.step()
        with self.assertRaises(IntegrityError):engine.close('failed')
        self.assertFalse(load_json(directory/'manifest.json')['ledger_complete'])
        with self.assertRaises(IntegrityError):verify_live(directory)

    def test_worker_owns_lifecycle_and_records_even_if_ui_does_not_poll_frames(self):
        engine=self.engine(source=SyntheticSource(interval=0.01))
        worker=LiveWorker(engine);worker.start()
        self.addCleanup(lambda:(worker.request_close(),worker.join(timeout=3)))
        def wait_message(kind):
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                found,value=worker.messages.get(timeout=2)
                if found=='error':self.fail(value)
                if found==kind:return value
            self.fail('worker timeout')
        wait_message('ready')
        worker.frames.get(timeout=2)
        worker.submit('region',box=[10,20,200,300],width=640,height=360);wait_message('region')
        worker.submit('start');started=wait_message('started')
        deadline=time.monotonic()+2
        while engine.recorder is not None and engine.recorder.manifest['recorded_frames']<3 and time.monotonic()<deadline:
            time.sleep(0.01)
        worker.submit('stop');wait_message('stopped')
        worker.request_close();worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertGreater(worker.display_replaced,0)
        self.assertGreaterEqual(verify_live(Path(started['directory']))['counts']['ok'],3)

    def test_live_inspect_command_and_invalid_camera_arguments(self):
        engine=self.ready(self.engine());directory=engine.start_measurement();engine.step();engine.stop_measurement();engine.close()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['live-inspect','--run',str(directory)]),0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['live','--camera','-1']),2)

    def test_camera_open_failure_does_not_switch_to_fake(self):
        class Capture:
            def isOpened(self):return False
            def release(self):self.released=True
        capture=Capture()
        with patch('cv2.VideoCapture',return_value=capture):
            with self.assertRaises(SourceError):CameraSource().open()
        self.assertTrue(capture.released)

    def test_empty_measurement_and_model_timestamp_collision(self):
        engine=self.ready(self.engine())
        directory=engine.start_measurement();engine.stop_measurement();engine.close()
        self.assertEqual(verify_live(directory)['counts'],{'ok':0,'failed':0})
        fixed=lambda:10**12
        engine=self.ready(self.engine(clock=fixed))
        directory=engine.start_measurement()
        with self.assertRaises(LiveFailure):engine.step()
        engine.close('failed',{'code':'MODEL_TIMESTAMP_COLLISION'})
        self.assertEqual(verify_live(directory)['counts'],{'ok':0,'failed':1})
        self.assertFalse(self.lines(directory,'live_frames.jsonl')[0]['sdk_call_started'])

    def test_camera_bgr_rgb_conversion_and_unknown_fps(self):
        class Capture:
            def isOpened(self):return True
            def get(self,key):return float('nan')
            def getBackendName(self):return 'TEST'
            def read(self):return True,np.array([[[1,2,3]]],dtype=np.uint8)
            def release(self):self.released=True
        capture=Capture()
        with patch('cv2.VideoCapture',return_value=capture):
            source=CameraSource();profile=source.open()
            self.assertIsNone(profile['driver_reported_fps'])
            self.assertEqual(source.read().tolist(),[[[3,2,1]]])
            source.close()
        self.assertTrue(capture.released)

    def test_live_profile_mutation_is_detected(self):
        engine=self.ready(self.engine());directory=engine.start_measurement();engine.step();engine.stop_measurement();engine.close()
        manifest=load_json(directory/'manifest.json')
        manifest['adapter_profile']['options']['people']=9
        atomic_json(directory/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(directory)
