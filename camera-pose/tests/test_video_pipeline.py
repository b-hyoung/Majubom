"""Generated nonhuman video and injected outputs; no AI accuracy claims."""
import contextlib
import copy
from fractions import Fraction
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import av
import numpy as np
from bed_pose.cli import main
from bed_pose.config import load_json, load_run_config, record_validator
from bed_pose.contracts import AdapterCallError, IntegrityError, SCHEMA_VERSION, SourceError
from bed_pose.demo import create_demo_video
from bed_pose.io_utils import atomic_json, file_hash, json_text
from bed_pose.mapping import MappingProfile, map_keypoints
from bed_pose.pose.fake import FakeAdapter, FakeOptions
from bed_pose.recording import ResultRecorder, verify_records
from bed_pose.runner import run_benchmark, validate_run
from bed_pose.video import VideoSource, canonical_rgb, model_timestamps, prepare_input, validate_input

ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = create_demo_video(self.root / 'nonhuman.mkv')
        self.input = prepare_input(self.video, self.root / 'input', self.root / 'runs')
        self.config = self.input.parent / 'run_config.fake.json'

    def execute(self, options=None, adapter=None):
        return run_benchmark(validate_run(self.config, options), adapter=adapter)

    def lines(self, path):
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    def update_config(self, **changes):
        config = load_json(self.config)
        config.update(changes)
        atomic_json(self.config, config)

    def test_success_zero_detection_multi_person_and_missing_joint(self):
        path, manifest = self.execute(FakeOptions(no_detection_at=(1,), people=2, missing_joints=(3,)))
        self.assertEqual(manifest['run_status'], 'succeeded')
        self.assertTrue(manifest['ledger_complete'])
        self.assertEqual(manifest['execution_kind'], 'synthetic')
        self.assertEqual(verify_records(path), {'ok': 6, 'failed': 0, 'not_processed': 0})
        frames = self.lines(path / 'frame_results.jsonl')
        raw = self.lines(path / 'raw_results.jsonl')
        mapped = self.lines(path / 'keypoints.jsonl')
        self.assertEqual([r['source_pts'] for r in frames], ['0', '40', '100', '140', '220', '260'])
        self.assertEqual(frames[1]['candidate_count'], 0)
        self.assertIsNotNone(frames[1]['raw_ref'])
        self.assertEqual(raw[1]['candidates'], [])
        self.assertEqual(mapped[1]['people'], [])
        self.assertEqual(len(raw[0]['candidates']), 2)
        joints = mapped[0]['people'][0]['joints']
        self.assertEqual(joints[0]['x_px'], 0.0)
        self.assertEqual(joints[0]['availability'], 'predicted')
        self.assertIsNone(joints[3]['x_px'])
        self.assertEqual(joints[3]['reason'], 'not_output')
        self.assertTrue(all(r['target']['status'] == 'not_configured' for r in frames))
        for row in frames:
            record_validator().validate(row)

    def test_failure_records_rest_without_running_them(self):
        options = FakeOptions(fail_at=2)
        path, manifest = self.execute(options)
        self.assertEqual(manifest['run_status'], 'failed')
        self.assertTrue(manifest['ledger_complete'])
        self.assertEqual(verify_records(path), {'ok': 2, 'failed': 1, 'not_processed': 3})
        frames = self.lines(path / 'frame_results.jsonl')
        self.assertEqual(frames[2]['failure_code'], 'INFERENCE_ERROR')
        self.assertIsNone(frames[2]['candidate_count'])
        self.assertIsNone(frames[2]['raw_ref'])
        self.assertTrue(all(r['processing_status'] == 'not_processed' for r in frames[3:]))
        self.assertTrue(all(r['timing']['adapter_start_ns'] is None for r in frames[3:]))
        self.assertEqual(len(self.lines(path / 'raw_results.jsonl')), 2)

    def test_sequence_timestamp_is_kept_on_failure(self):
        self.update_config(execution_mode='ordered_sequence')
        path, _ = self.execute(FakeOptions(fail_at=2))
        rows = self.lines(path / 'frame_results.jsonl')
        self.assertEqual([r['model_timestamp_ms'] for r in rows[:3]], ['0', '40', '100'])
        self.assertTrue(all(r['model_timestamp_ms'] is None for r in rows[3:]))
        self.assertFalse(rows[2]['sdk_call_started'])

    def test_bad_mapping_preserves_raw_result(self):
        class BadMapping(FakeAdapter):
            def estimate(self, frame):
                output = super().estimate(frame)
                output.candidates[0]['pose_blocks'][0]['values'].pop()
                return output
        path, result = self.execute(adapter=BadMapping())
        row = self.lines(path / 'frame_results.jsonl')[0]
        self.assertEqual(result['run_status'], 'failed')
        self.assertEqual(row['failure_code'], 'MAPPING_ERROR')
        self.assertIsNotNone(row['raw_ref'])
        self.assertIsNone(row['keypoints_ref'])
        self.assertEqual(len(self.lines(path / 'raw_results.jsonl')), 1)
        verify_records(path)

    def test_nonfinite_original_is_preserved_with_missing_common_coordinate(self):
        class Nonfinite(FakeAdapter):
            def estimate(self, frame):
                output = super().estimate(frame)
                output.candidates[0]['pose_blocks'][0]['values'][0] = [float('nan'), 3.0]
                return output
        path, result = self.execute(adapter=Nonfinite())
        self.assertEqual(result['run_status'], 'succeeded')
        raw = self.lines(path / 'raw_results.jsonl')[0]
        self.assertEqual(raw['candidates'][0]['pose_blocks'][0]['values'][0][0], {'nonfinite': 'nan'})
        joint = self.lines(path / 'keypoints.jsonl')[0]['people'][0]['joints'][0]
        self.assertIsNone(joint['x_px'])
        self.assertEqual(joint['reason'], 'non_finite')

    def test_cleanup_failure_does_not_declare_success(self):
        class BrokenClose(FakeAdapter):
            def close(self):
                super().close()
                raise RuntimeError('synthetic close failure')
        path, result = self.execute(adapter=BrokenClose())
        self.assertEqual(result['run_status'], 'failed')
        self.assertEqual(result['counts']['ok'], 6)
        self.assertTrue(result['ledger_complete'])
        self.assertEqual(result['errors'][-1]['code'], 'CLEANUP_ERROR')
        verify_records(path)

    def test_warmup_resets_before_first_measured_frame(self):
        class Tracking(FakeAdapter):
            def __init__(self):
                super().__init__()
                self.calls = []
                self.opens = self.resets = 0
            def open(self, settings):
                self.opens += 1
                return super().open(settings)
            def reset_for_video(self):
                self.resets += 1
                return super().reset_for_video()
            def estimate(self, frame):
                self.calls.append(frame.frame.frame_id)
                return super().estimate(frame)
        self.update_config(warmup_frames=2, execution_mode='ordered_sequence')
        adapter = Tracking()
        _, result = self.execute(adapter=adapter)
        self.assertEqual(result['run_status'], 'succeeded')
        self.assertEqual(adapter.opens, 2)
        self.assertEqual(adapter.resets, 1)
        self.assertEqual(adapter.calls, [0, 1, 0, 1, 2, 3, 4, 5])

    def test_video_hash_change_is_rejected_before_results(self):
        self.video.write_bytes(self.video.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            validate_run(self.config)
        self.assertFalse((self.root / 'runs').exists())

    def test_config_and_frame_manifest_hashes_are_checked(self):
        original = load_json(self.config)
        self.update_config(input_manifest_sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            validate_run(self.config)
        atomic_json(self.config, original)
        frames = self.input.parent / 'frames.jsonl'
        frames.write_text(frames.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            validate_run(self.config)

    def test_declared_pts_change_fails_during_decode_without_relabelling(self):
        frames_path = self.input.parent / 'frames.jsonl'
        rows = self.lines(frames_path)
        rows[0]['source_pts'] = '1'
        frames_path.write_text(''.join(json_text(row) + '\n' for row in rows), encoding='utf-8')
        manifest = load_json(self.input)
        manifest['frame_manifest_sha256'] = file_hash(frames_path)
        atomic_json(self.input, manifest)
        self.update_config(input_manifest_sha256=file_hash(self.input))
        path, result = self.execute()
        self.assertEqual(result['counts'], {'ok': 0, 'failed': 1, 'not_processed': 5})
        self.assertEqual(self.lines(path / 'frame_results.jsonl')[0]['failure_code'], 'DECODE_ERROR')
        self.assertTrue(result['ledger_complete'])

    def test_tampered_reference_and_extra_raw_record_are_detected(self):
        path, _ = self.execute()
        raw_path = path / 'raw_results.jsonl'
        original = raw_path.read_text(encoding='utf-8')
        raw_path.write_text(original + original.splitlines()[0] + '\n', encoding='utf-8')
        with self.assertRaises(IntegrityError):
            verify_records(path)
        with self.assertRaises(IntegrityError):
            verify_records(path, check_hashes=False)
        raw_path.write_text(original, encoding='utf-8')
        frames_path = path / 'frame_results.jsonl'
        rows = self.lines(frames_path)
        rows[0]['raw_ref']['record_id'] = 'frame:999'
        frames_path.write_text(''.join(json_text(row) + '\n' for row in rows), encoding='utf-8')
        with self.assertRaises(IntegrityError):
            verify_records(path, check_hashes=False)

    def test_manifest_cannot_relabel_failed_run_successful(self):
        path, _ = self.execute(FakeOptions(fail_at=2))
        manifest = load_json(path / 'manifest.json')
        manifest['run_status'] = 'succeeded'
        atomic_json(path / 'manifest.json', manifest)
        with self.assertRaises(IntegrityError):
            verify_records(path)

    def test_write_failure_never_completes_ledger(self):
        original_append = ResultRecorder.append
        def fail(recorder, row, raw=None, mapped=None):
            if row['frame_id'] == 1:
                recorder.broken = True
                raise OSError('synthetic storage failure')
            return original_append(recorder, row, raw, mapped)
        with patch.object(ResultRecorder, 'append', fail):
            _, result = self.execute()
        self.assertEqual(result['run_status'], 'failed')
        self.assertFalse(result['ledger_complete'])

    def test_output_directory_is_unique_and_input_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            prepare_input(self.video, self.input.parent)
        first, _ = self.execute()
        second, _ = self.execute()
        self.assertNotEqual(first, second)
        self.assertTrue((first / 'manifest.json').exists())

    def test_unsupported_real_adapter_is_not_silently_faked(self):
        config = load_json(self.config)
        config.update(adapter_id='mediapipe_pose', requested_device='cpu',
                      assets=[{'role': 'weights', 'path': 'missing.task', 'sha256': '0' * 64}],
                      adapter_options_path='not-present.json', adapter_options_sha256='0' * 64)
        atomic_json(self.config, config)
        with self.assertRaisesRegex(ValueError, 'pose_landmarker_task'):
            validate_run(self.config)

    def test_inspect_reports_missing_artifact_as_integrity_error(self):
        path, _ = self.execute()
        (path / 'keypoints.jsonl').unlink()
        with self.assertRaises(IntegrityError):
            verify_records(path)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['inspect', '--run', str(path)]), 4)

    def test_audio_only_preparation_is_not_marked_complete(self):
        import wave
        audio = self.root / 'audio_only.wav'
        with wave.open(str(audio), 'w') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b'\x00\x00' * 80)
        output = self.root / 'audio_input'
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(['prepare', '--video', str(audio), '--output', str(output)])
        self.assertEqual(code, 2)
        self.assertFalse(load_json(output / 'input_manifest.json')['complete'])

    def test_configuration_rejects_nonfinite_and_prediction_label_fields(self):
        bad = self.root / 'invalid.json'
        for text in ('{"value":1e999}', '{"value":NaN}'):
            bad.write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError):
                load_json(bad)
        from jsonschema import ValidationError
        self.update_config(planned_pose='supine')
        with self.assertRaises(ValidationError):
            validate_run(self.config)

    def test_packaged_contract_matches_design_schema(self):
        self.assertEqual((ROOT / 'docs/schemas/benchmark-v0.1.schema.json').read_bytes(),
                         (ROOT / 'src/bed_pose/schemas/benchmark-v0.1.schema.json').read_bytes())

    def test_paths_are_config_relative_from_other_working_directory(self):
        old = Path.cwd()
        try:
            os.chdir(self.root)
            resolved = load_run_config(self.config)
            validated = validate_run(self.config)
        finally:
            os.chdir(old)
        self.assertEqual(Path(resolved['output_root']), (self.root / 'runs').resolve())
        self.assertEqual(validated.video_path, self.video.resolve())

    def test_cli_demo_run_and_inspect(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['demo', '--output', str(self.root / 'demo'), '--fake-no-detection-at', '1'])
        self.assertEqual(code, 0)
        summary = json.loads(output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['inspect', '--run', summary['run_directory']])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['integrity'], 'verified')
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['run', '--config', str(self.config), '--fake-fail-at', '2'])
        self.assertEqual(code, 3)


class TimeAndGeometryTests(unittest.TestCase):
    def test_timestamp_rounding_collision_is_rejected(self):
        rows = [{'timestamp_source': 'pts', 'source_pts': str(i),
                 'time_base_num': 1, 'time_base_den': 10000} for i in (0, 1)]
        self.assertEqual(model_timestamps(rows, 'independent_frames'), [None, None])
        with self.assertRaises(ValueError):
            model_timestamps(rows, 'ordered_sequence')
        rows[1]['source_pts'] = '0'
        with self.assertRaises(ValueError):
            model_timestamps(rows, 'ordered_sequence')

    def test_missing_pts_is_not_replaced_by_fps(self):
        row = {'timestamp_source': 'unavailable', 'source_pts': None,
               'time_base_num': None, 'time_base_den': None}
        self.assertEqual(model_timestamps([row], 'independent_frames'), [None])
        with self.assertRaises(ValueError):
            model_timestamps([row], 'ordered_sequence')

    def test_display_rotation_is_applied_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rotated.mp4'
            with av.open(str(path), 'w') as container:
                stream = container.add_stream('mpeg4', rate=25)
                stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
                stream.set_display_rotation(90)
                pixels = np.zeros((48, 64, 3), dtype=np.uint8)
                pixels[:10, :20] = [240, 0, 0]
                for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format='rgb24')):
                    container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            with av.open(str(path)) as container:
                decoded = next(container.decode(video=0))
                expected = np.ascontiguousarray(np.rot90(decoded.to_ndarray(format='rgb24'), 1))
                self.assertTrue(np.array_equal(canonical_rgb(decoded), expected))
            manifest_path = prepare_input(path, Path(directory) / 'input')
            manifest, rows, video = validate_input(manifest_path, file_hash(manifest_path))
            self.assertEqual((manifest['canonical_width'], manifest['canonical_height']), (48, 64))
            source = VideoSource(manifest, rows, video)
            source.open()
            try:
                self.assertTrue(np.array_equal(source.read_next().rgb, expected))
                self.assertIsNone(source.read_next())
            finally:
                source.close()

    def test_coordinate_transform_is_not_applied_twice(self):
        from bed_pose.contracts import FramePacket, ModelInput
        pixels = np.zeros((50, 100, 3), dtype=np.uint8)
        adapter = FakeAdapter()
        adapter.open({'execution_mode': 'independent_frames'})
        output = adapter.estimate(ModelInput(FramePacket('v', 0, pixels, {}), None))
        block = output.candidates[0]['pose_blocks'][0]
        block['values'][0] = [.25, .5]
        block['coordinate_space'] = 'normalized_image'
        block['transform_to_canonical'] = {
            'kind': 'normalized_to_canonical', 'matrix': [[100, 0, 0], [0, 50, 0], [0, 0, 1]],
            'source_space': 'normalized_image', 'destination_space': 'canonical_pixels'}
        people = map_keypoints(output, 100, 50)
        self.assertEqual((people[0]['joints'][0]['x_px'], people[0]['joints'][0]['y_px']), (25.0, 25.0))
        block['coordinate_space'] = 'canonical_pixels'
        block['transform_to_canonical']['kind'] = 'identity'
        block['transform_to_canonical']['source_space'] = 'canonical_pixels'
        with self.assertRaises(ValueError):
            map_keypoints(output, 100, 50)
