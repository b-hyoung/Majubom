import unittest
from pathlib import Path
import test_web_workspace as fixtures
import test_group_session as groups
from bed_pose.measurement_plan import validate_plan
from bed_pose.io_utils import load_json

class PlanTests(unittest.TestCase):
    setUp=fixtures.WebTests.setUp
    cleanup=fixtures.WebTests.cleanup
    request=fixtures.WebTests.request
    wait=fixtures.WebTests.wait
    demo=fixtures.WebTests.demo
    control=fixtures.WebTests.control
    prepare_pair=groups.GroupTests.prepare_pair

    def start_plan(self,mode='manual',prepare=0,duration=None):
        ids=self.prepare_pair()
        status,result=self.request('/api/group',{'action':'start','inputs':ids,'max_skew_ms':1000,
            'measurement_plan':{'goal_posture':'supine','preparation_mode':mode,
                                 'preparation_seconds':prepare,'duration_seconds':duration}})
        self.assertEqual(status,200)
        return ids,result['group_id']

    def test_manual_preparation_reservation_ready_and_goal_not_gt(self):
        ids,gid=self.start_plan()
        group=self.workspace.groups[gid]
        self.assertEqual(group.data['phase'],'preparing')
        self.assertTrue(all(m['run_directory'] is None for m in group.data['members']))
        self.assertEqual(self.control(ids[0],'start')[0],400)
        self.request('/api/group',{'action':'ready','group_id':gid})
        self.wait(lambda x:x['groups'][0]['phase']=='recording' and all(s['recorded_frames']>=2 for s in x['sessions']))
        self.request('/api/group',{'action':'stop','group_id':gid})
        result=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        self.assertEqual(result['measurement_plan']['ready_trigger'],'operator_ready')
        root=Path(result['directory'])
        import json
        rows=[json.loads(l) for l in (root/'fusion_inputs.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(r['target_posture']=='supine' and r['verified_gt'] is None and r['posture_prediction']['label'] is None for r in rows))

    def test_timer_prepare_and_duration_auto_stop(self):
        ids,gid=self.start_plan('timer',.05,.25)
        result=self.wait(lambda x:x['groups'][0]['phase']=='completed')['groups'][0]
        self.assertEqual(result['measurement_plan']['ready_trigger'],'timer_elapsed')
        self.assertEqual(result['measurement_plan']['stop_trigger'],'duration_elapsed')
        self.assertTrue(all(m['run_directory'] is not None for m in result['members']))

    def test_cancel_preparation_creates_no_camera_measurement(self):
        ids,gid=self.start_plan('timer',100)
        self.request('/api/group',{'action':'stop','group_id':gid})
        group=self.workspace.groups[gid]
        self.assertEqual(group.data['phase'],'cancelled')
        self.assertTrue(group.finished)
        self.assertTrue(all(m['run_directory'] is None for m in group.data['members']))
        self.assertIsNone(self.workspace.active_group(ids[0]))

    def test_invalid_plan_rejected_before_reservation(self):
        for value in ({'goal_posture':'prone','preparation_mode':'manual'},
                      {'goal_posture':'supine','preparation_mode':'timer','preparation_seconds':-1},
                      {'goal_posture':'supine','preparation_mode':'manual','duration_seconds':True}):
            with self.assertRaises(ValueError):validate_plan(value)

    def test_ready_overrides_timer_and_closed_input_cancels_preparation(self):
        ids,gid=self.start_plan('timer',100)
        self.request('/api/group',{'action':'ready','group_id':gid})
        self.wait(lambda x:x['groups'][0]['phase']=='recording')
        self.request('/api/group',{'action':'stop','group_id':gid})
        self.wait(lambda x:x['groups'][0]['phase']=='completed')
        self.assertEqual(self.workspace.groups[gid].data['measurement_plan']['ready_trigger'],'operator_ready')
        # A new preparation group can be cancelled through input removal without recording frames.
        status,value=self.request('/api/group',{'action':'start','inputs':ids,
            'measurement_plan':{'goal_posture':'side','preparation_mode':'manual'}})
        self.assertEqual(status,200)
        self.control(ids[0],'remove')
        self.wait(lambda x:len(x['sessions'])==1)
        group=self.workspace.groups[value['group_id']]
        self.assertTrue(group.finished)
        self.assertEqual(group.data['phase'],'cancelled')
        self.assertTrue(all(m['run_directory'] is None for m in group.data['members']))

    def test_region_edit_during_preparation_and_missing_region_blocks_start(self):
        ids,gid=self.start_plan()
        sid=ids[0];frame=self.workspace.sessions[sid].state['frame_id']
        self.assertEqual(self.control(sid,'region',box=None,selected_frame_id=frame)[0],200)
        self.wait(lambda x:x['sessions'][0]['pending'] is None and x['sessions'][0]['region'] is None)
        self.request('/api/group',{'action':'ready','group_id':gid})
        self.wait(lambda x:x['groups'][0]['phase']=='failed')
        self.assertTrue(all(m['run_directory'] is None for m in self.workspace.groups[gid].data['members']))
