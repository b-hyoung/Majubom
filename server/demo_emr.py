"""Idempotent fictional EMR seed. Never writes the live patient's records."""
import json
from pathlib import Path
import db

FIXTURE = Path(__file__).with_name('demo_emr.json')
DEMO_IDS = {'bed_sim_calm','bed_sim_bedexit','bed_sim_gaitcaution','bed_sim_risk','bed_sim_fall'}


def seed_records():
    if not FIXTURE.exists():
        return {'inserted': 0, 'status': 'fixture_pending'}
    fixture = json.loads(FIXTURE.read_text())
    assert fixture['fictional'] is True
    inserted = 0
    with db.get_conn() as conn:
        for patient in fixture['patients']:
            bed = patient['bed_id']
            assert bed in DEMO_IDS, 'Demo fixture cannot alter live patients'
            if not conn.execute('SELECT 1 FROM patients WHERE bed_id=?', (bed,)).fetchone():
                continue
            for record in patient['records']:
                assert record['text'].startswith('[가상 예시 EMR]') and record['source'].startswith('가상 예시')
                key = (bed,record['record_date'],record['source'],record['record_type'])
                if conn.execute('SELECT 1 FROM emr_records WHERE bed_id=? AND record_date=? AND source=? AND record_type=?',key).fetchone():
                    continue
                conn.execute('INSERT INTO emr_records(bed_id,record_date,source,record_type,text) VALUES(?,?,?,?,?)',key+(record['text'],))
                inserted += 1
    return {'inserted': inserted, 'status': 'ready'}


def seed_vectors():
    """Local only, idempotent; an unavailable vector library never blocks SQL records."""
    import emr_kb
    return {bed:emr_kb.build_index(bed) for bed in sorted(DEMO_IDS)}
