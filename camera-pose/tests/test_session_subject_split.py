"""참여자 평가에서 빈 침대 촬영 묶음과 시험 참여자 분리를 확인한다."""
import unittest
from bed_pose.tof_learning.session_data import outer_folds, validation_split


class SubjectSplitTests(unittest.TestCase):
    def test_empty_sessions_stay_with_their_capture_group(self):
        samples = []
        for person in ('S01', 'S02'):
            for rep in (1, 2, 3):
                for label in range(6):
                    samples.append(dict(subject='없음' if label == 0 else person,
                                        collection_subject=person, rep=rep, y=label))
        mode, folds = outer_folds(samples)
        self.assertEqual(mode, 'subject')
        for held_out, train, test in folds:
            self.assertEqual({samples[i]['collection_subject'] for i in test}, {held_out})
            self.assertNotIn(held_out, {samples[i]['collection_subject'] for i in train})
            inner_train, inner_valid = validation_split(samples, train)
            self.assertTrue(set(inner_train).isdisjoint(inner_valid))
            self.assertTrue(set(inner_train) | set(inner_valid) <= set(train))
            self.assertEqual({samples[i]['y'] for i in inner_train}, set(range(6)))
            self.assertEqual({samples[i]['y'] for i in inner_valid}, set(range(6)))


if __name__ == '__main__':
    unittest.main()
