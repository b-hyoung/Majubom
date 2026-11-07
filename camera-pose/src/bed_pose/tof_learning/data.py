"""Strict 8x8 sensor samples, explicit label provenance and leakage-safe sequences."""
from collections import defaultdict
import hashlib,json,math
from pathlib import Path
import numpy as np

LABELS=('supine','prone','left_side','right_side','sitting','edge_sitting')
ALIASES={'side_left':'left_side','side_right':'right_side','edge_sit':'edge_sitting'}


def label(value):
    value=ALIASES.get(value,value)
    if value not in LABELS:raise ValueError(f'Unsupported six-posture label: {value}')
    return value


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(path)


def sensor_frame(record,received_unix):
    if not isinstance(record,dict):raise ValueError('invalid_frame_object')
    if record.get('type') not in (None,'frame') or record.get('kind') not in ('tof1','tof2'):
        raise ValueError('not_tof_frame')
    p=record.get('payload',record)
    if not isinstance(p,dict):raise ValueError('invalid_frame_payload')
    dist=p.get('distances_mm');status=p.get('target_status');targets=p.get('targets')
    if not isinstance(dist,list) or len(dist)!=64:raise ValueError('requires_real_8x8_64_zones')
    for a in (status,targets):
        if a is not None and (not isinstance(a,list) or len(a)!=64):raise ValueError('invalid_status_length')
        if a is not None and any(type(v) is not int or not 0<=v<=255 for v in a):raise ValueError('invalid_status_value')
    valid=[type(d) in (int,float) and math.isfinite(d) and 0<d<=4000
           and (status is None or type(status[i]) is int and status[i]==5)
           and (targets is None or type(targets[i]) in (int,float) and math.isfinite(targets[i]) and targets[i]>0) for i,d in enumerate(dist)]
    frame_id=record.get('seq',p.get('frame_id',p.get('timestamp',record.get('received_at',record.get('pi_rx_unix')))))
    if frame_id is None:raise ValueError('sensor_frame_identity_missing')
    if not math.isfinite(received_unix):raise ValueError('invalid_time')
    return dict(sensor=record['kind'],frame_id=str(frame_id),laptop_rx_unix=received_unix,
                pi_rx_unix=record.get('pi_rx_unix'),distances_mm=[float(d) if ok else -1. for d,ok in zip(dist,valid)],
                valid_mask=valid,valid_fraction=sum(valid)/64,targets=targets,target_status=status,
                resolution='8x8',mount=record.get('mount'))


def camera_eligibility(camera,policy):
    p=camera.get('posture_3d') or {}
    if p.get('status')!='stable' or p.get('label') not in LABELS:return 'posture_not_stable'
    if p.get('evidence_level')!='full' or p.get('warnings'):return 'partial_or_conflicting_evidence'
    stable=p.get('stable_seconds')
    if type(stable) not in (int,float) or not math.isfinite(stable) or stable<policy['min_stable_seconds']:return 'stable_duration_short'
    q=p.get('quality_score')
    if type(q) not in (int,float) or not math.isfinite(q) or q<policy['min_joint_quality']:return 'joint_quality_low'
    if policy['label_policy']=='verified' and not (p.get('accuracy_verified') is True and p.get('eligible_for_recording') is True):
        return 'camera_accuracy_not_verified'
    timing=camera.get('learning_observation') or {}
    age=timing.get('age_ms')
    if type(age) not in (int,float) or not math.isfinite(age) or age<0 or age>policy['max_camera_age_ms']:return 'camera_observation_stale'
    if not timing.get('observation_id') or type(timing.get('observed_unix')) not in (int,float) or not math.isfinite(timing['observed_unix']):return 'camera_timing_missing'
    return None


def joined_sample(a,b,camera,policy,subject,session):
    reason=camera_eligibility(camera,policy)
    if reason:raise ValueError(reason)
    if min(a['valid_fraction'],b['valid_fraction'])<policy['min_valid_fraction']:raise ValueError('tof_valid_fraction_low')
    if abs(a['laptop_rx_unix']-b['laptop_rx_unix'])>policy['max_sensor_skew_s']:raise ValueError('tof_pair_skew')
    observed=camera['learning_observation']['observed_unix'];stamp=(a['laptop_rx_unix']+b['laptop_rx_unix'])/2
    skew=max(abs(a['laptop_rx_unix']-observed),abs(b['laptop_rx_unix']-observed))
    if skew>policy['max_camera_skew_s']:raise ValueError('camera_tof_skew')
    stable=camera['posture_3d']['stable_seconds']
    if min(a['laptop_rx_unix'],b['laptop_rx_unix'])<observed-stable:raise ValueError('tof_before_stable_posture')
    p=camera['posture_3d'];identity=f'{session}|{a["frame_id"]}|{a["pi_rx_unix"]}|{b["frame_id"]}|{b["pi_rx_unix"]}'
    return dict(version='camera-tof-1',sample_id=hashlib.sha256(identity.encode()).hexdigest(),
        subject_id=subject,session_id=session,timestamp_unix=stamp,sensors={'tof1':a,'tof2':b},
        camera_observation=camera['learning_observation'],camera_prediction=p,
        label=p['label'],label_source='camera_verified_prediction' if policy['label_policy']=='verified' else 'camera_pseudo',
        ground_truth=None,review_required=True,policy=policy,
        synchronization=dict(basis='laptop_receive_wall_clock_not_sensor_exposure',max_skew_s=skew,
            network_latency_compensated=False,pi_times_preserved=True))


def tensor_sample(row):
    result=[];masks=[]
    for sid in ('tof1','tof2'):
        s=row['sensors'][sid];d=np.array(s['distances_mm'],np.float32);mask=np.array(s['valid_mask'],bool)
        if d.shape!=(64,) or mask.shape!=(64,) or not np.isfinite(d).all():raise ValueError('invalid_8x8_sample')
        result.append(np.where(mask,np.clip(d,0,4000)/4000,0).reshape(8,8));masks.append(mask.astype(np.float32).reshape(8,8))
    return np.stack(result+masks)


def load_rows(paths,reviews=None):
    rows=[];seen=set();review_map={}
    if reviews:
        for line in Path(reviews).read_text(encoding='utf-8-sig').splitlines():
            r=json.loads(line)
            if r.get('reviewer') and r.get('confirmed') is True:
                if r['sample_id'] in review_map:raise ValueError('duplicate_review')
                review_map[r['sample_id']]=r
    for path in paths:
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            r=json.loads(line)
            if r.get('version')!='camera-tof-1':raise ValueError('Use camera-tof-1 paired dataset, not legacy 4x4 or planned-label files')
            if r['sample_id'] in seen:raise ValueError('duplicate_sample_id')
            if type(r.get('timestamp_unix')) not in (int,float) or not math.isfinite(r['timestamp_unix']):raise ValueError('invalid_sample_time')
            if not r.get('subject_id') or not r.get('session_id'):raise ValueError('subject_session_required')
            seen.add(r['sample_id']);r=dict(r);review=review_map.get(r['sample_id'])
            r['reviewed']=review is not None;r['training_label']=label(review['label'] if review else r['label']);tensor_sample(r)
            rows.append(r)
    return rows


def make_windows(rows,split,window=16,stride=4,max_gap=.3,allow_pseudo_train=False):
    if type(window) is not int or window<1 or type(stride) is not int or stride<1:raise ValueError('positive_window_stride_required')
    key=split.get('group_by','subject_id')
    if key not in ('subject_id','session_id'):raise ValueError('group_by_must_be_subject_or_session')
    mapping={}
    for part in ('train','validation','test'):
        if not split.get(part):raise ValueError(f'empty_{part}_groups')
        for group in split[part]:
            if group in mapping:raise ValueError('group_split_overlap')
            mapping[group]=part
    grouped=defaultdict(list)
    for r in rows:
        group=r[key]
        if group not in mapping:continue
        part=mapping[group]
        if not r['reviewed'] and not(part=='train' and allow_pseudo_train):continue
        grouped[(part,r['subject_id'],r['session_id'])].append(r)
    outputs={part:[] for part in ('train','validation','test')}
    for (part,subject,session),values in grouped.items():
        values.sort(key=lambda r:r['timestamp_unix']);segments=[];segment=[]
        for r in values:
            if segment and (r['training_label']!=segment[-1]['training_label'] or not 0<r['timestamp_unix']-segment[-1]['timestamp_unix']<=max_gap):
                segments.append(segment);segment=[]
            segment.append(r)
        if segment:segments.append(segment)
        for segment in segments:
            for i in range(0,len(segment)-window+1,stride):
                batch=segment[i:i+window];outputs[part].append(dict(x=np.stack([tensor_sample(r) for r in batch]),
                    y=LABELS.index(batch[-1]['training_label']),sample_ids=[r['sample_id'] for r in batch],group=subject if key=='subject_id' else session))
    for part,examples in outputs.items():
        if not examples:raise ValueError(f'No eligible continuous windows for {part}; collect/review data first')
        present={e['y'] for e in examples}
        if present!=set(range(6)):raise ValueError(f'{part} needs all six classes; present={[LABELS[i] for i in sorted(present)]}')
    return outputs
