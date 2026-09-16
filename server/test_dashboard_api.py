"""No hardware, production database, API credentials, or paid requests are used."""
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from flask import Flask

import ai_agent
import dashboard_api
import db
import integrated_analysis


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(db, "DB_PATH", os.path.join(self.temp.name, "test.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        dashboard_api.init_dashboard_schema()
        self.runner = Mock(return_value={"success": True, "analysis_id": 7, "report": {"현재_상태": "자료 부족"}})
        self.capability = {"configured": True, "available": True, "message": "준비", "error_type": None}
        self.app = Flask(__name__)
        self.app.register_blueprint(dashboard_api.create_blueprint(self.runner, lambda: self.capability))
        self.client = self.app.test_client()

    def add_other_patient(self):
        with db.get_conn() as conn:
            conn.execute("INSERT INTO patients (bed_id, name, mmw_target_id) VALUES (?, ?, ?)",
                         ("bed_00", "기존 환자", "other_room"))

    def wait_job(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.client.get("/api/dashboard/analyses/" + job_id).get_json()
            if result["status"] in ("completed", "failed"):
                return result
            time.sleep(0.01)
        self.fail("test worker did not finish")

    def test_initial_patient_is_first_without_invented_medical_data(self):
        self.add_other_patient()
        dashboard_api.init_dashboard_schema()
        rows = self.client.get("/api/dashboard/patients").get_json()["patients"]
        self.assertEqual([row["bed_id"] for row in rows], ["bed_01", "bed_00"])
        self.assertEqual(rows[0]["mmw_target_id"], "room_01")
        self.assertIsNone(rows[0]["diagnosis"])
        self.assertIsNone(rows[0]["age"])
        self.assertEqual(rows[1]["name"], "기존 환자")
        self.assertEqual(self.client.get("/api/dashboard/patients/bed_01").get_json()["medications"], [])

    def test_nurse_can_add_edit_and_remove_a_medication(self):
        created = self.client.post("/api/dashboard/patients/bed_01/medications",
                                   json={"name": "Donepezil 5mg", "dose": "5mg", "frequency": "1일 1회 (저녁)"})
        self.assertEqual(created.status_code, 201)
        med = created.get_json()["medication"]
        self.assertEqual(med["name"], "Donepezil 5mg")
        self.assertEqual(self.client.get("/api/dashboard/patients/bed_01").get_json()["medications"][0]["name"],
                         "Donepezil 5mg")

        updated = self.client.put(f"/api/dashboard/patients/bed_01/medications/{med['id']}",
                                  json={"dose": "10mg", "notes": "용량 조정"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["medication"]["dose"], "10mg")

        # Editing through another bed_id is rejected — a nurse can't cross-edit another patient's record.
        self.add_other_patient()
        cross = self.client.put(f"/api/dashboard/patients/bed_00/medications/{med['id']}", json={"dose": "1mg"})
        self.assertEqual(cross.status_code, 404)

        removed = self.client.delete(f"/api/dashboard/patients/bed_01/medications/{med['id']}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard/patients/bed_01").get_json()["medications"], [])

    def test_create_patient_adds_profile_only_row_without_inventing_data(self):
        rejected = self.client.post("/api/dashboard/patients", json={"name": "  "})
        self.assertEqual(rejected.status_code, 400)
        created = self.client.post("/api/dashboard/patients", json={"name": "김이박", "room": "402"})
        self.assertEqual(created.status_code, 201)
        patient = created.get_json()["patient"]
        self.assertEqual(patient["name"], "김이박")
        self.assertEqual(patient["room"], "402")
        self.assertIsNone(patient["age"])
        self.assertIsNone(patient["diagnosis"])
        self.assertFalse(patient["is_live"])
        rows = self.client.get("/api/dashboard/patients").get_json()["patients"]
        self.assertEqual([row["bed_id"] for row in rows], ["bed_01", patient["bed_id"]])

    def test_shared_tof_history_is_exclusive_and_report_json_is_decoded(self):
        self.add_other_patient()
        db.insert_tof("tof1", datetime.now().isoformat(), "8x8", [1400] * 64, [1] * 64, 8, True)
        db.insert_mmw("room_01", datetime.now().isoformat(), {"speed": 0.4}, {}, {}, alert_level="normal")
        db.insert_ai_analysis("bed_01", "integrated_status", None, "자료 부족", [], [], ["paper_1"],
                              "test", "test", None, {"현재_상태": "자료 부족"})
        first = self.client.get("/api/dashboard/patients/bed_01").get_json()
        other = self.client.get("/api/dashboard/patients/bed_00").get_json()
        self.assertEqual(len(first["history"]["tof"]), 1)
        self.assertEqual(len(first["history"]["mmwave"]), 1)
        self.assertEqual(other["history"], {"tof": [], "mmwave": []})
        self.assertEqual(first["analyses"][0]["report"], {"현재_상태": "자료 부족"})
        self.assertEqual(first["analyses"][0]["evidence_ids"], ["paper_1"])

    def test_get_never_generates_and_invalid_patient_is_rejected(self):
        for path in ("/api/dashboard/status", "/api/dashboard/analyses", "/api/dashboard/patients"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.post("/api/dashboard/analyses", json={"bed_id": "demo_02"}).status_code, 404)
        self.assertEqual(self.client.post("/api/dashboard/analyses", json={}).status_code, 400)
        self.runner.assert_not_called()

    def test_report_is_async_and_duplicate_requests_share_job(self):
        entered, release = threading.Event(), threading.Event()
        def generate(bed_id):
            entered.set()
            release.wait(2)
            return {"success": True, "bed_id": bed_id, "analysis_id": 8}
        self.runner.side_effect = generate
        first = self.client.post("/api/dashboard/analyses", json={"bed_id": "bed_01"})
        self.assertEqual(first.status_code, 202)
        self.assertTrue(entered.wait(1))
        try:
            duplicate = self.client.post("/api/dashboard/analyses", json={"bed_id": "bed_01"}).get_json()
            self.assertEqual(first.get_json()["job_id"], duplicate["job_id"])
            self.assertEqual(duplicate["status"], "running")
        finally:
            release.set()
        job = self.wait_job(first.get_json()["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["analysis_id"], 8)
        self.runner.assert_called_once_with("bed_01")

    def test_timeout_is_reported_and_internal_exception_is_sanitized(self):
        self.runner.return_value = {"success": False, "error_type": "timeout", "message": "응답 시간 초과"}
        request = self.client.post("/api/dashboard/analyses", json={"bed_id": "bed_01"}).get_json()
        job = self.wait_job(request["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["error_type"], "timeout")
        self.runner.side_effect = RuntimeError("private-server-value")
        request = self.client.post("/api/dashboard/analyses", json={"bed_id": "bed_01"}).get_json()
        job = self.wait_job(request["job_id"])
        self.assertEqual(job["error"]["error_type"], "server_error")
        self.assertNotIn("private-server-value", json.dumps(job))

    def test_key_configuration_is_boolean_and_unavailable_ai_does_not_run(self):
        with patch("dashboard_api.importlib.util.find_spec", return_value=object()), \
             patch("dashboard_api.os.path.isdir", return_value=True), \
             patch("ai_report.get_api_key", return_value="test-secret-never-return"):
            status = dashboard_api.capabilities()
        self.assertIs(status["configured"], True)
        self.assertNotIn("test-secret", json.dumps(status))
        self.capability.update(available=False, error_type="missing_key", message="키 미설정")
        response = self.client.post("/api/dashboard/analyses", json={"bed_id": "bed_01"})
        self.assertEqual(response.status_code, 503)
        self.runner.assert_not_called()

    def test_agent_uses_current_tof_only_for_first_patient_and_ignores_stale(self):
        self.add_other_patient()
        now = datetime.now().isoformat()
        fresh = {"tof1": {"received_at": now}, "posture": "side_left",
                 "risk": {"posture": "side_left", "status": "anomaly", "at": now}}
        def get(url, **kwargs):
            if url.endswith("/tof/latest"):
                return Mock(status_code=200, json=Mock(return_value=fresh))
            if url.endswith("/tof/presence"):
                return Mock(status_code=200, json=Mock(return_value={"in_bed": True}))
            return Mock(status_code=200, json=Mock(return_value={"received_at": "2020-01-01T00:00:00", "alert_level": "critical"}))
        with patch("ai_agent.requests.get", side_effect=get) as http:
            state = ai_agent.gather_patient_state("bed_01")
            self.assertEqual(state["tof_behavior"]["posture_baseline"]["alert_level"], "warning")
            self.assertIsNone(state["mmw_latest"])
            http.reset_mock()
            other = ai_agent.gather_patient_state("bed_00")
            self.assertIsNone(other["tof_behavior"])
            self.assertEqual(http.call_count, 1)
            self.assertEqual(http.call_args.kwargs["params"], {"target": "other_room"})
            fresh["tof1"]["received_at"] = (datetime.now() - timedelta(seconds=60)).isoformat()
            stale = ai_agent.gather_patient_state("bed_01")
            self.assertIsNone(stale["tof_behavior"])

    def test_missing_and_held_measurements_never_mean_zero_risk(self):
        for state in ({}, {"mmw_latest": {"alert_level": "normal", "measuring_held": True}},
                      {"mmw_latest": {"alert_level": "critical", "quality": {"reliable": False}}}):
            result = integrated_analysis.compute_risk_score(state)
            self.assertIsNone(result["combined_risk_score"])
            self.assertIsNone(integrated_analysis.compute_data_confidence(state, result))

    def test_server_serves_new_assets_safely_and_billable_legacy_get_is_disabled(self):
        # db.DB_PATH still points to this test's temporary database during import.
        import tof_server
        client = tof_server.app.test_client()
        self.assertEqual(client.get("/dashboard").status_code, 200)
        self.assertEqual(client.get("/dashboard_design.html").status_code, 200)
        asset = client.get("/dashboard-assets/data.js")
        self.assertEqual(asset.status_code, 200)
        asset.close()
        self.assertEqual(client.get("/dashboard-assets/../../server/ai_report.py").status_code, 404)
        for route in ("/agent/analyze", "/agent/investigate", "/kb/search"):
            self.assertEqual(client.get(route).status_code, 405)

    def test_cloud_proxy_preserves_async_status_and_api_destination(self):
        import cloud_dashboard
        reply = Mock(status=202, headers={"Content-Type": "application/json"})
        reply.read.return_value = b'{"success":true,"job_id":"sample","status":"pending"}'
        context = Mock()
        context.__enter__ = Mock(return_value=reply)
        context.__exit__ = Mock(return_value=False)
        with patch("cloud_dashboard.urllib.request.urlopen", return_value=context) as request:
            response = cloud_dashboard.app.test_client().post("/api/dashboard/analyses", json={"bed_id": "bed_01"})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(request.call_args.args[0].full_url.endswith(":5001/api/dashboard/analyses"))
        self.assertEqual(response.get_json()["job_id"], "sample")


if __name__ == "__main__":
    unittest.main()
