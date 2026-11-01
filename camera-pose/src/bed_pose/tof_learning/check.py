"""Read-only live integration checks. Temporary labels never enter camera or training state."""
from collections import Counter,deque
from datetime import datetime
import json,math,threading,time,urllib.request
from urllib.parse import urlsplit
import numpy as np
from .collect import DEFAULT_POLICY
from .data import ALIASES,LABELS,camera_eligibility,sensor_frame,write_json


def canonical(value):
    value=ALIASES.get(value,value)
    return value if value in (*LABELS,'empty') else None


def sensor_timing(latest,presence):
    """Compare Pi timestamps with the Pi clock; never assume the laptop clock matches."""
    try:
        now=datetime.fromisoformat(presence['server_now'])
        stamps=[datetime.fromisoformat(latest[s]['received_at']) for s in ('tof1','tof2')]
        ages=[(now-t).total_seconds() for t in stamps]
        skew=abs((stamps[0]-stamps[1]).total_seconds())
        valid=all(-.1<=a<=.5 for a in ages) and skew<=.25
        return dict(fresh=valid,age_seconds=ages,pair_skew_seconds=skew)
    except (KeyError,TypeError,ValueError):return dict(fresh=False,reason='pi_receive_timestamps_missing_or_invalid')


def compare(camera,latest,presence,temporary_label=None):
    tof=canonical(latest.get('posture_raw'))
    confidence=latest.get('posture_conf');timing=sensor_timing(latest,presence)
    tof_ok=timing['fresh'] and tof is not None and type(confidence) in (int,float) and math.isfinite(confidence) and confidence>=.6
    reason=camera_eligibility(camera,dict(DEFAULT_POLICY,label_policy='pseudo'))
    observed=canonical((camera.get('posture_3d') or {}).get('label')) if reason is None else None
    usable=tof_ok and observed is not None
    result=dict(tof_label=tof,tof_confidence=confidence,tof_timing=timing,
        camera_label=observed,camera_exclusion=reason,
        independent_comparison=dict(status='compared' if usable else 'held',match=observed==tof if usable else None,
            accuracy_verified=False,semantics='prediction_agreement_not_ground_truth'),
        baseline_applied=False)
    if temporary_label is not None:
        injected=canonical(temporary_label)
        if injected is None:raise ValueError('unsupported_temporary_label')
        result['temporary_input_check']=dict(injected_camera_label=injected,tof_label=tof,
            match=injected==tof if tof_ok else None,status='compared' if tof_ok else 'held',
            semantics='injected_input_wiring_check_not_camera_accuracy',accuracy_verified=False,
            camera_state_changed=False,training_sample_saved=False)
    return result


def stability(frames):
    """Still people also produce stable ranges: stability never certifies an empty bed."""
    if not frames:return dict(frames=0,stationary=False,empty_bed_verified=False,baseline_apply_allowed=False)
    spreads=[];medians=[]
    for z in range(64):
        values=[f['distances_mm'][z] for f in frames if f['valid_mask'][z]]
        if len(values)<max(3,.75*len(frames)):medians.append(-1.);continue
        medians.append(float(np.median(values)));spreads.append(float(np.percentile(values,95)-np.percentile(values,5)))
    span=frames[-1]['laptop_rx_unix']-frames[0]['laptop_rx_unix']
    stationary=len(frames)>=10 and span>=5 and len(spreads)>=48 and sum(v<=50 for v in spreads)/64>=.75
    return dict(frames=len(frames),observed_seconds=span,valid_zones=len(spreads),
        median_zone_spread_mm=float(np.median(spreads)) if spreads else None,stationary=stationary,
        candidate_medians_mm=medians,empty_bed_verified=False,baseline_apply_allowed=False,
        reason='stable_ranges_do_not_exclude_a_motionless_person')


def run(args):
    parts=urlsplit(args.camera_url)
    if parts.scheme!='http' or parts.hostname not in ('localhost','127.0.0.1') or not parts.fragment:raise ValueError('localhost camera URL with #token required')
    if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in args.pi):raise ValueError('pi must be a hostname or IPv4 address')
    if not args.pi:raise ValueError('pi host required')
    from pathlib import Path
    if Path(args.out).exists():raise ValueError('Check output already exists; preserve previous evidence')
    camera_url=f'{parts.scheme}://{parts.netloc}/api/room/live'
    pi_url=f'http://{args.pi}:5001';stream_url=f'http://{args.pi}:5012/api/stream?sensors=tof1,tof2'
    stop=threading.Event();lock=threading.Lock();frames={s:deque(maxlen=600) for s in ('tof1','tof2')};counts=Counter();errors=Counter();seen=set()
    def get(url,headers=None):
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers or {}),timeout=2) as response:
            data=json.load(response)
            if not isinstance(data,dict):raise ValueError('invalid_api_object')
            return data
    def stream():
        clock_offset=None
        try:
            with urllib.request.urlopen(stream_url,timeout=2) as response:
                while not stop.is_set():
                    line=response.readline(1_000_001)
                    if not line:break
                    if len(line)>1_000_000:raise ValueError('oversized_stream_record')
                    try:
                        record=json.loads(line)
                        if not isinstance(record,dict):raise ValueError('invalid_frame_object')
                        now=time.time()
                        if record.get('type') in ('hello','heartbeat'):
                            if type(record.get('pi_unix')) in (int,float) and math.isfinite(record['pi_unix']):
                                offset=now-record['pi_unix'];clock_offset=offset if clock_offset is None else min(clock_offset,offset)
                            continue
                        frame=sensor_frame(record,now)
                        if clock_offset is None or type(frame['pi_rx_unix']) not in (int,float) or not math.isfinite(frame['pi_rx_unix']) or not -.1<=now-frame['pi_rx_unix']-clock_offset<=.2:
                            with lock:counts['stream_clock_or_backlog_rejected']+=1
                            continue
                        identity=(frame['sensor'],frame['frame_id'],frame['pi_rx_unix'])
                        with lock:
                            if identity in seen:counts['duplicate_frame']+=1;continue
                            seen.add(identity);frames[frame['sensor']].append(frame);counts[frame['sensor']]+=1
                    except (ValueError,TypeError):
                        with lock:counts['invalid_stream_record']+=1
        except (OSError,ValueError) as e:
            with lock:errors['stream:'+type(e).__name__]+=1
    worker=threading.Thread(target=stream,daemon=True);worker.start();started=time.monotonic();samples=[];last_identity=None
    try:
        while time.monotonic()-started<args.seconds:
            try:
                latest=get(pi_url+'/tof/latest');presence=get(pi_url+'/tof/presence')
            except (OSError,ValueError) as e:errors['tof_api:'+type(e).__name__]+=1;stop.wait(.5);continue
            try:camera=get(camera_url,{'X-BedPose-Token':parts.fragment})
            except (OSError,ValueError) as e:errors['camera_api:'+type(e).__name__]+=1;camera={}
            identity=tuple((latest.get(s) or {}).get('received_at') for s in ('tof1','tof2'))
            if identity!=last_identity:
                row=compare(camera,latest,presence,args.temporary_camera_label);row['laptop_observed_unix']=time.time()
                samples.append(row);last_identity=identity
            stop.wait(.25)
    finally:stop.set();worker.join(3)
    summaries={s:stability(list(f)) for s,f in frames.items()}
    comparisons=[r['independent_comparison'] for r in samples if r['independent_comparison']['status']=='compared']
    report=dict(version='camera-tof-connection-check-1',pi_host=args.pi,stream_counts=dict(counts),errors=dict(errors),
        both_sensors_streaming=all(summaries[s]['frames']>=2 for s in summaries),sensors=summaries,
        observations=samples,independent_comparable_observations=len(comparisons),independent_matches=sum(r['match'] for r in comparisons),
        actual_posture_accuracy_measured=False,baseline_applied=False,training_samples_saved=0,
        checked_unix=time.time(),camera_token_saved=False)
    write_json(args.out,report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('sensors','observations')},ensure_ascii=False),flush=True)
    return report
