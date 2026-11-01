"""Stage 5/6 label gates, 8x8 format and group/window leakage prevention."""
from copy import deepcopy
import json,tempfile,unittest
from pathlib import Path
import numpy as np
from bed_pose.tof_learning.data import LABELS,sensor_frame,camera_eligibility,joined_sample,tensor_sample,load_rows,make_windows,label
from bed_pose.tof_learning.collect import DEFAULT_POLICY


def camera():
    return dict(posture_3d=dict(status='stable',label='supine',evidence_level='full',warnings=[],stable_seconds=2.,quality_score=.95,
        accuracy_verified=True,eligible_for_recording=True),learning_observation=dict(age_ms=20,observed_unix=100.,observation_id='f1'))


def sensor(sid,stamp=100.,identity=1):
    return sensor_frame(dict(type='frame',kind=sid,pi_rx_unix=stamp,seq=identity,payload=dict(distances_mm=[1000]*64,target_status=[5]*64,targets=[1]*64)),stamp)


def dataset():
    rows=[]
    for subject in ('A','B','C'):
        for index,cls in enumerate(LABELS):
            for i in range(20):
                r=joined_sample(sensor('tof1'),sensor('tof2'),camera(),DEFAULT_POLICY,subject,f'{subject}-{cls}')
                r.update(sample_id=f'{subject}-{cls}-{i}',timestamp_unix=100+index*5+i*.1,training_label=cls,reviewed=True)
                rows.append(r)
    return rows


class LearningDataTests(unittest.TestCase):
    def test_strict_policy_rejects_current_unverified_predictions(self):
        c=camera();self.assertIsNone(camera_eligibility(c,DEFAULT_POLICY))
        c['posture_3d']['accuracy_verified']=False;c['posture_3d']['eligible_for_recording']=False
        self.assertEqual(camera_eligibility(c,DEFAULT_POLICY),'camera_accuracy_not_verified')
        self.assertIsNone(camera_eligibility(c,dict(DEFAULT_POLICY,label_policy='pseudo')))
        r=joined_sample(sensor('tof1'),sensor('tof2'),c,dict(DEFAULT_POLICY,label_policy='pseudo'),'S01','test')
        self.assertEqual(r['label_source'],'camera_pseudo');self.assertIsNone(r['ground_truth']);self.assertTrue(r['review_required'])

    def test_stale_partial_low_quality_and_conflicting_samples_rejected(self):
        for field,value in [('status','candidate'),('quality_score',.6),('quality_score',float('nan')),('stable_seconds',.5),('stable_seconds',float('nan')),('stable_seconds','invalid'),('warnings',['cross_view_disagreement']),('evidence_level','partial')]:
            c=camera();c['posture_3d'][field]=value;self.assertIsNotNone(camera_eligibility(c,DEFAULT_POLICY))
        c=camera();c['learning_observation']['age_ms']=500;self.assertEqual(camera_eligibility(c,DEFAULT_POLICY),'camera_observation_stale')
        c['learning_observation']['age_ms']=float('nan');self.assertEqual(camera_eligibility(c,DEFAULT_POLICY),'camera_observation_stale')

    def test_real_sensor_64_zone_validation_mask_and_channels(self):
        a=sensor('tof1');b=sensor('tof2');r=joined_sample(a,b,camera(),DEFAULT_POLICY,'S','T')
        x=tensor_sample(r);self.assertEqual(x.shape,(4,8,8));self.assertAlmostEqual(float(x[0,0,0]),.25)
        self.assertTrue(np.all(x[2:]==1))
        with self.assertRaises(ValueError):sensor_frame(dict(kind='tof1',seq=1,payload={'distances_mm':[1]*16}),1.)
        with self.assertRaisesRegex(ValueError,'invalid_status_value'):sensor_frame(dict(kind='tof1',seq=1,payload={'distances_mm':[900]*64,'targets':[float('nan')]*64}),1.)
        with self.assertRaisesRegex(ValueError,'invalid_frame_object'):sensor_frame([],1.)
        a=sensor('tof1');a['valid_fraction']=.1
        with self.assertRaisesRegex(ValueError,'valid_fraction'):joined_sample(a,b,camera(),DEFAULT_POLICY,'S','T')

    def test_skew_and_transition_rejected(self):
        with self.assertRaisesRegex(ValueError,'tof_pair_skew'):joined_sample(sensor('tof1',99.),sensor('tof2'),camera(),DEFAULT_POLICY,'S','T')
        with self.assertRaisesRegex(ValueError,'camera_tof_skew'):joined_sample(sensor('tof1',101.),sensor('tof2',101.),camera(),DEFAULT_POLICY,'S','T')

    def test_group_split_never_mixes_subjects_or_labels_in_windows(self):
        rows=dataset();split=dict(group_by='subject_id',train=['A'],validation=['B'],test=['C'])
        out=make_windows(rows,split,window=8,stride=4)
        sets=[{sid for w in out[p] for sid in w['sample_ids']} for p in ('train','validation','test')]
        self.assertFalse(sets[0]&sets[1] or sets[0]&sets[2] or sets[1]&sets[2])
        self.assertEqual({w['y'] for w in out['test']},set(range(6)))
        split['test']=['A']
        with self.assertRaisesRegex(ValueError,'overlap'):make_windows(rows,split)

    def test_unreviewed_not_test_truth_and_pseudo_train_only_by_explicit_flag(self):
        rows=dataset();split=dict(group_by='subject_id',train=['A'],validation=['B'],test=['C'])
        for r in rows:
            if r['subject_id']=='A':r['reviewed']=False
        with self.assertRaisesRegex(ValueError,'train'):make_windows(rows,split,8,4)
        self.assertTrue(make_windows(rows,split,8,4,allow_pseudo_train=True)['train'])
        for r in rows:
            if r['subject_id']=='C':r['reviewed']=False
        with self.assertRaisesRegex(ValueError,'test'):make_windows(rows,split,8,4,allow_pseudo_train=True)

    def test_no_windows_cross_gaps_and_missing_classes_fail(self):
        rows=dataset();split=dict(group_by='subject_id',train=['A'],validation=['B'],test=['C'])
        for r in rows:
            if r['subject_id']=='A' and r['training_label']=='prone':r['timestamp_unix']*=10
        with self.assertRaisesRegex(ValueError,'six classes'):make_windows(rows,split,8,4)

    def test_review_is_explicit_and_alias_mapping_not_empty_to_prone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'samples.jsonl';r=dataset()[0];r.pop('reviewed');r.pop('training_label');path.write_text(json.dumps(r)+'\n')
            review=Path(tmp)/'review.jsonl';review.write_text(json.dumps(dict(sample_id=r['sample_id'],confirmed=True,reviewer='tester',label='side_left'))+'\n')
            self.assertFalse(load_rows([path])[0]['reviewed'])
            self.assertEqual(load_rows([path],review)[0]['training_label'],'left_side')
            self.assertEqual(label('edge_sit'),'edge_sitting')
            with self.assertRaises(ValueError):label('empty')
