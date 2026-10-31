"""Synthetic frames: validate decode/filter/transform and non-persistent API."""
import math
from pathlib import Path
import struct
import sys
import threading
import time
from unittest import mock
import sqlite3
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnostic_server as D
import send_mmw as G


def frame(tlvs):
    parts = [struct.pack('<II', k, len(p)) + p for k, p in tlvs]
    size = 40 + sum(map(len, parts))
    header = G.MAGIC + struct.pack('<8I', 1, size, 0, 99, 0, 0, len(parts), 0)
    binary = header + b''.join(parts)
    return G.parse_frame_header(binary), binary


class DiagnosticsTests(unittest.TestCase):
    def test_point_filter_mount_and_raw_preservation(self):
        units = struct.pack('<5f', .01, .01, .1, .01, .1)
        good = struct.pack('<bbhHH', 0, 0, 3, 200, 200)
        invalid = struct.pack('<bbhHH', 0, 0, 0, 0, 0)
        h, f = frame([(1020, units + good + invalid)])
        d = D.decode_frame(h, f)
        self.assertEqual(len(d['raw_points']), 2)
        self.assertEqual(len(d['points']), 1)
        self.assertEqual(d['rejected_points'], 1)
        p = d['points'][0]
        self.assertAlmostEqual(p['sensor'][1], 2)
        self.assertAlmostEqual(p['world'][2], 2.5 - 2 * math.sin(math.radians(50)), places=5)
        self.assertEqual(d['raw_points'][0]['range'], 200)
        self.assertEqual(len(D.decode_frame(h, f, min_snr=30)['points']), 0)

    def test_empty_frame_and_no_fake_cloud_from_tracks(self):
        h, f = frame([])
        self.assertEqual(D.decode_frame(h, f)['points'], [])
        values = [1., 2., 1.5] + [0.] * 24
        h, f = frame([(1010, struct.pack('<I27f', 7, *values))])
        d = D.decode_frame(h, f)
        self.assertEqual(d['points'], [])
        self.assertEqual(d['tracks'][0]['world'], [1., 2., 1.5])

    def test_bad_tlv_and_nonfinite_values(self):
        h, f = frame([(1020, struct.pack('<5f', float('nan'), .01, .1, .01, .1) + struct.pack('<bbhHH', 1, 1, 0, 200, 200))])
        d = D.decode_frame(h, f)
        self.assertEqual(d['points'], [])
        self.assertIsNone(d['units'][0])
        h = dict(h, numTLVs=2)
        self.assertTrue(D.decode_frame(h, f)['malformed'])

    def test_api_memory_only_no_cache_and_latest_replacement(self):
        state = D.LiveState()
        state.update(frame={'points': [1]})
        state.update(frame={'points': []}, frames=2)
        client = D.create_app(state).test_client()
        with mock.patch('builtins.open', side_effect=AssertionError('file writes forbidden')), mock.patch.object(sqlite3, 'connect', side_effect=AssertionError('DB forbidden')):
            response = client.get('/api/snapshot')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.json['frame']['points'], [])
            response = client.post('/api/ingest', json={'kind': 'tof1', 'snapshot': {'distances_mm': [500]*64, 'raw_distances_mm': [501]*64, 'target_status': [5]*64}})
            self.assertEqual(response.status_code, 200)
            self.assertLess(client.get('/api/snapshot').json['tof']['tof1']['age_s'], 1)
        self.assertEqual(client.post('/api/ingest', json={'kind': 'tof1', 'snapshot': {'distances_mm': [500]*64, 'target_status': 'bad'}}).status_code, 400)
        self.assertEqual(client.post('/api/ingest', json={'kind': 'mmwave', 'snapshot': {'frames': True, 'bytes': 0}}).status_code, 400)
        self.assertEqual(client.post('/api/ingest', json={'kind': 'tof1', 'snapshot': {'distances_mm': [500]*64}}, environ_base={'REMOTE_ADDR': '192.168.0.88'}).status_code, 403)
        state.update(producer_seen_at=time.time()-5, status='streaming')
        self.assertEqual(client.get('/api/snapshot').json['status'], 'disconnected')
        self.assertNotIn('db', D.__dict__)
        self.assertNotIn('requests', D.__dict__)


    def test_operations_proxy_only_reads_selected_context_and_isolates_failure(self):
        import io
        import json
        import urllib.parse
        calls = []
        def read(url, timeout):
            calls.append(url)
            if ':5002/' in url:
                raise TimeoutError('unavailable')
            return io.BytesIO(json.dumps({'runs': []} if 'reports' in url else {'sensor': 'tof'}).encode())
        client = D.create_app(D.LiveState()).test_client()
        with mock.patch.object(D.urllib.request, 'urlopen', side_effect=read):
            response = client.get('/api/operations?bed_id=bed_01&target=room_01')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(len(calls), 3)
        self.assertIsNone(response.json['mmwave'])
        self.assertEqual(response.json['errors']['mmwave']['error_type'], 'TimeoutError')
        self.assertEqual(response.json['ai']['runs'], [])
        for url in calls:
            self.assertIn('/api/diagnostics/', url)
            self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['bed_id'], ['bed_01'])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(next(u for u in calls if ':5002/' in u)).query)['target'], ['room_01'])

    def test_live_observation_rejects_demo_context_without_reading_servers(self):
        client = D.create_app(D.LiveState()).test_client()
        with mock.patch.object(D.urllib.request,'urlopen',side_effect=AssertionError('No demo sensor reads')):
            response = client.get('/api/operations?bed_id=bed_sim_calm&target=sim_bed_sim_calm')
        self.assertEqual(response.json['errors']['scope']['error_type'],'live_patient_only')
        self.assertIsNone(response.json['tof'])
        self.assertIsNone(response.json['mmwave'])
        self.assertEqual(response.json['ai']['runs'],[])

    def test_window_average_matches_actual_feature_and_empty_is_not_measurement(self):
        from collections import deque
        samples = [(100+i/10, i/30, 2., 1.+i/100, .3+i/100, .1) for i in range(30)]
        history = {7: deque(samples)}
        raw, quality, presence = G.select_patient(history, 103)
        view = G.gait_diagnostic_inputs(history, presence, 103)
        self.assertEqual(view['speed']['n'], 30)
        self.assertAlmostEqual(view['speed']['sum']/30, view['speed']['mean'])
        self.assertEqual(view['speed']['stored_speed_if_ready'], raw['speed'])
        self.assertEqual(len(view['sample_preview']), 10)
        self.assertTrue(quality['walking'])
        empty = G.gait_diagnostic_inputs({}, {'main_tid': None}, 103)
        self.assertEqual(empty['status'], 'no_samples')
        self.assertNotIn('speed', empty)


class BranchTests(unittest.TestCase):
    def test_decode_failure_does_not_stop_original_posts(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server'))
        import live_diagnostics
        import diagnostic_decode
        h, packet = frame([])
        clock = [100.0]
        class UART:
            closed = False
            n = 0
            def read(self, count):
                self.n += 1
                if self.n > 1:
                    raise KeyboardInterrupt()
                clock[0] += 7
                return packet
            def close(self):
                self.closed = True
        uart = UART()
        pub = mock.Mock()
        response = mock.Mock(status_code=200)
        response.json.return_value = {'ok': True}
        with mock.patch.object(G.serial, 'Serial', return_value=uart), mock.patch.object(G.time, 'time', side_effect=lambda: clock[0]), mock.patch.object(G.requests, 'post', return_value=response) as posts, mock.patch.object(live_diagnostics, 'LatestPublisher', return_value=pub), mock.patch.object(diagnostic_decode, 'decode_frame', side_effect=ValueError('bad TLV')):
            G.run('cli', 'data', None, target_id='test_bed')
        urls = [call.args[0] for call in posts.call_args_list]
        self.assertIn(G.SERVER_URL, urls)
        self.assertIn(G.LIVE_URL, urls)
        self.assertIn('decode failed', pub.publish.call_args.args[1]['error'])
        self.assertTrue(uart.closed)

    def test_latest_publisher_nonblocking_and_outage(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server'))
        import live_diagnostics
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(1)
            raise OSError('diagnostic service offline')
        pub = live_diagnostics.LatestPublisher()
        with mock.patch.object(live_diagnostics.urllib.request, 'urlopen', side_effect=blocked):
            pub.publish('tof1', {'value': 1})
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            for value in range(2, 102):
                pub.publish('tof1', {'value': value})
            self.assertLess(time.monotonic() - start, .2)
            with pub.condition:
                self.assertEqual(pub.pending['tof1']['value'], 101)
                self.assertEqual(len(pub.pending), 1)
            release.set()


if __name__ == '__main__':
    unittest.main()
