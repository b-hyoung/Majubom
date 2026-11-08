"""Portable viewer data safety and preservation of the source image geometry."""
import base64
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from bed_pose.demo import create_demo_video
from bed_pose.io_utils import load_json
from bed_pose.pose.fake import FakeOptions
from bed_pose.rendering import render_overlay
from bed_pose.runner import run_benchmark, validate_run
from bed_pose.video import prepare_input
from bed_pose.viewer import viewer_html


class PayloadParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_payload = False
        self.payload = ''
        self.scripts = 0
        self.external_assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.scripts += 1
            self.in_payload = attrs.get('id') == 'frame-data'
        if tag in ('img', 'script', 'link', 'iframe'):
            value = attrs.get('src', attrs.get('href'))
            if value and not value.startswith('data:'):
                self.external_assets.append(value)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_payload = False

    def handle_data(self, data):
        if self.in_payload:
            self.payload += data


class ViewerTests(unittest.TestCase):
    def test_single_file_escapes_ids_without_losing_original_values(self):
        identifier = '</script><script>alert("x")</script>한글__FIRST_FRAME____DATA_BADGE__'
        catalog = [{'frame_id': 0, 'candidate_ids': [identifier]}]
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><title>한글</title></svg>'
        html = viewer_html({'run_id': identifier, 'run_status': 'failed', 'execution_kind': 'synthetic'},
                           {'canonical_width': 64, 'canonical_height': 48}, catalog, [svg])
        parser = PayloadParser()
        parser.feed(html)
        self.assertEqual(parser.scripts, 2)
        self.assertEqual(parser.external_assets, [])
        self.assertIn('합성 데이터 · 실제 인식 아님', html.split('<script')[0])
        data = json.loads(parser.payload)
        self.assertEqual(data['run_id'], identifier)
        self.assertEqual(data['frames'][0]['candidate_ids'], [identifier])
        self.assertEqual(base64.b64decode(data['frames'][0]['svg']).decode(), svg)

    def test_render_data_distinguishes_zero_and_unknown_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = create_demo_video(root / 'input.mkv')
            prepared = prepare_input(video, root / 'input', root / 'runs')
            run, _ = run_benchmark(validate_run(prepared.parent / 'run_config.fake.json',
                FakeOptions(no_detection_at=(1,), fail_at=2, people=2, missing_joints=(5,))))
            output, _ = render_overlay(video, run, root / 'render')
            catalog = load_json(output / 'frames.json')
            self.assertEqual(catalog[0]['drawable_joint_count'], 32)
            self.assertEqual(catalog[0]['candidate_ids'], ['p0', 'p1'])
            self.assertEqual(catalog[1]['candidate_count'], 0)
            self.assertEqual(catalog[1]['drawable_joint_count'], 0)
            self.assertIsNone(catalog[2]['candidate_count'])
            self.assertIsNone(catalog[2]['drawable_joint_count'])
            self.assertEqual(catalog[3]['processing_status'], 'not_processed')
            svg = ET.parse(output / catalog[0]['file']).getroot()
            self.assertEqual(svg.attrib['viewBox'], '0 0 96 64')
            image = svg.find('{http://www.w3.org/2000/svg}image')
            self.assertEqual(image.attrib['y'], '0')
            parser = PayloadParser()
            parser.feed((output / 'index.html').read_text(encoding='utf-8'))
            payload = json.loads(parser.payload)
            self.assertEqual(len(payload['frames']), 6)
            self.assertEqual(payload['frames'][2]['source_pts'], '100')
            self.assertEqual(parser.external_assets, [])

    def test_limited_preview_preserves_source_count_and_record_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = create_demo_video(root / 'input.mkv')
            prepared = prepare_input(video, root / 'input', root / 'runs')
            run, _ = run_benchmark(validate_run(prepared.parent / 'run_config.fake.json'))
            output, report = render_overlay(video, run, root / 'preview', max_frames=2)
            self.assertEqual(report['frame_count'], 2)
            self.assertEqual(report['source_frame_count'], 6)
            self.assertEqual(report['rendered_frame_range'], [0, 2])
            catalog = load_json(output / 'frames.json')
            self.assertEqual([row['frame_id'] for row in catalog], [0, 1])
            self.assertEqual([row['source_pts'] for row in catalog], ['0', '40'])
            parser = PayloadParser()
            parser.feed((output / 'index.html').read_text(encoding='utf-8'))
            self.assertEqual(json.loads(parser.payload)['total_source_frames'], 6)
            self.assertEqual(load_json(run / 'manifest.json')['counts']['ok'], 6)
            with self.assertRaises(ValueError):
                render_overlay(video, run, root / 'invalid', max_frames=0)
