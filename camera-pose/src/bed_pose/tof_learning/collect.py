"""Read-only Majubom NDJSON subscriber and confidence-gated camera/ToF join."""
from collections import Counter,deque
import json,math,threading,time,urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from .data import sensor_frame,joined_sample,write_json

DEFAULT_POLICY=dict(label_policy='verified',min_joint_quality=.8,min_stable_seconds=1.5,
    min_valid_fraction=.75,max_sensor_skew_s=.1,max_camera_skew_s=.15,max_camera_age_ms=300)


def collect(args):
    if getattr(args, 'capture_all', False):
        from .session_collect import collect_all
        return collect_all(args)
    policy=dict(DEFAULT_POLICY,label_policy=args.label_policy)
    parts=urlsplit(args.camera_url)
    if parts.scheme!='http' or parts.hostname not in ('127.0.0.1','localhost') or not parts.fragment:
        raise ValueError('camera-url must be this laptop localhost monitor URL including #token')
    camera_url=f'{parts.scheme}://{parts.netloc}/api/room/live'
    stream=urlsplit(args.stream_url)
    if stream.scheme not in ('http','https') or not stream.hostname or stream.username:raise ValueError('invalid_stream_url')
    directory=Path(args.out)/args.session
    directory.mkdir(parents=True,exist_ok=False)
    stop=threading.Event();lock=threading.Lock();cameras=deque(maxlen=100);counts=Counter();errors=deque(maxlen=10)
    write_json(directory/'manifest.json',dict(version='collection-1',subject_id=args.subject,session_id=args.session,
        policy=policy,label_provenance='camera_prediction_not_ground_truth',camera_token_saved=False,
        sensor_schema='Majubom :5012/api/stream tof1/tof2 payload',started_unix=time.time()))
    def poll():
        while not stop.is_set():
            try:
                request=urllib.request.Request(camera_url,headers={'X-BedPose-Token':parts.fragment})
                with urllib.request.urlopen(request,timeout=2) as response:r=json.load(response)
                if not isinstance(r,dict) or any(not isinstance(r.get(k),dict) for k in ('posture_3d','learning_observation')):raise ValueError('invalid_camera_schema')
                # Do not retain JPEGs or raw camera images in the training collector.
                keep={k:r.get(k) for k in ('posture_3d','learning_observation')}
                with lock:cameras.append(keep)
            except (OSError,ValueError) as e:
                with lock:counts['camera_connection_error']+=1
            stop.wait(.05)
    def report_progress():
        while not stop.wait(5):
            snapshot=dict(counts)
            print(json.dumps(snapshot),flush=True)
            write_json(directory/'status.json',dict(counts=snapshot,last_updated_unix=time.time(),data_is_ground_truth=False))
    worker=threading.Thread(target=poll,daemon=True);worker.start()
    reporter=threading.Thread(target=report_progress,daemon=True);reporter.start()
    pending={'tof1':deque(maxlen=100),'tof2':deque(maxlen=100)};seen=set();order=deque();started=time.monotonic();clock_offset=None
    print('Collection running. Strict verified policy can correctly save ZERO samples with current unverified calibration.',flush=True)
    try:
        with (directory/'samples.jsonl').open('x',encoding='utf-8') as output:
            while not stop.is_set() and (args.seconds<=0 or time.monotonic()-started<args.seconds):
                try:
                    with urllib.request.urlopen(args.stream_url,timeout=5) as response:
                        clock_offset=None
                        for queue in pending.values():queue.clear()
                        for line in response:
                            now=time.time()
                            if args.seconds>0 and time.monotonic()-started>=args.seconds:stop.set();break
                            if len(line)>1_000_000:counts['oversize_record']+=1;continue
                            try:record=json.loads(line)
                            except ValueError:counts['malformed_json']+=1;continue
                            if not isinstance(record,dict):counts['invalid_frame_object']+=1;continue
                            if record.get('type') in ('hello','heartbeat'):
                                if type(record.get('pi_unix')) in (int,float) and math.isfinite(record['pi_unix']):
                                    offset=now-record['pi_unix'];clock_offset=offset if clock_offset is None else min(clock_offset,offset)
                                counts['stream_heartbeat']+=1
                                dropped=record.get('dropped',0)
                                if type(dropped) is int and dropped>=0:counts['upstream_dropped_max']=max(counts['upstream_dropped_max'],dropped)
                                continue
                            try:frame=sensor_frame(record,now)
                            except (ValueError,TypeError) as e:counts[str(e)]+=1;continue
                            if clock_offset is None or type(frame['pi_rx_unix']) not in (int,float) or not math.isfinite(frame['pi_rx_unix']):
                                counts['clock_heartbeat_required']+=1;continue
                            delay=now-(frame['pi_rx_unix']+clock_offset)
                            if not -.1<=delay<=.2:counts['network_backlog_or_clock_jump']+=1;continue
                            frame['network_delay_estimate_s']=delay
                            identity=(frame['sensor'],frame['frame_id'],frame['pi_rx_unix'])
                            if identity in seen:counts['duplicate_sensor_frame']+=1;continue
                            seen.add(identity);order.append(identity)
                            if len(order)>10000:seen.discard(order.popleft())
                            counts['tof_received']+=1;pending[frame['sensor']].append(frame)
                            for queue in pending.values():
                                while queue and now-queue[0]['laptop_rx_unix']>.5:queue.popleft();counts['stale_unpaired']+=1
                            if not all(pending.values()):continue
                            a,b=min(((a,b) for a in pending['tof1'] for b in pending['tof2']),key=lambda pair:abs(pair[0]['laptop_rx_unix']-pair[1]['laptop_rx_unix']))
                            if abs(a['laptop_rx_unix']-b['laptop_rx_unix'])>policy['max_sensor_skew_s']:continue
                            # Wait briefly for the matching camera observation, then select by its receive time.
                            stop.wait(.05)
                            with lock:options=list(cameras)
                            options=[c for c in options if isinstance((c.get('learning_observation') or {}).get('observed_unix'),(int,float))]
                            camera=min(options,key=lambda c:abs(c['learning_observation']['observed_unix']-(a['laptop_rx_unix']+b['laptop_rx_unix'])/2)) if options else {}
                            for sid,selected in [('tof1',a),('tof2',b)]:
                                while pending[sid]:
                                    if pending[sid].popleft() is selected:break
                            try:r=joined_sample(a,b,camera,policy,args.subject,args.session)
                            except ValueError as e:counts['reject:'+str(e)]+=1
                            else:
                                output.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n');output.flush();counts['saved']+=1
                except (OSError,ValueError) as e:
                    counts['stream_connection_error']+=1;errors.append(type(e).__name__);stop.wait(1)
    except KeyboardInterrupt:pass
    finally:
        stop.set();worker.join(3);reporter.join(3)
        report=dict(counts=counts,errors=list(errors),stopped_unix=time.time(),data_is_ground_truth=False)
        write_json(directory/'status.json',report);print(json.dumps(report),flush=True)
