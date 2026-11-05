"""dataset_v3 수신 시각 짝·운영자 선언 라벨·센서별 3채널 및 분할 계약."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv
import hashlib
import json
import math
import numpy as np

LABELS = ('empty', 'supine', 'side_left', 'side_right', 'sitting', 'edge_sit')
CAMERA_MAP = {'left_side': 'side_left', 'right_side': 'side_right', 'edge_sitting': 'edge_sit'}
KST = timezone(timedelta(hours=9))


def timestamp(value):
    if type(value) in (int, float):
        result = float(value)
    else:
        parsed = datetime.fromisoformat(value)
        result = (parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)).timestamp()
    if not math.isfinite(result): raise ValueError('유한한 수신 시각이 필요합니다.')
    return result


def distances(values):
    if not isinstance(values, list) or len(values) != 64:
        raise ValueError('8×8 거리 64개가 필요합니다.')
    return np.asarray([float(v) if type(v) in (int, float) and math.isfinite(v) and v > 0 else -1.
                       for v in values], np.float32)


def load_baseline(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    return np.stack([distances(value[s]) for s in ('tof1', 'tof2')])


def channels(raw, baseline):
    raw = np.asarray(raw, np.float32); baseline = np.asarray(baseline, np.float32)
    if raw.shape != (2,64) or baseline.shape != (2,64): raise ValueError('센서별 64칸 입력이 필요합니다.')
    valid = np.isfinite(raw) & (raw > 0) & (raw <= 4000)
    base_valid = np.isfinite(baseline) & (baseline > 0) & (baseline <= 4000)
    current = np.where(valid, np.clip(raw, 0, 4000)/4000, 0)
    delta = np.where(valid & base_valid, np.clip(baseline-raw, -4000, 4000)/4000, 0)
    return np.stack([current, delta, valid.astype(np.float32)], axis=1).reshape(2,3,8,8)


def orientation_verified(path):
    if not path: return False
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if (value.get('verified') is not True or value.get('left_right_definition') != 'subject_anatomical'
        or not str(value.get('evidence', '')).strip()
        or any(value.get(s, {}).get('horizontal_flip_validated') is not True for s in ('tof1','tof2'))):
        raise ValueError('두 센서의 열 방향과 인체 좌우 반전 대응을 먼저 확인해야 합니다.')
    return True


def mirror(x, y):
    y = np.asarray(y).copy(); left = LABELS.index('side_left'); right = LABELS.index('side_right')
    a = y == left; b = y == right; y[a] = right; y[b] = left
    return np.flip(x, axis=-1).copy(), y


def load_dataset(directory, baseline_path, *, synthetic=False, source_sessions=None):
    root = Path(directory).resolve(); index = root/'sessions.csv'
    with index.open(encoding='utf-8-sig', newline='') as stream: sessions = list(csv.DictReader(stream))
    metadata_path = root / 'session_metadata.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    baseline = load_baseline(baseline_path); samples = []; audit = []; seen_sessions = set()
    for meta in sessions:
        sid = meta['session']; label = meta['label']
        session_info = metadata.get(sid, {})
        collection_subject = session_info.get('collection_subject', meta['subject'])
        if label != 'empty' and collection_subject != meta['subject'] and not synthetic:
            raise ValueError('참여자와 촬영 묶음이 다릅니다.')
        if sid in seen_sessions: raise ValueError('중복 세션: '+sid)
        seen_sessions.add(sid)
        if label not in LABELS: raise ValueError('지원하지 않는 정답 라벨: '+label)
        source = (root/meta['file']).resolve()
        if not source.is_relative_to(root): raise ValueError('데이터 경로가 저장 폴더를 벗어났습니다.')
        rows = [json.loads(line) for line in source.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        if not rows: raise ValueError('빈 녹화 파일: '+meta['file'])
        parent_session = None
        if synthetic:
            parent_session = rows[0].get('source_session')
            if not source_sessions or parent_session not in source_sessions:
                raise ValueError('학습 원본 목록에 없는 합성 출처 세션: '+str(parent_session))
            parent = source_sessions[parent_session]
            if parent.get('synthetic') or parent['label'] != label or parent['rep'] != int(meta['rep']):
                raise ValueError('합성 데이터의 출처·라벨·회차가 원본과 다릅니다.')
            collection_subject = parent['collection_subject']
        for row in rows:
            if synthetic:
                if (row.get('synthetic') is not True or row.get('source_session') != parent_session
                        or not isinstance(row.get('synth'), dict) or not row['synth']):
                    raise ValueError('합성 데이터의 출처 또는 변환 기록이 누락됐습니다.')
            elif row.get('synthetic'):
                raise ValueError('합성 데이터는 --synthetic-dataset으로 별도 지정해야 합니다.')
            for key in ('session','label','subject','cond'):
                if row.get(key) != meta.get(key): raise ValueError('CSV·원시 메타데이터 불일치: '+key)
            if int(row['rep']) != int(meta['rep']): raise ValueError('CSV·회차 불일치')
        stamps = [timestamp(r['t']) for r in rows]; start, end = min(stamps)+1, max(stamps)-1
        tracks = {}
        for sensor in ('tof1','tof2'):
            unique = {}
            for row in rows:
                if row['sensor'] != sensor: continue
                stamp = timestamp(row['t']); d = distances(row['distances_mm'])
                if stamp in unique and not np.array_equal(d, unique[stamp]):
                    raise ValueError('동일 수신 시각에 서로 다른 프레임이 있습니다.')
                unique[stamp] = d
            tracks[sensor] = sorted(unique.items())
        a, b = tracks['tof1'], tracks['tof2']; j = 0; used = set(); before = len(samples)
        for ta, d1 in a:
            if not start <= ta <= end or not b: continue
            # 기존 train_posture_v2.load의 최근접·1회 사용 규칙과 같은 순서.
            while j+1 < len(b) and abs(b[j+1][0]-ta) <= abs(b[j][0]-ta): j += 1
            tb, d2 = b[j]
            if j in used or abs(tb-ta) > .25 or not start <= tb <= end: continue
            used.add(j); raw = np.stack([d1,d2]); identity = f'{sid}|{ta:.9f}|{tb:.9f}'
            samples.append(dict(sample_id=hashlib.sha256(identity.encode()).hexdigest(), raw=raw,
                x=channels(raw, baseline), y=LABELS.index(label), label=label, subject=meta['subject'],
                rep=int(meta['rep']), session=sid, cond=meta['cond'], pi_unix=(ta+tb)/2,
                collection_subject=collection_subject,
                synthetic=synthetic, source_session=parent_session,
                pair_skew_s=abs(ta-tb), label_source='synthetic_from_operator_session' if synthetic else 'operator_spoken_session_declaration'))
        audit.append(dict(session=sid, label=label, subject=meta['subject'], collection_subject=collection_subject,
                          pairs=len(samples)-before, rep=int(meta['rep']), synthetic=synthetic,
                          source_session=parent_session, synth=rows[0].get('synth') if synthetic else None,
                          trim_start_unix=start, trim_end_unix=end, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    if not samples: raise ValueError('앞뒤 1초 제외·250ms 짝 조건을 통과한 실측 표본이 없습니다.')
    if not synthetic and {s['label'] for s in samples} != set(LABELS): raise ValueError('empty 포함 여섯 자세 녹화가 모두 필요합니다.')
    return samples, baseline, audit


def with_training_derivatives(samples, real_indices):
    """현재 학습 세션에서 파생된 합성 표본만 추가한다. 시험·검증 출처는 제외한다."""
    if any(samples[i].get('synthetic') for i in real_indices):
        raise ValueError('원본 학습 인덱스에 합성 자료가 포함됐습니다.')
    sessions = {samples[i]['session'] for i in real_indices}
    derived = [i for i, sample in enumerate(samples)
               if sample.get('synthetic') and sample.get('source_session') in sessions]
    return np.concatenate([np.asarray(real_indices, dtype=int), np.asarray(derived, dtype=int)])


def outer_folds(samples):
    if any(sample.get('synthetic') for sample in samples):
        raise ValueError('외부 평가 분할에는 실제 표본만 전달해야 합니다.')
    people = sorted({s['subject'] for s in samples if s['subject'] not in ('없음','none','empty')})
    mode = 'subject' if len(people) >= 2 else 'rep'
    if mode == 'subject':
        mapping = {p:i for i,p in enumerate(people)}
        groups = np.array([mapping[s.get('collection_subject', s['subject'])]
                           if s.get('collection_subject', s['subject']) in mapping
                           else (s['rep']-1)%len(people) for s in samples])
    else: groups = np.array([s['rep'] for s in samples])
    folds = []
    for group in sorted(set(groups)):
        train = np.flatnonzero(groups != group); test = np.flatnonzero(groups == group)
        if set(samples[i]['y'] for i in train) != set(range(6)) or set(samples[i]['y'] for i in test) != set(range(6)):
            raise ValueError('각 학습·시험 묶음에 여섯 클래스가 필요합니다. 회차·참가자 녹화를 확인하세요.')
        folds.append((people[group] if mode == 'subject' else str(group), train, test))
    if len(folds) < 2: raise ValueError('분리 가능한 회차 또는 참가자가 부족합니다.')
    return mode, folds


def validation_split(samples, indices):
    if any(samples[i].get('synthetic') for i in indices):
        raise ValueError('내부 검증 분할에는 실제 표본만 전달해야 합니다.')
    # 시험 묶음을 넘겨받지 않는다. 남은 사람 또는 남은 회차 하나로 epoch만 선택.
    for key in ('subject','rep'):
        groups = sorted({samples[i][key] for i in indices}, key=str)
        if len(groups) < 2: continue
        for held in groups:
            train = np.array([i for i in indices if samples[i][key] != held], dtype=int)
            valid = np.array([i for i in indices if samples[i][key] == held], dtype=int)
            if all({samples[i]['y'] for i in part} == set(range(6)) for part in (train, valid)):
                return train, valid
    raise ValueError('학습 묶음 안의 독립 검증 회차가 부족합니다. 클래스별 3회 이상 녹화하세요.')


def camera_accuracy(samples, camera_paths):
    observations = {}; aliases = CAMERA_MAP
    for path in camera_paths:
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            row = json.loads(line); comparison = row.get('comparison') or {}
            stamp = comparison.get('observed_unix'); prediction = row.get('camera_posture') or {}
            if type(stamp) not in (int,float) or not math.isfinite(stamp): continue
            key = prediction.get('observation_id') or str(stamp)
            observations[key] = (stamp, aliases.get(prediction.get('label'), prediction.get('label')),
                                 comparison.get('views') or [], prediction.get('views') or [])
    stamps = np.array([s['pi_unix'] for s in samples]); labels = np.array([s['y'] for s in samples])
    cm = np.zeros((6,7), int); used_samples = set(); camera_used = set()
    # カメラ比較は Pi 時刻に変換済みの比較記録のみ。offset がない旧資料は不使用。
    for path in camera_paths:
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            row = json.loads(line); obs = row.get('comparison') or {}; estimate = row.get('clock_offset_estimate_s')
            t = obs.get('observed_unix'); prediction = row.get('camera_posture') or {}; key = prediction.get('observation_id')
            if type(t) not in (int,float) or type(estimate) not in (int,float) or not key or key in camera_used: continue
            delta = np.abs(stamps-(t-estimate)); index = int(delta.argmin())
            if delta[index] > .25 or index in used_samples: continue
            used_samples.add(index); camera_used.add(key)
            pred = aliases.get(prediction.get('label'), prediction.get('label'))
            cm[labels[index], LABELS.index(pred) if pred in LABELS else 6] += 1
    total = int(cm.sum()); matched = int(np.trace(cm[:,:6])); classified = int(cm[:,:6].sum())
    return dict(status='evaluated' if total else 'not_evaluated_no_aligned_camera_observations',
        reference='operator_spoken_session_declaration',labels=list(LABELS),prediction_labels=[*LABELS,'unclassified'],
        matched_observations=total, accuracy=matched/total if total else None,
        coverage=classified/total if total else None,confusion_matrix=cm.tolist(),
        missing_tof_pairs=len(samples)-len(used_samples),unclassified_is_error=True)
