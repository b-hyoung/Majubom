"""프레임 시각 시험: python -m unittest mmWave/tests/test_frame_clock.py

시리얼 포트는 한 번에 프레임 여러 개를 읽는다. 읽은 시각 하나를 모든 프레임에 붙이면
시간 차이 대부분이 0이 되고, 보폭 계산의 샘플링 주파수(1/median(dt>0))가 실제(약 18.2Hz)보다 크게 틀어진다.
"""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from send_mmw import FRAME_PERIOD_SEC, FrameClock, frame_period_from_cfg  # noqa: E402


def batched_reads(n_frames, per_read, period=0.055, t0=1000.0):
    """레이더가 period마다 만든 프레임을 per_read개씩 묶어 읽는 상황: [(프레임 번호, 읽은 시각)]."""
    out = []
    for i in range(n_frames):
        last_in_read = (i // per_read + 1) * per_read - 1
        out.append((500 + i, t0 + last_in_read * period + 0.01))
    return out


def fs_like_reader(times):
    dt = np.diff(times)
    dt = dt[dt > 0]
    return 1.0 / np.median(dt)


class FrameClockTests(unittest.TestCase):
    def test_batched_reads_keep_true_sampling_rate(self):
        reads = batched_reads(110, per_read=5)               # 6초, 한 번에 5프레임씩
        old = [now for _, now in reads]                      # 이전 방식: 읽은 시각을 그대로 붙임
        self.assertLess(fs_like_reader(old), 4.0)            # 18.2Hz가 3.6Hz로 잘못 계산된다
        clock = FrameClock(0.055)
        new = [clock.stamp(f, now) for f, now in reads]
        self.assertAlmostEqual(fs_like_reader(new), 1 / 0.055, places=3)
        self.assertTrue(all(b > a for a, b in zip(new, new[1:])))

    def test_stamps_never_run_ahead_of_wall_clock_much(self):
        clock = FrameClock(0.055)
        for f, now in batched_reads(200, per_read=5):
            self.assertLessEqual(clock.stamp(f, now) - now, 1.0)

    def test_frame_number_reset_reanchors(self):
        clock = FrameClock(0.055)
        clock.stamp(900, 1000.0)
        clock.stamp(901, 1000.1)
        self.assertEqual(clock.stamp(3, 1005.0), 1005.0)     # 레이더 재시작으로 번호가 처음부터
        self.assertAlmostEqual(clock.stamp(4, 1005.1), 1005.055)

    def test_large_drift_reanchors(self):
        clock = FrameClock(0.055)
        clock.stamp(0, 1000.0)
        self.assertEqual(clock.stamp(10, 1003.0), 1003.0)    # 번호로는 1000.55인데 벽시계와 2.45초 어긋남

    def test_period_from_cfg(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.cfg")
            with open(p, "w", encoding="utf-8") as f:
                f.write("% 주석\nsensorStop\nframeCfg 0 1 32 0 55.00 1 0\nsensorStart\n")
            self.assertAlmostEqual(frame_period_from_cfg(p), 0.055)
            self.assertEqual(frame_period_from_cfg(os.path.join(d, "없음.cfg")), FRAME_PERIOD_SEC)
            self.assertEqual(frame_period_from_cfg(None), FRAME_PERIOD_SEC)


if __name__ == "__main__":
    unittest.main()
