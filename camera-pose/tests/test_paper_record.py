"""논문 기록에서 미측정값과 외부 시험값을 구분하는 회귀 검사."""
import tempfile
import unittest
from pathlib import Path
from bed_pose.tof_learning.paper_record import render_record, write_record, parameter_snapshot


class PaperRecordTests(unittest.TestCase):
    def test_library_nan_parameter_is_preserved_as_explicit_setting(self):
        import json
        snapshot = parameter_snapshot({'missing':float('nan')})
        self.assertEqual(snapshot, {'missing':'nan'})
        json.dumps(snapshot, allow_nan=False)

    def test_missing_values_remain_blank(self):
        text = render_record()
        self.assertIn('| RF 원시 | 거리 128개 |  |  |  |', text)
        self.assertIn('| 학습에 사용한 GPU 이름 |  |', text)
        self.assertIn('| 시간 정렬된 비교의 일치율 |  |', text)
        self.assertNotIn('None', text)
        self.assertNotIn('5070', text)

    def test_unaligned_camera_is_not_reported_as_zero_score(self):
        text = render_record({'camera':{'status':'not_evaluated_no_aligned_camera_observations',
            'accuracy':None, 'matched_observations':0, 'confusion_matrix':[[0]*7]*6}})
        self.assertIn('| 시간 정렬된 비교 수 |  |', text)
        self.assertIn('| 혼동 행렬 |  |', text)

    def test_only_observed_results_are_rendered(self):
        record = dict(completed=True, models={'raw_rf':dict(accuracy=.75,macro_f1=.625,
            per_class_recall={'empty':0},confusion_matrix=[[1,0,0,0,0,0]]*6)},
            settings={'gpu':'검사 장치'},camera={'accuracy':0,'matched_observations':3})
        with tempfile.TemporaryDirectory() as directory:
            path = write_record(directory, record)
            text = Path(path).read_text(encoding='utf-8')
            self.assertIn('| RF 원시 | 거리 128개 | 0.75 | 0.625 |  |', text)
            self.assertIn('| 시간 정렬된 비교의 일치율 | 0 |', text)
            self.assertIn('| CNN | 센서별 3채널 8×8 |  |  |  |', text)


if __name__ == '__main__':
    unittest.main()
