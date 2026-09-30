import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask
import ai_agent
import dashboard_api
import db
import integrated_analysis as reports
import report_review as review


class ReportReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(db, 'DB_PATH', os.path.join(self.tmp.name, 'review.db'))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        dashboard_api.init_dashboard_schema()
        dashboard_api.ensure_demo_patients()
        self.bed = 'bed_sim_calm'
        self.state = {'tof_behavior': {'posture_baseline': {'alert_level': 'caution'}},
                      'mmw_latest': {'alert_level': 'warning', 'quality': {'reliable': True, 'walking': False}}}
        self.risk = reports.compute_risk_score(self.state)
        self.payload = {'환자_프로필': {'bed_id': self.bed}, '데이터_출처': 'simulation',
                        'ToF_현재_판정': self.state['tof_behavior'], 'mmWave_현재_판정': self.state['mmw_latest'],
                        '계산된_위험점수': self.risk, '데이터_신뢰도_비율': 0.67,
                        '논문_근거_충분성_판단': {'sufficient': True}}
        self.report = {k: '정보 부족' for k in review.REQUIRED_FIELDS}
        self.report['현재_상태'] = '시뮬레이션 기반 리포트입니다.'
        self.resolved = {'success': True, 'found': [{'chunk_id': 'test::abstract::0', 'section': 'abstract', 'title': '검증용', 'original_text': '자료'}], 'missing_chunk_ids': []}

    def insert(self, payload=True):
        aid = db.insert_ai_analysis(self.bed, 'integrated_status', 0.7, '요약', [], [], ['test::abstract::0'],
                                    'test-model', reports.PROMPT_VERSION, 0.67, self.report, self.risk,
                                    input_snapshot=self.payload if payload else None)
        return db.get_analysis_by_id(aid)

    def test_recalculates_risk_and_availability_from_saved_inputs(self):
        row = self.insert()
        r = review.build_review(row, self.resolved)
        self.assertEqual(r['status'], 'checks_passed')
        changed = {**row, 'risk_score': 0.95, 'confidence': 1}
        r = review.build_review(changed, self.resolved)
        self.assertEqual(r['status'], 'needs_review')
        failed = {c['name'] for c in r['checks'] if c['status'] == 'fail'}
        self.assertEqual(failed, {'위험점수 계산', '신호 확보 비율'})

    def test_detects_sensor_payload_inconsistent_with_saved_risk(self):
        self.payload['mmWave_현재_판정'] = {'alert_level': 'normal'}
        r = review.build_review(self.insert(), self.resolved)
        self.assertIn('센서 입력과 판정 일치', [c['name'] for c in r['checks'] if c['status'] == 'fail'])

    def test_legacy_report_never_claims_full_verification(self):
        r = review.build_review(self.insert(payload=False), self.resolved)
        self.assertEqual(r['status'], 'limited')
        self.assertFalse(r['input_available'])

    def test_missing_sources_are_failures_and_lookup_failure_is_limited(self):
        row = self.insert()
        self.assertEqual(review.build_review(row, {'success': True, 'found': [], 'missing_chunk_ids': ['test::abstract::0']})['status'], 'needs_review')
        self.assertEqual(review.build_review(row, {'success': False})['status'], 'limited')

    def test_negative_claim_and_warning_are_distinguished(self):
        self.report['보행_변화'] = '현재 보행이 불가능한 상태입니다.'
        r = review.build_review(self.insert(), self.resolved)
        self.assertIn('walking_incapacity', [f['code'] for f in r['findings']])
        self.report['보행_변화'] = '현재 걷는 신호가 없습니다. 보행이 불가능하다고 단정할 수 없습니다.'
        self.report['최근_변화'] = '최근 낙상 기록이 없습니다.'
        r = review.build_review(self.insert(), self.resolved)
        self.assertFalse(r['findings'])

    def test_unrecorded_events_do_not_prove_absence(self):
        self.report['최근_변화'] = '최근 24시간에 낙상이 발생하지 않았습니다.'
        r = review.build_review(self.insert(), self.resolved)
        self.assertIn('unobserved_fall_absence', [f['code'] for f in r['findings']])

    def test_old_dates_wrong_counts_and_fabricated_citation_are_flagged(self):
        self.payload.update({'분석_시점': '2026-09-29T00:00:00+00:00', '최근_24시간_활동_요약': {'n_events': 0}})
        self.report['최근_변화'] = '최근 24시간에 3건 발생했습니다. 2026년 9월 22일 낙상입니다.'
        self.report['관련_연구_근거'] = '연구가 있습니다. [근거: ID]'
        r = review.build_review(self.insert(), self.resolved)
        codes = {f['code'] for f in r['findings']}
        self.assertTrue({'event_count_mismatch', 'old_event_as_current', 'unknown_citation'} <= codes)

    def test_previous_report_cannot_be_denied_when_supplied(self):
        self.payload['직전_분석_기록'] = {'id': 10, 'risk_score': 0.7}
        self.report['과거_데이터와의_비교'] = '직전 분석 기록이 없습니다.'
        r = review.build_review(self.insert(), self.resolved)
        self.assertIn('previous_report_denied', [f['code'] for f in r['findings']])

    def test_sensor_numbers_levels_and_absent_change_are_not_reinterpreted(self):
        self.payload['mmWave_현재_판정']['raw'] = {'speed': 0.02}
        self.report['보행_변화'] = '보행 속도는 0입니다.'
        self.report['ToF_변화'] = "위험 상태는 '정상'입니다."
        self.report['활동_변화'] = '활동 이벤트가 없으므로 변화는 없습니다.'
        codes = {f['code'] for f in review.build_review(self.insert(), self.resolved)['findings']}
        self.assertTrue({'speed_mismatch', 'tof_level_mismatch', 'no_records_as_no_change'} <= codes)

    def test_changed_report_or_input_invalidates_saved_review(self):
        row = self.insert()
        review.review_and_save(row, self.resolved)
        self.assertIsNotNone(review.saved_review(row))
        self.assertIsNone(review.saved_review({**row, 'input_snapshot_json': '{}'}))
        self.assertIsNone(review.saved_review({**row, 'report_json': '{}'}))
        stale_review = review.saved_review(row)
        with db.get_conn() as conn:
            conn.execute('UPDATE ai_analysis SET report_json=? WHERE id=?', ('{}', row['id']))
        with self.assertRaises(ValueError):
            review.save_review(row, stale_review)

    def test_direct_review_is_separate_and_survives_read(self):
        row = self.insert()
        review.review_and_save(row, self.resolved)
        review.attach_direct_review(row, conclusion='원자료 대조', findings=[{'field': '보행', 'explanation': '직접 확인 필요'}], checked=['두 판정 대조'])
        stored = review.saved_review(row)
        self.assertEqual(stored['status'], 'needs_review')
        self.assertEqual(stored['direct_review']['reviewer'], 'Codex')

    def test_get_is_read_only_and_patient_scoped(self):
        row = self.insert()
        app = Flask(__name__)
        app.register_blueprint(dashboard_api.create_blueprint())
        with patch.object(review, '_resolve', return_value=self.resolved), patch('ai_report.call_openai', side_effect=AssertionError('No model call')):
            with app.test_client() as client:
                response = client.get(f"/api/dashboard/analyses/{row['id']}/review?bed_id={self.bed}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['input_snapshot'], self.payload)
                self.assertEqual(client.get(f"/api/dashboard/analyses/{row['id']}/review?bed_id=bed_01").status_code, 404)
                self.assertEqual(client.get('/api/dashboard/analyses/99999/review').status_code, 404)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_report_reviews').fetchone()[0], 0)

    def test_retrieval_excludes_identity_only_results(self):
        import paper_kb
        candidates = [{'chunk_id': 'title-only', 'section': 'identity'}, {'chunk_id': 'content', 'section': 'abstract'}]
        with patch.object(ai_agent, 'gather_patient_state', return_value={}), \
             patch.object(ai_agent, 'generate_queries', return_value={'success': True, 'queries': ['test']}), \
             patch.object(ai_agent, 'judge_sufficiency', return_value={'success': True, 'sufficient': True}), \
             patch.object(paper_kb, 'search_hybrid', return_value={'success': True, 'results': candidates}):
            result = ai_agent.investigate(self.bed, max_rounds=1, top_k=1)
        self.assertEqual([r['chunk_id'] for r in result['final_results']], ['content'])

    def test_activity_context_excludes_old_future_and_ai_generated_events(self):
        now = datetime.now(timezone.utc)
        for kind, stamp in [('sim_toss', now - timedelta(minutes=1)),
                            ('sim_fall', now - timedelta(days=7)),
                            ('sim_exit', now + timedelta(days=1)),
                            ('ai_analysis_completed', now - timedelta(seconds=1))]:
            db.insert_sensor_event(self.bed, kind, ts=stamp.isoformat(), level='caution')
        state = ai_agent.gather_patient_state(self.bed)
        self.assertEqual([e['event_type'] for e in state['recent_events']], ['sim_toss'])
        self.assertEqual(state['recent_24h']['n_events'], 1)
        self.assertEqual(set(state['recent_24h']['by_event_type']), {'sim_toss'})
        self.assertFalse(ai_agent._recent_record('bad-date'))


if __name__ == '__main__':
    unittest.main()
