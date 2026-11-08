"""Loopback integration for the blackbox record protection control."""
import unittest
import test_web_workspace as web


class BlackboxApiTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    demo=web.WebTests.demo
    control=web.WebTests.control

    def test_protect_record_via_http(self):
        sid=self.demo()
        self.control(sid,'region',box=[1,1,200,200],selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.assertEqual(self.control(sid,'start',blackbox=True,segment_seconds=60,retention_max_bytes=1024**3)[0],200)
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.control(sid,'stop')
        self.wait(lambda x:x['sessions'][0]['integrity']=='verified')
        status,catalog=self.request('/api/records?storage=blackbox')
        self.assertEqual(status,200);record_id=catalog['items'][0]['record_id']
        self.assertEqual(self.request('/api/records/protect',{'record_id':record_id,'protected':True})[0],200)
        status,catalog=self.request('/api/records?storage=blackbox')
        self.assertTrue(catalog['items'][0]['protected'])
        self.assertEqual(self.request('/api/records/protect',{'record_id':record_id,'protected':False})[0],200)
        self.assertFalse(self.request('/api/records?storage=blackbox')[1]['items'][0]['protected'])
        first=self.request('/api/records/open',{'storage':'blackbox','record_id':record_id})[1]
        second=self.request('/api/records/open',{'storage':'blackbox','record_id':record_id})[1]
        self.request('/api/records/release',{'storage':'blackbox','record_id':record_id,'lease_id':first['lease_id']})
        self.assertIn(record_id,self.workspace.record_library('blackbox').opened)
        self.request('/api/records/release',{'storage':'blackbox','record_id':record_id,'lease_id':second['lease_id']})
        self.assertNotIn(record_id,self.workspace.record_library('blackbox').opened)

    def test_group_segment_pair_protection_via_http(self):
        from pathlib import Path
        ids=[self.demo(),self.demo()]
        self.wait(lambda x:len(x['sessions'])==2 and all(s['frame_id'] is not None for s in x['sessions']))
        for sid in ids:self.control(sid,'region',box=[1,1,200,200],selected_frame_id=0)
        self.wait(lambda x:all(s['region'] is not None for s in x['sessions']))
        status,result=self.request('/api/group',{'action':'start','inputs':ids,'blackbox':True,
            'segment_seconds':1,'max_skew_ms':100,'retention_max_bytes':1024**3})
        self.assertEqual(status,200)
        group=self.wait(lambda x:bool(x['groups'][0].get('segments')) and x['groups'][0]['segments'][0]['status']=='completed')['groups'][0]
        pair=[Path(p).name for p in group['segments'][0]['members']]
        self.assertEqual(self.request('/api/records/protect',{'record_id':pair[0],'protected':True})[0],200)
        items=self.request('/api/records?storage=blackbox')[1]['items']
        self.assertEqual({item['record_id'] for item in items if item['record_id'] in pair},set(pair))
        self.assertTrue(all(item['protected'] for item in items if item['record_id'] in pair))
        self.assertEqual(self.request('/api/records/protect',{'record_id':pair[1],'protected':False})[0],200)
        items=self.request('/api/records?storage=blackbox')[1]['items']
        self.assertTrue(all(not item['protected'] for item in items if item['record_id'] in pair))
        self.request('/api/group',{'action':'stop','group_id':result['group_id']})
        self.wait(lambda x:x['groups'][0]['phase']=='completed')
