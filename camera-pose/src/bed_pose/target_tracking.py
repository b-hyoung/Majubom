"""Experimental pose-motion association, never a verified biometric identity."""
from collections import deque
from dataclasses import asdict, dataclass
import math
import statistics
import threading
import uuid


@dataclass(frozen=True)
class TrackingConfig:
    max_speed_diagonals_per_second: float = 1.0
    max_gap_ms: float = 500.0
    min_shared_joints: int = 4

    @classmethod
    def load(cls, values=None):
        config=cls(**(values or {}))
        if (type(config.min_shared_joints) is not int or not 1<=config.min_shared_joints<=17
                or any(type(v) not in (float,int) or not math.isfinite(v) or v<=0
                       for v in (config.max_speed_diagonals_per_second,config.max_gap_ms))):
            raise ValueError('추적 설정은 양의 유한한 값, 공통 관절 수는 1~17이어야 합니다.')
        return config


def visible_points(person,width,height):
    return {i:(j['x_px'],j['y_px']) for i,j in enumerate(person['joints'])
            if j.get('availability')=='predicted' and type(j.get('x_px')) in (float,int)
            and type(j.get('y_px')) in (float,int) and math.isfinite(j['x_px']) and math.isfinite(j['y_px'])
            and 0<=j['x_px']<width and 0<=j['y_px']<height}


class PoseMotionTrack:
    def __init__(self, snapshot, candidate_id, config):
        self.config=config
        self.track_id=uuid.uuid4().hex
        self.segment_start=snapshot['frame_id']
        self.seed_candidate_id=candidate_id
        person=next((p for p in snapshot['people'] if p['candidate_id']==candidate_id),None)
        if person is None:raise ValueError('선택한 프레임에 후보가 없습니다.')
        self.width,self.height=snapshot['width'],snapshot['height']
        self.points=visible_points(person,self.width,self.height)
        if len(self.points)<config.min_shared_joints:
            raise ValueError('추적을 시작하기에 화면 내 관절 정보가 부족합니다.')
        self.last_ns=snapshot['time_ns']
        self.state=self.result('tracking_provisional',candidate_id,snapshot['frame_id'],'operator_initialized')

    def result(self,status,candidate_id,frame_id,reason):
        return {'status':status,'candidate_id':candidate_id,'track_id':self.track_id,
                'frame_id':frame_id,'segment_start_frame_id':self.segment_start,'seed_candidate_id':self.seed_candidate_id,'reason':reason,
                'method':'pose_motion_processing_aware_gate_v2','identity_verified':False,'config':asdict(self.config)}

    def update(self,snapshot):
        frame=snapshot['frame_id']
        if self.state['status']=='held':
            self.state=self.result('held',None,frame,self.state['reason'])
            return dict(self.state)
        reason=None
        elapsed=(snapshot['time_ns']-self.last_ns)/1e9
        if (snapshot['width'],snapshot['height'])!=(self.width,self.height):reason='geometry_changed'
        elif elapsed<=0:reason='time_not_increasing'
        elif max(0.,elapsed-snapshot.get('previous_processing_ns',0)/1e9)*1000>self.config.max_gap_ms:reason='observation_gap'
        elif snapshot['processing_status']!='ok':reason='processing_failed'
        elif not snapshot['people']:reason='target_missing'
        candidates=[]
        if reason is None:
            for person in snapshot['people']:
                points=visible_points(person,self.width,self.height)
                shared=self.points.keys()&points.keys()
                if len(shared)<self.config.min_shared_joints:continue
                distance=statistics.median(math.dist(self.points[i],points[i]) for i in shared)/math.hypot(self.width,self.height)
                # Slow inference must not widen the spatial association gate.
                gate_elapsed=min(elapsed,self.config.max_gap_ms/1000)
                if distance/gate_elapsed<=self.config.max_speed_diagonals_per_second:
                    candidates.append((person,points))
            if len(candidates)>1:reason='target_ambiguous'
            elif not candidates:reason='motion_or_visibility_gate_failed'
        if reason:
            self.state=self.result('held',None,frame,reason)
        else:
            person,self.points=candidates[0]
            self.last_ns=snapshot['time_ns']
            slow=elapsed*1000>self.config.max_gap_ms
            self.state=self.result('tracking_provisional',person['candidate_id'],frame,
                                   'unique_motion_candidate_after_processing_delay' if slow else 'unique_motion_candidate')
        return dict(self.state)


class TargetTracker:
    def __init__(self,history_limit=256):
        self.history=deque(maxlen=history_limit)
        self.lock=threading.RLock()
        self.track=None

    def observe(self,row,width,height):
        snapshot={'frame_id':row['stream_frame_id'],'time_ns':int(row.get('source_media',{}).get('relative_time_ns',row['received_ns'])),
                  'people':row['mapped_people'] or [],'width':width,'height':height,
                  'processing_status':row['processing_status']}
        if 'source_media' not in row and 'previous_processing_ns' in row:
            value=row['previous_processing_ns']
            if not isinstance(value,str) or not value.isascii() or not value.isdigit():
                raise ValueError('Invalid preceding processing duration')
            snapshot['previous_processing_ns']=int(value)
        with self.lock:
            self.history.append(snapshot)
            return self.track.update(snapshot) if self.track else {'status':'not_configured','candidate_id':None}

    def select(self,frame_id,candidate_id,config=None):
        with self.lock:
            snapshots=list(self.history)
            seed=next((s for s in snapshots if s['frame_id']==frame_id),None)
            if seed is None:raise ValueError('선택 프레임이 추적 이력에서 만료되었습니다. 다시 지정하세요.')
            track=PoseMotionTrack(seed,candidate_id,TrackingConfig.load(config))
            for snapshot in snapshots:
                if snapshot['frame_id']>frame_id:track.update(snapshot)
            self.track=track
            return dict(track.state)

    def reset(self):
        with self.lock:
            self.track=None

    def hold(self,reason):
        with self.lock:
            if self.track and self.track.state['status']!='held':
                state=self.track.state
                self.track.state=self.track.result('held',None,state['frame_id'],reason)

    def current(self):
        with self.lock:
            return dict(self.track.state) if self.track else {'status':'not_configured','candidate_id':None}
