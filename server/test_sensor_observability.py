"""Diagnostic reads and instrumentation must preserve existing ingest semantics."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import db
import diagnostic_observer as D
import mmw_logic as L
import mmw_server as M


class ObserverTests(unittest.TestCase):
    def test_bounded_latest_and_events_have_no_sensor_archive(self):
        obs = D.Observer(max_scopes=2, max_events=3)
        for n in range(9):
            obs.record('one', 'ingest', {'raw': [n], 'level': 'normal'}, event=True)
        snap = obs.snapshot('one')
        self.assertEqual(snap['latest']['ingest']['raw'], [8])
        self.assertEqual(len(snap['events']), 3)
        self.assertTrue(all('raw' not in e for e in snap['events']))
        snap['latest']['ingest']['raw'][0] = 99
        self.assertEqual(obs.snapshot('one')['latest']['ingest']['raw'], [8])
        obs.record('two', 'ingest')
        obs.record('three', 'ingest')
        self.assertEqual(obs.snapshot('one')['latest'], {})

    def test_write_calls_original_once_and_propagates_failure(self):
        obs = D.Observer()
        calls = []
        def success(value):
            calls.append(value)
            return 27
        self.assertEqual(obs.write('one', 'tof_readings', success, 'sample', labels={'sensor':'tof1'}),27)
        self.assertEqual(calls, ['sample'])
        self.assertEqual(obs.snapshot('one')['operations'][0]['status'], 'committed')
        def failure():
            raise sqlite3.OperationalError('locked')
        with self.assertRaises(sqlite3.OperationalError):
            obs.write('one', 'tof_readings', failure)
        self.assertEqual(obs.snapshot('one')['operations'][-1]['row_id'], None)
        self.assertEqual(obs.snapshot('one')['operations'][-1]['status'], 'failed')

    def test_read_only_missing_database_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'never.db'
            snap = D.database_snapshot(path, 'tof', 'tof')
            self.assertIn('error', snap)
            self.assertFalse(path.exists())

    def test_database_rows_are_filtered_and_file_not_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE mmw_readings(id INTEGER PRIMARY KEY, target_id TEXT, speed REAL)')
                conn.executemany('INSERT INTO mmw_readings VALUES(?,?,?)', [(1,'room_01',.4),(2,'other',.9)])
            conn.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            snap=D.database_snapshot(path,'mmwave','room_01')
            self.assertEqual([r['id'] for r in snap['rows']['mmw_readings']],[1])
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertIn('activity_log', snap['errors'])


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = {}
        self.obs = D.Observer()
        self.patches = [patch.object(M,'baseline_store',self.store),
                        patch.object(M,'latest_by_target',{}), patch.object(M,'mmw_log',[]),
                        patch.object(M,'observer',self.obs), patch.object(D,'observer',self.obs),
                        patch.object(M,'_prior_for',return_value=None),
                        patch.object(L,'save_baseline'),
                        patch.object(db,'DB_PATH',str(Path(self.tmp.name)/'test.db'))]
        for p in self.patches: p.start()
        db.init_db()  # Temporary database only; same schema and actual original insert calls.
        self.client=M.app.test_client()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def payload(self, **quality):
        return {'target_id':'room_01', 'raw': {'speed':.4,'speed_cv':.2,'sway':.1,
                'freeze_ratio':.1,'height_drop':0,'stride_length':.3,'stride_cv':.1},
                'quality': {'reliable':True,'walking':True,'samples_count':20, **quality},
                'presence': {'n_targets':1,'patient_locked':True}}

    def test_actual_commit_and_update_then_read_does_not_change_baseline(self):
        data = self.payload()
        data['diagnostic_inputs'] = {'status':'computed', 'n_samples':20, 'sample_preview': [[1,2,3,4,5,6]]}
        self.assertEqual(self.client.post('/mmw',json=data).status_code,200)
        before=json.dumps(self.store,sort_keys=True)
        result=self.client.get('/api/diagnostics/mmwave?target=room_01')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers['Cache-Control'],'no-store')
        snap=result.get_json()
        self.assertTrue(snap['latest']['evaluation']['baseline_updated'])
        self.assertEqual(snap['latest']['evaluation']['diagnostic_inputs']['n_samples'],20)
        self.assertNotIn('diagnostic_inputs', json.dumps(snap['storage']['rows']['mmw_readings']))
        self.assertNotIn('sample_preview', json.dumps(snap['storage']['rows']['mmw_readings']))
        self.assertEqual(snap['baseline']['after']['n'],1)
        self.assertEqual(snap['baseline']['metrics']['speed']['mean'],.4)
        self.assertEqual(snap['baseline']['metrics']['speed']['sigma_used'],.05)
        self.assertEqual(snap['storage']['operations'][0]['row_id'],snap['storage']['rows']['mmw_readings'][0]['id'])
        self.assertEqual(snap['storage']['operations'][0]['status'],'committed')
        self.assertEqual(before,json.dumps(self.store,sort_keys=True))

    def test_no_person_gate_excludes_baseline_but_actual_row_still_saved(self):
        data=self.payload(walking=False)
        data['presence']={'n_targets':0,'patient_locked':False}
        self.client.post('/mmw',json=data)
        snap=self.client.get('/api/diagnostics/mmwave').get_json()
        self.assertEqual(self.store,{})
        self.assertFalse(snap['latest']['evaluation']['baseline_updated'])
        self.assertTrue(snap['current_result']['measuring_held'])
        self.assertEqual(len(snap['storage']['rows']['mmw_readings']),1)
        self.assertIsNone(snap['current_result']['zscore'])

    def test_mismatched_bed_target_does_not_mix_patient_rows(self):
        self.client.post('/mmw', json=self.payload())
        snap = self.client.get('/api/diagnostics/mmwave?bed_id=bed_02&target=room_01').get_json()
        self.assertIn('scope_error', snap)
        self.assertNotIn('storage', snap)
        self.assertNotIn('current_result', snap)

    def test_held_prior_does_not_claim_zscore_comparison(self):
        prior={'metrics':{'speed':{'mean':.6,'tau':.1,'sigma_within_prior':.1}}}
        with patch.object(M, '_prior_for', return_value=prior):
            self.client.post('/mmw',json=self.payload(walking=False))
        snap = self.client.get('/api/diagnostics/mmwave').get_json()
        self.assertEqual(snap['latest']['evaluation']['comparison_order'], 'not_executed')
        self.assertIsNone(snap['latest']['evaluation']['baseline_used'])
        for row in snap['baseline']['comparison'].values():
            self.assertFalse(row['comparison_executed'])
            self.assertIsNone(row['comparison_mean'])
            self.assertNotIn('provisional_k', row)

    def test_urgent_fall_does_not_invent_persistent_event(self):
        data=self.payload(walking=False)
        data['raw']['height_drop']=1.3
        self.client.post('/mmw',json=data)
        snap=self.client.get('/api/diagnostics/mmwave').get_json()
        self.assertTrue(snap['current_result']['alarm'])
        self.assertEqual(snap['current_result']['alert_level'],'critical')
        self.assertEqual(snap['storage']['rows']['activity_log'],[])
        self.assertTrue(any(e.get('alarm') for e in snap['events']))

    def test_insert_failure_reported_and_original_response_preserved(self):
        with patch.object(db,'insert_mmw',side_effect=sqlite3.OperationalError('locked')):
            response=self.client.post('/mmw',json=self.payload())
        self.assertEqual(response.status_code,200)
        snap=self.client.get('/api/diagnostics/mmwave').get_json()
        self.assertEqual(snap['storage']['operations'][0]['status'],'failed')
        self.assertEqual(snap['storage']['operations'][0]['error'],'locked')
        self.assertEqual(snap['storage']['rows']['mmw_readings'],[])

    def test_invalid_input_exclusion_visible(self):
        data=self.payload()
        data['raw']['speed_cv']='bad'
        self.client.post('/mmw',json=data)
        snap=self.client.get('/api/diagnostics/mmwave').get_json()
        self.assertEqual(snap['latest']['evaluation']['invalid_fields'],['speed_cv'])
        self.assertFalse(snap['baseline']['comparison']['speed']['included_in_baseline'])
        self.assertEqual(snap['current_result']['raw']['speed_cv'],0)
        self.assertEqual(self.store,{})

    def test_prior_compare_before_update_and_missing_stride_excluded(self):
        prior={'id':12,'metrics':{'speed':{'mean':.6,'tau':.1,'sigma_within_prior':.1,'use_for_provisional':True}}}
        data=self.payload()
        data['raw']['stride_length']=0
        with patch.object(M,'_prior_for',return_value=prior):
            self.client.post('/mmw',json=data)
        snap=self.client.get('/api/diagnostics/mmwave').get_json()
        self.assertEqual(snap['latest']['evaluation']['comparison_order'],'before_update_prior')
        self.assertTrue(snap['current_result']['provisional'])
        self.assertEqual(snap['baseline']['metrics']['stride_length']['valid_only']['v_n'],0)
        self.assertFalse(snap['baseline']['comparison']['stride_length']['included_in_valid_baseline'])
        self.assertEqual(snap['baseline']['comparison']['speed']['provisional_k'],1)


class ToFReadTests(unittest.TestCase):
    def test_diagnostics_never_predict_or_advance_risk_counters(self):
        import dashboard_api
        with patch.object(dashboard_api,'register_dashboard'):
            server=importlib.import_module('tof_server')
        with tempfile.TemporaryDirectory() as tmp, patch.object(db,'DB_PATH',str(Path(tmp)/'missing.db')), \
             patch.object(server,'_predict_posture',create=True) as predict:
            # diagnostics shows already-evaluated state without invoking /tof/latest.
            before=dict(server._risk_pending)
            response=server.app.test_client().get('/api/diagnostics/tof')
            self.assertEqual(response.status_code,200)
            predict.assert_not_called()
            self.assertEqual(before,server._risk_pending)
            self.assertFalse(Path(db.DB_PATH).exists())
            denied = server.app.test_client().get('/api/diagnostics/tof?bed_id=bed_sim_calm').json
            self.assertIn('scope_error', denied)
            self.assertNotIn('algorithms', denied)
            trace = server.app.test_client().get('/api/diagnostics/reports?bed_id=bed_sim_calm').json
            self.assertEqual(trace['runs'], [])
            algorithms=response.get_json()['algorithms']
            self.assertTrue(any(a['id']=='posture' for a in algorithms))


if __name__=='__main__':
    unittest.main()
