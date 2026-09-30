"""Patient records reach reports without cross-patient leakage or paid requests."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

import ai_agent
import dashboard_api
import db
import gait_prior
import integrated_analysis as reports


class PatientReportContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(db, "DB_PATH", os.path.join(self.temp.name, "test.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        dashboard_api.init_dashboard_schema()
        dashboard_api.ensure_demo_patients()
        self.bed = "bed_sim_calm"
        with db.get_conn() as conn:
            gait_prior.add_emr_records(conn, self.bed, [{"source": "검증용 기록", "text": "대상 환자의 기록", "record_date": "2026-09-29"}])
            gait_prior.add_emr_records(conn, "bed_sim_risk", [{"text": "다른 환자의 기록"}])
            conn.execute("CREATE TABLE nurse_reports (id INTEGER PRIMARY KEY, bed_id TEXT, report_date TEXT, body TEXT)")
            conn.execute("INSERT INTO nurse_reports VALUES (1, ?, '2026-09-29', '대상 환자의 간호 기록')", (self.bed,))
            conn.execute("INSERT INTO nurse_reports VALUES (2, 'bed_sim_risk', '2026-09-29', '다른 환자의 간호 기록')")

    def state(self):
        with patch.object(ai_agent.requests, "get", side_effect=AssertionError("No live calls for demo patients")):
            return ai_agent.gather_patient_state(self.bed)

    def investigation(self):
        return {"success": True, "patient_state": self.state(), "final_results": [], "sufficient": False}

    def test_schema_creation_is_idempotent_and_preserves_profile(self):
        before = db.get_patient(self.bed)
        dashboard_api.init_dashboard_schema()
        dashboard_api.ensure_demo_patients()
        self.assertEqual(db.get_patient(self.bed), before)
        self.assertEqual(len(db.list_medications(self.bed)), 2)
        self.assertEqual(len(db.list_risk_factors(self.bed)), 3)
        self.assertEqual(len(db.list_emr_records(self.bed)), 1)
        with db.get_conn() as conn:
            self.assertIn("input_snapshot_json", [r["name"] for r in conn.execute("PRAGMA table_info(ai_analysis)")])

    def test_exact_patient_records_reach_report_and_query_state(self):
        state = self.state()
        self.assertEqual(state["patient_profile"]["bed_id"], self.bed)
        self.assertEqual(state["data_source"], "simulation")
        self.assertEqual([r["text"] for r in state["emr_records"]], ["대상 환자의 기록"])
        self.assertEqual([r["body"] for r in state["nursing_records"]], ["대상 환자의 간호 기록"])
        payload = reports._build_payload(self.bed, {"patient_state": state}, {}, None, None)
        self.assertEqual(payload["입원_기록_원문"], state["emr_records"])
        self.assertEqual(payload["간호_기록_원문"], state["nursing_records"])
        self.assertEqual(payload["데이터_출처"], "simulation")
        self.assertNotIn("다른 환자의", json.dumps(payload, ensure_ascii=False))

    def test_absent_optional_records_remain_empty(self):
        self.assertEqual(db.list_emr_records("bed_01"), [])
        with db.get_conn() as conn:
            conn.execute("DROP TABLE nurse_reports")
        self.assertEqual(db.list_nursing_records(self.bed), [])

    def test_previous_report_does_not_recursively_copy_snapshots(self):
        previous = {"id": 8, "summary": "이전 분석", "input_snapshot_json": '{"old":"input"}'}
        payload = reports._build_payload(self.bed, self.investigation(), {}, None, previous)
        self.assertEqual(payload["직전_분석_기록"], {"id": 8})
        self.assertIn("input_snapshot_json", previous)

    def test_report_saves_the_exact_payload_sent_to_model(self):
        valid = {k: "검증용 설명" for k in reports.REPORT_FIELDS}
        investigation = self.investigation()
        with patch.object(ai_agent, "investigate", return_value=investigation), \
             patch.object(reports.ai_report, "call_openai", return_value={"success": True, "text": json.dumps(valid), "model": "test-model"}) as call:
            result = reports.generate_integrated_report(self.bed)
        self.assertTrue(result["success"])
        row = db.get_analysis_by_id(result["analysis_id"])
        snapshot = json.loads(row["input_snapshot_json"])
        sent = json.loads(call.call_args.kwargs["user_content"].split("\n\n", 1)[1])
        self.assertEqual(snapshot, sent)
        self.assertEqual(snapshot["입원_기록_원문"][0]["text"], "대상 환자의 기록")
        self.assertEqual(row["prompt_version"], reports.PROMPT_VERSION)

    def test_invalid_model_output_is_not_stored_as_a_report(self):
        investigation = self.investigation()
        for malformed in ([], {"현재_상태": "항목 누락"}, {k: 123 for k in reports.REPORT_FIELDS}):
            with self.subTest(malformed=type(malformed).__name__), \
                 patch.object(ai_agent, "investigate", return_value=investigation), \
                 patch.object(reports.ai_report, "call_openai", return_value={"success": True, "text": json.dumps(malformed)}):
                result = reports.generate_integrated_report(self.bed)
            self.assertFalse(result["success"])
            self.assertEqual(result["error_type"], "invalid_report_schema")
        self.assertEqual(db.get_analysis_count(self.bed), 0)

    def test_snapshot_roundtrip_with_explicit_timestamp_and_safe_list_metadata(self):
        aid = db.insert_ai_analysis(self.bed, "integrated_status", None, "예시", [], [], [],
                                    "demo-history-seed", "seed", None, {"현재_상태": "예시"},
                                    created_at="2026-09-01 00:00:00", input_snapshot={"private": "input"})
        row = db.get_analysis_by_id(aid)
        self.assertEqual(json.loads(row["input_snapshot_json"]), {"private": "input"})
        decoded = dashboard_api._decode_rows([row])[0]
        self.assertEqual(decoded["report_source"], "demo_seed")
        self.assertTrue(decoded["input_snapshot_available"])
        self.assertNotIn("input_snapshot", decoded)
        self.assertNotIn("input_snapshot_json", decoded)
        row.update(model_version="gpt-4o-mini", input_snapshot_json=None)
        legacy = dashboard_api._decode_rows([row])[0]
        self.assertEqual(legacy["report_source"], "simulation_ai")
        self.assertFalse(legacy["input_snapshot_available"])

    def test_patient_api_returns_only_that_patients_records(self):
        app = Flask(__name__)
        app.register_blueprint(dashboard_api.create_blueprint(lambda bed: {}, lambda: {}))
        with app.test_client() as client:
            data = client.get('/api/dashboard/patients/' + self.bed).get_json()
        self.assertEqual([r["text"] for r in data["emr_records"]], ["대상 환자의 기록"])
        self.assertEqual([r["body"] for r in data["nursing_records"]], ["대상 환자의 간호 기록"])


if __name__ == "__main__":
    unittest.main()
