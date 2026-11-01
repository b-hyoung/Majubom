"""측정 범위 필터 시험: python -m unittest mmWave/tests/test_room_filter.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from send_mmw import ROOM_POLYGON, in_room  # noqa: E402


class InRoomTest(unittest.TestCase):
    def test_measured_positions_are_inside(self):
        for x, y in [(0.0, 0.0), (-0.82, -0.38), (-1.03, 1.63), (1.47, 0.67), (-0.73, 1.61)]:
            self.assertTrue(in_room(x, y), (x, y))

    def test_tracks_seen_outside_the_room_are_outside(self):
        for x, y in [(-1.38, -3.41), (-1.38, 3.50), (-1.37, 2.98), (-0.29, 3.13), (2.67, -0.41), (0.64, -2.65)]:
            self.assertFalse(in_room(x, y), (x, y))

    def test_bed_corners_are_inside(self):
        for x, y in [(-1.56, -1.21), (-0.07, -1.21), (-0.07, 0.45), (-1.56, 0.45)]:
            self.assertTrue(in_room(x, y), (x, y))

    def test_polygon_is_closed_and_has_area(self):
        area = sum(ROOM_POLYGON[i - 1][0] * ROOM_POLYGON[i][1] - ROOM_POLYGON[i][0] * ROOM_POLYGON[i - 1][1]
                   for i in range(len(ROOM_POLYGON))) / 2
        self.assertGreater(abs(area), 10.0)


if __name__ == "__main__":
    unittest.main()
