"""fall_risk.py: Morse 점수·위험군, 변동 점수(쿨다운·감점), 거동 범위별 침상 알림."""
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import fall_risk as F


class FallRiskTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        F.init_schema(self.conn)

    def test_morse_score_and_groups(self):
        items = {"fall_history": "yes", "secondary_dx": "yes", "ambulatory_aid": "cane_walker",
                 "iv": "no", "gait": "impaired", "mental": "forgets_limitations"}
        self.assertEqual(F.morse_score(items), 25 + 15 + 15 + 20 + 15)
        self.assertEqual(F.group_of(0), "low")
        self.assertEqual(F.group_of(24), "low")
        self.assertEqual(F.group_of(25), "moderate")
        self.assertEqual(F.group_of(45), "high")

    def test_live_default_is_low_risk_free_mobility(self):
        F.ensure_profile(self.conn, "bed_01", mobility="free", source="live_default_low")
        p = F.get_profile(self.conn, "bed_01")
        self.assertEqual((p["score"], p["group"], p["mobility"]), (0, "low", "free"))

    def test_low_risk_free_mobility_exit_is_normal(self):
        F.ensure_profile(self.conn, "b")
        p = F.get_profile(self.conn, "b")
        day = datetime(2026, 10, 12, 14, 0)
        self.assertEqual(F.bed_alert(p, "exit", day)["level"], "normal")
        self.assertEqual(F.bed_alert(p, "not_returned", day)["level"], "caution")

    def test_fall_report_worsened_makes_edge_sit_a_warning(self):
        F.ensure_profile(self.conn, "b")
        p = F.record_fall(self.conn, "b", note="화장실 앞 낙상", worsened=True)
        self.assertEqual(p["mobility"], "bed_rest")
        self.assertEqual(p["morse"]["fall_history"]["value"], "yes")
        self.assertEqual(p["score"], 25 + 15)          # Morse 낙상 이력 25 + 변동 15
        day = datetime(2026, 10, 12, 14, 0)
        self.assertEqual(F.bed_alert(p, "edge_sit", day)["level"], "warning")
        night = datetime(2026, 10, 12, 23, 0)
        self.assertEqual(F.bed_alert(p, "edge_sit", night)["level"], "critical")

    def test_signal_cooldown_and_daily_decay(self):
        F.ensure_profile(self.conn, "b")
        t0 = datetime.now()
        self.assertTrue(F.add_signal(self.conn, "b", "gait_warning", now=t0))
        self.assertFalse(F.add_signal(self.conn, "b", "gait_warning", now=t0 + timedelta(minutes=30)))
        self.assertTrue(F.add_signal(self.conn, "b", "gait_warning", now=t0 + timedelta(minutes=61)))
        self.assertEqual(F.get_profile(self.conn, "b")["dynamic_points"], 6)
        with patch.object(F, "_now", return_value=t0 + timedelta(minutes=61, days=2, seconds=5)):
            self.assertEqual(F.get_profile(self.conn, "b")["dynamic_points"], 2)

    def test_reset_dynamic_keeps_morse_and_mobility(self):
        F.ensure_profile(self.conn, "b")
        F.add_signal(self.conn, "b", "floor_critical")
        before = F.get_profile(self.conn, "b")
        self.assertGreater(before["dynamic_points"], 0)
        after = F.reset_dynamic(self.conn, "b", by="test")
        self.assertEqual(after["dynamic_points"], 0)
        self.assertEqual((after["morse_score"], after["mobility"]), (before["morse_score"], before["mobility"]))
        self.assertEqual(after["recent_events"][0]["kind"], "reset")

    def test_mmw_floor_critical_and_provisional_ignored(self):
        F.ensure_profile(self.conn, "b")
        self.assertEqual(F.on_mmw_result(self.conn, "b", {"alert_level": "critical",
                         "reasons": ["바닥 근처 몸 높이(z=0.2m < 0.4m) → 즉시 위험"]}), "floor_critical")
        self.assertIsNone(F.on_mmw_result(self.conn, "b", {"alert_level": "warning", "alarm": False,
                          "provisional": True, "reasons": []}))
        self.assertEqual(F.get_profile(self.conn, "b")["dynamic_points"], 10)

    def test_situation_from_tof(self):
        now = datetime(2026, 10, 12, 14, 0)
        self.assertEqual(F.situation_from_tof("edge_sit", True, None, now), "edge_sit")
        self.assertEqual(F.situation_from_tof("empty", False, (now - timedelta(minutes=3)).isoformat(), now), "exit")
        self.assertEqual(F.situation_from_tof("empty", False, (now - timedelta(minutes=30)).isoformat(), now), "not_returned")
        self.assertIsNone(F.situation_from_tof("empty", False, (now - timedelta(hours=5)).isoformat(), now))

    def test_bad_assessment_values_are_rejected(self):
        F.ensure_profile(self.conn, "b")
        with self.assertRaises(ValueError):
            F.set_assessment(self.conn, "b", {"gait": "flying"}, None)
        with self.assertRaises(ValueError):
            F.set_assessment(self.conn, "b", None, "running")


if __name__ == "__main__":
    unittest.main()
