"""ToF + mmWave raw dashboard relay. Latest snapshots in RAM only; no DB or files."""
import argparse
import copy
import math
from pathlib import Path
import struct
import threading
import time
import json
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, send_from_directory, request
import serial
import send_mmw as protocol

from diagnostic_decode import decode_frame, finite_json
SITE = Path(__file__).resolve().parents[1] / 'site'

class LiveState:
    def __init__(self, height=2.5, tilt=50.0):
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.data = {'status': 'starting', 'error': None, 'bytes': 0, 'frames': 0,
                     'tof': {'tof1': None, 'tof2': None}, 'producer_seen_at': None, 'last_frame_at': None, 'mount': {'height_m': height, 'down_tilt_deg': tilt}, 'frame': None}

    def update(self, **values):
        with self.lock:
            self.data.update(values)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.data)
        result['uptime_s'] = round(time.monotonic() - self.started, 1)
        now = time.time()
        for sensor in result['tof'].values():
            if sensor is not None:
                sensor['age_s'] = round(max(0, now - sensor['received_at_unix']), 2)
        result['producer_age_s'] = None if result['producer_seen_at'] is None else round(max(0, now - result['producer_seen_at']), 2)
        if result['producer_age_s'] is not None and result['producer_age_s'] > 2:
            result['status'] = 'disconnected'
        result['age_s'] = None if result['last_frame_at'] is None else round(time.time() - result['last_frame_at'], 2)
        return result


def configure(port, cfg):
    """Require actual Done responses; do not treat silence as successful configuration."""
    warnings = []
    with serial.Serial(port, 115200, timeout=0.1, exclusive=True) as cli:
        cli.reset_input_buffer()
        for raw in Path(cfg).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith('%'):
                continue
            cli.write((line + '\n').encode())
            response = b''
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                response += cli.read(4096)
                if b'mmwDemo:/>' in response:
                    break
            text = response.decode(errors='replace')
            if 'error' in text.lower() or 'not recognized' in text.lower():
                raise RuntimeError(f'cfg rejected: {line}: {text.strip()}')
            if 'Done' not in text:
                if line == 'sensorStart':
                    warnings.append('sensorStart ACK 미확인: 실제 UART 프레임 수신으로 시작 여부 확인')
                else:
                    raise RuntimeError(f'cfg unconfirmed: {line}: {text.strip()}')
    return warnings


def read_sensor(args, state):
    try:
        if args.cfg:
            state.update(status='configuring')
            state.update(config_warnings=configure(args.cli, args.cfg))
        state.update(status='waiting')
        with serial.Serial(args.data, 921600, timeout=0.1, exclusive=True) as uart:
            buffer = b''
            total_bytes = total_frames = 0
            while True:
                chunk = uart.read(4096)
                if not chunk:
                    continue
                total_bytes += len(chunk)
                buffer += chunk
                frames, buffer = protocol.iter_frames(buffer)
                state.update(bytes=total_bytes)
                for header, raw in frames:
                    decoded = decode_frame(header, raw, args.height, args.tilt, args.min_snr)
                    total_frames += 1
                    state.update(status='streaming', frames=total_frames,
                                 last_frame_at=time.time(), frame=decoded)
    except Exception as exc:
        state.update(status='error', error=str(exc))


def create_app(state):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/')
    def dashboard():
        return send_from_directory(SITE, 'sensor-diagnostics.html')

    @app.get('/sensor-diagnostics.js')
    def script():
        return send_from_directory(SITE, 'sensor-diagnostics.js')

    @app.post('/api/ingest')
    def ingest():
        if request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify(error='local collector only'), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get('kind') not in ('tof1', 'tof2', 'mmwave') or not isinstance(payload.get('snapshot'), dict):
            return jsonify(error='kind and snapshot required'), 400
        kind, snap = payload['kind'], finite_json(payload['snapshot'])
        if kind == 'mmwave':
            if any(type(snap.get(k)) is not int or snap[k] < 0 for k in ('frames', 'bytes')):
                return jsonify(error='frames/bytes must be nonnegative integers'), 400
            if snap.get('frame') is not None and not isinstance(snap['frame'], dict):
                return jsonify(error='frame must be an object'), 400
            state.update(producer_seen_at=time.time())
            state.update(**{k: snap[k] for k in ('status', 'error', 'frames', 'bytes', 'last_frame_at', 'mount', 'frame') if k in snap})
        else:
            distances = snap.get('distances_mm')
            if not isinstance(distances, list) or len(distances) not in (16, 64) or any(v is not None and (type(v) not in (int, float)) for v in distances):
                return jsonify(error='ToF distances must contain 16 or 64 numbers'), 400
            for field in ('raw_distances_mm', 'target_status', 'targets'):
                value = snap.get(field)
                if value is not None and (not isinstance(value, list) or len(value) != len(distances) or any(v is not None and type(v) not in (int, float) for v in value)):
                    return jsonify(error=f'invalid {field}'), 400
            snap['received_at_unix'] = time.time()
            with state.lock:
                state.data['tof'][kind] = snap
        return jsonify(ok=True)

    @app.get('/api/operations')
    def operations():
        # Read-only inspection of clinical servers. Never call report generation or calibration.
        bed_id = request.args.get('bed_id', 'bed_01')[:100]
        target = request.args.get('target', 'room_01')[:100]
        if bed_id != 'bed_01':
            return jsonify(bed_id=bed_id,target=target,tof=None,mmwave=None,ai={'runs':[]},
                           errors={'scope':{'error_type':'live_patient_only',
                                            'message':'실측 진단은 실제 센서 연결 환자 bed_01 전용입니다. 예시 EMR은 별도 화면에서 확인하세요.'}})
        endpoints = {
            'tof': 'http://127.0.0.1:5001/api/diagnostics/tof?' + urllib.parse.urlencode({'bed_id': bed_id}),
            'mmwave': 'http://127.0.0.1:5002/api/diagnostics/mmwave?' + urllib.parse.urlencode({'target': target, 'bed_id': bed_id}),
            'ai': 'http://127.0.0.1:5001/api/diagnostics/reports?' + urllib.parse.urlencode({'bed_id': bed_id}),
        }
        def read(item):
            kind, url = item
            try:
                with urllib.request.urlopen(url, timeout=1.5) as response:
                    return kind, json.load(response), None
            except Exception as exc:
                return kind, None, {'error_type': type(exc).__name__, 'message': '운영 서버 관측 API를 조회하지 못했습니다.'}
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(read, endpoints.items()))
        out = {'bed_id': bed_id, 'target': target, 'errors': {}, 'storage': 'read-only; no diagnostic persistence'}
        for kind, value, error in results:
            out[kind] = value
            if error:
                out['errors'][kind] = error
        return jsonify(out)

    @app.get('/api/snapshot')
    def snapshot():
        return jsonify(state.snapshot())

    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', help='standalone mode only: exclusively read USB; omit for dual-dashboard relay')
    ap.add_argument('--cli')
    ap.add_argument('--cfg')
    ap.add_argument('--height', type=float, default=2.5)
    ap.add_argument('--tilt', type=float, default=50.0)
    ap.add_argument('--min-snr', type=float, default=0.0)
    ap.add_argument('--port', type=int, default=5012)
    args = ap.parse_args()
    if args.cfg and not (args.cli and args.data):
        ap.error('--cfg requires --cli')
    state = LiveState(args.height, args.tilt)
    if args.data:
        threading.Thread(target=read_sensor, args=(args, state), daemon=True).start()
    else:
        state.update(status='waiting')
    create_app(state).run(host='0.0.0.0', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
