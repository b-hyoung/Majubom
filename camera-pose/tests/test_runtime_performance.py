"""Resource policy/timing tests; no claim about physical camera FPS."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from bed_pose import clip_posture
from bed_pose.live_engine import LiveEngine
from bed_pose.live_sources import SyntheticSource
from bed_pose.pose.fake import FakeAdapter


class RuntimePerformanceTests(unittest.TestCase):
    def test_cpu_budget_is_bounded_once_and_explicit_override_is_validated(self):
        torch=Mock();torch.get_num_threads.return_value=32
        with patch.object(clip_posture,'_CPU_THREADS',None), patch.dict(os.environ,{},clear=True), patch('os.cpu_count',return_value=16):
            self.assertEqual(clip_posture.configure_cpu_threads(torch),4)
            self.assertEqual(clip_posture.configure_cpu_threads(torch),4)
            torch.set_num_threads.assert_called_once_with(4)
        for value in ('0','-1','65','four','1.5','４'):
            with patch.object(clip_posture,'_CPU_THREADS',None), patch.dict(os.environ,{'BEDPOSE_CLIP_CPU_THREADS':value}):
                with self.assertRaises(ValueError):clip_posture.configure_cpu_threads(torch)
        with patch.object(clip_posture,'_CPU_THREADS',None), patch.dict(os.environ,{'BEDPOSE_CLIP_CPU_THREADS':'2'}):
            self.assertEqual(clip_posture.configure_cpu_threads(torch),2)

    def test_small_existing_thread_budget_is_preserved(self):
        torch=Mock();torch.get_num_threads.return_value=1
        with patch.object(clip_posture,'_CPU_THREADS',None), patch.dict(os.environ,{},clear=True):
            self.assertEqual(clip_posture.configure_cpu_threads(torch),1)

    def test_auto_device_uses_available_cuda_and_explicit_cpu_is_preserved(self):
        torch=Mock();torch.backends.mps.is_available.return_value=False
        with patch.dict('sys.modules',{'torch':torch}):
            for available,expected in ((True,'cuda'),(False,'cpu')):
                torch.cuda.is_available.return_value=available
                self.assertEqual(clip_posture.resolve_device('auto'),expected)
                self.assertEqual(clip_posture.resolve_device('cpu'),'cpu')
        with self.assertRaises(ValueError):clip_posture.resolve_device('invalid')

    def test_auto_mps_and_cpu_fallback_on_non_cuda_hosts(self):
        torch=Mock();torch.cuda.is_available.return_value=False
        with patch.dict('sys.modules',{'torch':torch}):
            torch.backends.mps.is_available.return_value=True
            self.assertEqual(clip_posture.resolve_device('auto'),'mps')
            self.assertEqual(clip_posture.resolve_device('cpu'),'cpu')
            self.assertEqual(clip_posture.resolve_device('mps'),'mps')
            torch.backends.mps.is_available.return_value=False
            self.assertEqual(clip_posture.resolve_device('auto'),'cpu')
            torch.backends=None
            self.assertEqual(clip_posture.resolve_device('auto'),'cpu')

    def test_stage_times_measure_work_without_dropping_frames(self):
        now=[0]
        class Source(SyntheticSource):
            def read(self):
                now[0]+=10_000_000
                return super().read()
        class Adapter(FakeAdapter):
            def estimate(self,model_input):
                now[0]+=25_000_000
                return super().estimate(model_input)
        with tempfile.TemporaryDirectory() as d:
            engine=LiveEngine(Source(interval=0),Adapter(),{'execution_mode':'ordered_sequence'},d,
                              execution_kind='synthetic',clock=lambda:now[0])
            engine.open()
            try:
                for i in range(3):
                    frame=engine.step()
                    self.assertEqual(frame['row']['stream_frame_id'],i)
                    self.assertEqual(frame['processing_ms'],{'read':10.,'pose':25.,'image':None,'total':35.})
            finally:engine.close()
