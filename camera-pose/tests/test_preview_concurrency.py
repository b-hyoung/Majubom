"""Controlled encoder stalls must not block another input or workspace control."""
import threading
import unittest
from unittest.mock import patch
import test_web_workspace as web
from bed_pose.live_recording import verify_live
from bed_pose.preview_worker import PreviewWorker
from bed_pose.preview_quality import encode_preview

class WorkerTests(unittest.TestCase):
    def test_only_latest_pending_job_is_retained(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event();seen=[]
        def encode(value,quality):
            if value==1:entered.set();release.wait(3)
            return value,None
        def deliver(job,result,error):
            seen.append(result[0])
            if result[0]==3:finished.set()
        worker=PreviewWorker(encode,deliver)
        try:
            worker.submit({'frame':{'rgb':1},'quality':'high'})
            self.assertTrue(entered.wait(1))
            worker.submit({'frame':{'rgb':2},'quality':'high'})
            worker.submit({'frame':{'rgb':3},'quality':'high'})
            release.set();self.assertTrue(finished.wait(1));self.assertEqual(seen,[1,3])
        finally:release.set();worker.stop();worker.thread.join(1)

class ConcurrencyTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    demo=web.WebTests.demo
    control=web.WebTests.control

    def test_slow_encoder_does_not_block_state_other_input_recording_or_removal(self):
        a=self.demo();b=self.demo()
        self.wait(lambda x:all(s['sequence']>0 for s in x['sessions']))
        view=self.request('/api/view/'+a)[1]
        self.control(a,'region',box=[10,20,200,300],selected_frame_id=view['frame_id'])
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(a,'start');self.wait(lambda x:x['sessions'][0]['recorded_frames']>=1)
        session=self.workspace.sessions[a];encoder=session.preview_worker.thread
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def slow(rgb,quality):
            if threading.current_thread() is encoder:entered.set();release.wait(5)
            return encode_preview(rgb,quality)
        with patch('bed_pose.live_web.encode_preview',side_effect=slow):
            self.assertTrue(entered.wait(2))
            state=self.request('/api/state')[1];before=state['sessions'][0]['recorded_frames'];other=state['sessions'][1]['sequence']
            state=self.wait(lambda x:x['sessions'][0]['recorded_frames']>=before+3 and x['sessions'][1]['sequence']>other)
            self.assertTrue(state['sessions'][0]['recording'])
            directory=state['sessions'][0]['directory']
            self.assertEqual(self.control(a,'remove')[0],200)
            self.wait(lambda x:all(s['id']!=a for s in x['sessions']))
            release.set();encoder.join(.5)
            self.assertEqual(self.request('/api/view/'+a)[0],404)
            self.assertEqual(len(self.workspace.sessions),1)
            self.assertFalse(encoder.is_alive())
            report=verify_live(directory)
            self.assertEqual(report['integrity'],'verified')
            self.assertGreaterEqual(report['counts']['ok'],before+3)

    def test_quality_encoding_runs_outside_workspace_lock_and_stale_result_cannot_win(self):
        sid=self.demo();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        outcome=[]
        def slow(rgb,quality):
            if threading.current_thread().name=='quality-switch':entered.set();release.wait(5)
            return encode_preview(rgb,quality)
        def change():
            try:outcome.append(self.workspace.set_preview_quality('standard'))
            except Exception as error:outcome.append(error)
        with patch('bed_pose.live_web.encode_preview',side_effect=slow):
            thread=threading.Thread(target=change,name='quality-switch');thread.start()
            try:
                self.assertTrue(entered.wait(2))
                before=self.request('/api/state')[1]['sessions'][0]['sequence']
                self.wait(lambda x:x['sessions'][0]['sequence']>before+1)
                release.set();thread.join(2);self.assertFalse(thread.is_alive())
                self.assertIsInstance(outcome[0],dict)
                self.wait(lambda x:x['sessions'][0]['preview']['quality']=='standard')
                frame=self.request('/api/view/'+sid)[1]
                self.assertEqual(frame['frame_id'],frame['posture_assessment']['stream_frame_id'])
                self.assertEqual(frame['preview']['quality'],'standard')
            finally:release.set();thread.join(2)
