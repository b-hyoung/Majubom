"""Add fictional demo records and local vectors; preserve all existing records."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import dashboard_api
import demo_emr
import emr_kb

if __name__=='__main__':
    dashboard_api.init_dashboard_schema()
    dashboard_api.ensure_demo_patients()
    print(demo_emr.seed_records())
    for bed in sorted(demo_emr.DEMO_IDS):
        result=emr_kb.build_index(bed)
        print(bed,result)
        if not result.get('success'):sys.exit(1)
