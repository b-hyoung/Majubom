"""Read-only ToF comparison; agreement is never ground truth or camera accuracy."""
from copy import deepcopy
import json,math,threading,time,urllib.request
from .tof_learning.check import sensor_timing,canonical


def compare(camera,latest,presence):
    timing=sensor_timing(latest,presence);maps={};valid=True
    for sid in ('tof1','tof2'):
        values=(latest.get(sid) or {}).get('distances_mm')
        ok=isinstance(values,list) and len(values)==64 and all(type(v) in (int,float) and math.isfinite(v) for v in values)
        maps[sid]=dict(distances_mm=values if ok else None,valid_zones=sum(v>0 for v in values) if ok else 0)
        valid=valid and ok and maps[sid]['valid_zones']>=16
    label=canonical(latest.get('posture_raw'));confidence=latest.get('posture_conf')
    reliable=(timing['fresh'] and valid and label is not None and type(confidence) in (int,float)
              and math.isfinite(confidence) and confidence>=.8)
    cam=camera.get('label');status='unavailable'
    if timing['fresh'] and valid:status='low_confidence' if not reliable else 'match' if cam==label else 'conflict' if cam else 'tof_only'
    assisted=cam;source='camera' if cam else None
    # User requires abstention over uncertain labels. A ToF-only prediction
    # must not become a confirmed or replacement camera posture.
    return dict(status=status,camera_label=cam,tof_raw_label=label,tof_filtered_label=latest.get('posture'),
                tof_confidence=confidence,timing=timing,maps=maps,assisted_label=assisted,assisted_source=source,
                accuracy_verified=False,eligible_for_recording=False,semantics='agreement_not_ground_truth',
                note='기존 ToF 모델 · 현재 설치 정확도 미검증 · 엎드림 클래스 없음')


class SensorCrosscheck:
    def __init__(self,url,model_path=None):
        self.model=None
        if model_path:
            import joblib
            self.model=joblib.load(model_path)
            from .tof_learning.session_data import LABELS
            self.labels=LABELS
            if self.model.n_features_in_!=128 or list(self.model.classes_)!=list(range(len(LABELS))):
                raise ValueError('실험 RF의 입력 또는 클래스 순서가 다릅니다.')
        self.url=url.rstrip('/');self.stop=threading.Event();self.lock=threading.Lock()
        self.data=None;self.error=None;self.at=None
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def _run(self):
        while not self.stop.is_set():
            try:
                def get(path):
                    with urllib.request.urlopen(self.url+path,timeout=2) as r:return json.loads(r.read(1024*1024))
                latest=get('/tof/latest');presence=get('/tof/presence')
                if self.model is not None:
                    latest.update(posture_raw=None,posture_conf=None,posture=None)
                    if sensor_timing(latest,presence)['fresh']:
                        from .tof_learning.session_data import distances
                        import numpy as np
                        inputs=np.concatenate([distances(latest[s]['distances_mm']) for s in ('tof1','tof2')])
                        scores=self.model.predict_proba(inputs.reshape(1,128))[0]
                        prediction=int(scores.argmax())
                        latest.update(posture_raw=self.labels[prediction],posture_conf=float(scores[prediction]))
                with self.lock:self.data=(latest,presence);self.error=None;self.at=time.monotonic()
            except Exception as e:
                with self.lock:self.error=type(e).__name__
            self.stop.wait(.2)
    def snapshot(self,camera):
        with self.lock:
            if self.error or self.data is None or self.at is None or time.monotonic()-self.at>1:
                return dict(status='unavailable',error=self.error,accuracy_verified=False,maps={},camera_label=camera.get('label'))
            latest,presence=deepcopy(self.data)
        result=compare(camera,latest,presence)
        if self.model is not None:
            result['note']='Bed C S01 실험 · 원시 거리 RF · 새 참여자 검증 중'
        return result
    def close(self):self.stop.set();self.thread.join(timeout=5)
