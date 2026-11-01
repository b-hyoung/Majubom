"""Score provenance and missing-value tests; fixtures are not inference results."""
import copy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from bed_pose.contracts import IntegrityError, PoseOutput
from bed_pose.demo import create_demo_video
from bed_pose.evaluation import read_run
from bed_pose.io_utils import file_hash, load_json
from bed_pose.joint_inspection import inspect_joints
from bed_pose.mapping import map_keypoints
from bed_pose.pose.fake import FakeAdapter, FakeOptions
from bed_pose.pose.mediapipe import MediaPipeAdapter, mapping_profile
from bed_pose.rendering import render_overlay
from bed_pose.runner import run_benchmark, validate_run
from bed_pose.video import prepare_input


@dataclass
class Landmark:
    x: float
    y: float
    z: float = 0.0
    visibility: float | None = 0.0
    presence: float | None = 0.0


def fixture():
    result = SimpleNamespace(
        pose_landmarks=[[Landmark(i/100, i/100, visibility=i/100, presence=1-i/100) for i in range(33)] for _ in range(2)],
        pose_world_landmarks=[[Landmark(i, i, visibility=0.99, presence=0.98) for i in range(33)] for _ in range(2)])
    output = PoseOutput(tuple(MediaPipeAdapter._candidates(result, 200, 100)), None)
    mapped = {'people': map_keypoints(output, 200, 100, mapping_profile())}
    return {'candidates': list(output.candidates)}, mapped


class InspectionTests(unittest.TestCase):
    def test_source_indices_and_candidate_ids_not_common_indices_or_world_scores(self):
        raw, mapped = fixture()
        raw['candidates'][1]['score_blocks'][0]['values'][25] = 0.42
        before = copy.deepcopy((raw, mapped))
        raw['candidates'].reverse()
        people = inspect_joints(mapped, raw)
        left_knee = people[0]['joints'][13]
        self.assertEqual(left_knee['name'], 'left_knee')
        self.assertEqual(left_knee['label'], '왼쪽 무릎')
        self.assertEqual(left_knee['source_index'], 25)
        self.assertEqual(left_knee['visibility'], {'status': 'reported', 'value': 0.25})
        self.assertEqual(left_knee['presence']['value'], 0.75)
        self.assertEqual(people[1]['joints'][13]['visibility']['value'], 0.42)
        self.assertEqual(mapped, before[1])
        self.assertEqual(raw['candidates'][1], before[0]['candidates'][0])

    def test_zero_null_nonfinite_and_malformed_are_distinct(self):
        raw, mapped = fixture()
        values = raw['candidates'][0]['score_blocks'][0]['values']
        for source, value in ((0, 0.0), (2, None), (5, {'nonfinite': 'nan'}), (7, True), (8, -2.0), (11, 3.0)):
            values[source] = value
        joints = inspect_joints(mapped, raw)[0]['joints']
        self.assertEqual(joints[0]['visibility'], {'status': 'reported', 'value': 0.0})
        self.assertEqual(joints[1]['visibility']['status'], 'not_output')
        self.assertEqual(joints[2]['visibility']['status'], 'non_finite')
        self.assertEqual(joints[3]['visibility']['status'], 'invalid')
        self.assertIsNone(joints[1]['visibility']['value'])
        self.assertEqual(joints[4]['visibility']['value'], -2.0)
        self.assertEqual(joints[5]['visibility']['value'], 3.0)

    def test_no_image_score_does_not_fall_back_to_world(self):
        raw, mapped = fixture()
        raw['candidates'][0]['score_blocks'] = [b for b in raw['candidates'][0]['score_blocks'] if b['source_block_id']=='world']
        joints = inspect_joints(mapped, raw)[0]['joints']
        self.assertTrue(all(j['visibility']['status']=='not_output' for j in joints))

    def test_shape_mismatch_and_duplicate_scores_are_not_silently_used(self):
        raw, mapped = fixture()
        raw['candidates'][0]['score_blocks'][0]['values'].pop()
        self.assertEqual(inspect_joints(mapped, raw)[0]['joints'][0]['visibility']['status'], 'invalid')
        raw, mapped = fixture()
        raw['candidates'][0]['score_blocks'].append(copy.deepcopy(raw['candidates'][0]['score_blocks'][0]))
        self.assertEqual(inspect_joints(mapped, raw)[0]['joints'][0]['visibility']['status'], 'invalid')

    def test_coordinate_absence_and_outside_flags_do_not_change_raw_scores(self):
        raw, mapped = fixture()
        joint = mapped['people'][0]['joints'][13]
        joint.update(availability='missing', reason='not_output', x_px=None, y_px=None)
        inspected = inspect_joints(mapped, raw)[0]['joints'][13]
        self.assertIsNone(inspected['x_px'])
        self.assertEqual(inspected['visibility']['value'], 0.25)
        joint.update(availability='predicted', reason=None, x_px=220.0, y_px=50.0, flags=['outside_image'])
        inspected = inspect_joints(mapped, raw)[0]['joints'][13]
        self.assertEqual(inspected['x_px'], 220)
        self.assertEqual(inspected['flags'], ['outside_image'])

    def test_missing_association_rejected_and_no_candidate_has_no_joints(self):
        raw, mapped = fixture()
        with self.assertRaises(IntegrityError):
            inspect_joints(mapped, {'candidates': []})
        self.assertEqual(inspect_joints({'people': []}, {'candidates': []}), [])
        self.assertEqual(inspect_joints(None, None), [])

    def test_existing_run_can_render_scores_without_changing_record_files(self):
        class ScoredFake(FakeAdapter):
            def estimate(self, frame):
                output = super().estimate(frame)
                for candidate in output.candidates:
                    candidate['score_blocks'] = [{'source_block_id': 'image', 'score_type': kind,
                                                 'values': [i/20 for i in range(17)]}
                                                for kind in ('visibility', 'presence')]
                return output
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = create_demo_video(root/'video.mkv')
            prepared = prepare_input(video, root/'input', root/'runs')
            options = FakeOptions(no_detection_at=(1,), fail_at=2)
            run, _ = run_benchmark(validate_run(prepared.parent/'run_config.fake.json', options), adapter=ScoredFake(options))
            before = {p.name: file_hash(p) for p in run.iterdir() if p.is_file()}
            output, _ = render_overlay(video, run, root/'render')
            catalog = load_json(output/'frames.json')
            self.assertEqual(catalog[0]['joint_inspection'][0]['joints'][13]['visibility']['value'], 0.65)
            self.assertEqual(catalog[1]['joint_inspection'], [])
            self.assertEqual(catalog[2]['joint_inspection'], [])
            self.assertEqual(catalog[3]['joint_inspection'], [])
            self.assertEqual(before, {p.name: file_hash(p) for p in run.iterdir() if p.is_file()})
            self.assertEqual(len(read_run(run, include_raw=True)['raw_results']), 2)
