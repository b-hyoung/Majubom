"""합성 자료가 외부 시험·내부 검증 원본에서 학습으로 새지 않는지 검사한다."""
import unittest
import numpy as np
from bed_pose.tof_learning.session_data import with_training_derivatives, outer_folds, validation_split


class SyntheticSplitTests(unittest.TestCase):
    def test_only_derivatives_of_training_sessions_are_added(self):
        samples = [dict(session='S01-r1', synthetic=False),
                   dict(session='S01-r2', synthetic=False),
                   dict(session='S02-r1', synthetic=False),
                   dict(session='S03syn-r1', synthetic=True, source_session='S01-r1'),
                   dict(session='S03syn-r2', synthetic=True, source_session='S01-r2'),
                   dict(session='S04syn-r1', synthetic=True, source_session='S02-r1')]
        # S02를 외부 시험으로, S01-r2를 내부 검증으로 잡으면 S01-r1 파생만 추가한다.
        np.testing.assert_array_equal(with_training_derivatives(samples, np.array([0])), [0, 3])
        # 외부 재학습에는 S01 파생 모두 허용하지만 S02 파생은 계속 제외한다.
        np.testing.assert_array_equal(with_training_derivatives(samples, np.array([0, 1])), [0, 1, 3, 4])
        np.testing.assert_array_equal(with_training_derivatives(samples, np.array([2])), [2, 5])

    def test_synthetic_sample_cannot_enter_real_split_input(self):
        with self.assertRaises(ValueError):
            with_training_derivatives([dict(synthetic=True)], [0])

    def test_synthetic_subjects_cannot_become_external_test_subjects(self):
        with self.assertRaises(ValueError):
            outer_folds([dict(synthetic=True)])
        with self.assertRaises(ValueError):
            validation_split([dict(synthetic=True)], [0])


if __name__ == '__main__':
    unittest.main()
