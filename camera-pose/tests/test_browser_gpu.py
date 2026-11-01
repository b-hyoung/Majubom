"""Contract tests for the optional browser GPU adapter; real GPU tested separately."""
import unittest
from unittest.mock import patch
import numpy as np

from bed_pose.contracts import AdapterCallError, FramePacket, ModelInput
from bed_pose.live_factory import make_engine
from bed_pose.mapping import map_keypoints
from bed_pose.pose.browser_gpu import BrowserGPUAdapter
from bed_pose.pose.mediapipe import mapping_profile


class BrowserGPUContractTest(unittest.TestCase):
    def test_result_mapping_preserves_dimensions_and_missing_presence(self):
        adapter = BrowserGPUAdapter()
        point = {'x': .25, 'y': .5, 'z': -.2, 'visibility': .9}
        result = {'landmarks': [[point.copy() for _ in range(33)]],
                  'worldLandmarks': [[point.copy() for _ in range(33)]]}
        frame = FramePacket('camera', 7, np.zeros((20, 40, 3), dtype=np.uint8), {})
        with patch.object(adapter, '_rpc', return_value=result) as rpc:
            output = adapter.estimate(ModelInput(frame, 12))
        self.assertEqual([call.kwargs['timestamp'] for call in rpc.call_args_list], [12,13])
        self.assertEqual([call.kwargs['mode'] for call in rpc.call_args_list], ['video','probe'])
        self.assertEqual(output.model_timestamp_ms, 12)
        self.assertTrue(output.sdk_call_started)
        self.assertIsNone(output.candidates[0]['sdk_landmarks']['image'][0]['presence'])
        point = map_keypoints(output, 40, 20, mapping_profile())[0]['joints'][0]
        self.assertEqual((point['x_px'], point['y_px']), (10, 10))
        with self.assertRaises(AdapterCallError):
            adapter.estimate(ModelInput(frame, 12))

    def test_gpu_bridge_failure_does_not_return_cpu_or_synthetic_result(self):
        adapter = BrowserGPUAdapter()
        frame = FramePacket('camera', 0, np.zeros((4, 4, 3), dtype=np.uint8), {})
        with patch.object(adapter, '_rpc', side_effect=RuntimeError('GPU unavailable')):
            with self.assertRaises(AdapterCallError) as error:
                adapter.estimate(ModelInput(frame, 1))
        self.assertFalse(error.exception.sdk_call_started)
        self.assertIn('GPU unavailable', str(error.exception))

        with patch.object(adapter, '_rpc', return_value={'error': 'inference error', 'sdkCallStarted': True}):
            with self.assertRaises(AdapterCallError) as error:
                adapter.estimate(ModelInput(frame, 2))
        self.assertTrue(error.exception.sdk_call_started)

    def test_gpu_silhouette_is_explicitly_rejected(self):
        with self.assertRaisesRegex(ValueError, '실루엣'):
            make_engine(None, 'missing.task', '.', '.', pose_device='browser_gpu', silhouette=True)

    def test_single_target_option_keeps_existing_default_and_rejects_invalid_limits(self):
        import tempfile,json
        from pathlib import Path
        for limit in (0,4,True):
            with self.assertRaises(ValueError):make_engine(None,'missing.task','.','.',pose_max_people=limit)
        with tempfile.TemporaryDirectory() as d:
            model=Path(d)/'model.task';model.write_bytes(b'explicit test placeholder; not loaded')
            for value in (1,3):
                engine=make_engine(None,model,d,d,pose_device='browser_gpu',pose_max_people=value)
                options=json.loads(Path(engine.settings['adapter_options_path']).read_text(encoding='utf-8'))
                self.assertEqual(options['num_poses'],value)


if __name__ == '__main__':
    unittest.main()
