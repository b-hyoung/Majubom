"""fall_report.py — 낙상 사건 리포트의 원인 후보 규칙과 LLM 실패 시 대체 리포트."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402


def facts(**over):
    base = {"데이터_출처": "live", "발생_시각": "2026-10-13T23:10:00", "야간": True,
            "사건": {"양상": "급격한 하강", "세로_속도_최저_m_s": -1.7, "바닥에_머문_초": 3.2},
            "직전_30분_사건": [{"시각": "23:07:30", "ts": "2026-10-13T23:07:30", "종류": "bed_alert", "제목": "침상 이탈", "수준": "warning", "메모": None},
                          {"시각": "22:45:00", "ts": "2026-10-13T22:45:00", "종류": "bed_alert", "제목": "침상 이탈", "수준": "warning", "메모": None}],
            "Morse_항목": {"gait": "weak", "mental": "oriented"}, "투약_주의": ["진정·수면: Zolpidem 5mg"],
            "위험_요인": ["Zolpidem 복용", "고관절 수술력"], "최근_보행_속도": []}
    base.update(over)
    return base


class FallReportRules(unittest.TestCase):
    def setUp(self):
        import fall_report
        self.fr = fall_report

    def test_causes_come_only_from_facts(self):
        names = [c["원인"] for c in self.fr.candidate_causes(facts())]
        self.assertTrue(any("미끄러짐" in n for n in names))
        self.assertTrue(any("침상 이탈" in n for n in names))
        self.assertTrue(any("야간" in n for n in names))
        self.assertTrue(any("약물" in n for n in names))
        self.assertTrue(any("보행 약화" in n for n in names))
        self.assertFalse(any("인지 저하" in n for n in names))          # Morse 정신 상태가 정상이면 만들지 않는다
        self.assertFalse(any("Zolpidem 복용" in n for n in names))       # 이미 약물로 나온 요인은 다시 쓰지 않는다

    def test_bed_exit_counts_only_within_five_minutes(self):
        f = facts(직전_30분_사건=[{"시각": "22:45:00", "ts": "2026-10-13T22:45:00", "종류": "bed_alert", "제목": "침상 이탈",
                              "수준": "warning", "메모": None}])
        self.assertFalse(any("침상 이탈" in c["원인"] for c in self.fr.candidate_causes(f)))

    def test_report_is_saved_even_when_llm_fails(self):
        tmp = tempfile.mkdtemp()
        with patch.object(db, "DB_PATH", os.path.join(tmp, "t.db")):
            db.init_db()
            import dashboard_api
            dashboard_api.init_dashboard_schema()
            dashboard_api.ensure_demo_patients()
            import ai_report
            with patch.object(ai_report, "call_openai", return_value={"success": False, "error_type": "missing_key", "message": ""}):
                res = self.fr.generate("bed_sim_fall", {"at": 1791900000.0, "kind": "slow", "low_sec": 3.1, "z": 0.1, "ref": 1.3}, "live")
            self.assertTrue(res["success"])
            self.assertEqual(res["model"], "rule-fallback")
            row = db.get_analysis_by_id(res["analysis_id"])
            self.assertEqual(row["analysis_type"], "fall_incident")
            self.assertIn("낙상 사건", row["report_json"])
            self.assertIn("charts", row["report_json"])


if __name__ == "__main__":
    unittest.main()
