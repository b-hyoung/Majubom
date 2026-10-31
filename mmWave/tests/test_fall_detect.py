"""fall_detect.py를 2026-10-12 실측 기록으로 재생해 판정이 바뀌지 않았는지 확인한다."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fall_detect as F  # noqa: E402

TRIALS = json.loads((Path(__file__).parent / 'data' / 'fall_trials_20261012.json').read_text(encoding='utf-8'))['trials']


def replay(name):
    det, events = F.FallDetector(), []
    for t, tracks in TRIALS[name]:
        events += det.update(t, [{'tid': a, 'posX': x, 'posY': y, 'posZ': z} for a, x, y, z in tracks])
    return events


class FallDetectReplay(unittest.TestCase):
    def test_slow_lying_down_is_fallen_after_three_seconds_on_floor(self):
        for name in ('slow2m', 'slowfar', 'bedside'):
            events = replay(name)
            self.assertEqual([(e['stage'], e['kind']) for e in events], [('fallen', 'slow')], name)
            self.assertGreaterEqual(events[0]['low_sec'], F.CONFIRM_SEC)

    def test_fast_sit_down_is_fallen_immediately(self):
        events = replay('fast')
        self.assertEqual([(e['stage'], e['kind']) for e in events], [('fallen', 'fast')])
        self.assertLessEqual(events[0]['drop_sec'], F.FAST_SEC)

    def test_picking_something_up_is_caution_not_fall(self):
        # 바닥 높이까지 내려갔다가 3초 안에 일어남(쪼그려 물건 줍기) → 낙상이 아니라 주의(사용자 결정 10-13)
        det, events, t = F.FallDetector(), [], 0.0
        for z, secs in ((1.5, 10), (1.1, 0.6), (0.7, 0.6), (0.3, 2.0), (0.8, 0.6), (1.5, 3)):
            for _ in range(int(secs / 0.05)):
                events += det.update(t, [{'tid': 1, 'posX': 0, 'posY': 2, 'posZ': z}]); t += 0.05
        self.assertEqual([(e['stage'], e['kind']) for e in events], [('caution', 'dip')])

    def test_sitting_down_fast_on_a_chair_is_not_a_fall(self):
        # 10-12 라이브: 보통 의자 앉기(평소의 ≈40%까지 0.2~0.4초)가 낙상으로 12회 오탐 → 바닥 높이(25%)까지 가야 낙상
        det, events, t = F.FallDetector(), [], 0.0
        for z, secs in ((1.15, 10), (0.45, 10)):
            for _ in range(int(secs / 0.05)):
                events += det.update(t, [{'tid': 29, 'posX': 0.2, 'posY': 4.6, 'posZ': z}]); t += 0.05
        self.assertEqual(events, [])

    def test_leaning_forward_while_seated_is_not_a_fall(self):
        # 계속 앉아 있던 사람(0.75m)이 앞으로 숙여 0.33m: 평소 높이가 앉은 높이로 내려가면 안 된다(REF_MIN)
        det, events, t = F.FallDetector(), [], 0.0
        for z, secs in ((0.75, 60), (0.33, 1.0), (0.75, 5)):
            for _ in range(int(secs / 0.05)):
                events += det.update(t, [{'tid': 23, 'posX': 0.1, 'posY': 1.2, 'posZ': z}]); t += 0.05
        self.assertEqual(events, [])

    def test_sitting_down_slowly_on_a_chair_is_normal(self):
        det, events, t = F.FallDetector(), [], 0.0
        for z, secs in ((1.15, 10), (0.95, 0.5), (0.75, 0.5), (0.6, 0.5), (0.45, 10)):
            for _ in range(int(secs / 0.05)):
                events += det.update(t, [{'tid': 29, 'posX': 0.2, 'posY': 4.6, 'posZ': z}]); t += 0.05
        self.assertEqual(events, [])

    def test_floor_ghost_next_to_walking_person_is_not_a_fall(self):
        # 2026-10-12 라이브 오탐: 걷는 사람(tid 1) 옆 0.5m에 바닥 높이 가짜 추적(tid 7)이 새로 생김
        det, events, t = F.FallDetector(), [], 0.0
        for i in range(300):
            walker = {'tid': 1, 'posX': -0.5 + i * 0.005, 'posY': 2.0, 'posZ': 1.45}
            tracks = [walker] + ([{'tid': 7, 'posX': walker['posX'] + 0.5, 'posY': 2.1, 'posZ': -0.15}] if i >= 200 else [])
            events += det.update(t, tracks); t += 0.05
        self.assertEqual(events, [])

    def walk(self, det, t, z_fn, secs, x0=-1.5, vx=0.8, tid=3):
        events = []
        for i in range(int(secs / 0.05)):
            events += det.update(t, [{'tid': tid, 'posX': x0 + vx * 0.05 * i, 'posY': 2.0, 'posZ': z_fn(i)}])
            t += 0.05
        return events, t

    def test_height_is_learned_from_walking_per_person(self):
        # 키 큰 사람: 걸을 때 몸 중심 1.75m → 평소 높이 1.75 (예전 방식은 위로 튀는 값까지 섞여 기준이 들쭉날쭉)
        det = F.FallDetector()
        _, t = self.walk(det, 0.0, lambda i: 1.75, 3.0)
        tr = det.tracks[3]
        self.assertEqual(tr.ref_source(), 'walking')
        self.assertAlmostEqual(tr.ref(), 1.75, delta=0.01)

    def test_walking_person_height_spike_is_not_a_fall(self):
        # 걷는 중 높이 값이 0.3초 동안 바닥 높이로 튐(10-13 라이브 오탐 유형) → 하강이 0.6초보다 빠름 → 낙상 아님
        det = F.FallDetector()
        events, t = self.walk(det, 0.0, lambda i: 0.1 if 60 <= i < 66 else 1.4, 6.0)
        self.assertEqual(events, [])

    def test_walking_while_height_reads_low_is_not_a_fall(self):
        # 걷는 속도로 움직이는 동안 높이가 계속 낮게 잡혀도(4초) 넘어진 사람은 걸어 다니지 않으므로 낙상 아님
        det = F.FallDetector()
        events, t = self.walk(det, 0.0, lambda i: 1.4 if i < 40 else (1.4 - 0.05 * (i - 40) if i < 64 else 0.15), 6.0)
        self.assertEqual(events, [])

    def test_real_fall_after_walking_is_still_caught(self):
        # 걷다가 1.3초 동안 바닥으로 넘어져 그대로 있음(서 있음 80%→바닥 25% 구간 약 0.8초) → 급격한 하강 낙상
        det = F.FallDetector()
        _, t = self.walk(det, 0.0, lambda i: 1.4, 3.0)
        events = []
        for i in range(26):                                   # 1.3초 하강
            events += det.update(t, [{'tid': 3, 'posX': 0.9, 'posY': 2.0, 'posZ': 1.4 - 1.3 * (i + 1) / 26}]); t += 0.05
        for _ in range(80):
            events += det.update(t, [{'tid': 3, 'posX': 0.95, 'posY': 2.05, 'posZ': 0.1}]); t += 0.05
        self.assertEqual([(e['stage'], e['kind']) for e in events], [('fallen', 'fast')])   # 서 있음 80%→바닥 25% 구간 약 0.8초
        self.assertGreaterEqual(events[0]['low_sec'], F.CONFIRM_SEC)

    def test_very_fast_real_fall_is_caught_after_three_seconds_on_floor(self):
        # 0.4초 만에 바닥으로: 바닥 높이에 3초 머물면 급격한 하강 낙상
        det = F.FallDetector()
        _, t = self.walk(det, 0.0, lambda i: 1.4, 3.0)
        events = []
        for i in range(8):                                    # 0.4초 하강
            events += det.update(t, [{'tid': 3, 'posX': 0.9, 'posY': 2.0, 'posZ': 1.4 - 1.3 * (i + 1) / 8}]); t += 0.05
        for _ in range(80):
            events += det.update(t, [{'tid': 3, 'posX': 0.92, 'posY': 2.02, 'posZ': 0.1}]); t += 0.05
        self.assertEqual([(e['stage'], e['kind']) for e in events], [('fallen', 'fast')])
        self.assertGreaterEqual(events[0]['low_sec'], F.CONFIRM_SEC)

    def test_spike_shorter_than_one_second_is_not_a_fall(self):
        # 서 있는 사람의 높이 값이 0.8초 동안 바닥으로 튐 → 1초를 못 버팀 → 낙상도 주의도 아님
        det, events, t = F.FallDetector(), [], 0.0
        for z, secs in ((1.4, 10), (0.1, 0.8), (1.4, 5)):
            for _ in range(int(secs / 0.05)):
                events += det.update(t, [{'tid': 1, 'posX': 0, 'posY': 2, 'posZ': z}]); t += 0.05
        self.assertEqual(events, [])

    def test_floor_reflection_shadow_is_ignored(self):
        # 10-13 라이브 그림자 3건 유형: 진짜 사람(1.4m) 바로 아래 0.3m 안에서 −0.2m 높이로 같이 움직이는 추적 → 무시
        det, events, t = F.FallDetector(), [], 0.0
        for i in range(200):
            x = -1.0 + 0.01 * i if i < 100 else 0.0
            events += det.update(t, [{'tid': 0, 'posX': x, 'posY': 2.0, 'posZ': 1.4},
                                     {'tid': 29, 'posX': x + 0.2, 'posY': 2.1, 'posZ': -0.2}]); t += 0.05
        self.assertEqual(events, [])
        self.assertTrue(det.tracks[29].is_shadow())
        self.assertEqual(det.patient_tid, 0)

    def test_patient_is_closest_to_bed_after_three_seconds(self):
        # 여러 명: 침대에 가장 가까운 사람이 3초 이상 이어져야 환자 추적이 옮겨간다. 한 명이면 바로 그 사람.
        det, t = F.FallDetector(), 0.0
        far = {'tid': 5, 'posX': 1.0, 'posY': 3.5, 'posZ': 1.3}   # 침대에서 2.4m(10-16: 후보는 3m 안)
        det.update(t, [far]); self.assertEqual(det.patient_tid, 5)                      # 혼자 → 바로
        near = {'tid': 8, 'posX': 0.3, 'posY': 1.3, 'posZ': 1.4}
        for _ in range(40):                                                             # 2초 → 아직
            t += 0.05; det.update(t, [far, near])
        self.assertEqual(det.patient_tid, 5)
        for _ in range(30):                                                             # 3.5초 → 옮김
            t += 0.05; det.update(t, [far, near])
        self.assertEqual(det.patient_tid, 8)

    def test_other_person_fall_is_not_reported(self):
        # 환자가 아닌 사람(멀리 있는 사람)의 낙상은 판정하지 않는다
        det, events, t = F.FallDetector(), [], 0.0
        for i in range(300):
            far_z = 1.4 if i < 100 else 0.1
            events += det.update(t, [{'tid': 1, 'posX': 0.2, 'posY': 1.2, 'posZ': 1.4},
                                     {'tid': 2, 'posX': 1.5, 'posY': 4.5, 'posZ': far_z}]); t += 0.05
        self.assertEqual(events, [])

    def test_drop_without_measurements_is_not_a_fall(self):
        # 반사점이 끊긴 채(값 고정) 높이만 바닥으로 바뀐 경우 → 하강이 측정으로 확인되지 않아 낙상 아님
        det, events, t = F.FallDetector(), [], 0.0
        for _ in range(200):
            events += det.update(t, [{'tid': 1, 'posX': 0, 'posY': 2, 'posZ': 1.4, 'npts': 6}]); t += 0.05
        for _ in range(100):
            events += det.update(t, [{'tid': 1, 'posX': 0, 'posY': 2, 'posZ': 0.2, 'npts': 0}]); t += 0.05
        self.assertEqual(events, [])

    def test_walking_with_low_reading_gives_no_caution(self):
        # 10-13 라이브 1건 유형: 초당 0.6m로 걷는 동안 높이만 1.5초 낮게 잡혔다가 돌아옴 → 낙상도 주의도 아님
        det, events, t = F.FallDetector(), [], 0.0
        for i in range(160):
            z = 0.15 if 80 <= i < 110 else 1.35
            events += det.update(t, [{'tid': 4, 'posX': -1.5 + 0.03 * i, 'posY': 3.0, 'posZ': z}]); t += 0.05
        self.assertEqual(events, [])

    def test_standing_and_seated_people_never_trigger(self):
        self.assertEqual(replay('posR2'), [])

    def test_track_seen_only_near_floor_is_not_a_fall(self):
        # 다른 사람이 지나갈 때 바닥 높이에 잠깐 생긴 가짜 추적: 서 있던 것이 관찰되지 않았으므로 제외
        det = F.FallDetector()
        events = []
        for i in range(200):
            events += det.update(i * 0.05, [{'tid': 14, 'posX': -1.1, 'posY': 2.46, 'posZ': 0.07}])
        self.assertEqual(events, [])

    def test_recovered_after_getting_up(self):
        det, events = F.FallDetector(), []
        t = 0.0
        for z, secs in ((1.5, 10), (0.1, 7), (1.5, 3)):
            for _ in range(int(secs / 0.05)):
                events += det.update(t, [{'tid': 1, 'posX': 0, 'posY': 2, 'posZ': z}])
                t += 0.05
        # 순간 하강이어도 바닥 높이에 1초 이상 머물면 값 튐이 아니라 낙상(사용자 결정 10-13)
        self.assertEqual([(e['stage'], e.get('kind')) for e in events], [('fallen', 'fast'), ('recovered', None)])

    def test_new_track_id_after_fall_inherits_history(self):
        det, events = F.FallDetector(), []
        t = 0.0
        for _ in range(200):
            events += det.update(t, [{'tid': 5, 'posX': 0.5, 'posY': 2.0, 'posZ': 1.4}]); t += 0.05
        events += det.update(t, []); t += 1.0          # 넘어지는 순간 추적이 끊김
        for _ in range(140):
            events += det.update(t, [{'tid': 9, 'posX': 0.7, 'posY': 2.2, 'posZ': 0.1}]); t += 0.05
        self.assertIn('fallen', [e['stage'] for e in events])


SESSION = json.loads((Path(__file__).parent / 'data' / 'rec_session_20261013.json').read_text(encoding='utf-8'))['frames']


class LabeledSession20261013(unittest.TestCase):
    """10-13 라벨 녹화 재생. 실시간에서는 바닥 반사를 따라가던 추적이 진짜 사람으로 갈아탄 뒤에도 '가짜'로 기억돼
    천천히 눕기·매트 주저앉기를 둘 다 놓쳤고, 쪼그림 때는 6m 떨어진 다른 사람이 환자로 잡혔다."""

    @classmethod
    def setUpClass(cls):
        det, cls.events, cls.patient = F.FallDetector(), [], {}
        for t, label, tracks in SESSION:
            for e in det.update(t, [{'tid': a, 'posX': x, 'posY': y, 'posZ': z, 'velZ': vz, 'npts': n, 'top_z': tz}
                                    for a, x, y, z, vz, n, tz in tracks]):
                cls.events.append((label[0], e['stage'], e.get('kind')))
            p = next((tr for tr in tracks if tr[0] == det.patient_tid), None)
            if p:
                cls.patient.setdefault(label[0], []).append((p[1] ** 2 + p[2] ** 2) ** 0.5)

    def test_slow_lie_down_and_fast_sit_down_are_falls(self):
        self.assertIn(('7', 'fallen', 'slow'), self.events)
        self.assertIn(('8', 'fallen', 'fast'), self.events)

    def test_crouch_and_chair_are_not_falls(self):
        self.assertFalse([e for e in self.events if e[0] in '456' and e[1] == 'fallen'])

    def test_patient_is_the_person_not_someone_six_meters_away(self):
        far = [d for d in self.patient['5'] if d > 5.0]
        self.assertLess(len(far), 0.05 * len(self.patient['5']))

    def test_distance_correction_makes_standing_height_flat(self):
        # 1.2m에서 1.21, 4.2m에서 1.53 → 보정 후 차이 0.1m 이내
        self.assertLess(abs(F.dist_corrected_z(0, 1.17, 1.21) - F.dist_corrected_z(2.08, 3.6, 1.53)), 0.1)


class PatientLock20261016(unittest.TestCase):
    """10-16 사용자 결정: 침대에서 내려온 사람을 환자로 고정, 환자가 바뀌기 전에 시작된 하강은 넘김, 화면 지정, 3m 느슨한 범위."""

    @staticmethod
    def walk(det, t, frames):
        ev = []
        for tracks in frames:
            ev += det.update(t, tracks); t += 0.05
        return t, ev

    def test_person_leaving_bed_is_locked_even_if_visitor_is_closer(self):
        det, t = F.FallDetector(), 0.0
        visitor = lambda: {'tid': 7, 'posX': 0.4, 'posY': 1.5, 'posZ': 1.4}
        det.set_bed(t, True)
        t, _ = self.walk(det, t, [[visitor()]] * 20)
        t, _ = self.walk(det, t, [[visitor(), {'tid': 3, 'posX': 0.1, 'posY': 1.2, 'posZ': z}] for z in (0.6, 0.6, 0.6, 0.8, 1.0, 1.2)])  # 걸터앉았다 일어남
        det.set_bed(t, False)                                                      # 침대에서 내려옴
        t, _ = self.walk(det, t, [[visitor(), {'tid': 3, 'posX': 0.1, 'posY': 1.2, 'posZ': 1.3}]] * 10)
        self.assertEqual((det.patient_tid, det.locked), (3, 'exit'))
        # 환자가 걸어 나가고 방문객이 침대 옆에 오래 있어도 환자는 그대로
        frames = [[{'tid': 7, 'posX': 0.2, 'posY': 1.2, 'posZ': 1.4}, {'tid': 3, 'posX': 0.1 + 0.03 * i, 'posY': 1.2 + 0.03 * i, 'posZ': 1.3}]
                  for i in range(120)]
        t, _ = self.walk(det, t, frames)
        self.assertEqual(det.patient_tid, 3)
        det.set_bed(t, True)                                                       # 10-22: 다시 누워도 같은 사람을 계속 따라감
        self.assertEqual((det.patient_tid, det.locked), (3, 'exit'))

    def test_patient_track_number_change_is_followed(self):
        det, t = F.FallDetector(), 0.0
        det.set_bed(t, True)
        t, _ = self.walk(det, t, [[{'tid': 3, 'posX': 0.2, 'posY': 1.3, 'posZ': 0.6}, {'tid': 9, 'posX': 2.5, 'posY': 3.5, 'posZ': 1.4}]] * 5)
        det.set_bed(t, False)
        t, _ = self.walk(det, t, [[{'tid': 3, 'posX': 0.2, 'posY': 1.3, 'posZ': 1.3}, {'tid': 9, 'posX': 2.5, 'posY': 3.5, 'posZ': 1.4}]] * 10)
        self.assertEqual(det.patient_tid, 3)
        t, _ = self.walk(det, t, [[{'tid': 12, 'posX': 0.4, 'posY': 1.4, 'posZ': 1.3}, {'tid': 9, 'posX': 0.6, 'posY': 1.0, 'posZ': 1.4}]] * 10)
        self.assertEqual(det.patient_tid, 12)                                      # 사라진 자리 1m 안 → 같은 사람

    def test_descent_that_began_before_becoming_patient_is_skipped(self):
        # 다른 사람이 앉아 있다가(이미 바닥 높이) 환자가 된 경우: 환자가 되기 전에 시작된 하강은 낙상이 아니다
        det, t = F.FallDetector(), 0.0
        far = lambda z: {'tid': 2, 'posX': 1.0, 'posY': 2.5, 'posZ': z}
        t, _ = self.walk(det, t, [[{'tid': 1, 'posX': 0.2, 'posY': 1.2, 'posZ': 1.4}, far(1.4)]] * 40)
        t, _ = self.walk(det, t, [[{'tid': 1, 'posX': 0.2, 'posY': 1.2, 'posZ': 1.4}, far(0.1)]] * 30)
        t, ev = self.walk(det, t, [[far(0.1)]] * 120)                              # 환자(1)가 사라지고 2만 남음
        self.assertEqual(det.patient_tid, 2)
        self.assertFalse([e for e in ev if e['stage'] == 'fallen'])

    def test_manual_assignment(self):
        det, t = F.FallDetector(), 0.0
        t, _ = self.walk(det, t, [[{'tid': 1, 'posX': 0.2, 'posY': 1.2, 'posZ': 1.4}, {'tid': 2, 'posX': 1.5, 'posY': 2.5, 'posZ': 1.4}]] * 10)
        self.assertEqual(det.patient_tid, 1)
        det.set_manual(t, 2, seq=1.0)
        t, _ = self.walk(det, t, [[{'tid': 1, 'posX': 0.2, 'posY': 1.2, 'posZ': 1.4}, {'tid': 2, 'posX': 1.5, 'posY': 2.5, 'posZ': 1.4}]] * 100)
        self.assertEqual((det.patient_tid, det.locked), (2, 'manual'))

    def test_lone_person_far_from_bed_is_not_patient(self):
        det, t = F.FallDetector(), 0.0
        t, _ = self.walk(det, t, [[{'tid': 4, 'posX': 2.5, 'posY': 4.5, 'posZ': 1.4}]] * 10)
        self.assertIsNone(det.patient_tid)


class LateConfirm20261020(unittest.TestCase):
    def test_long_low_track_does_not_become_fall_minutes_later(self):
        # 10-20 13:31 라이브 오탐: 바닥 높이에 한참(196초) 있다가 뒤늦게 낙상이 확정됨 → 바닥에 내려간 뒤 8초 안에 확정 안 되면 포기
        det, t, ev = F.FallDetector(), 0.0, []
        tr = lambda z, n, x: {'tid': 4, 'posX': x, 'posY': 1.5, 'posZ': z, 'npts': n}
        for _ in range(60):
            ev += det.update(t, [tr(1.35, 20, 0.3)]); t += 0.05                   # 서 있음
        for z in (1.2, 1.1, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.15):  # 반사점 있는 하강(0.6초)
            ev += det.update(t, [tr(z, 10, 0.3)]); t += 0.05
        for i in range(400):                                                       # 20초 동안 바닥 높이로 계속 이동(초당 0.4m, 확정 안 됨)
            ev += det.update(t, [tr(0.15, 10, 0.3 + 0.02 * i)]); t += 0.05
        for _ in range(200):                                                       # 그 뒤 멈춤
            ev += det.update(t, [tr(0.15, 10, 0.3 + 0.02 * 400)]); t += 0.05
        self.assertFalse([e for e in ev if e['stage'] == 'fallen'])


class FallSplit20261022(unittest.TestCase):
    def test_fall_split_into_two_tracks_is_still_a_fall(self):
        # 10-22 라이브: 빠르게 쓰러지는 순간 몸이 옆 바닥 추적(23)으로 넘어가고 환자 추적(8)은 0.6~1.0m로 올라가
        # '주의'로만 끝났다 → 1.5초 안에 원래 키로 안 돌아오면 옆 바닥 추적을 이어받아 낙상
        d = json.loads((Path(__file__).parent / 'data' / 'fall_split_20261022.json').read_text(encoding='utf-8'))
        det, ev = F.FallDetector(), []
        for t, trs in d['before'] + d['after']:
            ev += det.update(t, [{'tid': a[0], 'posX': a[1], 'posY': a[2], 'posZ': a[3], 'velZ': a[6], 'npts': a[7], 'top_z': a[8]}
                                 for a in trs])
        self.assertIn('fallen', [e['stage'] for e in ev])


class BedPersonOnly20261022(unittest.TestCase):
    """10-22 사용자 결정: ToF가 연결돼 있으면 환자는 침대에 있던 사람뿐 — 다른 사람으로 안 바뀐다."""

    @staticmethod
    def feed(det, t, frames):
        for tracks in frames:
            det.update(t, tracks); t += 0.05
        return t

    def test_follows_bed_person_and_never_switches(self):
        det, t = F.FallDetector(), 0.0
        det.set_bed(t, True)
        visitor = {'tid': 7, 'posX': 0.5, 'posY': 1.6, 'posZ': 1.45}                # 침대 옆에 서 있는 방문객
        t = self.feed(det, t, [[{'tid': 3, 'posX': 0.1, 'posY': 1.2, 'posZ': 0.45}, visitor]] * 20)
        self.assertEqual((det.patient_tid, det.locked), (3, 'bed'))
        det.set_bed(t, False)                                                      # 내려옴 → 같은 사람 그대로
        frames = [[{'tid': 3, 'posX': 0.1 + 0.04 * i, 'posY': 1.2 + 0.04 * i, 'posZ': 1.35}, dict(visitor, posX=0.15, posY=1.2)]
                  for i in range(100)]                                             # 방문객이 침대 바로 옆으로 와도
        t = self.feed(det, t, frames)
        self.assertEqual((det.patient_tid, det.locked), (3, 'exit'))

    def test_no_patient_when_nobody_was_in_bed(self):
        det, t = F.FallDetector(), 0.0
        det.set_bed(t, False)
        t = self.feed(det, t, [[{'tid': 5, 'posX': 0.3, 'posY': 1.5, 'posZ': 1.4}]] * 100)   # 침대 옆에 혼자 서 있어도
        self.assertIsNone(det.patient_tid)

    def test_lost_patient_reacquired_near_last_position_only(self):
        det, t = F.FallDetector(), 0.0
        det.set_bed(t, True)
        t = self.feed(det, t, [[{'tid': 3, 'posX': 0.1, 'posY': 1.2, 'posZ': 0.5}]] * 10)
        det.set_bed(t, False)
        t = self.feed(det, t, [[{'tid': 3, 'posX': 1.0, 'posY': 2.0, 'posZ': 1.3}]] * 10)
        t = self.feed(det, t, [[{'tid': 9, 'posX': 2.5, 'posY': 3.0, 'posZ': 1.3}]] * 100)   # 사라지고 먼 곳에 다른 사람
        self.assertNotEqual(det.patient_tid, 9)
        t = self.feed(det, t, [[{'tid': 11, 'posX': 1.3, 'posY': 2.2, 'posZ': 1.3}, {'tid': 9, 'posX': 2.5, 'posY': 3.0, 'posZ': 1.3}]] * 10)
        self.assertEqual(det.patient_tid, 11)                                      # 마지막 위치 1m 안 → 같은 사람


class ManualOnly20261022(unittest.TestCase):
    """10-22 사용자 결정: 환자는 화면 클릭으로만, 다른 사람으로 자동으로 바뀌지 않음(같은 자리 0.5m·1초 안 번호 바뀜만 이어받기)."""

    @staticmethod
    def feed(det, t, frames):
        ev = []
        for tracks in frames:
            ev += det.update(t, tracks); t += 0.05
        return t, ev

    def test_no_patient_until_click_then_never_switches(self):
        det, t = F.FallDetector(manual_only=True), 0.0
        a = lambda x=0.2: {'tid': 1, 'posX': x, 'posY': 1.2, 'posZ': 1.4}
        b = {'tid': 2, 'posX': 0.1, 'posY': 1.2, 'posZ': 1.5}
        det.set_bed(t, True)
        t, _ = self.feed(det, t, [[a(), b]] * 20)
        self.assertIsNone(det.patient_tid)                    # 침대·가까운 사람 규칙 없음
        det.set_manual(t, 1, seq=1.0)
        det.set_bed(t, False)
        t, _ = self.feed(det, t, [[a(0.2 + 0.03 * i), b] for i in range(100)])
        self.assertEqual(det.patient_tid, 1)

    def test_id_change_right_there_is_followed_but_far_one_is_not(self):
        det, t = F.FallDetector(manual_only=True), 0.0
        t, _ = self.feed(det, t, [[{'tid': 1, 'posX': 0.5, 'posY': 2.0, 'posZ': 1.4}]] * 10)
        det.set_manual(t, 1, seq=1.0)
        t, _ = self.feed(det, t, [[{'tid': 1, 'posX': 0.5, 'posY': 2.0, 'posZ': 1.4}]] * 5)
        t, _ = self.feed(det, t, [[{'tid': 7, 'posX': 0.7, 'posY': 2.1, 'posZ': 1.4}]] * 10)   # 0.22m 옆에서 번호만 바뀜
        self.assertEqual(det.patient_tid, 7)
        t, _ = self.feed(det, t, [[{'tid': 9, 'posX': 2.0, 'posY': 3.0, 'posZ': 1.4}]] * 60)   # 사라지고 먼 곳에 다른 사람
        self.assertIsNone(det.patient_tid)

    def test_fall_split_still_detected_after_click(self):
        d = json.loads((Path(__file__).parent / 'data' / 'fall_split_20261022.json').read_text(encoding='utf-8'))
        det, ev = F.FallDetector(manual_only=True), []
        first = True
        for tt, trs in d['before'] + d['after']:
            if first:
                det.update(tt, [{'tid': a[0], 'posX': a[1], 'posY': a[2], 'posZ': a[3], 'velZ': a[6], 'npts': a[7], 'top_z': a[8]} for a in trs])
                det.set_manual(tt, 8, seq=1.0)               # 넘어지기 전에 화면에서 8번(당신)을 지정
                first = False
                continue
            ev += det.update(tt, [{'tid': a[0], 'posX': a[1], 'posY': a[2], 'posZ': a[3], 'velZ': a[6], 'npts': a[7], 'top_z': a[8]}
                                  for a in trs])
        self.assertIn('fallen', [e['stage'] for e in ev])


class DemoOneSecond20261022(unittest.TestCase):
    def _split(self, confirm):
        d = json.loads((Path(__file__).parent / 'data' / 'fall_split_20261022.json').read_text(encoding='utf-8'))
        det, ev, first, t0 = F.FallDetector(manual_only=True, confirm_sec=confirm), [], True, d['event']['at']
        for tt, trs in d['before'] + d['after']:
            tg = [{'tid': a[0], 'posX': a[1], 'posY': a[2], 'posZ': a[3], 'velZ': a[6], 'npts': a[7], 'top_z': a[8]} for a in trs]
            if first:
                det.update(tt, tg); det.set_manual(tt, 8, seq=1.0); first = False; continue
            ev += [(e['at'] - t0, e['stage']) for e in det.update(tt, tg)]
        return [t for t, st in ev if st == 'fallen']

    def test_one_second_mode_catches_split_fall_within_about_a_second(self):
        # 10-22 시연 결정: 바닥 1초. 10:44 기록은 바닥에 닿은 게 약 -1.5초 → 낙상이 0초 전에 나와야 한다
        falls = self._split(1.0)
        self.assertTrue(falls and falls[0] < 0.0)

    def test_crouch_then_walk_away_is_not_a_split_fall(self):
        # 10-22 11:39: 빠르게 쪼그렸다 일어나 1.3m 걸어간 사람 — 발밑 바닥 반사를 쓰러진 몸으로 이어받으면 안 된다
        d = json.loads((Path(__file__).parent / 'data' / 'crouch_walk_20261022.json').read_text(encoding='utf-8'))
        det, ev = F.FallDetector(confirm_sec=1.0), []
        for tt, trs in d['before'] + d['after']:
            ev += det.update(tt, [{'tid': a[0], 'posX': a[1], 'posY': a[2], 'posZ': a[3], 'velZ': a[6], 'npts': a[7], 'top_z': a[8]}
                                  for a in trs])
        self.assertNotIn('fallen', [e['stage'] for e in ev])


class StaleTwins20261022(unittest.TestCase):
    def test_two_frozen_tracks_side_by_side_show_as_one(self):
        # 10-22 점심 감시: 반사점 0개로 멈춘 두 추적이 0.4m 붙어 30초 동안 둘로 보임(앉아 있던 한 사람)
        det, t = F.FallDetector(manual_only=True), 0.0
        for _ in range(80):
            det.update(t, [{'tid': 8, 'posX': 2.47, 'posY': 0.58, 'posZ': 1.16, 'npts': 0},
                           {'tid': 1, 'posX': 2.10, 'posY': 0.72, 'posZ': 1.43, 'npts': 0}]); t += 0.05
        roles = det.roles()
        self.assertEqual(sorted(roles.values()), ['dup', 'other'])


class NoAutoSwitchAfterFall20261022(unittest.TestCase):
    def test_lying_patient_not_handed_to_person_standing_next_to_them(self):
        # 10-22 오후: 넘어져 누운 환자(반사점 없음) 0.4m 옆에 사람이 서면 그 사람에게 환자가 넘어갔다
        det, t = F.FallDetector(manual_only=True, confirm_sec=1.0), 0.0
        for _ in range(20):
            det.update(t, [{'tid': 4, 'posX': 0.5, 'posY': 2.0, 'posZ': 1.5, 'npts': 30}]); t += 0.05
        det.set_manual(t, 4, seq=1.0)
        for z in (1.2, 0.9, 0.6, 0.3, 0.2):
            det.update(t, [{'tid': 4, 'posX': 0.5, 'posY': 2.0, 'posZ': z, 'npts': 20}]); t += 0.05
        for i in range(120):                                                    # 누운 채 반사점 없음 + 옆에 도우미
            det.update(t, [{'tid': 4, 'posX': 0.5, 'posY': 2.0, 'posZ': 0.2, 'npts': 0},
                           {'tid': 9, 'posX': 0.8, 'posY': 2.25, 'posZ': 1.55, 'npts': 40}]); t += 0.05
        self.assertEqual(det.patient_tid, 4)

    def test_reused_track_number_far_away_is_not_followed(self):
        # 칩이 사라진 환자 번호를 2m 떨어진 다른 사람에게 바로 다시 붙이면 따라가지 않는다(환자 없음 → 다시 클릭)
        det, t = F.FallDetector(manual_only=True), 0.0
        for _ in range(20):
            det.update(t, [{'tid': 24, 'posX': -0.3, 'posY': 1.4, 'posZ': 1.6, 'npts': 30}]); t += 0.05
        det.set_manual(t, 24, seq=1.0)
        det.update(t, [{'tid': 24, 'posX': -0.3, 'posY': 1.4, 'posZ': 1.6, 'npts': 30}]); t += 0.05
        det.update(t, [{'tid': 24, 'posX': 1.5, 'posY': 2.6, 'posZ': 1.6, 'npts': 30}]); t += 0.05
        self.assertIsNone(det.patient_tid)


class OnlyClickedPerson20261022Evening(unittest.TestCase):
    def setUp(self):
        self.det, self.t = F.FallDetector(manual_only=True), 0.0

    def feed(self, frames, n):
        for _ in range(n):
            self.det.update(self.t, frames); self.t += 0.05

    def test_not_handed_to_person_who_was_already_next_to_patient(self):
        # 환자 추적이 사라질 때 원래 0.4m 옆에 있던 다른 사람(이미 있던 번호)은 이어받지 않는다
        a = {'tid': 3, 'posX': 0.0, 'posY': 2.0, 'posZ': 1.6, 'npts': 30}
        b = {'tid': 7, 'posX': 0.4, 'posY': 2.0, 'posZ': 1.6, 'npts': 30}
        self.feed([a, b], 40)
        self.det.set_manual(self.t, 3, seq=1.0)
        self.feed([a, b], 5)
        self.feed([b], 10)
        self.assertNotEqual(self.det.patient_tid, 7)

    def test_renumbered_patient_is_still_followed(self):
        # 같은 자리에 새 번호로 다시 잡히면(번호만 바뀜) 계속 따라간다
        a = {'tid': 3, 'posX': 0.0, 'posY': 2.0, 'posZ': 1.6, 'npts': 30}
        self.feed([a], 40)
        self.det.set_manual(self.t, 3, seq=1.0)
        self.feed([a], 5)
        self.feed([], 6)
        self.feed([{'tid': 11, 'posX': 0.1, 'posY': 2.05, 'posZ': 1.6, 'npts': 30}], 5)
        self.assertEqual(self.det.patient_tid, 11)

    def test_reused_number_after_short_gap_is_not_followed(self):
        # 0.6초 끊겼다 2.5m 떨어진 곳에 같은 번호가 나타나면 다른 사람
        a = {'tid': 24, 'posX': 0.0, 'posY': 2.0, 'posZ': 1.6, 'npts': 30}
        self.feed([a], 40)
        self.det.set_manual(self.t, 24, seq=1.0)
        self.feed([a], 5)
        self.feed([], 12)
        self.feed([{'tid': 24, 'posX': 1.8, 'posY': 3.7, 'posZ': 1.6, 'npts': 30}], 2)
        self.assertIsNone(self.det.patient_tid)

    def test_person_sitting_still_is_hidden_but_patient_is_not(self):
        sit = {'tid': 27, 'posX': -1.7, 'posY': 2.8, 'posZ': 0.9, 'npts': 0}
        pat = {'tid': 5, 'posX': 0.5, 'posY': 2.0, 'posZ': 1.6, 'npts': 30}
        self.feed([sit, pat], 20)
        self.det.set_manual(self.t, 5, seq=1.0)
        self.feed([sit, pat], 20)
        self.assertEqual(self.det.roles()[27], 'other')
        self.feed([sit, pat], 200)                                              # 10초 넘게 제자리
        self.assertEqual(self.det.roles()[27], 'still')
        self.assertEqual(self.det.roles()[5], 'patient')                        # 환자는 가만히 있어도 보인다
        self.feed([dict(sit, posX=-1.0)], 1)                                     # 움직이면 바로 다시 보인다
        self.assertEqual(self.det.roles()[27], 'other')

    def test_nobody_hidden_before_click(self):
        # 클릭 전에는 10초 넘게 서 있어도 숨기지 않는다(누구든 눌러 지정할 수 있어야 함, 10-22 사용자 결정)
        self.feed([{'tid': 23, 'posX': -2.0, 'posY': 2.4, 'posZ': 1.6, 'npts': 20}], 300)
        self.assertEqual(self.det.roles()[23], 'other')

    def test_click_accepted_even_if_radar_height_dips(self):
        # 걷는 사람 높이가 순간 0.2m로 흔들릴 때 눌러도 지정된다(10-22 저녁: 클릭 세 번 무시)
        self.feed([{'tid': 12, 'posX': -0.9, 'posY': 1.8, 'posZ': 1.6, 'npts': 20}], 20)
        self.feed([{'tid': 12, 'posX': -0.9, 'posY': 1.8, 'posZ': 0.2, 'npts': 20}], 2)
        self.det.set_manual(self.t, 12, seq=2.0)
        self.assertEqual(self.det.patient_tid, 12)


class BedParkManual20261022(unittest.TestCase):
    def setUp(self):
        self.det, self.t = F.FallDetector(manual_only=True, confirm_sec=1.0), 0.0

    def feed(self, frames, n, in_bed=None):
        for _ in range(n):
            if in_bed is not None:
                self.det.set_bed(self.t, in_bed)
            self.det.update(self.t, frames); self.t += 0.05

    def _lie_down(self):
        self.feed([{'tid': 12, 'posX': -0.4, 'posY': 1.6, 'posZ': 1.6, 'npts': 30}], 40, in_bed=False)
        self.det.set_manual(self.t, 12, seq=1.0)
        self.feed([{'tid': 12, 'posX': 0.0, 'posY': 1.3, 'posZ': 1.0, 'npts': 30}], 10, in_bed=False)
        self.feed([{'tid': 12, 'posX': 0.1, 'posY': 1.2, 'posZ': 0.6, 'npts': 10}], 10, in_bed=True)
        self.feed([], 60, in_bed=True)                                           # 누워서 레이더가 놓침
        self.assertIsNone(self.det.patient_tid)
        self.assertTrue(self.det.patient_info()['bed_park'])

    def test_followed_again_after_leaving_bed(self):
        self._lie_down()
        self.det.set_bed(self.t, False)                                          # ToF: 침대에서 내려옴
        self.feed([{'tid': 30, 'posX': -0.6, 'posY': 1.6, 'posZ': 1.5, 'npts': 30}], 5, in_bed=False)
        self.assertEqual(self.det.patient_tid, 30)

    def test_visitor_standing_by_bed_not_taken_while_patient_in_bed(self):
        self._lie_down()
        self.feed([{'tid': 31, 'posX': -0.5, 'posY': 1.5, 'posZ': 1.6, 'npts': 30}], 40, in_bed=True)
        self.assertIsNone(self.det.patient_tid)

    def test_redetected_lying_in_bed(self):
        self._lie_down()
        self.feed([{'tid': 33, 'posX': 0.2, 'posY': 1.1, 'posZ': 0.5, 'npts': 8}], 5, in_bed=True)
        self.assertEqual(self.det.patient_tid, 33)


class FallOntoExistingFloorTrack20261022(unittest.TestCase):
    def test_fallen_body_on_existing_floor_track_is_followed_and_confirmed(self):
        # 17:36 실측: 넘어진 몸이 원래 있던 발밑 바닥 추적으로 넘어가고 환자 추적은 사라짐 → 이어받아 낙상
        det, t = F.FallDetector(manual_only=True, confirm_sec=1.0), 0.0
        floor = {'tid': 9, 'posX': 0.3, 'posY': 2.2, 'posZ': 0.1, 'npts': 3}
        for _ in range(60):
            det.update(t, [{'tid': 4, 'posX': 0.0, 'posY': 2.0, 'posZ': 1.6, 'npts': 30}, floor]); t += 0.05
        det.set_manual(t, 4, seq=1.0)
        for z in (1.6, 1.4, 1.2, 1.0, 0.8, 0.6, 0.45):
            det.update(t, [{'tid': 4, 'posX': 0.1, 'posY': 2.1, 'posZ': z, 'npts': 20}, floor]); t += 0.05
        evs = []
        for _ in range(80):                                                     # 환자 추적은 사라지고 바닥 추적만
            evs += det.update(t, [dict(floor, npts=12)]); t += 0.05
        self.assertEqual(det.patient_tid, 9)
        self.assertIn('fallen', [e['stage'] for e in evs])


class FloorHeightIsFallForClickedPatient20261022(unittest.TestCase):
    """10-22 저녁 사용자 결정: 클릭한 환자가 바닥 높이까지 내려가면 추적이 날아가거나 사라져도 낙상."""
    def _fall(self, after):
        det, t = F.FallDetector(manual_only=True, confirm_sec=1.0), 0.0
        for i in range(60):
            det.update(t, [{'tid': 25, 'posX': 0.0 + i * 0.005, 'posY': 2.0, 'posZ': 1.6, 'npts': 40}]); t += 0.05
        det.set_manual(t, 25, seq=1.0)
        for z in (1.5, 1.3, 1.1, 0.9, 0.7, 0.5, 0.3, 0.1):
            det.update(t, [{'tid': 25, 'posX': 0.3, 'posY': 2.1, 'posZ': z, 'npts': 30}]); t += 0.05
        evs = []
        for i in range(60):
            evs += det.update(t, after(i)); t += 0.05
        return [e['stage'] for e in evs]

    def test_track_drifting_away_at_floor_height(self):
        # 20:30·20:41 실측: 반사점 없이 바닥 아래로 꺼진 채 옆으로 날아감
        self.assertIn('fallen', self._fall(lambda i: [{'tid': 25, 'posX': 0.3 + i * 0.05, 'posY': 2.1 + i * 0.02,
                                                      'posZ': -0.1 - i * 0.02, 'npts': 0}]))

    def test_track_vanishing_at_floor_height(self):
        self.assertIn('fallen', self._fall(lambda i: [{'tid': 25, 'posX': 0.3, 'posY': 2.1, 'posZ': 0.05, 'npts': 3}] if i < 4 else []))



class AutoBedPatient20261029(unittest.TestCase):
    """10-29 사용자 결정 '트래킹 자동으로': 클릭 전엔 ToF 침대 위 사람을 자동으로 잡고, 잡은 뒤엔 다른 사람으로 안 바뀐다."""
    def setUp(self):
        self.det, self.t = F.FallDetector(manual_only=True, confirm_sec=1.0, auto_bed=True), 0.0

    def feed(self, frames, n, in_bed=None):
        for _ in range(n):
            if in_bed is not None:
                self.det.set_bed(self.t, in_bed)
            self.det.update(self.t, frames); self.t += 0.05

    def test_person_lying_in_bed_is_taken(self):
        self.feed([{'tid': 8, 'posX': 0.1, 'posY': 1.2, 'posZ': 0.5, 'npts': 10}], 20, in_bed=True)
        self.assertEqual(self.det.patient_tid, 8)

    def test_visitor_standing_by_bed_not_taken(self):
        self.feed([{'tid': 9, 'posX': -0.5, 'posY': 1.5, 'posZ': 1.6, 'npts': 30}], 40, in_bed=True)
        self.assertIsNone(self.det.patient_tid)

    def test_no_one_taken_without_bed(self):
        self.feed([{'tid': 9, 'posX': 0.1, 'posY': 1.2, 'posZ': 1.5, 'npts': 30}], 40, in_bed=False)
        self.assertIsNone(self.det.patient_tid)

    def test_kept_after_leaving_bed_and_not_switched(self):
        self.feed([{'tid': 8, 'posX': 0.1, 'posY': 1.2, 'posZ': 0.5, 'npts': 10}], 20, in_bed=True)
        walk = lambda i: {'tid': 8, 'posX': -0.3 - 0.05 * i, 'posY': 1.4, 'posZ': 1.5, 'npts': 30}
        for i in range(30):
            self.feed([walk(i), {'tid': 20, 'posX': 1.5, 'posY': 0.5, 'posZ': 1.6, 'npts': 30}], 1, in_bed=False)
        self.assertEqual(self.det.patient_tid, 8)

    def test_off_without_flag(self):
        det = F.FallDetector(manual_only=True, confirm_sec=1.0)
        for i in range(20):
            det.set_bed(i * 0.05, True)
            det.update(i * 0.05, [{'tid': 8, 'posX': 0.1, 'posY': 1.2, 'posZ': 0.5, 'npts': 10}])
        self.assertIsNone(det.patient_tid)

if __name__ == '__main__':
    unittest.main()
