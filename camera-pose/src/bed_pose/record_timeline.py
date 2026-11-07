"""Bounded summaries of stored decisions, never a classifier or duration estimate."""
import math

VERSION = 'record-timeline-0.1'
LABELS = ('supine', 'side', 'edge_sitting')
PREDICTION_LABELS = LABELS + ('sitting', 'prone', 'lying', 'standing', 'other')
STATES = PREDICTION_LABELS + ('held', 'inactive', 'unrecorded', 'failed')


def time_value(row, manifest, basis):
    if basis == 'receive':
        return int(row['received_ns']) - int(manifest['started_ns'])
    if basis == 'media':
        return int(row['source_media']['relative_time_ns'])
    raise ValueError('지원하지 않는 시간 기준입니다.')


def posture_state(row):
    if row['processing_status'] == 'failed':
        return 'failed'
    assessment = row.get('posture_assessment')
    if assessment is None:
        return 'unrecorded'
    if assessment.get('label') in PREDICTION_LABELS:
        return assessment['label']
    return 'held' if assessment.get('status') == 'held' else 'inactive'


def count_reason(counts, reason):
    # Keep even unusual legacy records bounded; disclose grouped remainder.
    key = reason if reason in counts or len(counts) < 63 else '__other__'
    counts[key] = counts.get(key, 0) + 1


def top_reasons(counts):
    return [{'reason': key, 'frames': count} for key, count in
            sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]]


def new_bucket():
    return {'frame_count': 0, 'first_frame_id': None, 'last_frame_id': None,
            'first_held_frame_id': None, 'first_conflict_frame_id': None,
            'start_ns': None, 'end_ns': None,
            'postures': dict.fromkeys(STATES, 0), 'hold_reasons': {},
            'silhouette': dict.fromkeys(('consistent', 'conflict', 'unavailable', 'unrecorded'), 0),
            'scores': {name: {'count': 0, 'min': None, 'max': None, 'sum': 0.,
                              'missing_count': 0, 'ineligible_count': 0} for name in LABELS}}


def summarize(rows, manifest, basis='receive', bins=240):
    """rows() is a fresh iterator each pass; do not retain raw joint/video rows."""
    if type(bins) is not int or not 1 <= bins <= 600:
        raise ValueError('시간축 구간 수는 1~600이어야 합니다.')
    if basis not in ('receive', 'media') or basis == 'media' and manifest['source_profile']['kind'] != 'video_file':
        raise ValueError('이 기록에서 지원하지 않는 시간 기준입니다.')
    total = 0
    start = end = None
    for row in rows():
        stamp = time_value(row, manifest, basis)
        start = stamp if start is None else min(start, stamp)
        end = stamp if end is None else max(end, stamp)
        total += 1
    buckets = [new_bucket() for _ in range(1 if total and start == end else min(bins, total))]
    totals = new_bucket()
    for row in rows():
        stamp = time_value(row, manifest, basis)
        index = 0 if end == start else min(len(buckets)-1, (stamp-start)*len(buckets)//(end-start))
        state = posture_state(row)
        assessment = row.get('posture_assessment') or {}
        decision = assessment.get('rule_features', {}).get('score_decision')
        visual = (row.get('silhouette_assessment') or {}).get('status', 'unrecorded')
        if visual not in totals['silhouette']:
            visual = 'unavailable'
        for bucket in (buckets[index], totals):
            bucket['frame_count'] += 1
            if bucket['first_frame_id'] is None:
                bucket['first_frame_id'] = row['frame_id']
            bucket['last_frame_id'] = row['frame_id']
            bucket['start_ns'] = str(stamp) if bucket['start_ns'] is None else str(min(int(bucket['start_ns']), stamp))
            bucket['end_ns'] = str(stamp) if bucket['end_ns'] is None else str(max(int(bucket['end_ns']), stamp))
            bucket['postures'][state] += 1
            bucket['silhouette'][visual] += 1
            if state == 'held':
                if bucket['first_held_frame_id'] is None:
                    bucket['first_held_frame_id'] = row['frame_id']
                for reason in set(assessment.get('reasons', [])):
                    count_reason(bucket['hold_reasons'], reason)
            if visual == 'conflict' and bucket['first_conflict_frame_id'] is None:
                bucket['first_conflict_frame_id'] = row['frame_id']
            for name, score in bucket['scores'].items():
                value = (decision or {}).get('scores', {}).get(name)
                if decision is None:
                    score['missing_count'] += 1
                elif type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                    score['ineligible_count'] += 1
                else:
                    score['count'] += 1
                    score['sum'] += value
                    score['min'] = value if score['min'] is None else min(score['min'], value)
                    score['max'] = value if score['max'] is None else max(score['max'], value)
    for bucket in buckets + [totals]:
        bucket['hold_reasons'] = top_reasons(bucket['hold_reasons'])
        bucket['focus_frame_id'] = next((bucket[key] for key in
            ('first_held_frame_id', 'first_conflict_frame_id', 'first_frame_id') if bucket[key] is not None), None)
        for score in bucket['scores'].values():
            score['mean'] = score.pop('sum') / score['count'] if score['count'] else None
            score.pop('sum', None)
    if totals['frame_count'] != total:
        raise ValueError('시간축을 읽는 동안 기록이 변경되었습니다.')
    return {'version': VERSION, 'time_basis': basis, 'start_ns': None if start is None else str(start),
            'end_ns': None if end is None else str(end), 'total_frames': total,
            'requested_bins': bins, 'buckets': buckets, 'totals': totals,
            'semantics': 'stored_frame_counts_not_duration_or_accuracy',
            'scores': 'per_bin_mean_min_max_not_probability_no_interpolation'}
