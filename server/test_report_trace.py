"""Report observation tests: no API requests, no production data or trace files."""
import json
import threading
import types
import unittest
from unittest.mock import patch

import ai_agent
import integrated_analysis as reports
import report_trace as T


class ReportTraceTests(unittest.TestCase):
    def setUp(self):
        with T._LOCK:
            T._RUNS.clear()

    def test_real_workflow_stages_preserve_results_without_extra_calls(self):
        state = {'bed_id': 'test_bed', 'patient_profile': None, 'data_source': 'simulation',
                 'tof_behavior': None, 'mmw_latest': None}
        paper = types.ModuleType('paper_kb')
        counter = [0]
        @T.traced_stage('hybrid_search', '검증 검색')
        def search(query, top_k=5):
            counter[0] += 1
            return {'success': True, 'results': [{'chunk_id': f'c{counter[0]}', 'paper_id': 'test-paper',
                    'section': 'abstract_ko', 'title': 'Synthetic study', 'original_text': 'synthetic evidence',
                    'source_url': 'https://example.invalid/synthetic', 'doi': '', 'score': .02, 'vector_rank': 1, 'bm25_rank': 2}]}
        paper.search_hybrid = paper.search = search
        valid_report = {key: '검증용 설명' for key in reports.REPORT_FIELDS}
        responses = [
            {'queries': ['첫 검색 질문'], 'reasoning': '명시적 출력 설명'},
            {'sufficient': False, 'reasoning': '근거 부족', 'additional_queries': ['추가 질문']},
            {'sufficient': True, 'reasoning': '근거 확보', 'additional_queries': []},
            valid_report,
        ]
        with patch.dict('sys.modules', {'paper_kb': paper}), patch.object(ai_agent, 'gather_patient_state', return_value=state), patch.object(reports, '_gait_prior_facts', return_value=None), patch.object(reports.db, 'get_latest_ai_analysis', return_value=None), patch.object(ai_agent.ai_report, 'call_openai', side_effect=[{'success': True, 'text': json.dumps(x), 'model': 'test-model'} for x in responses]) as llm:
            result = reports.generate_integrated_report('test_bed', max_rounds=2, persist=False)
        self.assertTrue(result['success'])
        self.assertEqual(llm.call_count, 4)
        self.assertEqual(counter[0], 2)
        runs = T.snapshot('test_bed')['runs']
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['status'], 'completed')
        stages = [event['stage'] for event in runs[0]['events']]
        for stage in ['query_generation', 'sufficiency', 'additional_queries', 'evidence_selection', 'risk_fusion', 'report_validation', 'report_storage']:
            self.assertIn(stage, stages)
        queries = next(e['details']['outputs']['queries'] for e in runs[0]['events'] if e['stage'] == 'query_generation' and e['status'] == 'completed')
        self.assertEqual(queries, ['첫 검색 질문'])
        self.assertEqual(T.snapshot('other_bed')['runs'], [])

    def test_failure_and_secrets_and_embeddings(self):
        @T.traced_stage('embedding', 'embedding')
        def vectors():
            return {'success': True, 'embeddings': [[.1, .2]], 'api_key': 'never display'}
        @T.traced_run('report')
        def run(bed_id):
            vectors()
            T.emit('test', 'running', 'test', {'password': 'hidden', 'nested': {'text': 'sk-AbCdEf1234567890abcdef'}})
            raise ValueError('expected')
        with self.assertRaises(ValueError):
            run('bed_a')
        snap = T.snapshot('bed_a')
        self.assertEqual(snap['runs'][0]['status'], 'failed')
        text = json.dumps(snap)
        self.assertNotIn('never display', text)
        self.assertNotIn('hidden', text)
        self.assertNotIn('sk-Ab', text)
        self.assertIn('embedding_summary', text)

    def test_bounded_history_and_context_isolation(self):
        barrier = threading.Barrier(2)
        @T.traced_run('report')
        def run(bed_id):
            barrier.wait()
            for i in range(T.MAX_EVENTS + 5):
                T.emit('step', 'completed', 'tick', {'bed_id': bed_id, 'i': i})
            return {'success': True}
        workers = [threading.Thread(target=run, args=(bed,)) for bed in ('a', 'b')]
        for worker in workers: worker.start()
        for worker in workers: worker.join(2)
        for bed in ('a', 'b'):
            events = T.snapshot(bed)['runs'][0]['events']
            self.assertLessEqual(len(events), T.MAX_EVENTS)
            self.assertTrue(all(e['details']['bed_id'] == bed for e in events if e['stage'] == 'step'))
        @T.traced_run('report')
        def quick(bed_id): return {'success': True}
        for i in range(T.MAX_RUNS + 5): quick(str(i))
        with T._LOCK: self.assertEqual(len(T._RUNS), T.MAX_RUNS)

    def test_untraced_calls_do_not_make_history(self):
        @T.traced_stage('plain', 'plain')
        def plain(): return 7
        self.assertEqual(plain(), 7)
        self.assertEqual(T.snapshot()['runs'], [])


if __name__ == '__main__': unittest.main()
