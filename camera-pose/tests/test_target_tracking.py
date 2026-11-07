import unittest
from bed_pose.target_tracking import TargetTracker,TrackingConfig


def person(cid,x):
    return {'candidate_id':cid,'joints':[{'availability':'predicted','x_px':x+i,'y_px':20+i} for i in range(17)]}


def row(frame,people,status='ok'):
    return {'stream_frame_id':frame,'received_ns':str((frame+1)*100_000_000),
            'processing_status':status,'mapped_people':people}


class TrackingTests(unittest.TestCase):
    def test_follows_geometry_when_candidate_order_changes(self):
        tracker=TargetTracker()
        tracker.observe(row(0,[person('p0',10),person('p1',80)]),100,100)
        tracker.select(0,'p0')
        result=tracker.observe(row(1,[person('p0',80),person('p1',12)]),100,100)
        self.assertEqual(result['candidate_id'],'p1')
        self.assertEqual(result['status'],'tracking_provisional')
        self.assertFalse(result['identity_verified'])

    def test_missing_is_latched_until_manual_reselection(self):
        t=TargetTracker();t.observe(row(0,[person('p0',10)]),100,100);first=t.select(0,'p0')
        self.assertEqual(t.observe(row(1,[]),100,100)['reason'],'target_missing')
        self.assertEqual(t.observe(row(2,[person('p0',11)]),100,100)['status'],'held')
        second=t.select(2,'p0')
        self.assertNotEqual(first['track_id'],second['track_id'])
        self.assertEqual(second['status'],'tracking_provisional')

    def test_ambiguity_speed_gap_visibility_and_failure_hold(self):
        for people,reason in [([person('a',11),person('b',12)],'target_ambiguous'),
                              ([person('a',70)],'motion_or_visibility_gate_failed'),
                              ([{'candidate_id':'a','joints':[]}],'motion_or_visibility_gate_failed')]:
            t=TargetTracker();t.observe(row(0,[person('p0',10)]),100,100);t.select(0,'p0')
            self.assertEqual(t.observe(row(1,people),100,100)['reason'],reason)
        t=TargetTracker();t.observe(row(0,[person('p0',10)]),100,100);t.select(0,'p0')
        self.assertEqual(t.observe(row(10,[person('p0',10)]),100,100)['reason'],'observation_gap')
        t=TargetTracker();t.observe(row(0,[person('p0',10)]),100,100);t.select(0,'p0')
        self.assertEqual(t.observe(row(1,[],'failed'),100,100)['reason'],'processing_failed')

    def test_selection_replays_all_retained_frames_and_expires_safely(self):
        t=TargetTracker(history_limit=3)
        t.observe(row(0,[person('p0',10)]),100,100);t.observe(row(1,[]),100,100)
        t.observe(row(2,[person('p0',10)]),100,100)
        self.assertEqual(t.select(0,'p0')['status'],'held')
        t.observe(row(3,[person('p0',10)]),100,100)
        with self.assertRaises(ValueError):t.select(0,'p0')
        with self.assertRaises(ValueError):TrackingConfig.load({'min_shared_joints':True})

    def test_geometry_time_and_close_holds_do_not_reacquire(self):
        t=TargetTracker();t.observe(row(0,[person('p0',10)]),100,100);t.select(0,'p0')
        self.assertEqual(t.observe(row(1,[person('p0',10)]),200,100)['reason'],'geometry_changed')
        t=TargetTracker();t.observe(row(1,[person('p0',10)]),100,100);t.select(1,'p0')
        self.assertEqual(t.observe(row(0,[person('p0',10)]),100,100)['reason'],'time_not_increasing')
        t=TargetTracker();t.observe(row(0,[person('p0',10)]),100,100);t.select(0,'p0')
        t.hold('input_closed')
        self.assertEqual(t.current()['status'],'held')
        self.assertEqual(t.observe(row(1,[person('p0',10)]),100,100)['status'],'held')
        poor=TargetTracker();poor.observe(row(0,[{'candidate_id':'p0','joints':[]}]),100,100)
        with self.assertRaises(ValueError):poor.select(0,'p0')
