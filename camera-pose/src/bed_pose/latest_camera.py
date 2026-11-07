"""Camera owner with one replaceable latest frame; file replay never uses this."""
import threading
import time
import numpy as np
from .contracts import SourceError


class LatestCameraSource:
    latest_camera = True
    is_file = False

    def __init__(self, source):
        self.source=source
        self.index=source.index
        self.condition=threading.Condition()
        self.stop=threading.Event()
        self.packet=None
        self.sequence=-1
        self.consumed=-1
        self.minimum_time=0
        self.profile=None
        self.error=None
        self.clock=time.perf_counter_ns
        self.utc_clock=time.time_ns
        self.thread=None

    def open(self):
        self.thread=threading.Thread(target=self._capture,daemon=True)
        self.thread.start()
        with self.condition:
            if not self.condition.wait_for(lambda:self.profile is not None or self.error is not None,timeout=15):
                self.stop.set()
                raise SourceError('카메라 연결 시간이 초과되었습니다.')
            if self.error:raise SourceError(str(self.error))
            return self.profile

    def _capture(self):
        try:
            profile=dict(self.source.open(),capture_policy='latest_camera_frame_v1')
            with self.condition:self.profile=profile;self.condition.notify_all()
            while not self.stop.is_set():
                started=self.clock()
                rgb=self.source.read()
                received=self.clock();utc=self.utc_clock()
                if not isinstance(rgb,np.ndarray) or rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[2]!=3 or min(rgb.shape[:2])<1:
                    raise SourceError('카메라가 유효한 RGB 영상을 반환하지 않았습니다.')
                rgb=np.array(rgb,copy=True,order='C');rgb.setflags(write=False)
                with self.condition:
                    self.sequence+=1
                    self.packet={'rgb':rgb,'capture_frame_id':self.sequence,'read_started_ns':started,'received_ns':received,'received_utc_ns':utc}
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:self.error=exc;self.condition.notify_all()
        finally:
            try:self.source.close()
            except Exception as exc:
                with self.condition:self.error=exc;self.condition.notify_all()

    def latest(self):
        with self.condition:
            return self.packet if not self.error and not self.stop.is_set() else None

    def read(self):
        with self.condition:
            def available():return self.packet is not None and self.sequence>self.consumed and self.packet['received_ns']>=self.minimum_time
            if not self.condition.wait_for(lambda:available() or self.error or self.stop.is_set(),timeout=5):
                raise SourceError('카메라 프레임 수신 시간이 초과되었습니다.')
            if self.error:raise SourceError(str(self.error))
            if self.stop.is_set():raise SourceError('카메라 입력이 종료되었습니다.')
            packet=self.packet
            self.analysis_capture=dict(packet,skipped_before=max(0,self.sequence-self.consumed-1))
            self.consumed=self.sequence
            return packet['rgb']

    def close(self):
        self.stop.set()
        with self.condition:self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=2)
            if self.thread.is_alive():raise SourceError('카메라 수신 종료가 지연되었습니다.')
            if self.error:raise SourceError(str(self.error))
