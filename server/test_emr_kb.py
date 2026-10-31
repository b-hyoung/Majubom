"""Use actual temporary SQLite/LanceDB stores, never a clinical DB or AI API."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask
import db
import dashboard_api as API
import demo_emr
import emr_kb as E


class EMRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(db,'DB_PATH',str(Path(self.tmp.name)/'emr.db')),
                        patch.object(E,'STORE',str(Path(self.tmp.name)/'vectors'))]
        for p in self.patches:p.start()
        API.init_dashboard_schema(); API.ensure_demo_patients()
        self.app=Flask(__name__);self.app.register_blueprint(API.create_blueprint())
        self.client=self.app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_fictional_seed_idempotent_and_live_records_unchanged(self):
        with db.get_conn() as conn:
            conn.execute("INSERT INTO emr_records(bed_id,text) VALUES('bed_01','existing real record')")
        before=db.list_emr_records('bed_01')
        self.assertEqual(demo_emr.seed_records()['inserted'],15)
        self.assertEqual(demo_emr.seed_records()['inserted'],0)
        self.assertEqual(db.list_emr_records('bed_01'),before)
        for bed in demo_emr.DEMO_IDS:
            rows=db.list_emr_records(bed)
            self.assertEqual(len(rows),3)
            self.assertTrue(all(r['source'].startswith('가상 예시') for r in rows))

    def test_real_vectors_idempotent_patient_scoped_and_no_external_calls(self):
        demo_emr.seed_records()
        with patch('requests.post',side_effect=AssertionError('No AI request')):
            for bed in ('bed_sim_calm','bed_sim_bedexit'):
                self.assertEqual(E.build_index(bed)['n_newly_embedded'],3)
                self.assertEqual(E.build_index(bed)['n_newly_embedded'],0)
            result=E.search('bed_sim_bedexit','편마비 보조기')
        self.assertTrue(result['success']);self.assertTrue(result['results'])
        self.assertTrue(all(r['patient_id']=='bed_sim_bedexit' for r in result['results']))
        vector=E.local_embedding('편마비 보조기')
        self.assertEqual(len(vector),512)
        self.assertAlmostEqual(sum(x*x for x in vector),1)
        self.assertEqual(vector,E.local_embedding('편마비 보조기'))
        status=E.status('bed_sim_calm')
        self.assertEqual(status['n_current'],3)
        self.assertEqual(status['n_semantic_embedded'],0)
        self.assertEqual(status['chunks'][0]['vector_dimensions'],512)

    def test_reads_never_index_and_stale_source_not_returned(self):
        demo_emr.seed_records()
        self.assertFalse(Path(E.STORE).exists())
        with patch.object(E,'build_index',side_effect=AssertionError('GET must not index')):
            d=self.client.get('/api/dashboard/patients/bed_sim_calm/emr-storage').json
        self.assertEqual(d['vector']['status'],'not_indexed')
        self.assertFalse(Path(E.STORE).exists())
        E.build_index('bed_sim_calm')
        with db.get_conn() as conn:
            conn.execute("UPDATE emr_records SET text='changed original' WHERE bed_id='bed_sim_calm'")
        self.assertEqual(E.status('bed_sim_calm')['n_current'],0)
        stale = E.search('bed_sim_calm','보행')
        self.assertEqual(stale['results'],[])
        self.assertFalse(stale['coverage']['complete'])
        self.assertTrue(stale['warning'])
        self.assertEqual(E.build_index('bed_sim_calm')['n_newly_embedded'],3)
        self.assertEqual(E.status('bed_sim_calm')['n_embedded'],3)

    def test_api_ingest_vectors_structure_and_context_validation(self):
        base='/api/dashboard/patients/bed_01'
        r=self.client.post(base+'/emr',json={'records':[{'text':'보행 보조기 사용 기록','source':'unit test'}]})
        self.assertEqual(r.status_code,201)
        self.assertTrue(r.json['vector_index']['success'])
        d=self.client.get(base+'/emr-storage').json
        self.assertEqual(d['vector']['n_current'],1)
        self.assertIn('text',[c['name'] for c in d['schema']['emr_records']])
        self.assertEqual(d['emr_records'][0]['bed_id'],'bed_01')
        self.assertEqual(self.client.get(base+'/emr-search?q=보조기').status_code,200)
        self.assertEqual(self.client.get(base+'/emr-search').status_code,400)
        self.assertEqual(self.client.get('/api/dashboard/patients/unknown/emr-storage').status_code,404)
        self.assertEqual(self.client.post(base+'/emr-index',json=[]).status_code,400)
        self.assertEqual(self.client.post(base+'/emr-index',json={'method':'fake'}).status_code,400)


if __name__=='__main__':unittest.main()
