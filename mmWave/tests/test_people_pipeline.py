"""사람 ID, 표시 역할, 낙상 경보 경로 시험: python -m unittest mmWave/tests/test_people_pipeline.py"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from people_pipeline import FallWatch, RoomPipeline, polygon_distance  # noqa: E402
from send_mmw import BED_POLYGON, DOOR_POLYGON, ROOM_POLYGON, frame_is_valid, link_points  # noqa: E402


def target(tid, x, y, z=1.5, npts=20):
    return {"tid": tid, "posX": x, "posY": y, "posZ": z, "velX": 0.0, "velY": 0.0, "velZ": 0.0,
            "npts": npts, "linked_npts": npts, "top_z": z}


class Sim:
    def __init__(self, **kw):
        self.t = 1000.0
        self.pipe = RoomPipeline(ROOM_POLYGON, BED_POLYGON, DOOR_POLYGON, **kw)
        self.events = []
        self.last = None

    def run(self, seconds, make):
        n = int(seconds * 18)
        for i in range(n):
            self.t += 1 / 18
            self.last = self.pipe.update(self.t, make(i, n))
            self.events.extend(self.last["events"])
        return self.last

    def role(self, tid):
        return next((s["role"] for s in self.last["targets"] if s["tid"] == tid), None)


def walk(tid, start, end, z=1.5, npts=20):
    return lambda i, n: [target(tid, start[0] + (end[0] - start[0]) * i / n, start[1] + (end[1] - start[1]) * i / n, z, npts)]


def fall_at(sim, x, y, tid=1):
    """서 있던 사람이 그 자리에서 빠르게 바닥으로 내려간다."""
    sim.run(5, lambda i, n: [target(tid, x, y, 1.6)])
    sim.run(0.4, lambda i, n: [target(tid, x, y, 1.6 - 1.5 * i / n)])
    sim.run(6, lambda i, n: [target(tid, x, y, 0.1, 3)])


class FallRouteTests(unittest.TestCase):
    def designated(self, x, y):
        sim = Sim()
        sim.run(2, walk(1, (x + 0.5, y), (x, y)))
        self.assertEqual(sim.pipe.designate(sim.t, 1), "P1")
        return sim

    def test_fall_in_the_room_is_an_alarm(self):
        sim = self.designated(1.5, 0.0)
        fall_at(sim, 1.5, 0.0)
        self.assertEqual([e["stage"] for e in sim.events], ["fallen"])
        self.assertEqual(sim.last["falls"][0]["pid"], "P1")

    def test_fall_on_the_bed_is_not_sent(self):
        sim = self.designated(-0.8, -0.5)
        fall_at(sim, -0.8, -0.5)
        self.assertEqual(sim.events, [])
        self.assertEqual(sim.last["falls"], [])

    def test_fall_just_outside_the_door_zone_needs_checking(self):
        # S6 녹화의 오탐 위치: 문 구역 동쪽 경계에서 몇 cm 밖
        sim = self.designated(-0.36, 1.94)
        fall_at(sim, -0.36, 1.94)
        self.assertEqual([(e["stage"], e["door_check"], e["detected_stage"]) for e in sim.events], [("caution", True, "fallen")])
        self.assertEqual(sim.last["falls"], [])

    def test_fall_soon_after_the_door_zone_needs_checking(self):
        sim = self.designated(0.6, 1.2)
        sim.run(1, walk(1, (0.6, 1.2), (-0.8, 1.9)))
        sim.run(0.5, walk(1, (-0.8, 1.9), (0.6, 1.2)))
        watch = sim.pipe.falls.watches["P1"]
        self.assertIsNotNone(watch["door_t"])
        routed = sim.pipe.falls._route(watch["door_t"] + 4.9, watch, {"stage": "fallen", "x": 0.6, "y": 1.2})
        self.assertTrue(routed["door_check"])
        routed = sim.pipe.falls._route(watch["door_t"] + 5.1, watch, {"stage": "fallen", "x": 0.6, "y": 1.2})
        self.assertNotIn("door_check", routed)

    def test_recovered_is_sent_only_after_an_alarm(self):
        watch = {"alarmed": False, "door_t": None}
        fw = FallWatch(BED_POLYGON, DOOR_POLYGON)
        self.assertIsNone(fw._route(0.0, watch, {"stage": "recovered", "x": 1.5, "y": 0.0}))
        fw._route(0.0, watch, {"stage": "fallen", "x": 1.5, "y": 0.0})
        self.assertTrue(watch["alarmed"])
        self.assertEqual(fw._route(1.0, watch, {"stage": "recovered", "x": 1.5, "y": 0.0})["stage"], "recovered")
        self.assertFalse(watch["alarmed"])


class RoleTests(unittest.TestCase):
    def test_unmoved_track_is_clickable_until_a_patient_is_chosen(self):
        sim = Sim()
        make = lambda i, n: [target(1, 1.5, 0.0), target(2, 0.5, -1.0)]
        sim.run(3, make)
        self.assertEqual((sim.role(1), sim.role(2)), ("other", "other"))
        self.assertEqual(sim.pipe.designate(sim.t, 1), "P1")
        sim.run(1, make)
        self.assertEqual((sim.role(1), sim.role(2)), ("patient", "still"))
        self.assertEqual(sim.last["patient_tid"], 1)
        self.assertIs(sim.last["patient_target"]["tid"], 1)
        self.assertEqual(sim.last["n_people"], 1)

    def test_track_outside_the_room_is_not_shown(self):
        sim = Sim()
        sim.run(3, lambda i, n: [target(1, -2.3, 0.4)])
        self.assertIsNone(sim.role(1))
        sim = Sim(room_filter=False)
        sim.run(3, lambda i, n: [target(1, -2.3, 0.4)])
        self.assertEqual(sim.role(1), "other")

    def test_last_click_chooses_the_patient(self):
        sim = Sim()
        make = lambda i, n: [target(1, 1.5, 0.0), target(2, 0.5, -1.0)]
        sim.run(3, make)
        sim.pipe.designate(sim.t, 1)
        sim.pipe.designate(sim.t, 2)
        sim.run(1, make)
        self.assertEqual((sim.role(1), sim.role(2)), ("other", "patient"))
        self.assertEqual(sim.last["patient"]["pid"], "P2")

    def test_max_people_hides_the_rest_and_refuses_more(self):
        sim = Sim(max_people=1)
        make = lambda i, n: [target(1, 1.5, 0.0), target(2, 0.5, -1.0)]
        sim.run(3, make)
        sim.pipe.designate(sim.t, 1)
        sim.run(1, make)
        self.assertTrue(sim.last["full"])
        self.assertEqual(sim.role(2), "dup")
        with self.assertRaises(ValueError):
            sim.pipe.designate(sim.t, 2)

    def test_unknown_track_click_is_ignored(self):
        sim = Sim()
        sim.run(1, lambda i, n: [target(1, 1.5, 0.0)])
        self.assertIsNone(sim.pipe.designate(sim.t, 99))
        self.assertIsNone(sim.pipe.patient_pid)


class AutoPatientTests(unittest.TestCase):
    """클릭이 없어도 방 안에 움직이는 사람이 혼자면 자동으로 사람 ID를 붙이고 환자로 정한다(2026-11-05)."""

    def test_lone_walker_becomes_patient_and_falls_are_watched(self):
        sim = Sim()
        sim.run(3, walk(1, (2.5, 0.0), (1.5, 0.0)))
        self.assertEqual(sim.last["patient_tid"], 1)
        self.assertEqual((sim.last["patient"]["pid"], sim.last["patient"]["locked"]), ("P1", "auto"))
        fall_at(sim, 1.5, 0.0)
        self.assertEqual([e["stage"] for e in sim.events], ["fallen"])

    def test_two_walkers_wait_for_a_click(self):
        sim = Sim()
        sim.run(3, lambda i, n: walk(1, (2.5, 0.0), (1.5, 0.0))(i, n) + walk(2, (0.0, -1.0), (1.0, -1.0))(i, n))
        self.assertIsNone(sim.last["patient_tid"])
        self.assertEqual(sim.last["persons"], [])

    def test_unmoved_track_is_not_chosen(self):
        sim = Sim()
        sim.run(5, lambda i, n: [target(1, 1.5, 0.0)])      # 빈 방의 멈춘 유령 번호 같은 경우
        self.assertIsNone(sim.last["patient_tid"])

    def test_second_walker_is_not_auto_chosen_and_click_wins(self):
        sim = Sim()
        sim.run(3, walk(1, (2.5, 0.0), (1.5, 0.0)))
        both = lambda i, n: [target(1, 1.5, 0.0)] + walk(2, (0.0, -1.0), (1.0, -1.0))(i, n)
        sim.run(3, both)
        self.assertEqual(sim.last["patient_tid"], 1)          # 두 번째 사람은 자동으로 바꾸지 않는다
        self.assertEqual(sim.pipe.designate(sim.t, 2), "P2")
        sim.run(1, lambda i, n: [target(1, 1.5, 0.0), target(2, 1.0, -1.0)])
        self.assertEqual((sim.last["patient_tid"], sim.last["patient"]["locked"]), (2, "manual"))

    def test_ghost_that_stopped_long_ago_is_not_auto_chosen(self):
        """2026-11-07 Pi 걷기 확인: 사람을 따라 움직이다 x- 벽에 멈춘 유령 번호(13번)가 반사점을 계속 내서,
        사람 번호가 끊긴 뒤 환자로 자동 지정됐다. 멈춘 지 오래된 번호는 후보가 아니어야 한다."""
        sim = Sim()
        ghost_stop = (-1.2, 1.0)
        sim.run(3, lambda i, n: walk(1, (2.0, -1.0), (1.5, 0.5))(i, n) + walk(2, (-0.2, 1.2), ghost_stop)(i, n))
        self.assertEqual(sim.pipe.designate(sim.t, 1), "P1")
        sim.run(8, lambda i, n: walk(1, (1.5, 0.5), (1.5, -0.5))(i, n) + [target(2, *ghost_stop)])   # 유령은 멈추고 사람은 걷는다
        sim.run(4, lambda i, n: [target(2, *ghost_stop)])                                           # 사람 번호가 사라진다
        self.assertIsNone(sim.last["patient_tid"], "멈춘 유령 번호가 환자로 자동 지정됨")
        sim.run(3, lambda i, n: walk(3, (1.0, -1.0), (2.0, -1.0))(i, n) + [target(2, *ghost_stop)])  # 사람이 새 번호로 다시 걷는다
        self.assertEqual((sim.last["patient_tid"], sim.last["patient"]["pid"]), (3, "P1"))


class PersonLogTests(unittest.TestCase):
    """리더가 사람 ID 사건을 로그에 남겨야 환자 번호가 왜 바뀌었는지 확인할 수 있다(2026-11-07)."""

    def test_auto_designation_and_loss_are_logged_with_reason(self):
        logged = []
        sim = Sim(emit=lambda now, event, pid, pos, text, guess: logged.append((event, text)))
        sim.run(3, walk(1, (2.5, 0.0), (1.5, 0.0)))
        self.assertEqual(sim.last["patient_tid"], 1)
        self.assertTrue(any(e == "person_link" and "자동 지정" in t for e, t in logged), logged)
        sim.run(4, lambda i, n: [])                                   # 번호가 방 안에서 사라진다
        self.assertTrue(any(e == "person_lost" and "1번" in t for e, t in logged), logged)

    def test_click_is_logged_as_user_mark(self):
        logged = []
        sim = Sim(emit=lambda now, event, pid, pos, text, guess: logged.append((event, text)))
        make = lambda i, n: [target(1, 1.5, 0.0), target(2, 0.5, -1.0)]
        sim.run(3, make)
        sim.pipe.designate(sim.t, 2)
        self.assertTrue(any(e == "person_link" and "사용자 표시" in t for e, t in logged), logged)


class HelperTests(unittest.TestCase):
    def test_frame_validation(self):
        tlv = struct.pack("<2I", 1021, 4) + b"\0" * 4
        frame = b"\0" * 40 + tlv
        self.assertTrue(frame_is_valid({"numTLVs": 1}, frame))
        self.assertFalse(frame_is_valid({"numTLVs": 2}, frame), "TLV 수가 모자람")
        self.assertFalse(frame_is_valid({"numTLVs": 1}, frame[:-1]), "길이가 프레임을 넘음")
        self.assertFalse(frame_is_valid({"numTLVs": 1}, b"\0" * 40 + struct.pack("<2I", 9999, 4) + b"\0" * 4), "모르는 타입")

    def test_polygon_distance(self):
        self.assertEqual(polygon_distance(-1.0, 2.0, DOOR_POLYGON), 0.0)
        self.assertAlmostEqual(polygon_distance(-0.36, 2.0, DOOR_POLYGON), 0.07)

    def test_link_points_uses_previous_frame(self):
        t = [target(5, 0.0, 0.0), target(6, 0.0, 0.0)]
        link_points(t, struct.pack("3B", 5, 5, 255), [1.0, 1.4, 0.2])
        self.assertEqual((t[0]["npts"], t[0]["linked_npts"], t[0]["top_z"]), (2, 2, 1.4))
        self.assertEqual((t[1]["npts"], t[1]["linked_npts"], t[1]["top_z"]), (0, 0, None))

    def test_link_points_with_different_length_keeps_count_only(self):
        t = [target(5, 0.0, 0.0)]
        link_points(t, struct.pack("2B", 5, 5), [1.0])
        self.assertEqual((t[0]["npts"], t[0]["linked_npts"], t[0]["top_z"]), (2, 0, None))


if __name__ == "__main__":
    unittest.main()


class BedPreferenceAndRestoreTests(unittest.TestCase):
    """2026-11-14: 리더 재시작 뒤 침대 밖 번호가 자동 지정됨. 침대 안 사람을 먼저 고르고, 재시작 전 환자를 이어받는다."""
    BED_XY = (-0.8, -0.3)        # BED_POLYGON 안

    def test_lying_person_in_bed_wins_over_recent_walker_outside(self):
        sim = Sim()
        lying = lambda i, n: [target(9, *self.BED_XY, z=0.9, npts=30)]
        sim.run(3, lambda i, n: lying(i, n) + walk(1, (2.0, 1.0), (1.0, 1.0))(i, n))
        self.assertEqual(sim.last["patient_tid"], 9)

    def test_most_points_in_bed_is_chosen_when_split(self):
        sim = Sim()
        sim.run(2, lambda i, n: [target(3, -0.9, -0.6, z=0.9, npts=6), target(9, *self.BED_XY, z=0.9, npts=40)])
        self.assertEqual(sim.last["patient_tid"], 9)

    def test_restore_keeps_previous_patient_and_blocks_auto(self):
        sim = Sim()
        sim.pipe.restore(sim.t, 22, auto=False)
        sim.run(2, lambda i, n: [target(9, *self.BED_XY, z=0.9, npts=40), target(22, 1.0, 1.0, npts=10)])
        self.assertEqual((sim.last["patient_tid"], sim.last["patient"]["locked"]), (22, "manual"))

    def test_restore_gives_up_when_number_is_gone(self):
        sim = Sim()
        sim.pipe.restore(sim.t, 77, auto=False)
        sim.run(12, lambda i, n: [target(9, *self.BED_XY, z=0.9, npts=40)])
        self.assertEqual((sim.last["patient_tid"], sim.last["patient"]["locked"]), (9, "auto"))


class PatientStateFileTests(unittest.TestCase):
    def test_saved_state_round_trip_and_age_limit(self):
        import tempfile
        from send_mmw import load_patient_state, save_patient_state
        path = os.path.join(tempfile.mkdtemp(), "p.json")
        save_patient_state(1000.0, {"tid": 22, "locked": "manual"}, path)
        self.assertEqual(load_patient_state(1010.0, path), (22, False))
        self.assertIsNone(load_patient_state(1100.0, path))            # 60초 넘게 지난 기록
        save_patient_state(1000.0, {"tid": None, "locked": None}, path)
        self.assertIsNone(load_patient_state(1001.0, path))            # 환자 없음


class HybridPatientTests(unittest.TestCase):
    """2026-11-14 합친 방식: 침대 소속도로 자동 지정·애매하면 보류·잘못 잡힌 자동 지정 바로잡기, 클릭은 그대로 우선."""
    BED = (-0.8, -0.3)
    OUT = (1.5, 1.0)

    def lie(self, tid=9, xy=None, npts=30):
        xy = xy or self.BED
        return [target(tid, *xy, z=0.9, npts=npts)]

    def test_two_people_far_apart_on_bed_are_held_not_guessed(self):
        sim = Sim()
        sim.run(3, lambda i, n: self.lie(9, (-1.3, -1.2)) + self.lie(11, (-0.3, 0.3)))
        self.assertIsNone(sim.last["patient_tid"])
        self.assertIn("침대 위 후보 2명", sim.last["patient"]["hold"])

    def test_wrong_auto_patient_outside_is_corrected_to_person_staying_in_bed(self):
        sim = Sim()
        sim.run(3, walk(1, (2.0, 1.5), self.OUT))                          # 침대가 빈 동안 걷던 번호가 자동 지정
        self.assertEqual((sim.last["patient_tid"], sim.last["patient"]["basis"]), (1, "auto_move"))
        sim.run(35, lambda i, n: [target(1, *self.OUT)] + self.lie())       # 침대에 사람이 35초 머문다
        self.assertEqual((sim.last["patient_tid"], sim.last["patient"]["basis"]), (9, "corrected"))

    def test_manual_patient_is_never_switched_only_flagged(self):
        sim = Sim()
        sim.run(2, lambda i, n: [target(1, *self.OUT)])
        sim.pipe.designate(sim.t, 1)
        sim.run(35, lambda i, n: [target(1, *self.OUT)] + self.lie())
        self.assertEqual((sim.last["patient_tid"], sim.last["patient"]["locked"]), (1, "manual"))
        self.assertIn("확인 필요", sim.last["patient"]["suspect"])

    def test_patient_walking_away_from_empty_bed_is_kept(self):
        sim = Sim()
        sim.run(5, lambda i, n: self.lie(1))
        self.assertEqual((sim.last["patient_tid"], sim.last["patient"]["basis"]), (1, "auto_bed"))
        sim.run(70, walk(1, self.BED, self.OUT))                            # 침대를 떠나 70초 동안 걷는다(침대는 빔)
        self.assertEqual(sim.last["patient_tid"], 1)

    def test_visitor_sitting_on_bed_briefly_does_not_take_over(self):
        sim = Sim()
        sim.run(5, lambda i, n: self.lie(1))
        sim.run(40, walk(1, self.BED, self.OUT))                            # 환자가 걸어 나가 침대 소속도가 떨어진 뒤
        sim.run(15, lambda i, n: [target(1, *self.OUT)] + self.lie(5))      # 보호자가 15초 앉는다(30초 미만)
        self.assertEqual(sim.last["patient_tid"], 1)

    def test_split_body_on_bed_counts_as_one_person(self):
        sim = Sim()
        sim.run(3, lambda i, n: self.lie(22, (-0.63, -0.50), 44) + self.lie(3, (-0.33, 0.20), 6))   # 11-14 실측 배치
        self.assertEqual(sim.last["patient_tid"], 22)
        self.assertIsNone(sim.last["patient"]["hold"])
