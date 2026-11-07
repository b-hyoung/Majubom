"""Synthetic geometry and loopback integration, never physical calibration evidence."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import http.client
import json
import tempfile
import threading
import time
import unittest
import cv2
import numpy as np
from bed_pose.calibration import board_points, corners, solve_samples, validate
from bed_pose.room_monitor import RoomMonitor, default_config, reconstruct, validate_config
from bed_pose.live_web import WebWorkspace, WorkspaceHTTPServer
from bed_pose.mapping import MappingProfile


def fixture():
    k = [[700, 0, 320], [0, 710, 240], [0, 0, 1]]
    return dict(version='room-stereo-1', unit='m', coordinate_system='room_world',
                calibration_validity='measured', calibration_timestamp='2026-11-01T00:00:00+00:00',
                stereo_reprojection_error=0., anchor_reprojection_error=0.,
                quality=dict(max_reprojection_error_px=2., min_ray_angle_degrees=1., min_visibility=.5),
                cameras=[dict(camera_id=cid, image_resolution=[640,480], intrinsic_matrix=k,
                    distortion_coefficients=[.1,-.02,.001,0,0], rotation=np.eye(3).tolist(), translation=t,
                    reprojection_error=0.) for cid,t in [('camera_a',[0,0,0]),('camera_b',[-.7,0,0])]])


def projected_pair(cal, xyz):
    pair=[]
    for c in cal['cameras']:
        points,_=cv2.projectPoints(np.asarray(xyz, float),cv2.Rodrigues(np.asarray(c['rotation'],float))[0],
                                   np.asarray(c['translation'],float),np.asarray(c['intrinsic_matrix'],float),
                                   np.asarray(c['distortion_coefficients'],float))
        pair.append(dict(joints=[[*p,.95] for p in points.reshape(-1,2)]))
    return pair


class GeometryTests(unittest.TestCase):
    def test_distorted_pixels_recover_metric_room_points_and_missing_joints(self):
        cal=validate(fixture()); xyz=np.array([[.1+i*.006,.2+i*.003,3+i*.01] for i in range(17)])
        pair=projected_pair(cal,xyz)
        skeleton=reconstruct(pair,cal)
        actual=np.array([[j['x'],j['y'],j['z']] for j in skeleton['joints']])
        np.testing.assert_allclose(actual,xyz,atol=1e-6)
        self.assertTrue(all(j['valid'] for j in skeleton['joints']))
        pair[1]['joints'][5]=None
        self.assertFalse(reconstruct(pair,cal)['joints'][5]['valid'])
        pair[1]['joints'][0][1]+=80
        self.assertFalse(reconstruct(pair,cal)['joints'][0]['valid'])

    def test_malformed_calibration_rejected(self):
        for mutate in [lambda c:c.update(unit='cm'), lambda c:c.update(cameras=['a','b']),
                       lambda c:c['cameras'][0].update(rotation=[[1]*3]*3),
                       lambda c:c['cameras'][0].update(distortion_coefficients=[float('nan')]*5),
                       lambda c:c['cameras'][1].update(translation=[0,0,0]),
                       lambda c:c.update(stereo_reprojection_error=float('nan'))]:
            c=fixture();mutate(c)
            with self.assertRaises(ValueError):validate(c)

    def test_checkerboard_detection_and_rejection(self):
        # Black border/background deliberately avoids depending on real images.
        board=np.full((480,640,3),127,np.uint8)
        for y in range(7):
            for x in range(10):board[50+y*45:50+(y+1)*45,60+x*45:60+(x+1)*45]=255 if (x+y)%2 else 0
        self.assertEqual(corners(board,(9,6)).shape,(54,1,2))
        with self.assertRaises(ValueError):corners(np.zeros_like(board),(9,6))

    def test_full_numeric_intrinsic_stereo_and_floor_anchor_solve(self):
        obj=board_points(9,6,.035);k=np.array([[800.,0,320],[0,790,240],[0,0,1]])
        d=np.zeros(5);rab=cv2.Rodrigues(np.array([0.,.15,.02]))[0];tab=np.array([-.6,.01,.02])
        objects=[];a=[];b=[]
        floor=np.column_stack((obj[:,0],np.zeros(len(obj)),obj[:,1])).astype(np.float32)
        ra=np.array([[1.,0,0],[0,0,-1],[0,1,0]]);ta=np.array([-.1,.15,2.])
        for i in range(18):
            if i==0:
                r=ra @ np.array([[1,0,0],[0,0,-1],[0,1,0]])
                t=ta
            else:
                r=cv2.Rodrigues(np.array([.1*(i%5)-.2,.13*(i%4)-.15,.04*i]))[0]
                t=np.array([-.25+.035*(i%6),-.15+.025*(i%5),1.1+.08*(i%7)])
            objects.append(obj.copy())
            a.append(cv2.projectPoints(obj,cv2.Rodrigues(r)[0],t,k,d)[0])
            b.append(cv2.projectPoints(obj,cv2.Rodrigues(rab@r)[0],rab@t+tab,k,d)[0])
        result=solve_samples(objects,a,b,(640,480),0,floor)
        self.assertLess(result['stereo_reprojection_error'],.001)
        self.assertLess(result['anchor_reprojection_error'],.001)
        np.testing.assert_allclose(result['cameras'][0]['intrinsic_matrix'],k,atol=.03)
        np.testing.assert_allclose(result['cameras'][0]['rotation'],ra,atol=1e-3)
        np.testing.assert_allclose(result['cameras'][0]['translation'],ta,atol=1e-3)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.monitor=RoomMonitor(Path(self.temp.name)/'monitor')
        c=default_config();c['bindings']=['a','b'];self.monitor.configure(c)
        self.sessions={sid:SimpleNamespace(id=sid,kind='camera',state={'closed':False,'phase':'ready'},
                    worker=SimpleNamespace(engine=SimpleNamespace(origin=0,profile=MappingProfile()))) for sid in ('a','b')}

    def frame(self, stamp, frame_id=1, size=(480,640)):
        return dict(rgb=np.zeros((*size,3),np.uint8),row=dict(stream_frame_id=frame_id,received_ns=str(stamp),
                    processing_status='ok',mapped_people=[],raw_candidates=[]))

    def push(self, slot, stamp, frame_id=1, size=(480,640)):
        self.monitor.ingest(self.sessions[slot],self.frame(stamp,frame_id,size))

    def test_time_matching_no_reuse_and_disconnect_staleness(self):
        now=time.perf_counter_ns()
        self.push('a',now);self.push('b',now+10000000)
        self.assertEqual(self.monitor.paired,1)
        self.assertEqual(self.monitor.status(self.sessions)['reason'],'uncalibrated')
        self.push('b',now+20000000,2)
        self.assertEqual(self.monitor.paired,1)
        self.push('a',now+25000000,2)
        self.assertEqual(self.monitor.paired,2)
        self.sessions['b'].state['closed']=True
        self.assertEqual(self.monitor.status(self.sessions)['reason'],'inputs_unavailable')
        self.sessions['b'].state['closed']=False
        for p in self.monitor.latest_pair:p['time_ns']=now-int(2e9)
        self.assertEqual(self.monitor.status(self.sessions)['reason'],'stale_pair')

    def test_skew_buffer_and_resolution_gates_and_restore(self):
        self.monitor.install(fixture());self.monitor.config.update(installation_confirmed=True,same_person_confirmed=True)
        now=time.perf_counter_ns()
        for i in range(12):self.push('a',now+i*1000000,i)
        self.assertEqual(len(self.monitor.buffers[0]),8);self.assertEqual(self.monitor.dropped[0],4)
        self.push('b',now+200000000)
        self.assertEqual(self.monitor.paired,0)
        self.assertEqual(self.monitor.reason,'timestamp_skew_exceeded')
        self.push('a',now+201000000,20,size=(720,1280))
        self.assertEqual(self.monitor.reason,'resolution_changed_recalibrate')
        restored=RoomMonitor(self.monitor.directory)
        self.assertFalse(restored.config['installation_confirmed']);self.assertIsNotNone(restored.calibration)

    def test_config_validation_capture_persistence_and_duplicates(self):
        bad=default_config();bad['room_size'][0]=-1
        with self.assertRaises(ValueError):validate_config(bad)
        now=time.perf_counter_ns();self.push('a',now);self.push('b',now+1000000)
        saved=self.monitor.capture(self.sessions)
        self.assertTrue((Path(saved['directory'])/'camera_a.png').is_file())
        self.assertTrue((Path(saved['directory'])/'pair.json').is_file())
        with self.assertRaises(ValueError):self.monitor.capture(self.sessions)

    def test_visibility_excludes_low_quality_and_requires_single_person(self):
        # Native scores must survive mapping; unavailable visibility is never invented.
        mapped=[dict(candidate_id='p',raw_candidate_id='p',joints=[dict(source_index=i,x_px=100.,y_px=100.,
                    availability='predicted',reason=None,flags=[]) for i in range(17)])]
        raw=[dict(candidate_id='p',pose_blocks=[dict(block_id='image',shape=[17,2])],
                  score_blocks=[dict(source_block_id='image',score_type='visibility',values=[.1]+[.95]*16)])]
        frame=self.frame(time.perf_counter_ns());frame['row'].update(mapped_people=mapped,raw_candidates=raw)
        self.monitor.ingest(self.sessions['a'],frame)
        joints=self.monitor.buffers[0][0]['joints']
        self.assertIsNone(joints[0]);self.assertIsNotNone(joints[1])
        frame['row']['stream_frame_id']=2
        frame['row']['mapped_people']=mapped+deepcopy(mapped)
        self.monitor.ingest(self.sessions['a'],frame)
        self.assertTrue(all(j is None for j in self.monitor.buffers[0][-1]['joints']))


class APITests(unittest.TestCase):
    def test_local_access_config_calibration_and_no_camera_page(self):
        with tempfile.TemporaryDirectory() as d:
            w=WebWorkspace(output_root=Path(d)/'records',model_store=Path(d)/'model')
            server=WorkspaceHTTPServer(w);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            def request(path,value=None,auth=True):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=5)
                headers={'X-BedPose-Token':server.token} if auth else {}
                conn.request('GET' if value is None else 'POST',path,body=None if value is None else json.dumps(value),headers=headers)
                response=conn.getresponse();body=response.read();status=response.status;conn.close();return status,body
            try:
                self.assertEqual(request('/monitor',auth=False)[0],200)
                self.assertEqual(request('/monitor-assets/monitor.js',auth=False)[0],200)
                self.assertEqual(request('/monitor-assets/../../live_web.py',auth=False)[0],404)
                self.assertEqual(request('/api/room/status',auth=False)[0],403)
                self.assertEqual(request('/api/room/live',auth=False)[0],403)
                status,page=request('/screen-pattern?kind=charuco',auth=False)
                self.assertEqual(status,200);self.assertIn(b'data:image/png;base64,',page)
                self.assertEqual(request('/screen-pattern?kind=invalid',auth=False)[0],400)
                status,body=request('/api/room/status');self.assertEqual(status,200)
                self.assertEqual(json.loads(body)['reason'],'inputs_unavailable')
                self.assertEqual(json.loads(body)['setup']['method'],'automatic_background')
                self.assertEqual(json.loads(body)['calibration_job']['state'],'idle')
                self.assertEqual(request('/api/room/automatic-start',{},auth=False)[0],403)
                self.assertEqual(request('/api/room/automatic-inputs',{'detector':'ORB'})[0],200)
                code,job=request('/api/room/automatic-start',{'detector':'ORB'})
                self.assertEqual(code,200);self.assertEqual(json.loads(job)['state'],'countdown')
                self.assertEqual(request('/api/room/automatic-start',{})[0],400)
                self.assertEqual(request('/api/room/automatic-cancel',{})[0],200)
                self.assertEqual(request('/api/room/automatic-restore',{})[0],400)
                self.assertEqual(request('/api/room/config',default_config())[0],200)
                self.assertEqual(request('/api/room/calibration',{})[0],400)
                self.assertEqual(request('/api/room/calibration',fixture())[0],200)
                self.assertEqual(request('/api/room/capture',{})[0],400)
                self.assertEqual(request('/api/state')[0],200)
                # Run the actual capture/inference/monitor callback with two explicit synthetic sources.
                ids=[w.add({'kind':'demo'})['id'] for _ in range(2)]
                c=default_config();c.update(bindings=ids,installation_confirmed=True,same_person_confirmed=True,max_skew_ms=100.)
                self.assertEqual(request('/api/room/config',c)[0],200)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    with w.lock:
                        current=w.room_monitor.status(w.sessions)
                    if current['synchronization']['pairs']>0:break
                    time.sleep(.03)
                self.assertGreater(current['synchronization']['pairs'],0)
                code,live=request('/api/room/live');self.assertEqual(code,200)
                live=json.loads(live);self.assertEqual(len(live['views']),2);self.assertEqual(len(live['sessions']),2)
                self.assertNotIn('body_state_provider_profile',live['sessions'][0])
                self.assertIsNone(current['error'])
                self.assertIsNone(current['skeleton'])
                self.assertEqual(current['reason'],'live_camera_inputs_required')
                self.assertEqual(request('/api/room/capture',{})[0],400)
            finally:
                w.close();server.shutdown();server.server_close();t.join(2)


if __name__=='__main__':unittest.main()
