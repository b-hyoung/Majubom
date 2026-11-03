"""원시 스트림·카메라 실패까지 보존하는 새 침상 비교 수집. 정답 생성 없음."""
from collections import Counter, deque
from pathlib import Path
from urllib.parse import urlsplit
import json
import math
import threading
import time
import urllib.request

from .data import write_json


def camera_summary(room, received):
    # 명시적 허용 필드만 저장한다. JPEG·토큰·원본 영상·설정 경로는 제외한다.
    return dict(version='bed-session-camera-1', laptop_rx_unix=received,
                comparison=room.get('camera_comparison_observation'),
                learning_observation=room.get('learning_observation'),
                camera_posture=room.get('camera_posture'), posture_3d=room.get('posture_3d'),
                label_source='camera_pseudo_not_ground_truth')


def collect_all(args):
    if args.label_policy != 'pseudo':
        raise ValueError('전체 비교 수집은 --label-policy pseudo를 명시해야 합니다.')
    source = args.camera_url
    if not source:
        source = json.loads(Path(args.camera_connection).read_text(encoding='utf-8'))['url']
    parts = urlsplit(source)
    if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost') or not parts.fragment:
        raise ValueError('현재 노트북 작업실 주소와 인증이 필요합니다.')
    stream = urlsplit(args.stream_url)
    if stream.scheme not in ('http', 'https') or not stream.hostname or stream.username or stream.fragment:
        raise ValueError('인증 정보가 포함되지 않은 센서 스트림 주소가 필요합니다.')
    root = Path(args.out) / args.session
    root.mkdir(parents=True, exist_ok=False)
    write_json(root/'manifest.json', dict(version='bed-session-capture-1', subject_id=args.subject,
        session_id=args.session, started_unix=time.time(), ground_truth=None,
        camera_label_policy='pseudo', camera_token_saved=False, camera_missing_frames_retained=True,
        synchronization='Pi 수신 시각 및 heartbeat 오프셋; 하드웨어 노출 동기화 아님'))
    stop = threading.Event(); lock = threading.Lock(); counts = Counter(); cameras = deque(maxlen=120)
    began = time.monotonic(); clock_offset = None

    def emit(handle, row):
        handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n'); handle.flush()

    def poll():
        endpoint = f'{parts.scheme}://{parts.netloc}/api/room/live'
        with (root/'camera.jsonl').open('x', encoding='utf-8') as output:
            while not stop.is_set():
                try:
                    request = urllib.request.Request(endpoint, headers={'X-BedPose-Token': parts.fragment})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        room = json.load(response)
                    summary = camera_summary(room, time.time())
                    summary['clock_offset_estimate_s'] = clock_offset
                    emit(output, summary)
                    with lock:
                        cameras.append(summary); counts['camera_polls'] += 1
                except (OSError, ValueError, TypeError):
                    with lock: counts['camera_errors'] += 1
                stop.wait(.05)

    worker = threading.Thread(target=poll, daemon=True); worker.start()
    last_report = 0.
    print('원시 ToF·카메라 비교 수집 시작: 미검출도 저장하며 정답을 생성하지 않습니다.', flush=True)
    try:
        with (root/'tof.jsonl').open('x', encoding='utf-8') as raw, (root/'pairs.jsonl').open('x', encoding='utf-8') as paired:
            pending = {'tof1': deque(maxlen=100), 'tof2': deque(maxlen=100)}
            while not stop.is_set() and (args.seconds == 0 or time.monotonic()-began < args.seconds):
                if (root/'STOP').exists(): break
                try:
                    with urllib.request.urlopen(args.stream_url, timeout=3) as response:
                        pending = {'tof1': deque(maxlen=100), 'tof2': deque(maxlen=100)}
                        clock_offset = None
                        while not stop.is_set():
                            if (root/'STOP').exists() or args.seconds and time.monotonic()-began >= args.seconds: break
                            line = response.readline(1_000_001)
                            if not line: break
                            if len(line) > 1_000_000:
                                counts['oversize'] += 1; continue
                            try: record = json.loads(line)
                            except ValueError:
                                counts['malformed'] += 1; continue
                            if not isinstance(record, dict): continue
                            now = time.time()
                            if record.get('type') in ('hello', 'heartbeat'):
                                stamp = record.get('pi_unix')
                                if type(stamp) in (int, float) and math.isfinite(stamp):
                                    offset = now-stamp
                                    clock_offset = offset if clock_offset is None else min(clock_offset, offset)
                                emit(raw, dict(type=record['type'], laptop_rx_unix=now, pi_unix=stamp,
                                               clock_offset_estimate_s=clock_offset, dropped=record.get('dropped')))
                                continue
                            sid = record.get('kind'); payload = record.get('payload')
                            if sid not in pending or not isinstance(payload, dict): continue
                            distances = payload.get('distances_mm'); stamp = record.get('pi_rx_unix')
                            if (not isinstance(distances, list) or len(distances) != 64 or
                                type(stamp) not in (int, float) or not math.isfinite(stamp)):
                                counts['invalid_tof'] += 1; continue
                            # Pi 수신 시각과 원시 거리를 유지하며 카메라 판정으로 제외하지 않는다.
                            row = dict(sensor=sid, pi_rx_unix=stamp, laptop_rx_unix=now,
                                sensor_received_at=payload.get('received_at'), distances_mm=distances,
                                targets=payload.get('targets'), target_status=payload.get('target_status'),
                                clock_offset_estimate_s=clock_offset)
                            emit(raw, row); counts[sid] += 1; pending[sid].append(row)
                            while all(pending.values()):
                                a, b = pending['tof1'][0], pending['tof2'][0]
                                delta = a['pi_rx_unix']-b['pi_rx_unix']
                                if abs(delta) > .25:
                                    pending['tof1' if delta < 0 else 'tof2'].popleft(); counts['unpaired'] += 1; continue
                                pending['tof1'].popleft(); pending['tof2'].popleft()
                                stamp_pi = (a['pi_rx_unix']+b['pi_rx_unix'])/2
                                aligned = stamp_pi+clock_offset if clock_offset is not None else None
                                with lock: options = list(cameras)
                                def camera_time(c):
                                    return (c.get('comparison') or {}).get('observed_unix')
                                options = [c for c in options if type(camera_time(c)) in (int, float)]
                                camera = min(options, key=lambda c: abs(camera_time(c)-aligned)) if options and aligned is not None else None
                                skew = abs(camera_time(camera)-aligned) if camera else None
                                emit(paired, dict(version='bed-session-pair-1', subject_id=args.subject,
                                    session_id=args.session, pi_unix=stamp_pi, sensors=[a, b],
                                    camera=camera if skew is not None and skew <= .25 else None,
                                    camera_skew_s=skew, ground_truth=None, label_source='unlabeled_capture',
                                    synchronization='heartbeat_min_offset_not_exposure'))
                                counts['pairs'] += 1
                                if camera is None or skew > .25: counts['camera_unmatched'] += 1
                            if time.monotonic()-last_report >= 5:
                                last_report = time.monotonic()
                                with lock: snapshot = dict(counts)
                                write_json(root/'status.json', dict(counts=snapshot, updated_unix=now, running=True))
                                print('수집 현황: '+json.dumps(snapshot, ensure_ascii=False), flush=True)
                except (OSError, ValueError, TypeError):
                    counts['stream_errors'] += 1; stop.wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set(); worker.join(4)
        write_json(root/'status.json', dict(counts=dict(counts), stopped_unix=time.time(), running=False))
    print('수집 종료: '+json.dumps(dict(counts), ensure_ascii=False), flush=True)
    if min(counts['tof1'], counts['tof2']) == 0:
        raise RuntimeError('두 센서의 실측 프레임을 확보하지 못했습니다.')
