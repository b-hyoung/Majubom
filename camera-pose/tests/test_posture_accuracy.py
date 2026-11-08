"""Metric denominators and annotation separation, using prescribed data only."""
import copy
import tempfile
from pathlib import Path
import unittest
from bed_pose.posture_accuracy import summarize,load_annotations
from bed_pose.io_utils import atomic_json


class AccuracyTests(unittest.TestCase):
    def test_held_and_out_of_scope_predictions_cannot_inflate_accuracy(self):
        def row(i,label,state):
            return {'stream_frame_id':i,'processing_status':'ok','target_posture':'supine','verified_gt':'supine',
                    'sensor_results':{'label':'supine'},'posture_assessment':{'status':'predicted' if label else 'held',
                    'label':label,'rule_features':{'body_state':{'status':'predicted' if state else 'held','state':state}}}}
        rows=[row(1,'supine','lying'),row(2,None,None),row(3,'supine','lying'),row(4,'edge_sitting','sitting'),
              row(5,'supine','lying'),row(6,'side','lying'),row(7,'side','lying')]
        labels={i:{'label':label} for i,label in enumerate(('supine','supine','side','edge_sitting','standing','uncertain'),1)}
        original=copy.deepcopy(rows);m=summarize(rows,labels)
        self.assertEqual(rows,original);self.assertEqual(m['unannotated_frames'],1);self.assertEqual(m['uncertain_excluded_frames'],1)
        self.assertEqual(m['evaluated_target_frames'],4);self.assertEqual(m['coverage'],.75)
        self.assertAlmostEqual(m['accuracy_on_accepted'],2/3);self.assertEqual(m['correct_fraction_including_held'],.5)
        self.assertAlmostEqual(m['per_class']['supine']['f1'],.4)
        self.assertEqual(m['out_of_scope_false_acceptance_rate'],1.)
        self.assertAlmostEqual(m['full_three_class_macro_f1'],1.4/3)
        self.assertEqual(m['body_state']['coverage'],.8);self.assertEqual(m['body_state']['accuracy_on_accepted'],.75)

    def test_empty_annotations_do_not_make_metrics_and_unknown_frames_fail(self):
        m=summarize([],{});self.assertIsNone(m['coverage']);self.assertIsNone(m['full_three_class_macro_f1'])
        with self.assertRaises(ValueError):summarize([],{99:{'label':'supine'}})

    def test_annotation_identity_duplicates_and_uncertain_reason_are_checked(self):
        good={'version':'posture-annotations-0.1','measurement_id':'run','label_source':'reviewed_video','review_protocol_id':'protocol-1',
              'frames':[{'stream_frame_id':0,'label':'supine','annotation_id':'annotation-1','uncertainty_reason':None}]}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'labels.json';atomic_json(p,good);self.assertEqual(set(load_annotations(p,'run')[1]),{0})
            for field,value in (('measurement_id','other-run'),('label_source','planned_target'),('frames',good['frames']*2)):
                bad=copy.deepcopy(good);bad[field]=value;atomic_json(p,bad)
                with self.assertRaises(ValueError):load_annotations(p,'run')
            bad=copy.deepcopy(good);bad['frames'][0]['label']='uncertain';atomic_json(p,bad)
            with self.assertRaises(ValueError):load_annotations(p,'run')


if __name__=='__main__':unittest.main()
