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
