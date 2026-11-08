"""Collect stable camera/ToF conflicts and refit a local shadow RF."""
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from uuid import uuid4

import numpy as np

from .session_data import CAMERA_MAP, LABELS, distances, load_dataset


class LiveAdaptation:
    def __init__(self, directory, dataset, baseline):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal = self.directory/'camera_conflicts.jsonl'
        real, _, _ = load_dataset(dataset, baseline)
        self.real_x = np.stack([s['raw'].reshape(128) for s in real])
        self.real_y = np.array([s['y'] for s in real])
        self.rows = [json.loads(line) for line in self.journal.read_text(encoding='utf-8').splitlines()] if self.journal.exists() else []
        self.last_fit = 0
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tof-shadow-fit')
        self.pending = None
        self.candidate = None
        self.error = None
        self.last_pair = (None, None)
        self.last_view = None
        self.window = deque()
        self.cooldown = 0
        self.status = 'waiting_for_tof'

    def _fit(self, rows):
        import joblib
        from sklearn.ensemble import RandomForestClassifier

        # Limit each pseudo class to 5% of its real examples, then halve its weight.
        counts = Counter()
        real_counts = Counter(self.real_y)
        selected = []
        for row in reversed(rows):
            label = LABELS.index(row['camera_label'])
            if counts[label] < real_counts[label]//20:
                selected.append(row)
                counts[label] += 1
        x = np.concatenate((self.real_x, np.asarray([r['distances'] for r in selected],dtype=np.float32)))
        y = np.concatenate((self.real_y, [LABELS.index(r['camera_label']) for r in selected]))
        weights = np.concatenate((np.ones(len(self.real_y)), np.full(len(selected),.5)))
        model = RandomForestClassifier(n_estimators=200,class_weight='balanced',random_state=42,n_jobs=2)
        model.fit(x,y,sample_weight=weights)
        identifier = uuid4().hex[:12]
        temporary = self.directory/f'.{identifier}.tmp'
        target = self.directory/f'rf_shadow_{identifier}.joblib'
        joblib.dump(model,temporary)
        temporary.replace(target)
        return dict(file=target.name,pseudo_pairs=len(selected),real_pairs=len(self.real_y),verified=False)

    def snapshot(self):
        if self.pending and self.pending.done():
            try:
                self.candidate = self.pending.result()
                self.error = None
            except Exception as error:
                self.error = str(error)
            self.pending = None
        if self.pending is None and len(self.rows)-self.last_fit>=24:
            self.last_fit=len(self.rows)
            self.pending=self.pool.submit(self._fit,self.rows.copy())
        return dict(status=self.status,pseudo_pairs=len(self.rows),training=self.pending is not None,
                    candidate=self.candidate,error=self.error)

    def observe(self, camera, latest, comparison, polled_ns, round_trip):
        timing = comparison['timing']
        if not timing['fresh'] or round_trip>.25 or any(m['valid_zones']<16 for m in comparison['maps'].values()):
            self.window.clear()
            self.status = 'waiting_for_tof'
            return self.snapshot()
        pair = tuple(latest[s]['received_at'] for s in ('tof1','tof2'))
        if any(new<=old for new,old in zip(pair,self.last_pair) if old is not None):
            return self.snapshot()
        self.last_pair = pair
        camera_label = CAMERA_MAP.get(camera.get('label'),camera.get('label'))
        tof_label = CAMERA_MAP.get(comparison['tof_raw_label'],comparison['tof_raw_label'])
        views = camera.get('views',[])
        # Require two independently usable views and preserve each view's coordinate system.
        good = (camera.get('status')=='stable' and camera_label in LABELS and camera_label!='empty'
                and tof_label in LABELS and len(views)==2 and 'cross_view_disagreement' not in camera.get('warnings',[]))
        if good:
            good = all(v.get('torso_xy') and v.get('track_id') is not None and v.get('quality_score',0)>=.55
                       and v.get('relation',{}).get('relation') in ('inside','edge')
                       and CAMERA_MAP.get(winner,winner)==camera_label
                       and abs((polled_ns-v['received_ns'])/1e9-max(timing['age_seconds']))+round_trip/2<=.25
                       for v in views for winner in [max(v['scores'],key=v['scores'].get)])
        if not good or camera_label==tof_label:
            self.window.clear()
            self.status='match' if good else 'waiting_for_stable_camera'
            return self.snapshot()
        observation = tuple((v['input_id'],v['frame_id']) for v in views)
        if observation==self.last_view:
            return self.snapshot()
        self.last_view=observation
        now=time.monotonic()
        identity=(camera_label,tof_label,tuple((v['input_id'],v['track_id']) for v in views))
        torso=np.asarray([v['torso_xy'] for v in views])
        if self.window and (identity!=self.window[-1][1] or now-self.window[-1][0]>.65):
            self.window.clear()
        if self.window and max(float(np.linalg.norm(torso-row[2],axis=-1).max()) for row in self.window)>.045:
            self.window.clear()
        ranges=np.concatenate([distances(comparison['maps'][s]['distances_mm']) for s in ('tof1','tof2')])
        self.window.append((now,identity,torso,ranges.tolist(),pair))
        while self.window and now-self.window[0][0]>3:
            self.window.popleft()
        self.status='watching_conflict'
        if len(self.window)>=7 and now-self.window[0][0]>=2 and now>=self.cooldown:
            rows=[dict(camera_label=camera_label,tof_label=tof_label,distances=row[3],pi_received_at=row[4],
                       label_source='camera_pseudo',confirmed=False) for row in self.window]
            with self.journal.open('a',encoding='utf-8') as stream:
                for row in rows:
                    stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            self.rows.extend(rows)
            self.window.clear()
            self.cooldown=now+10
            self.status='conflict_saved'
        return self.snapshot()

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=True)
