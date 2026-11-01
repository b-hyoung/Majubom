"""Experimental Windows GPU delegate bridge; frames remain on localhost."""
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import urllib.request
import http.client
from urllib.parse import urlsplit

import numpy as np
from ..contracts import AdapterCallError, PoseOutput
from .mediapipe import MediaPipeAdapter, SDK_VERSION, validated_options


@dataclass
class Landmark:
    x: float
    y: float
    z: float
    visibility: float | None = None
    presence: float | None = None
    name: str | None = None


class BrowserGPUAdapter:
    def __init__(self):
        self.process = None
        self.replies = queue.Queue()
        self.sequence = 0
        self.last_timestamp = None
        self.inference_url = None
        self.inference_connection = None
        self.rotation_turns=0;self.rotation_probe=0;self.last_rotation_probe=None
        self.bridge_timestamp=-1

    def _read(self, process, replies):
        for line in process.stdout:
            replies.put(line)
        replies.put(None)

    def _rpc(self, operation, **arguments):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError('GPU bridge process is not running')
        self.sequence += 1
        request = dict(op=operation, id=self.sequence, **arguments)
        self.process.stdin.write(json.dumps(request, separators=(',', ':')) + '\n')
        self.process.stdin.flush()
        try:
            line = self.replies.get(timeout=45)
        except queue.Empty as exc:
            raise RuntimeError('GPU bridge response timed out') from exc
        if line is None:
            raise RuntimeError('GPU bridge exited unexpectedly')
        result = json.loads(line)
        if result.get('id') != self.sequence:
            raise RuntimeError('GPU bridge response sequence mismatch')
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'GPU bridge error'))
        return result.get('value')

    def open(self, settings):
        if self.process is not None:
            raise ValueError('GPU adapter is already open')
        if settings.get('requested_device') != 'browser_gpu' or settings.get('execution_mode') != 'ordered_sequence':
            raise ValueError('GPU adapter requires browser_gpu and ordered_sequence')
        options = validated_options(dict(settings, requested_device='cpu'))
        model = Path(settings['assets'][0]['path']).read_bytes()
        if hashlib.sha256(model).hexdigest() != settings['assets'][0]['sha256']:
            raise ValueError('MediaPipe model changed while loading')
        bridge = Path(__file__).resolve().parents[3] / 'tools' / 'gpu-preview' / 'bridge.cjs'
        node = shutil.which('node')
        if not node or not bridge.is_file() or not (bridge.parent / 'node_modules' / 'playwright-core').is_dir():
            raise ValueError('GPU bridge dependencies missing: run npm.cmd install in tools/gpu-preview')
        self.replies = queue.Queue()
        self.process = subprocess.Popen([node, str(bridge)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, args=(self.process, self.replies), daemon=True).start()
        try:
            details = self._rpc('init', model=base64.b64encode(model).decode('ascii'), options=options)
        except Exception:
            self.close()
            raise
        self.last_timestamp = None
        self.inference_url = details.get('inference_url')
        if self.inference_url:
            address=urlsplit(self.inference_url)
            self.inference_connection=http.client.HTTPConnection(address.hostname,address.port,timeout=45)
        return {'adapter_id':'mediapipe_pose', 'execution_kind':'model_inference',
                'implementation_validation':'experimental_browser_gpu', 'sdk_version':SDK_VERSION,
                'requested_device':'browser_gpu', 'actual_device':'browser_gpu', 'gpu_renderer':details['renderer'],
                'joint_definition':'mediapipe33', 'coordinate_space':'normalized_image',
                'internal_tracking':True, 'internal_smoothing':options['num_poses'] == 1,
                'max_returned_people':options['num_poses'], 'options':options, 'assets':settings['assets'],
                'estimated_world_coordinates':True, 'segmentation_masks':False, 'stable_person_ids':False,
                'landmark_presence_available':False, 'rotation_rescue':True}

    def reset_for_video(self):
        self._rpc('reset')
        self.last_timestamp = None
        self.rotation_turns=0;self.rotation_probe=0;self.last_rotation_probe=None;self.bridge_timestamp=-1
        return {'method':'recreate_task', 'caches_retained':False}

    def estimate(self, model_input):
        stamp = model_input.model_timestamp_ms
        if (type(stamp) is not int or not 0 <= stamp < 2**53 or
                (self.last_timestamp is not None and stamp <= self.last_timestamp)):
            raise AdapterCallError('MediaPipe GPU timestamp must be increasing and exactly representable')
        packet = model_input.frame
        rgb = np.asarray(packet.rgb)
        if rgb.dtype != np.uint8 or rgb.shape != (packet.height, packet.width, 3):
            raise AdapterCallError('MediaPipe GPU requires uint8 RGB input')
        try:
            def infer(turns):
                self.bridge_timestamp=max(stamp,self.bridge_timestamp+1)
                image=np.ascontiguousarray(np.rot90(rgb,turns)) if turns else np.ascontiguousarray(rgb)
                height,width=image.shape[:2]
                if self.inference_url:
                    self.inference_connection.request('POST',urlsplit(self.inference_url).path,
                        body=image.tobytes(),headers={'Content-Type':'application/octet-stream',
                        'X-Width':str(width),'X-Height':str(height),'X-Timestamp':str(self.bridge_timestamp)})
                    reply=self.inference_connection.getresponse();body=reply.read()
                    if reply.status!=200:raise RuntimeError(body.decode('utf-8',errors='replace'))
                    return json.loads(body)
                return self._rpc('estimate',rgb=base64.b64encode(image.tobytes()).decode('ascii'),
                    width=width,height=height,timestamp=self.bridge_timestamp)
            result=infer(self.rotation_turns)
            # Try at most one alternative per failed observation, at most once per 500ms.
            if (not result.get('error') and not result.get('landmarks') and
                    (self.last_rotation_probe is None or stamp-self.last_rotation_probe>=500)):
                self.last_rotation_probe=stamp
                alternatives=[t for t in (0,1,3,2) if t!=self.rotation_turns]
                trial=alternatives[self.rotation_probe%len(alternatives)];self.rotation_probe+=1
                self._rpc('reset') # Orientation changes must not inherit an unrelated tracking ROI.
                alternative=infer(trial)
                if not alternative.get('error') and alternative.get('landmarks'):
                    result=alternative;self.rotation_turns=trial;self.rotation_probe=0
                else:self._rpc('reset')
            from .rotation_rescue import restore_result
            result=restore_result(result,self.rotation_turns,packet.width,packet.height)
        except Exception as exc:
            raise AdapterCallError(f'MediaPipe GPU bridge failed: {exc}', sdk_call_started=False) from exc
        if result.get('error'):
            raise AdapterCallError(f"MediaPipe GPU inference failed: {result['error']}",
                                   sdk_call_started=result.get('sdkCallStarted') is True)
        try:
            self.last_timestamp = stamp
            class Result: pass
            converted = Result()
            def landmarks(groups):
                return [[Landmark(**{key: point.get(key) for key in ('x','y','z','visibility','presence','name')})
                         for point in group] for group in groups]
            converted.pose_landmarks = landmarks(result['landmarks'])
            converted.pose_world_landmarks = landmarks(result['worldLandmarks'])
            candidates=MediaPipeAdapter._candidates(converted, packet.width, packet.height)
            for candidate in candidates:candidate['inference_rotation_ccw_degrees']=self.rotation_turns*90
            return PoseOutput(tuple(candidates),stamp,sdk_call_started=True)
        except Exception as exc:
            raise AdapterCallError(f'MediaPipe GPU call/result conversion failed: {exc}', sdk_call_started=True) from exc

    def close(self):
        process, self.process = self.process, None
        self.last_timestamp = None
        self.inference_url = None
        if self.inference_connection:
            self.inference_connection.close();self.inference_connection=None
        if process is not None:
            if process.stdin:
                try: process.stdin.close()
                except OSError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout: process.stdout.close()
