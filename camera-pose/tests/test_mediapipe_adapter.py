"""SDK-boundary stubs: these tests do not assert successful model inference."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from bed_pose.contracts import AdapterCallError, COCO17, FramePacket, ModelInput
from bed_pose.demo import create_demo_video
from bed_pose.io_utils import atomic_json, file_hash, load_json
from bed_pose.mapping import map_keypoints
from bed_pose.pose.fake import FakeOptions
from bed_pose.pose.mediapipe import (COCO_INDICES, MP33, MediaPipeAdapter,
    mapping_profile, prepare_configuration, validated_options)
from bed_pose.recording import verify_records
from bed_pose.runner import run_benchmark, validate_run
from bed_pose.video import prepare_input


@dataclass
class Landmark:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    visibility: float | None = 0.7
    presence: float | None = 0.8


def result():
    return SimpleNamespace(
        pose_landmarks=[[Landmark(i / 100, i / 200, -i / 300) for i in range(33)]],
        pose_world_landmarks=[[Landmark(i, i + 1, i + 2) for i in range(33)]])


class StubTask:
    def __init__(self):
        self.calls = []
        self.closed = False

    def detect(self, image):
        self.calls.append(None)
        return result()

    def detect_for_video(self, image, stamp):
        self.calls.append(stamp)
        return result()

    def close(self):
        self.closed = True


def stub_module(tasks):
    def create(options):
        task = StubTask()
        tasks.append(task)
        return task
    def base_options(**kwargs):
        return SimpleNamespace(**kwargs)
    base_options.Delegate = SimpleNamespace(CPU='CPU')
    vision = SimpleNamespace(PoseLandmarkerOptions=lambda **kw: SimpleNamespace(**kw),
        PoseLandmarker=SimpleNamespace(create_from_options=create),
        RunningMode=SimpleNamespace(IMAGE='IMAGE', VIDEO='VIDEO'))
    return SimpleNamespace(tasks=SimpleNamespace(vision=vision, BaseOptions=base_options),
        Image=lambda **kw: SimpleNamespace(**kw), ImageFormat=SimpleNamespace(SRGB='SRGB'))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        video = create_demo_video(self.root / 'video.mkv')
        manifest = prepare_input(video, self.root / 'input', self.root / 'runs')
        self.model = self.root / 'placeholder.task'
        self.model.write_bytes(b'Test placeholder, not a model')
        self.config = prepare_configuration(manifest.parent / 'run_config.fake.json',
            self.model, self.root / 'model_config')
        self.settings = load_json(self.config)
        self.tasks = []
        self.addCleanup(patch.stopall)
        patch('bed_pose.pose.mediapipe.version', return_value='0.10.35').start()
        patch.dict('sys.modules', {'mediapipe': stub_module(self.tasks)}).start()

    def input(self, stamp=None):
        return ModelInput(FramePacket('v', 0, np.zeros((100, 200, 3), dtype=np.uint8), {}), stamp)

    def test_mapping_and_all_original_landmark_fields(self):
        adapter = MediaPipeAdapter()
        metadata = adapter.open(self.settings)
        output = adapter.estimate(self.input())
        self.assertTrue(output.sdk_call_started)
        self.assertEqual(metadata['execution_kind'], 'model_inference')
        self.assertFalse(metadata['internal_tracking'])
        self.assertEqual(tuple(MP33[i] for i in COCO_INDICES), COCO17)
        raw = output.candidates[0]
        self.assertEqual(raw['sdk_landmarks']['world'][32]['z'], 34)
        self.assertEqual(raw['sdk_landmarks']['image'][32]['presence'], 0.8)
        self.assertEqual(len(raw['score_blocks']), 4)
        mapped = map_keypoints(output, 200, 100, mapping_profile())[0]['joints']
        self.assertAlmostEqual(mapped[5]['x_px'], 22)
        self.assertAlmostEqual(mapped[5]['y_px'], 5.5)
        self.assertEqual(mapped[0]['x_px'], 0)
        adapter.close()
        self.assertTrue(self.tasks[-1].closed)

    def test_video_timestamp_and_recreation(self):
        adapter = MediaPipeAdapter()
        metadata = adapter.open(dict(self.settings, execution_mode='ordered_sequence'))
        self.assertTrue(metadata['internal_tracking'])
        self.assertTrue(metadata['internal_smoothing'])
        adapter.estimate(self.input(0))
        with self.assertRaises(AdapterCallError) as cm:
            adapter.estimate(self.input(0))
        self.assertFalse(cm.exception.sdk_call_started)
        old = self.tasks[-1]
        self.assertEqual(adapter.reset_for_video()['method'], 'recreate_task')
        self.assertTrue(old.closed)
        adapter.estimate(self.input(0))
        adapter.close()

    def test_sdk_failure_is_marked_as_called(self):
        adapter = MediaPipeAdapter()
        adapter.open(self.settings)
        with patch.object(self.tasks[-1], 'detect', side_effect=RuntimeError('test SDK failure')):
            with self.assertRaises(AdapterCallError) as cm:
                adapter.estimate(self.input())
        self.assertTrue(cm.exception.sdk_call_started)
        adapter.close()

    def test_zero_people_and_bad_landmark_count(self):
        adapter = MediaPipeAdapter()
        adapter.open(self.settings)
        with patch.object(self.tasks[-1], 'detect', return_value=SimpleNamespace(pose_landmarks=[], pose_world_landmarks=[])):
            self.assertEqual(adapter.estimate(self.input()).candidates, ())
        bad = result()
        bad.pose_landmarks[0].pop()
        with patch.object(self.tasks[-1], 'detect', return_value=bad):
            with self.assertRaises(AdapterCallError):
                adapter.estimate(self.input())
        adapter.close()

    def test_hash_device_and_options_validation(self):
        self.model.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            validate_run(self.config)
        self.model.write_bytes(b'Test placeholder, not a model')
        with self.assertRaisesRegex(ValueError, 'cpu'):
            validated_options(dict(self.settings, requested_device='gpu'))
        options = Path(self.settings['adapter_options_path'])
        atomic_json(options, dict(load_json(options), num_poses=True))
        changed = dict(self.settings, adapter_options_sha256=file_hash(options))
        with self.assertRaisesRegex(ValueError, 'positive integer'):
            validated_options(changed)

    def test_real_model_rejects_fake_injection_and_does_not_overwrite(self):
        with self.assertRaisesRegex(ValueError, 'Fake test options'):
            validate_run(self.config, FakeOptions(fail_at=1))
        with self.assertRaises(FileExistsError):
            prepare_configuration(self.config, self.model, self.config.parent)

    def test_model_boundary_stub_through_recorder_with_warmup(self):
        config = dict(self.settings, warmup_frames=2, execution_mode='ordered_sequence')
        atomic_json(self.config, config)
        directory, manifest = run_benchmark(validate_run(self.config))
        self.assertEqual(manifest['run_status'], 'succeeded')
        self.assertEqual(verify_records(directory), {'ok': 6, 'failed': 0, 'not_processed': 0})
        self.assertIsNone(manifest['testing_options'])
        rows = (directory / 'frame_results.jsonl').read_text(encoding='utf-8').splitlines()
        import json
        self.assertTrue(all(json.loads(row)['sdk_call_started'] for row in rows))
        self.assertEqual(self.tasks[-1].calls, [0, 40, 100, 140, 220, 260])
        self.assertTrue(all(task.closed for task in self.tasks))

    def test_wrong_sdk_version_fails_without_fake_fallback(self):
        with patch('bed_pose.pose.mediapipe.version', return_value='0.0.0'):
            directory, manifest = run_benchmark(validate_run(self.config))
        self.assertEqual(manifest['run_status'], 'failed')
        self.assertEqual(verify_records(directory), {'ok': 0, 'failed': 0, 'not_processed': 6})
        self.assertEqual(self.tasks, [])

    def test_mapping_failure_keeps_successful_sdk_call_flag(self):
        with patch('bed_pose.runner.map_keypoints', side_effect=ValueError('mapping failed')):
            directory, manifest = run_benchmark(validate_run(self.config))
        self.assertEqual(manifest['run_status'], 'failed')
        import json
        row = json.loads((directory / 'frame_results.jsonl').read_text(encoding='utf-8').splitlines()[0])
        self.assertTrue(row['sdk_call_started'])
        self.assertIsNotNone(row['raw_ref'])
        self.assertEqual(verify_records(directory), {'ok': 0, 'failed': 1, 'not_processed': 5})

    def test_configuration_command_creates_runnable_contract(self):
        from bed_pose.cli import main
        import contextlib
        import io
        destination = self.root / 'cli_config'
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['configure-mediapipe', '--config', str(self.config),
                         '--model', str(self.model), '--output', str(destination),
                         '--num-poses', '2', '--execution-mode', 'ordered_sequence'])
        self.assertEqual(code, 0)
        run = validate_run(destination / 'run_config.mediapipe.json')
        self.assertEqual(validated_options(run.config)['num_poses'], 2)
        adapter = MediaPipeAdapter()
        metadata = adapter.open(run.config)
        self.assertFalse(metadata['internal_smoothing'])
        adapter.close()
