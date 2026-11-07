import unittest
from copy import deepcopy
from bed_pose.pose_display import display_people, MP33_EDGES


class PoseDisplayTests(unittest.TestCase):
    def test_full_native_points_and_common_analysis_unchanged(self):
        native = [{'x': i / 100, 'y': i / 200, 'visibility': .8, 'presence': .9} for i in range(33)]
        row = {'mapped_people': [{'candidate_id': 'p0', 'joints': [{}] * 17}],
               'raw_candidates': [{'candidate_id': 'p0', 'sdk_landmarks': {'image': native}}]}
        original = deepcopy(row)
        people = display_people(row, 640, 480)
        self.assertEqual(row, original)
        self.assertEqual(len(people[0]['joints']), 33)
        self.assertEqual(people[0]['joints'][32]['name'], 'right_foot_index')
        self.assertAlmostEqual(people[0]['joints'][32]['x_px'], 204.8)
        self.assertEqual(people[0]['joints'][32]['visibility'], .8)
        self.assertEqual(set(sum(MP33_EDGES, ())), set(range(33)))
        native[31]['x'] = {'nonfinite': 'nan'}
        self.assertEqual(display_people(row, 640, 480)[0]['joints'][31]['availability'], 'missing')

    def test_fallback_and_candidate_pairing(self):
        person = {'candidate_id': 'p1', 'joints': [{}] * 17}
        row = {'mapped_people': [person], 'raw_candidates': []}
        self.assertEqual(display_people(row, 640, 480), [person])
        row['raw_candidates'] = [{'candidate_id': 'p0', 'sdk_landmarks': {'image': [{}]*33}}]
        self.assertEqual(display_people(row, 640, 480), [person])
        self.assertEqual(display_people({}, 640, 480), [])
