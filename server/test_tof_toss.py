"""tof_toss.py — 움직임 기반 뒤척임 판정(합성 프레임)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tof_toss as T  # noqa: E402

BASE = {"tof1": [1500] * 64, "tof2": [1500] * 64}
RNG = np.random.default_rng(0)


def shape(level):
    return {s: list(np.full(64, level) + RNG.normal(0, 8, 64)) for s in ("tof1", "tof2")}


def run(seq):
    d, t, events = T.TossDetector(), 0.0, []
    for fr, label in seq:
        out = d.update(t, fr, label, BASE)
        if out["active"] and (not events or events[-1]["at"] != d.last["at"]):
            events.append(dict(d.last))
        t += 0.5
    return events


class TossTests(unittest.TestCase):
    def test_posture_change_with_movement_is_tossing(self):
        seq = [(shape(1200), "supine")] * 10 + [(shape(1200 - 40 * k), "supine") for k in range(1, 6)] \
            + [(shape(1000), "side_left")] * 10
        ev = run(seq)
        self.assertEqual([(e["from"], e["to"]) for e in ev], [("supine", "side_left")])
        self.assertLess(ev[0]["sec"], 5)

    def test_label_flicker_without_movement_is_not_tossing(self):
        seq = [(shape(1200), "side_left" if i % 4 < 2 else "side_right") for i in range(60)]
        self.assertEqual(run(seq), [])

    def test_fidget_that_returns_to_same_shape_is_not_tossing(self):
        seq = [(shape(1200), "supine")] * 10 + [(shape(1100), "side_left")] * 2 + [(shape(1200), "supine")] * 10
        self.assertEqual(run(seq), [])

    def test_getting_up_to_sit_is_not_tossing(self):
        seq = [(shape(1200), "supine")] * 10 + [(shape(1000 - 50 * k), "sitting") for k in range(4)] \
            + [(shape(800), "sitting")] * 10
        self.assertEqual(run(seq), [])


if __name__ == "__main__":
    unittest.main()
