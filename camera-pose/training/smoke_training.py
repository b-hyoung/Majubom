"""Deliberately synthetic setup verification, not an accuracy benchmark or real dataset."""
import argparse,json
from pathlib import Path
import numpy as np
from bed_pose.tof_learning.collect import DEFAULT_POLICY
from bed_pose.tof_learning.data import LABELS,sensor_frame,joined_sample,write_json


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',default='outputs/synthetic-training-check');args=ap.parse_args()
    root=Path(args.out);root.mkdir(parents=True,exist_ok=False);rng=np.random.default_rng(1)
    with (root/'samples.jsonl').open('w',encoding='utf-8') as f,(root/'reviews.jsonl').open('w',encoding='utf-8') as g:
        for subject in ('synthetic-train','synthetic-validation','synthetic-test'):
            for idx,cls in enumerate(LABELS):
                for frame in range(32):
                    stamp=100+idx*10+frame*.1
                    sensors=[]
                    for sid in ('tof1','tof2'):
                        d=(np.ones(64)*(700+idx*300)+rng.normal(0,20,64)).tolist()
                        sensors.append(sensor_frame(dict(type='frame',kind=sid,seq=f'{subject}-{cls}-{frame}',payload=dict(distances_mm=d)),stamp))
                    c=dict(posture_3d=dict(status='stable',label=cls,evidence_level='full',warnings=[],quality_score=.95,stable_seconds=2.,accuracy_verified=False,eligible_for_recording=False),
                        learning_observation=dict(age_ms=20,observed_unix=stamp,observation_id=str(frame)))
                    r=joined_sample(*sensors,c,dict(DEFAULT_POLICY,label_policy='pseudo'),subject,f'{subject}-{cls}')
                    r['synthetic']=True;f.write(json.dumps(r)+'\n')
                    g.write(json.dumps(dict(sample_id=r['sample_id'],reviewer='synthetic_fixture_not_human',confirmed=True,label=cls,synthetic=True))+'\n')
    write_json(root/'split.json',dict(group_by='subject_id',train=['synthetic-train'],validation=['synthetic-validation'],test=['synthetic-test']))
    print(root.resolve())


if __name__=='__main__':main()
