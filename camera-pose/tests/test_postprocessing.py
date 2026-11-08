"""Hand-checkable diagnostics and rendered generated frames, not AI accuracy."""
import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
import av
import numpy as np
from bed_pose.cli import main
from bed_pose.config import record_validator
from bed_pose.contracts import IntegrityError, SourceError
from bed_pose.demo import create_demo_video
from bed_pose.evaluation import (build_evaluation, compare_runs, evaluate_run,
                                percentile, read_run)
from bed_pose.io_utils import atomic_json, file_hash, load_json
from bed_pose.pose.fake import FakeOptions
from bed_pose.rendering import BANNER, render_overlay
from bed_pose.runner import run_benchmark, validate_run
from bed_pose.video import prepare_input


class PostprocessingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.video = create_demo_video(self.root / 'video.mkv')
        prepared = prepare_input(self.video, self.root / 'input', self.root / 'runs')
        self.config = prepared.parent / 'run_config.fake.json'

    def execute(self, options=None):
        return run_benchmark(validate_run(self.config, options))[0]

    def values(self, report):
        return {m['metric_name']: m for m in report['metrics']}

    def test_output_rates_and_gt_unavailable(self):
        run = self.execute(FakeOptions(no_detection_at=(1, 2, 4)))
        before = {p.name: file_hash(p) for p in run.iterdir() if p.is_file()}
        output, report = evaluate_run(run)
        metrics = self.values(report)
        self.assertEqual(metrics['candidate_output_rate']['value'], 0.5)
        self.assertEqual(metrics['candidate_output_rate']['denominator'], 6)
        self.assertEqual(metrics['processing_ok_rate']['value'], 1)
        self.assertGreater(metrics['successful_pipeline_fps']['value'], 0)
        self.assertEqual(metrics['pck_end_to_end']['reason'], 'NO_VERIFIED_GT')
        self.assertEqual(metrics['target_joint_output_rate:nose']['reason'], 'NO_TARGET_ASSOCIATION')
        self.assertTrue(load_json(output / 'manifest.json')['complete'])
        self.assertEqual(before, {p.name: file_hash(p) for p in run.iterdir() if p.is_file()})
        for metric in report['metrics']:
            record_validator().validate(metric)

    def test_failure_keeps_full_denominator_and_separate_scope(self):
        run = self.execute(FakeOptions(fail_at=2, no_detection_at=(1,)))
        full = self.values(build_evaluation(run))
        self.assertTrue(all(m['value'] is None and m['reason'] == 'INCOMPLETE_RUN' for m in full.values()))
        partial = self.values(build_evaluation(run, 'diagnostic_partial'))
        self.assertAlmostEqual(partial['candidate_output_rate']['value'], 1 / 6)
        self.assertAlmostEqual(partial['processing_ok_rate']['value'], 2 / 6)
        self.assertEqual(partial['processing_ok_rate']['denominator'], 6)
        self.assertEqual(partial['successful_pipeline_fps']['reason'], 'INCOMPLETE_RUN')
        self.assertEqual(partial['adapter_failed_p50_ms']['n_samples'], 1)

    def test_hand_checked_percentiles_and_timing_samples(self):
        self.assertEqual(percentile([10, 20, 30, 40], 0.5), 25)
        self.assertAlmostEqual(percentile([10, 20, 30, 40], 0.95), 38.5)
        self.assertEqual(percentile([7], 0.95), 7)
        with self.assertRaises(ValueError):
            percentile([], 0.5)
        run = self.execute()
        bundle = read_run(run)
        # Controlled synthetic measurements with the actual per-frame contract.
        base = 0
        for i, (row, receipt) in enumerate(zip(bundle['frames'], bundle['receipts'])):
            for stage in ('decode', 'adapter', 'mapping'):
                row['timing'][stage + '_start_ns'] = str(base)
                base += (i + 1) * 10_000_000
                row['timing'][stage + '_end_ns'] = str(base)
            receipt['started_ns'] = str(base)
            base += 1_000_000
            receipt['finished_ns'] = str(base)
        bundle['manifest']['pipeline_started_ns'] = '0'
        bundle['manifest']['data_finished_ns'] = str(base)
        with patch('bed_pose.evaluation.read_run', return_value=bundle):
            report = build_evaluation(run)
        metrics = self.values(report)
        self.assertEqual(metrics['adapter_ok_p50_ms']['value'], 35)
        self.assertAlmostEqual(metrics['adapter_ok_p95_ms']['value'], 57.5)
        self.assertEqual(report['timing_details']['adapter:ok']['first_measured_ms'], 10)
        self.assertEqual(metrics['adapter_failed_p50_ms']['reason'], 'INSUFFICIENT_SAMPLES')
        self.assertAlmostEqual(metrics['successful_pipeline_fps']['value'], 6 / (base / 1e9))

    def test_corruption_invalidates_every_metric_and_cli_exit(self):
        run = self.execute()
        with (run / 'keypoints.jsonl').open('a', encoding='utf-8') as stream:
            stream.write('{}\n')
        report = build_evaluation(run)
        self.assertEqual(report['integrity'], 'invalid')
        self.assertTrue(all(m['status'] == 'invalid' and m['value'] is None for m in report['metrics']))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['evaluate', '--run', str(run)]), 4)
        with self.assertRaises(IntegrityError):
            render_overlay(self.video, run, self.root / 'bad_render')

    def test_manifest_input_provenance_is_verified(self):
        run = self.execute()
        manifest = load_json(run / 'manifest.json')
        manifest['input_manifest_sha256'] = '0' * 64
        atomic_json(run / 'manifest.json', manifest)
        self.assertEqual(build_evaluation(run)['integrity'], 'invalid')

    def test_invalid_clock_does_not_become_positive_fps(self):
        run = self.execute()
        manifest = load_json(run / 'manifest.json')
        manifest['data_finished_ns'] = '0'
        atomic_json(run / 'manifest.json', manifest)
        metrics = self.values(build_evaluation(run))
        self.assertEqual(metrics['successful_pipeline_fps']['status'], 'invalid')
        self.assertEqual(metrics['successful_pipeline_fps']['reason'], 'CLOCK_INVALID')
        self.assertEqual(metrics['processing_ok_rate']['value'], 1)

    def test_comparison_preserves_missing_failed_and_different_modes(self):
        good = self.execute()
        failed = self.execute(FakeOptions(fail_at=0))
        config = load_json(self.config)
        config['execution_mode'] = 'ordered_sequence'
        atomic_json(self.config, config)
        ordered = self.execute()
        missing = self.root / 'missing'
        out, report = compare_runs([good, failed, missing, ordered], self.root / 'comparison')
        self.assertEqual(len(report['runs']), 4)
        self.assertEqual(report['runs'][2]['integrity'], 'invalid')
        self.assertFalse(report['runs'][1]['eligible_for_comparison'])
        self.assertNotEqual(report['runs'][0]['comparison_group'], report['runs'][3]['comparison_group'])
        self.assertIn(str(missing.resolve()), (out / 'report.md').read_text(encoding='utf-8'))
        self.assertFalse(missing.exists())
        with self.assertRaises(FileExistsError):
            compare_runs([good], out)

    def test_frame_overlay_identity_missing_failure_and_original_pixels(self):
        run = self.execute(FakeOptions(no_detection_at=(1,), fail_at=3, missing_joints=(5,)))
        output, report = render_overlay(self.video, run, self.root / 'render')
        self.assertEqual(report['frame_count'], 6)
        catalog = load_json(output / 'frames.json')
        self.assertEqual([r['source_pts'] for r in catalog], ['0', '40', '100', '140', '220', '260'])
        self.assertEqual([r['processing_status'] for r in catalog], ['ok', 'ok', 'ok', 'failed', 'not_processed', 'not_processed'])
        ns = {'s': 'http://www.w3.org/2000/svg'}
        xml = ET.parse(output / catalog[0]['file'])
        self.assertEqual(len(xml.findall('.//s:circle', ns)), 16)
        image = xml.find('.//s:image', ns)
        payload = base64.b64decode(image.attrib['href'].split(',', 1)[1])
        with av.open(io.BytesIO(payload)) as container:
            decoded = next(container.decode(video=0)).to_ndarray(format='rgb24')
        with av.open(str(self.video)) as container:
            original = next(container.decode(video=0)).to_ndarray(format='rgb24')
        np.testing.assert_array_equal(decoded, original)
        circles = xml.findall('.//s:circle', ns)
        self.assertEqual(float(circles[0].attrib['cy']), BANNER)
        for i in (1, 3, 4, 5):
            xml = ET.parse(output / catalog[i]['file'])
            self.assertEqual(xml.findall('.//s:circle', ns), [])
            self.assertIn('SYNTHETIC', (output / catalog[i]['file']).read_text(encoding='utf-8'))
        with self.assertRaises(FileExistsError):
            render_overlay(self.video, run, output)

    def test_different_video_is_rejected(self):
        run = self.execute()
        bad = self.root / 'other.mkv'
        bad.write_bytes(b'not the input')
        with self.assertRaisesRegex(SourceError, 'does not match'):
            render_overlay(bad, run, self.root / 'render')
        self.assertFalse((self.root / 'render').exists())

    def test_new_commands_and_unique_evaluations(self):
        run = self.execute()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['evaluate', '--run', str(run)]), 0)
            self.assertEqual(main(['evaluate', '--run', str(run)]), 0)
            self.assertEqual(main(['render', '--run', str(run), '--video', str(self.video), '--output', str(self.root / 'render')]), 0)
            self.assertEqual(main(['compare', '--runs', str(run), '--output', str(self.root / 'compare')]), 0)
        self.assertEqual(len(list((run / 'evaluations').iterdir())), 2)

    def test_invalid_manifest_shapes_remain_diagnostic(self):
        missing = self.root / 'missing'
        with self.assertRaises(ValueError):
            evaluate_run(missing)
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            compare_runs([missing], missing)
        run = self.execute()
        manifest = load_json(run / 'manifest.json')
        manifest['resolved_config'] = []
        manifest['code'] = []
        atomic_json(run / 'manifest.json', manifest)
        report = build_evaluation(run)
        self.assertEqual(report['evaluation_status'], 'invalid')
        self.assertTrue(all(m['value'] is None for m in report['metrics']))

    def test_render_failure_leaves_incomplete_manifest(self):
        run = self.execute()
        from bed_pose.video import VideoSource
        original = VideoSource.read_next
        def fail_after_one(source):
            if source.index == 1:
                raise SourceError('deliberate source failure')
            return original(source)
        output = self.root / 'incomplete_render'
        with patch('bed_pose.rendering.VideoSource.read_next', fail_after_one):
            with self.assertRaises(SourceError):
                render_overlay(self.video, run, output)
        self.assertFalse(load_json(output / 'manifest.json')['complete'])
