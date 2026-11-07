import unittest
from bed_pose.camera_validation import evaluate
from bed_pose.bed_posture_3d import LABELS


def data():
    predictions=[];reviews=[]
    for label in LABELS:
        for i in range(30):
            key=f'{label}-{i}'
            predictions.append(dict(observation_id=key,predicted_label=label,setup_signature='a'*64))
            reviews.append(dict(observation_id=key,label=label,reviewed=True,reviewer='test-reviewer',
                evidence='synthetic unit fixture',subject_id='fixture',session_id='test',episode_id=str(i//10)))
    return predictions,reviews


class CameraValidationTests(unittest.TestCase):
    def test_full_reviewed_fixture_passes_target(self):
        p,a=data();r=evaluate(p,a);self.assertTrue(r['passed']);self.assertEqual(r['accuracy'],1)
        self.assertEqual(r['reviewed_samples'],180);self.assertEqual(r['scope'],'reviewed_dataset_only_not_population_accuracy')

    def test_empty_data_is_not_95_percent(self):
        r=evaluate([],[]);self.assertFalse(r['passed']);self.assertIsNone(r['accuracy'])

    def test_unclassified_predictions_are_in_denominator(self):
        p,a=data()
        for row in p[:30]:row['predicted_label']=None
        r=evaluate(p,a);self.assertFalse(r['passed']);self.assertEqual(r['reviewed_samples'],180)
        self.assertAlmostEqual(r['accuracy'],150/180);self.assertLess(r['coverage'],1)

    def test_planned_or_unreviewed_label_is_rejected(self):
        p,a=data();a[0]['reviewed']=False
        with self.assertRaises(ValueError):evaluate(p,a)

    def test_duplicate_frame_is_rejected(self):
        p,a=data()
        with self.assertRaises(ValueError):evaluate(p+[p[0]],a)
        with self.assertRaises(ValueError):evaluate(p,a+[a[0]])

    def test_missing_class_and_repeated_single_episode_do_not_pass(self):
        p,a=data();r=evaluate(p,a[:150]);self.assertFalse(r['passed'])
        for row in a:row['episode_id']='one'
        self.assertFalse(evaluate(p,a)['passed'])

    def test_one_weak_class_is_not_hidden_by_overall_accuracy(self):
        p,a=data()
        for row in p[:2]:row['predicted_label']=list(LABELS)[1]
        r=evaluate(p,a);self.assertGreater(r['accuracy'],.95);self.assertFalse(r['passed'])

    def test_unreviewed_or_mixed_camera_setups_cannot_pass(self):
        p,a=data();self.assertFalse(evaluate(p,a[:-1])['passed'])
        p[0]['setup_signature']='b'*64
        with self.assertRaises(ValueError):evaluate(p,a)


if __name__=='__main__':unittest.main()
