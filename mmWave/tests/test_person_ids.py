"""사람 ID 층 시험: python -m unittest mmWave/tests/test_person_ids.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from person_ids import PersonTracker  # noqa: E402

ROOM = [(-1.8, -1.9), (2.8, -1.9), (2.8, 2.0), (-1.7, 2.0)]
DOOR = [(-1.83, 1.66), (-0.43, 1.66), (-0.43, 2.5), (-1.83, 2.5)]
BED = [(-1.56, -1.21), (-0.07, -1.21), (-0.07, 0.45), (-1.56, 0.45)]


def track(tid, x, y, z=1.0, npts=20):
    return {"tid": tid, "world": [x, y, z], "npts": npts}


class Sim:
    def __init__(self):
        self.t = 1000.0
        self.pt = PersonTracker()
        self.snap = None

    def run(self, seconds, make):
        """make(i, 전체) → 그 프레임의 추적 목록. 18프레임/초로 진행한다."""
        n = int(seconds * 18)
        for i in range(n):
            self.t += 1 / 18
            self.snap = self.pt.update(self.t, make(i, n), ROOM, DOOR, BED)
        return self.snap

    def state(self, pid):
        return next(p for p in self.snap["persons"] if p["pid"] == pid)


def walk(tid, start, end, npts=20, z=1.0):
    return lambda i, n: [track(tid, start[0] + (end[0] - start[0]) * i / n, start[1] + (end[1] - start[1]) * i / n, z, npts)]


class PersonTests(unittest.TestCase):
    def enter_person(self, sim, tid=1, pos=(1.0, 0.0)):
        sim.run(2, walk(tid, (pos[0] - 0.5, pos[1]), pos))
        self.assertEqual(sim.pt.label(sim.t, tid, "person"), "P1")

    def leave_by_open_side(self, sim, tid=1):
        sim.run(3, walk(tid, (1.0, 0.0), (1.0, -1.6)))
        sim.run(5, lambda i, n: [track(tid, 1.0, -1.6, 1.0, 0)])

    def test_exit_on_open_side(self):
        sim = Sim()
        self.enter_person(sim)
        self.leave_by_open_side(sim)
        self.assertEqual(sim.state("P1")["state"], "out")
        self.assertEqual(sim.state("P1")["exit_kind"], "열린 쪽")
        self.assertEqual(sim.snap["hidden"].get(1), "나간 사람의 남은 번호")

    def test_person_who_left_open_side_is_not_linked_to_old_track_near_door(self):
        # 실제 오류: y−로 나간 사람이 나가기 전부터 문 근처에 있던 번호로 연결됐다
        sim = Sim()
        sim.run(2, lambda i, n: [track(1, 0.5 + 0.5 * i / n, 0.0), track(19, -0.1 + 0.4 * i / n, 1.9)])
        sim.pt.label(sim.t, 1, "person")
        sim.run(3, lambda i, n: [track(1, 1.0, -1.6 * i / n), track(19, 0.3 - 0.4 * i / n, 1.9)])
        sim.run(5, lambda i, n: [track(1, 1.0, -1.6, 1.0, 0), track(19, -0.1 + 0.4 * i / n, 1.9)])
        self.assertEqual(sim.state("P1")["state"], "out")
        sim.run(5, lambda i, n: [track(1, 1.0, -1.6, 1.0, 0), track(19, 0.3 - 0.4 * i / n, 1.9)])
        self.assertEqual(sim.state("P1")["state"], "out")

    def test_person_who_left_open_side_is_not_linked_to_new_track_from_door(self):
        sim = Sim()
        self.enter_person(sim)
        self.leave_by_open_side(sim)
        sim.run(4, lambda i, n: [track(1, 1.0, -1.6, 1.0, 0), track(5, -1.0, 2.0 - 1.0 * i / n)])
        self.assertEqual(sim.state("P1")["state"], "out")

    def test_person_returns_through_the_same_opening(self):
        sim = Sim()
        self.enter_person(sim)
        self.leave_by_open_side(sim)
        sim.run(4, lambda i, n: [track(1, 1.0, -1.6, 1.0, 0), track(5, 0.9, -1.7 + 1.5 * i / n)])
        self.assertEqual(sim.state("P1")["state"], "in")
        self.assertEqual(sim.state("P1")["tid"], 5)

    def test_person_stuck_on_the_door_counts_as_leaving_by_the_door(self):
        # 실제 오류: 문으로 나간 사람의 번호가 문에 붙어 계속 방 안으로 표시됐다
        sim = Sim()
        self.enter_person(sim)
        sim.run(3, walk(1, (1.0, 0.0), (-1.0, 1.85)))
        sim.run(6, lambda i, n: [track(1, -1.0, 1.85, 1.0, 8)])
        self.assertEqual(sim.state("P1")["state"], "out")
        self.assertEqual(sim.state("P1")["exit_kind"], "문")

    def test_door_swing_track_is_hidden_and_not_linked(self):
        sim = Sim()
        self.enter_person(sim)
        sim.run(3, walk(1, (1.0, 0.0), (-1.0, 1.85)))
        sim.run(6, lambda i, n: [track(1, -1.0, 1.85, 1.0, 8)])
        sim.run(1, lambda i, n: [track(1, -1.0, 1.85, 1.0, 8), track(9, -1.2, 1.8 + 0.3 * i / n)])
        sim.run(6, lambda i, n: [track(1, -1.0, 1.85, 1.0, 8), track(9, -1.2, 2.1)])
        self.assertEqual(sim.state("P1")["state"], "out")
        self.assertEqual(sim.snap["hidden"].get(9), "문")

    def test_person_returns_on_the_old_track_number(self):
        # 실제 오류: 문으로 나간 사람이 옛 번호 그대로 돌아왔는데 연결되지 않았다
        sim = Sim()
        self.enter_person(sim)
        sim.run(3, walk(1, (1.0, 0.0), (-1.0, 1.85)))
        sim.run(6, lambda i, n: [track(1, -1.0, 1.85, 1.0, 8)])
        self.assertEqual(sim.state("P1")["state"], "out")
        sim.run(4, walk(1, (-1.0, 1.85), (0.5, 0.0)))
        self.assertEqual(sim.state("P1")["state"], "in")
        self.assertEqual(sim.state("P1")["tid"], 1)

    def test_door_swing_on_the_old_number_does_not_bring_person_back(self):
        sim = Sim()
        self.enter_person(sim)
        sim.run(3, walk(1, (1.0, 0.0), (-1.0, 1.85)))
        sim.run(6, lambda i, n: [track(1, -1.0, 1.85, 1.0, 8)])
        sim.run(3, walk(1, (-1.0, 1.85), (-1.5, 2.2)))
        self.assertEqual(sim.state("P1")["state"], "out")

    def test_walking_far_past_the_open_side_counts_as_leaving(self):
        # 실제 오류: 열린 쪽으로 멀리 걸어 나가면 "끊김"으로 처리됐다
        sim = Sim()
        self.enter_person(sim)
        sim.run(5, walk(1, (1.0, 0.0), (1.0, -3.0)))
        self.assertEqual(sim.state("P1")["state"], "out")
        self.assertEqual(sim.state("P1")["exit_kind"], "열린 쪽")

    def test_jump_through_a_wall_is_not_leaving(self):
        sim = Sim()
        self.enter_person(sim)
        sim.run(3, walk(1, (1.0, 0.0), (2.5, 0.0)))
        sim.run(3, lambda i, n: [track(1, 3.2, 0.0)])
        self.assertEqual(sim.state("P1")["state"], "lost")

    def test_people_limit_rejects_a_new_person_when_everyone_is_inside(self):
        sim = Sim()
        sim.pt.max_people = 2
        people = lambda i, n: [track(1, 0.5 * i / n, 0.0), track(2, 1.0, 0.5 * i / n), track(3, -0.5, 0.5 * i / n)]
        sim.run(2, people)
        sim.pt.label(sim.t, 1, "person")
        self.assertFalse(sim.run(1, people)["full"])
        sim.pt.label(sim.t, 2, "person")
        self.assertTrue(sim.run(1, people)["full"])
        with self.assertRaises(ValueError):
            sim.pt.label(sim.t, 3, "person")

    def test_people_limit_reuses_the_person_who_left(self):
        sim = Sim()
        sim.pt.max_people = 1
        self.enter_person(sim)
        self.leave_by_open_side(sim)
        self.assertFalse(sim.snap["full"])
        sim.run(2, lambda i, n: [track(1, 1.0, -1.6, 1.0, 0), track(7, 0.0, 0.5 * i / n)])
        self.assertEqual(sim.pt.label(sim.t, 7, "person"), "P1")

    def test_leftover_track_without_points_is_hidden(self):
        # 실제 오류: 아무것도 없는 자리의 17번이 전처리에 계속 남았다
        sim = Sim()
        sim.run(3, walk(17, (1.0, -0.5), (1.7, -0.2)))
        sim.run(6, lambda i, n: [track(17, 1.7, -0.2, 1.5, 0)])
        self.assertEqual(sim.snap["hidden"].get(17), "반사점 없는 남은 번호")
        # 약한 점 하나로는 되살아나지 않는다
        sim.run(6, lambda i, n: [track(17, 1.7, -0.2, 1.5, 1 if i % 30 == 0 else 0)])
        self.assertEqual(sim.snap["hidden"].get(17), "반사점 없는 남은 번호")
        sim.run(1.5, lambda i, n: [track(17, 1.7, -0.2, 1.5, 10)])
        self.assertNotIn(17, sim.snap["hidden"])

    def test_labeled_person_without_points_mid_room_stays_inside(self):
        sim = Sim()
        self.enter_person(sim)
        sim.run(30, lambda i, n: [track(1, 1.0, 0.0, 0.7, 0)])
        self.assertEqual(sim.state("P1")["state"], "in")

    def test_low_unlabeled_track_without_points_is_not_hidden(self):
        sim = Sim()
        sim.run(3, walk(3, (1.0, 0.5), (1.0, 0.0)))
        sim.run(10, lambda i, n: [track(3, 1.0, 0.0, 0.1, 0)])
        sim.pt.tracks[3]["fresh_z"] = 0.1
        snap = sim.pt.snapshot(sim.t, ROOM, DOOR, BED)
        self.assertNotIn(3, snap["hidden"])

    def test_person_on_bed_near_open_side_does_not_leave(self):
        sim = Sim()
        sim.run(2, walk(1, (-0.8, 0.0), (-0.8, -1.0)))
        sim.pt.label(sim.t, 1, "person")
        sim.run(10, lambda i, n: [track(1, -0.8, -1.0, 0.7, 0)])
        self.assertEqual(sim.state("P1")["state"], "in")


if __name__ == "__main__":
    unittest.main()
