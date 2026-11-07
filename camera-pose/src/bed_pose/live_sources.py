"""Camera RGB input and an explicitly synthetic source for development checks."""
import math
import time
import numpy as np
from .contracts import SourceError


class CameraSource:
    def __init__(self, camera_index=0, *, resolution=None, fps=None):
        if type(camera_index) is not int or camera_index < 0:
            raise ValueError('Camera index must be a nonnegative integer')
        if resolution is not None and (not isinstance(resolution,(list,tuple)) or
                any(type(v) is not int for v in resolution) or tuple(resolution) not in ((640,480),(1280,720),(1920,1080))):
            raise ValueError('지원하는 카메라 해상도를 선택하세요.')
        if fps is not None and (type(fps) is not int or fps not in (15,30,60)):
            raise ValueError('카메라 FPS는 기본값 또는 15·30·60을 선택하세요.')
        self.requested = {'resolution':list(resolution) if resolution is not None else None,'fps':fps}
        self.index, self.capture = camera_index, None

    def open(self):
        import cv2
        self.cv2 = cv2
        self.capture = cv2.VideoCapture(self.index, cv2.CAP_ANY)
        if not self.capture.isOpened():
            self.close()
            raise SourceError('카메라를 열 수 없습니다. 연결·권한·장치 번호를 확인하세요.')
        results={}
        def request(prop,value):
            try:return bool(self.capture.set(prop,value))
            except Exception:return False
        try:
            if self.requested['resolution']:
                for name,prop,value in zip(('width','height'),(cv2.CAP_PROP_FRAME_WIDTH,cv2.CAP_PROP_FRAME_HEIGHT),self.requested['resolution']):
                    results[name]=request(prop,value)
            if self.requested['fps'] is not None:
                results['fps']=request(cv2.CAP_PROP_FPS,self.requested['fps'])
            def reported(prop):
                try:
                    value=self.capture.get(prop)
                    return value if type(value) in (int,float) and math.isfinite(value) and value>0 else None
                except Exception:return None
            rate=reported(cv2.CAP_PROP_FPS)
            driver_size=[reported(cv2.CAP_PROP_FRAME_WIDTH),reported(cv2.CAP_PROP_FRAME_HEIGHT)]
        except Exception:
            self.close()
            raise

        return {'kind': 'camera', 'camera_index': self.index,
                'backend': self.capture.getBackendName(), 'opencv_version': cv2.__version__,
                'requested_capture':self.requested, 'capture_set_results':results,
                'driver_reported_resolution':driver_size,
                'driver_reported_fps': rate,
                'timestamp_source': 'received_after_read', 'exposure_timestamp_available': False,
                'hardware_dropped_frames': None, 'pixel_format': 'rgb24', 'mirror': False}

    def read(self):
        ok, bgr = self.capture.read()
        if not ok or bgr is None:
            raise SourceError('카메라 프레임 수신이 중단되었습니다.')
        return self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2RGB)

    def close(self):
        if self.capture is not None:
            capture, self.capture = self.capture, None
            capture.release()


class PiCameraSource(CameraSource):
    """Local configuration supplies a private MJPEG URL; never include it in records."""
    def __init__(self):
        from pathlib import Path
        self.index = 'raspberry_pi'
        self.config = Path.home() / '.config' / 'bed-pose' / 'pi-camera-url.txt'
        self.capture = None

    def open(self):
        import cv2
        from urllib.parse import urlsplit
        self.cv2 = cv2
        try:
            url = self.config.read_text(encoding='ascii').strip()
            parsed = urlsplit(url)
            if parsed.scheme != 'http' or not parsed.hostname or not parsed.path or parsed.query or parsed.username:
                raise ValueError('invalid configuration')
            self.capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1500])
        except (OSError, ValueError, cv2.error) as exc:
            raise SourceError('라즈베리파이 카메라 설정 또는 연결을 확인하세요.') from exc
        if not self.capture.isOpened():
            self.close()
            raise SourceError('라즈베리파이 카메라에 연결할 수 없습니다.')
        return {'kind':'camera', 'camera_index':self.index, 'backend':'FFmpeg MJPEG',
                'opencv_version':cv2.__version__, 'timestamp_source':'received_after_read',
                'exposure_timestamp_available':False, 'hardware_dropped_frames':None,
                'pixel_format':'rgb24', 'mirror':False}


class SyntheticSource:
    def __init__(self, interval=0.1):
        self.interval, self.index = interval, 0

    def open(self):
        return {'kind': 'synthetic', 'timestamp_source': 'received_after_read',
                'exposure_timestamp_available': False, 'hardware_dropped_frames': None,
                'pixel_format': 'rgb24', 'mirror': False}

    def read(self):
        if self.interval:
            time.sleep(self.interval)
        image = np.zeros((360, 640, 3), dtype=np.uint8)
        image[:] = [24, 35, 51]
        image[80:280, 120:520] = [55, 70, 87]
        x = 130 + self.index % 300
        image[150:190, x:x+30] = [100, 155, 180]
        self.index += 1
        return image

    def close(self):
        pass


class VideoFileSource:
    """Sequential file replay. Never uses decoder wall time as media/model time."""
    is_file = True

    def __init__(self, path):
        from pathlib import Path
        self.path = Path(path).resolve()
        self.container = None
        self.media = None
        self.sha256 = None

    def open(self):
        import av
        from .video import _stream, decoder_profile
        from .io_utils import file_hash
        if not self.path.is_file():
            raise SourceError('영상 파일을 찾을 수 없습니다.')
        self.container = av.open(str(self.path))
        stream = _stream(self.container)
        self.frames = iter(self.container.decode(stream))
        self.first_time = self.last_time = None
        self.index = 0
        self.sha256 = file_hash(self.path)
        return {'kind': 'video_file', 'path': str(self.path), 'sha256': self.sha256,
                'decoder': decoder_profile(), 'timestamp_source': 'received_after_read',
                'model_timestamp_source': 'relative_media_pts', 'playback': 'processing_speed_no_skip',
                'exposure_timestamp_available': False, 'hardware_dropped_frames': None,
                'pixel_format': 'rgb24', 'mirror': False}

    def read(self):
        from .video import canonical_rgb, frame_spec, frame_time
        frame = next(self.frames)  # StopIteration means normal EOF, not camera failure.
        rgb = canonical_rgb(frame)
        spec = frame_spec(frame, rgb, str(self.path), self.index)
        current = frame_time(spec)
        if current is None or self.last_time is not None and current <= self.last_time:
            raise SourceError('영상 PTS가 없거나 증가하지 않아 연속 분석을 중단했습니다.')
        if self.first_time is None:
            self.first_time = current
        self.last_time = current
        self.media = {k: spec[k] for k in ('source_pts', 'time_base_num', 'time_base_den', 'pixel_sha256')}
        self.media.update(source_frame_id=self.index, relative_time_ns=str(int((current-self.first_time)*1_000_000_000)))
        self.index += 1
        return rgb

    def close(self):
        if self.container is not None:
            container, self.container = self.container, None
            container.close()
            from .io_utils import file_hash
            if self.sha256 is not None and file_hash(self.path) != self.sha256:
                raise SourceError('분석 도중 원본 영상이 변경되었습니다.')


def probe_cameras(indices=range(6), excluded=()):
    """Explicit bounded probe; OpenCV has no portable friendly-name enumeration."""
    import cv2
    found = []
    for index in indices:
        if index in excluded:
            continue
        capture = cv2.VideoCapture(index, cv2.CAP_ANY)
        try:
            if capture.isOpened():
                ok, frame = capture.read()
                if ok and frame is not None:
                    found.append(index)
        finally:
            capture.release()
    return found


def named_cameras():
    """Use backend-qualified OpenCV indices from the OS device enumerator."""
    from cv2_enumerate_cameras import enumerate_cameras
    devices, seen = [], set()
    import sys
    import cv2
    # One Windows backend avoids listing the same physical device via MSMF and DirectShow.
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    for camera in enumerate_cameras(backend):
        identity = camera.path or (camera.name, camera.index)
        if identity in seen:
            continue
        seen.add(identity)
        devices.append({'index': int(camera.index)+(backend if backend != cv2.CAP_ANY else 0), 'name': camera.name or '이름을 제공하지 않는 카메라'})
    return devices
