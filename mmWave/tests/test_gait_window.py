import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import send_mmw as M  # noqa: E402


class GaitWindowTests(unittest.TestCase):
    def test_turn_between_parts_still_counts_as_straight(self):
        # 3 s east, then 3 s north: the whole 6 s path is bent, each 3 s part is straight.
        t = np.arange(0, 6.0, 1 / 18)
        xy = np.array([[min(s, 3.0) * 0.6, max(s - 3.0, 0.0) * 0.6] for s in t])
        whole = np.hypot(*(xy[-1] - xy[0])) / np.sum(np.hypot(*np.diff(xy, axis=0).T))
        self.assertLess(whole, M.STRAIGHT_MIN + 0.25)
        self.assertGreater(M.straightness_by_part(t, xy), 0.95)

    def test_back_and_forth_part_fails(self):
        t = np.arange(0, 6.0, 1 / 18)
        xy = np.array([[0.6 * (s if s < 1.5 else 3.0 - s) if s < 3 else 0.6 * (s - 3), 0.0] for s in t])
        self.assertLess(M.straightness_by_part(t, xy), M.STRAIGHT_MIN)

    def test_sway_ignores_curved_path(self):
        # A smooth arc with no body wobble should have almost no sway.
        t = np.arange(0, 6.0, 1 / 18)
        ang = t / 6.0 * np.pi / 2
        arc = np.column_stack([1.5 * np.sin(ang), 1.5 * (1 - np.cos(ang))])
        self.assertLess(M._sway_along_heading(t, arc), 0.01)
        wobble = arc + np.column_stack([np.zeros_like(t), 0.05 * np.sin(2 * np.pi * 1.0 * t)])
        self.assertGreater(M._sway_along_heading(t, wobble), 0.01)

    def test_gait_from_samples_uses_heading_sway(self):
        t = np.arange(0, 6.0, 1 / 18)
        ang = t / 6.0 * np.pi / 2
        samples = [(ti, 1.5 * np.sin(a), 1.5 * (1 - np.cos(a)), 1.2, 0.4, 0.0) for ti, a in zip(t, ang)]
        self.assertLess(M._gait_from_samples(samples)["sway"], 0.01)


if __name__ == "__main__":
    unittest.main()
