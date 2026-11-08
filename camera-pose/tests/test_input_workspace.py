"""Real decoding plus fake inference; no physical camera/Windows accuracy claim."""
import contextlib
from fractions import Fraction
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import av
import numpy as np
from bed_pose.cli import main
from bed_pose.contracts import IntegrityError, SourceError
from bed_pose.io_utils import atomic_json, file_hash, load_json
from bed_pose.live_engine import LiveEngine, LiveWorker, LiveFailure
from bed_pose.live_recording import verify_live
from bed_pose.live_sources import VideoFileSource, SyntheticSource, probe_cameras
from bed_pose.pose.fake import FakeAdapter


class FileTestAdapter(FakeAdapter):
    def open(self, settings):
        # Explicit test-only fake SDK. Runtime never uses fake inference for files.
        return dict(super().open(settings),execution_kind='model_inference')


def make_video(path, count=12):
    with av.open(str(path),'w') as container:
        stream = container.add_stream('ffv1',rate=25)
        stream.width, stream.height, stream.pix_fmt = 160,120,'yuv420p'
        for index in range(count):
            rgb = np.full((120,160,3),index*10%255,np.uint8)
            frame = av.VideoFrame.from_ndarray(rgb,format='rgb24')
            frame.pts,frame.time_base = index,Fraction(1,25)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)


class InputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name);self.video=self.root/'영상.mkv'
        make_video(self.video)

    def engine(self):
        return LiveEngine(VideoFileSource(self.video),FileTestAdapter(),
                          {'execution_mode':'ordered_sequence'},self.root/'records')

    def test_file_pts_and_normal_eof(self):
        engine=self.engine();engine.open();first=engine.step()
        self.assertEqual(first['row']['model_timestamp_ms'],'0')
        engine.set_region([1,1,150,110],160,120);directory=engine.start_measurement()
        for index in range(1,12):
            row=engine.step()['row']
            self.assertEqual(int(row['model_timestamp_ms']),index*40)
            self.assertEqual(row['source_media']['source_frame_id'],index)
        with self.assertRaises(StopIteration):engine.step()
        engine.close()
        self.assertEqual(verify_live(directory)['counts'],{'ok':11,'failed':0})
        manifest=load_json(directory/'manifest.json')
        self.assertEqual(manifest['source_profile']['sha256'],file_hash(self.video))

    def test_video_pauses_before_measurement_and_finishes_successfully(self):
        worker=LiveWorker(self.engine());worker.start()
        self.addCleanup(lambda:(worker.request_close(),worker.join(timeout=2)))
        first=worker.frames.get(timeout=3)
        time.sleep(.08)
        self.assertEqual(worker.engine.next_frame_id,1)
        worker.submit('region',box=[1,1,150,110],width=160,height=120,selected_frame_id=0)
        worker.submit('start')
        worker.join(timeout=4);self.assertFalse(worker.is_alive())
        messages=[]
        while not worker.messages.empty():messages.append(worker.messages.get_nowait())
        self.assertTrue(any(k=='eof' for k,v in messages))
        self.assertFalse(any(k=='error' for k,v in messages))
        stopped=next(v for k,v in messages if k=='stopped')
        self.assertEqual(verify_live(stopped['directory'])['counts']['ok'],11)

    def test_wrong_media_time_rejected_even_with_updated_hashes(self):
        e=self.engine();e.open();e.step();e.set_region([1,1,150,110],160,120)
        directory=e.start_measurement();e.step();e.close()
        path=directory/'live_frames.jsonl';row=json.loads(path.read_text(encoding='utf-8'))
        row['model_timestamp_ms']='999';path.write_text(json.dumps(row)+'\n',encoding='utf-8')
        manifest=load_json(directory/'manifest.json');manifest['artifact_sha256'][path.name]=file_hash(path)
        atomic_json(directory/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(directory)

    def test_decoding_error_is_not_normal_eof(self):
        class Broken(VideoFileSource):
            def read(self):raise SourceError('bad decoder')
        e=LiveEngine(Broken(self.video),FileTestAdapter(),{'execution_mode':'ordered_sequence'},self.root/'runs')
        e.open()
        with self.assertRaises(LiveFailure):e.step()
        e.close()

    def test_camera_scan_releases_and_skips_busy_indices(self):
        captures=[]
        class Capture:
            released=False
            def __init__(self,index,*args):self.index=index;captures.append(self)
            def isOpened(self):return self.index==1
            def read(self):return True,np.zeros((1,1,3),np.uint8)
            def release(self):self.released=True
        with patch('cv2.VideoCapture',side_effect=Capture):
            self.assertEqual(probe_cameras(range(4),excluded={2}),[1])
        self.assertEqual([c.index for c in captures],[0,1,3])
        self.assertTrue(all(c.released for c in captures))

    def test_cli_picker_and_file_routing(self):
        with patch('bed_pose.live_gui.launch_live',return_value=0) as launch:
            self.assertEqual(main(['live']),0)
            self.assertEqual(launch.call_args.args,(None,None,Path('recordings/live'),False,None))
            self.assertEqual(main(['live','--video',str(self.video)]),0)
            self.assertEqual(launch.call_args.args[-1],self.video)
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            main(['live','--camera','0','--video',str(self.video)])

    def test_two_input_sessions_do_not_share_region_results_or_clock(self):
        first,second=self.engine(),self.engine()
        for e in (first,second):e.open();e.step()
        first.set_region([1,1,150,110],160,120)
        self.assertIsNone(second.region)
        second.set_region([2,2,100,100],160,120)
        one,two=first.start_measurement(),second.start_measurement()
        first.step();first.close()
        second.step();second.step();second.close()
        self.assertEqual(verify_live(one)['counts']['ok'],1)
        self.assertEqual(verify_live(two)['counts']['ok'],2)
        a,b=load_json(one/'manifest.json'),load_json(two/'manifest.json')
        self.assertNotEqual(a['clock_id'],b['clock_id'])
        self.assertNotEqual(a['initial_bed_region'],b['initial_bed_region'])

    def test_old_camera_schema_remains_readable(self):
        e=LiveEngine(SyntheticSource(0),FakeAdapter(),{'execution_mode':'ordered_sequence'},self.root/'runs',execution_kind='synthetic')
        e.open();e.step();e.set_region([1,1,150,110],640,360);directory=e.start_measurement()
        e.step();e.close()
        manifest=load_json(directory/'manifest.json');manifest['schema_version']='live-0.1-draft'
        path=directory/'live_frames.jsonl';row=json.loads(path.read_text(encoding='utf-8'));row['schema_version']='live-0.1-draft'
        path.write_text(json.dumps(row)+'\n',encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(directory/'manifest.json',manifest)
        self.assertEqual(verify_live(directory)['integrity'],'verified')
