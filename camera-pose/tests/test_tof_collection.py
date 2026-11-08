"""Loopback simulated Majubom stream and camera; no real device collection."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path
import json,tempfile,threading,time,unittest
from bed_pose.tof_learning.collect import collect


class CollectionTests(unittest.TestCase):
    def test_read_only_stream_camera_join_and_pseudo_provenance(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.end_headers()
                if self.path.startswith('/api/room/live'):
                    self.wfile.write(json.dumps(dict(posture_3d=dict(status='stable',label='supine',warnings=[],evidence_level='full',
                        stable_seconds=3.,quality_score=.95,accuracy_verified=False,eligible_for_recording=False),
                        learning_observation=dict(observation_id='camera',observed_unix=time.time(),age_ms=10),views=['PRIVATE_IMAGE_MUST_NOT_BE_SAVED'])).encode());return
                self.wfile.write((json.dumps(dict(type='hello',pi_unix=time.time()))+'\n').encode());self.wfile.flush()
                try:
                    for seq in range(20):
                        for sid in ('tof1','tof2'):
                            r=dict(type='frame',kind=sid,seq=seq,pi_rx_unix=time.time(),payload=dict(distances_mm=[900]*64,target_status=[5]*64))
                            self.wfile.write((json.dumps(r)+'\n').encode());self.wfile.flush()
                        time.sleep(.04)
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                origin=f'http://127.0.0.1:{server.server_address[1]}'
                args=SimpleNamespace(camera_url=origin+'/monitor#local-only',stream_url=origin+'/api/stream?sensors=tof1,tof2',
                    out=temp,session='simulated',subject='synthetic',label_policy='pseudo',seconds=.6)
                collect(args);path=Path(temp)/'simulated'/'samples.jsonl'
                text=path.read_text(encoding='utf-8');rows=[json.loads(s) for s in text.splitlines()]
                self.assertGreater(len(rows),0)
                self.assertTrue(all(r['label_source']=='camera_pseudo' and r['ground_truth'] is None for r in rows))
                self.assertNotIn('PRIVATE_IMAGE',text);self.assertNotIn('local-only',text)
                self.assertEqual(len({r['sample_id'] for r in rows}),len(rows))
        finally:server.shutdown();server.server_close();t.join(2)
