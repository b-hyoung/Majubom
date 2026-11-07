"""Demand-driven display and transport optimizations must preserve analysis data."""
import http.client
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock,patch
from bed_pose.live_web import WebWorkspace,Session
import test_web_workspace as web


class DemandTests(unittest.TestCase):
    def test_not_modified_response_has_no_body_write(self):
        from bed_pose.live_web import WorkspaceHandler
        h=WorkspaceHandler.__new__(WorkspaceHandler)
        h.send_response=Mock();h.send_header=Mock();h.end_headers=Mock();h.wfile=Mock()
        h._reply(304,b'',etag='"example"')
        h.wfile.write.assert_not_called()
        self.assertFalse(any(c.args[0]=='Content-Length' for c in h.send_header.call_args_list))

    def test_raw_encoding_only_runs_while_requested_and_resumes_at_latest(self):
        workspace=WebWorkspace.__new__(WebWorkspace);workspace.lock=threading.RLock();workspace.preview_quality='high'
        packet={'capture_frame_id':1,'received_ns':0,'rgb':'test-frame'}
        engine=SimpleNamespace(source=SimpleNamespace(latest=lambda:dict(packet)),clock=lambda:10)
        worker=SimpleNamespace(engine=engine)
        encoder=Mock()
        session=Session('a','camera','camera',worker,{'closed':False,'phase':'preview'},raw_worker=encoder)
        workspace.sessions={'a':session}
        with patch('bed_pose.live_web.time.monotonic',return_value=10):
            workspace._refresh_raw(session);encoder.submit.assert_not_called()
            self.assertIsNone(workspace.raw_view('a'));self.assertEqual(encoder.submit.call_count,1)
            workspace._refresh_raw(session);self.assertEqual(encoder.submit.call_count,1)
            packet['capture_frame_id']=2;workspace._refresh_raw(session);self.assertEqual(encoder.submit.call_count,2)
        with patch('bed_pose.live_web.time.monotonic',return_value=12):
            packet['capture_frame_id']=9;workspace._refresh_raw(session);self.assertEqual(encoder.submit.call_count,2)
            workspace.raw_view('a');self.assertEqual(encoder.submit.call_count,3)
            self.assertEqual(encoder.submit.call_args.args[0]['frame']['capture_frame_id'],9)
            workspace.preview_quality='standard';workspace._refresh_raw(session);self.assertEqual(encoder.submit.call_count,4)
            session.state['closed']=True;workspace._refresh_raw(session);encoder.stop.assert_called()

    def test_compact_snapshot_restores_identical_settings_and_tracks_new_settings(self):
        w=WebWorkspace.__new__(WebWorkspace);w.lock=threading.RLock();w.groups={};w.scan_state={};w.stop_requested=threading.Event()
        w.preview_quality='high';w.model=Path('/nonexistent/test-model');w.model_name=w.model.name;w.model_error=None;w.clip_status={};w.person_crop_status={}
        profile={'version':'example','decision_policy':{'values':[1,2]}}
        state={'id':'a','posture':'side','body_state_provider_profile':profile,'automatic_posture_profile':None}
        session=Session('a','a','camera',None,state);w.sessions={'a':session}
        complete=w.snapshot();compact=w.snapshot(compact=True);row=compact['sessions'][0];key=row.pop('settings_key')
        row.update(compact['profiles'][key]);self.assertEqual(row,complete['sessions'][0])
        compact['profiles'][key]['body_state_provider_profile']['decision_policy']['values'].append(9)
        self.assertEqual(profile['decision_policy']['values'],[1,2])
        again=w.snapshot(compact=True,known_profiles={key});self.assertEqual(again['profiles'],{})
        state['posture']='held';self.assertEqual(w.snapshot(compact=True,known_profiles={key})['sessions'][0]['posture'],'held')
        state['body_state_provider_profile']={'version':'new'}
        changed=w.snapshot(compact=True,known_profiles={key});self.assertNotEqual(changed['sessions'][0]['settings_key'],key)
        self.assertTrue(changed['profiles'])
        prior=changed['sessions'][0]['settings_key']
        state['posture_reference_profile']=None
        added=w.snapshot(compact=True,known_profiles={prior})
        self.assertNotEqual(added['sessions'][0]['settings_key'],prior)
        self.assertIn('posture_reference_profile',next(iter(added['profiles'].values())))


class ConditionalStateTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup

    def get(self,etag=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=5)
        headers={'X-BedPose-Token':self.server.token}
        if etag:headers['If-None-Match']=etag
        c.request('GET','/api/state?compact=1',headers=headers)
        r=c.getresponse();result=r.status,r.getheader('ETag'),r.read();c.close();return result

    def test_unchanged_state_has_no_body_and_changed_state_is_not_suppressed(self):
        status,etag,data=self.get();self.assertEqual(status,200);self.assertTrue(data)
        status,again,data=self.get(etag);self.assertEqual((status,again,data),(304,etag,b''))
        with self.workspace.lock:self.workspace.scan_state={'error':'changed'}
        status,new,data=self.get(etag);self.assertEqual(status,200);self.assertNotEqual(new,etag)
        self.assertEqual(json.loads(data)['scan']['error'],'changed')
