"""tof_autocal.py — 이탈 후 레이더 이동 확인, 5초 중앙값, 흔들린 칸 유지, 큰 변화 거절."""
import unittest

import tof_autocal as A


class AutoCalTests(unittest.TestCase):
    def test_radar_move_after_exit_only(self):
        hist = [(0.0, [(1, 0.0, 2.0)]), (5.0, [(1, 0.1, 2.0)]), (11.0, [(1, 0.15, 2.05)])]
        self.assertFalse(A.radar_moved(hist, since=0.0)["moved"])         # 누운 채 미세하게만 움직임
        hist.append((14.0, [(1, 0.6, 2.4)]))
        r = A.radar_moved(hist, since=0.0)
        self.assertTrue(r["moved"])
        self.assertGreaterEqual(r["max_move_m"], A.RADAR_MOVE_M)
        self.assertFalse(A.radar_moved(hist, since=20.0)["moved"])        # 이탈 전 기록은 보지 않는다

    def test_median_and_unstable_zone_keeps_old(self):
        old = [1000] * 64
        frames = []
        for i in range(30):
            f = [1010 + (i % 3)] * 64
            f[5] = 900 if i % 2 else 1300          # 누가 베개를 만지는 칸: 흔들림 400mm
            f[6] = -1 if i % 4 else 800            # 대부분 측정 실패
            frames.append(f)
        new, stats = A.build_baseline(frames, old)
        self.assertEqual(new[0], 1011)
        self.assertEqual(new[5], 1000)             # 흔들린 칸 → 예전 값
        self.assertEqual(new[6], 1000)             # 유효 부족 → 예전 값
        self.assertEqual((stats["unstable_zones"], stats["invalid_zones"]), (1, 1))

    def test_small_bedding_change_applies_big_change_refuses(self):
        old = [1000] * 64
        pillow = [1000] * 64
        for z in range(8):
            pillow[z] = 850                         # 베개를 옮겨 8칸이 15cm 가까워짐
        c = A.compare(old, pillow)
        self.assertTrue(c["apply"])
        self.assertEqual(c["changed"], 8)
        moved = [1500] * 64                         # 침대를 옮김: 모든 칸 50cm
        self.assertFalse(A.compare(old, moved)["apply"])

    def test_first_baseline_is_created(self):
        self.assertTrue(A.compare(None, [1000] * 64)["apply"])


if __name__ == "__main__":
    unittest.main()


class BedClear20261022(unittest.TestCase):
    def test_bed_must_be_clear_of_radar_tracks(self):
        import tof_autocal as A
        near = [(t * 0.5, [(1, 0.2, 1.4), (2, 2.5, 3.0)]) for t in range(12)]     # 침대 옆 0.22m에 사람
        far = [(t * 0.5, [(2, 2.5, 3.0)]) for t in range(12)]
        self.assertFalse(A.bed_clear(near, 5.5)["clear"])
        self.assertTrue(A.bed_clear(far, 5.5)["clear"])
        self.assertTrue(A.bed_clear([(t * 0.5, []) for t in range(12)], 5.5)["clear"])
        self.assertFalse(A.bed_clear(far[:2], 5.5)["clear"])                        # 기록 부족 → 안전하게 '아님'
