import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prefall_predictor import HERE, JsonTrees, PreFallPredictor  # noqa: E402


def walk(t0, sec, speed, z=1.0, fs=18.0):
    rows, x = [], 0.0
    for i in range(int(sec * fs)):
        t = t0 + i / fs
        x += speed / fs
        rows.append((t, x, 0.02 * np.sin(2 * np.pi * 0.7 * t), z, speed + 0.1 * np.sin(2 * np.pi * 0.7 * t), 60))
    return rows


class PreFallPredictorTests(unittest.TestCase):
    def setUp(self):
        self.p = PreFallPredictor()

    def test_needs_ten_seconds_then_scores_every_half_second(self):
        results = [self.p.update(*r) for r in walk(100.0, 20.0, 0.8)]
        self.assertTrue(all(r is None for r in results[: int(13 * 18) - 1]))
        scored = [r for r in results if r]
        self.assertGreaterEqual(len(scored), 12)
        self.assertTrue(all(0.0 <= r["prob"] <= 1.0 for r in scored))
        self.assertAlmostEqual(scored[1]["t"] - scored[0]["t"], 0.5, places=6)

    def test_no_alarm_while_body_is_low(self):
        rows = walk(0.0, 12.0, 0.8) + walk(12.0, 8.0, 0.05, z=0.4)
        out = [self.p.update(*r) for r in rows]
        low = [r for r in out if r and r["t"] > 13.5]
        self.assertTrue(low)
        self.assertTrue(all(not r["upright"] and not r["alarm"] for r in low))

    def test_reset_forgets_baseline(self):
        for r in walk(0.0, 14.0, 0.8):
            self.p.update(*r)
        self.assertIsNotNone(self.p.base)
        self.p.reset()
        self.assertIsNone(self.p.base)


class JsonTreesTests(unittest.TestCase):
    def test_numpy_trees_match_xgboost(self):
        try:
            from xgboost import XGBClassifier
        except ImportError:
            self.skipTest("xgboost not installed")
        path = os.path.join(HERE, "models", "prefall_xgb.json")
        ref = XGBClassifier()
        ref.load_model(path)
        rng = np.random.default_rng(0)
        X = np.column_stack([rng.uniform(0, 2, 300) for _ in range(15)])
        X[::17, 3] = np.nan
        self.assertLess(np.abs(JsonTrees(path).predict_proba(X)[:, 1] - ref.predict_proba(X)[:, 1]).max(), 1e-5)


if __name__ == "__main__":
    unittest.main()
