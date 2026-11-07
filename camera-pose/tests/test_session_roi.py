"""기존 ROI 재현과 원본 배열 보존, CNN 채널의 마스킹을 검사한다."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np
from bed_pose.tof_learning.session_data import channels
from bed_pose.tof_learning.session_roi import apply_roi


class SessionRoiTests(unittest.TestCase):
    def test_matches_existing_roi_and_preserves_original_arrays(self):
        source = Path(__file__).resolve().parents[2]/'TOF/ml/roi.py'
        spec = importlib.util.spec_from_file_location('reference_roi', source)
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        raw = np.full((2, 64), 3000, dtype=np.float32)
        baseline = np.full((2, 64), 3500, dtype=np.float32)
        raw[0, 0] = -1
        original = raw.copy()
        original_baseline = baseline.copy()
        samples = [dict(raw=raw, x=channels(raw, baseline), label='supine')]
        masked_baseline, audit = apply_roi(samples, baseline, source)
        expected = np.array([reference.mask_offbed(s, raw[i].tolist())
                             for i, s in enumerate(('tof1', 'tof2'))], dtype=np.float32)
        np.testing.assert_array_equal(samples[0]['raw'], expected)
        np.testing.assert_array_equal(raw, original)
        np.testing.assert_array_equal(baseline, original_baseline)
        self.assertGreater(sum(audit['statistics']['supine']['removed_valid_cells']), 0)
        invalid = (expected <= 0).reshape(2, 8, 8)
        self.assertTrue((samples[0]['x'][:, 0][invalid] == 0).all())
        self.assertTrue((samples[0]['x'][:, 1][invalid] == 0).all())
        self.assertTrue((samples[0]['x'][:, 2][invalid] == 0).all())
        np.testing.assert_array_equal(samples[0]['x'], channels(expected, masked_baseline))


if __name__ == '__main__':
    unittest.main()
