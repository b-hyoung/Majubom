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
        self.max_people=1;self.transport_errors=0;self.transport_fallbacks=0;self.rpc_until=-1
        self.person_guard=None
        self._clear_pose_state()

    def _clear_pose_state(self):
        self.rotation_turns=0;self.rotation_probe=0;self.last_rotation_probe=None;self.bridge_timestamp=-1
        self.active_crop=None;self.crop_until=-1;self.recent_box=None;self.recent_box_stamp=-1
        self.frame_context=None;self.diagnostics={}
        self.support_stamp=None;self.support_center=None
        self.needs_video_reacquire=False
        self.confirmation_misses=0
        self.confirmation_turn=None
        self.rpc_until=-1
        if self.person_guard is not None:self.person_guard.reset()

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
            self.inference_connection=http.client.HTTPConnection(address.hostname,address.port,timeout=5)
        self.max_people=options['num_poses']
        self._clear_pose_state();self.transport_errors=0;self.transport_fallbacks=0;self.rpc_until=-1
        guard_path=Path(__file__).resolve().parents[3]/'checkpoints/person-guard.json'
        if guard_path.is_file():
            try:
                from .person_guard import PersonGuard
                self.person_guard=PersonGuard(guard_path,max_people=self.max_people)
            except Exception:self.close();raise
        from .recovery import POLICY
        return {'adapter_id':'mediapipe_pose', 'execution_kind':'model_inference',
                'implementation_validation':'experimental_browser_gpu', 'sdk_version':SDK_VERSION,
                'requested_device':'browser_gpu', 'actual_device':'browser_gpu', 'gpu_renderer':details['renderer'],
                'joint_definition':'mediapipe33', 'coordinate_space':'normalized_image',
                'internal_tracking':True, 'internal_smoothing':options['num_poses'] == 1,
                'max_returned_people':options['num_poses'], 'options':options, 'assets':settings['assets'],
                'estimated_world_coordinates':True, 'segmentation_masks':False, 'stable_person_ids':False,
                'landmark_presence_available':False, 'rotation_rescue':True,'pose_recovery_policy':dict(POLICY),
                'probe_tracking':'separate_stateless_image_task','transport_recovery':'reconnect_once_then_private_stdio_same_gpu',
                'person_evidence':self.person_guard.profile if self.person_guard else None}

    def reset_for_video(self):
        self._rpc('reset',all_tasks=True)
        self.last_timestamp = None
        self._clear_pose_state()
        return {'method':'recreate_task', 'caches_retained':False}

    def _timestamp(self,stamp):
        self.bridge_timestamp=max(stamp,self.bridge_timestamp+1)
        if self.bridge_timestamp>=2**53:raise ValueError('Internal GPU timestamp exceeds exact integer range')
        return self.bridge_timestamp

    def _infer_image(self,image,stamp,probe=False):
        height,width=image.shape[:2];mode='probe' if probe else 'video'
        if min(width,height)<1 or width*height>4096*2160:raise ValueError('GPU input exceeds supported frame size')
        # A dropped keep-alive connection must not permanently close a camera.
        if self.inference_url and stamp>=self.rpc_until:
            address=urlsplit(self.inference_url)
            for attempt in range(2):
                try:
                    if self.inference_connection is None:self.inference_connection=http.client.HTTPConnection(address.hostname,address.port,timeout=5)
                    self.inference_connection.request('POST',address.path,body=image.tobytes(),headers={
                        'Content-Type':'application/octet-stream','X-Width':str(width),'X-Height':str(height),
                        'X-Timestamp':str(self._timestamp(stamp)),'X-Mode':mode})
                    reply=self.inference_connection.getresponse();body=reply.read()
                    if reply.status==400 and not body:raise http.client.RemoteDisconnected('GPU bridge still completing the previous request')
                    if reply.status!=200:raise RuntimeError(body.decode('utf-8',errors='replace'))
                    return json.loads(body)
                except (OSError,http.client.HTTPException):
                    self.transport_errors+=1
                    if self.inference_connection is not None:
                        try:self.inference_connection.close()
                        except OSError:pass
                    self.inference_connection=None
            # The very same GPU task remains available through the private process pipe.
            # Replays use fresh SDK timestamps while preserving the external frame identity.
            self.rpc_until=stamp+2000;self.transport_fallbacks+=1
        return self._rpc('estimate',rgb=base64.b64encode(image.tobytes()).decode('ascii'),
                         width=width,height=height,timestamp=self._timestamp(stamp),mode=mode)

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
            from .recovery import POLICY,quality,improves,proposals,restore
            context=(packet.video_id,packet.width,packet.height)
            if self.frame_context is not None and context!=self.frame_context:
                self._rpc('reset',all_tasks=True);self._clear_pose_state()
            self.frame_context=context
            probe_evidence={}
            def infer(turns,crop=None,probe=False):
                x0,y0,x1,y1=crop or (0,0,packet.width,packet.height)
                view=rgb[y0:y1,x0:x1];image=np.ascontiguousarray(np.rot90(view,turns))
                result=restore(self._infer_image(image,stamp,probe),turns,crop,packet.width,packet.height)
                if probe:probe_evidence.update(rotation_degrees=turns*90,sdk_people=len(result.get('landmarks',[])),guard_rejected=0)
                if probe and result.get('landmarks') and self.person_guard is not None:
                    people=result['landmarks'];world=result.get('worldLandmarks',[])
                    kept=[i for i,person in enumerate(people)
                          if self.person_guard.confirm(rgb,stamp,turns,quality({'landmarks':[person]}))]
                    result['landmarks']=[people[i] for i in kept]
                    probe_evidence['guard_rejected']=len(people)-len(kept)
                    result['worldLandmarks']=[world[i] for i in kept] if world else []
                    self.person_guard.diagnostics.update(confirmed=bool(kept),reason=None if kept else 'person_not_confirmed_at_configured_scale',
                                                         accepted_sdk_person_indices=kept,rejected_people=len(people)-len(kept))
                return result
            crop=self.active_crop
            # A cropped primary is re-detected on this image; it must not live indefinitely on VIDEO tracking.
            result=infer(self.rotation_turns,crop,probe=crop is not None);before=quality(result);chosen=before;strategy='validated_crop' if crop else 'full'
            attempted=False
            def same_center(a,b):
                return not a or not b or np.linalg.norm(np.array(a)-b)<=.2
            def supported(q,center=None):
                return q['torso']>=3 and q['visible']>=6 and same_center(q['center'],center)
            probe_available=self.last_rotation_probe is None or stamp-self.last_rotation_probe>=POLICY['probe_interval_ms']
            fresh_support=(self.support_stamp is not None and stamp-self.support_stamp<POLICY['crop_full_frame_recheck_ms']
                           and same_center(before['center'],self.support_center))
            # VIDEO may keep tracking a face printed on bedding after a person has left.
            # New acquisitions and each second of tracking require an independent IMAGE detection.
            if crop is None and result.get('landmarks') and not fresh_support and not result.get('error'):
                if probe_available:
                    self.last_rotation_probe=stamp;attempted=True
                    # A VIDEO track may exist while IMAGE repeatedly fails at
                    # that orientation. Rechecking the same orientation used
                    # the sole retry budget forever and starved rotation rescue.
                    turn=(self.rotation_turns+(0,1,3,2)[self.confirmation_misses%4])%4
                    self.confirmation_turn=turn
                    result=infer(turn,probe=True);chosen=quality(result);before=chosen
                    if not result.get('error') and supported(chosen):
                        if turn!=self.rotation_turns:self._rpc('reset')
                        self.rotation_turns=turn;self.confirmation_misses=0
                        if self.needs_video_reacquire:self._rpc('reset');self.needs_video_reacquire=False
                        self.support_stamp=stamp;self.support_center=chosen['center'];strategy='image_confirmed'
                    elif not result.get('error'):
                        self.confirmation_misses+=1
                        if self.support_stamp is not None:self._rpc('reset')
                        self.needs_video_reacquire=True
                        self.support_stamp=None;self.support_center=None;strategy='full_rejected_no_image_person'
                else:
                    result=dict(landmarks=[],worldLandmarks=[]);chosen=quality(result);strategy='awaiting_image_confirmation'
            elif crop is None and not result.get('landmarks'):
                self.support_stamp=None;self.support_center=None
            full_refresh=crop is not None and stamp>=self.crop_until
            needs_recovery=not result.get('landmarks') or self.max_people==1 and not before['healthy'] or full_refresh
            if (not attempted and not result.get('error') and needs_recovery and probe_available):
                self.last_rotation_probe=stamp;attempted=True
                recent=self.recent_box if stamp-self.recent_box_stamp<=1000 else None
                # Crop-only detections can mistake faces printed on bedding for people.
                # Require current full-frame torso evidence before searching a crop.
                allow_crops=self.max_people==1 and before['torso']>=3 and before['visible']>=6 and self.support_stamp is not None
                options=proposals(packet.width,packet.height,self.rotation_turns,(packet.spec or {}).get('pose_region_hint'),recent,crop,allow_crops)
                if options:
                    if full_refresh:
                        turn,trial_crop,name=self.rotation_turns,None,'full_refresh';self.crop_until=stamp+1000
                    else:
                        turn,trial_crop,name=options[self.rotation_probe%len(options)];self.rotation_probe+=1
                    alternative=infer(turn,trial_crop,probe=True);candidate_quality=quality(alternative)
                    same_person=(not before['center'] or not candidate_quality['center'] or np.linalg.norm(np.array(before['center'])-candidate_quality['center'])<=.2)
                    if full_refresh and not alternative.get('error') and supported(candidate_quality,before['center']):
                        self.support_stamp=stamp;self.support_center=candidate_quality['center']
                    if full_refresh and not alternative.get('error') and (candidate_quality['torso']<3 or candidate_quality['visible']<6 or not same_person):
                        # Missing independent full-frame evidence expires the crop hypothesis.
                        result=alternative;chosen=candidate_quality;crop=None;self.active_crop=None;strategy='crop_rejected_no_full_frame_person'
                        self.support_stamp=None;self.support_center=None;self.needs_video_reacquire=True;self._rpc('reset')
                    elif not alternative.get('error') and (improves(candidate_quality,before) or full_refresh and candidate_quality['healthy'] and same_person):
                        result=alternative;chosen=candidate_quality;strategy=name;crop=trial_crop
                        self.rotation_turns=turn;self.active_crop=trial_crop;self.crop_until=stamp+1000 if trial_crop else -1
                        if trial_crop is None:self.support_stamp=stamp;self.support_center=chosen['center']
                        if chosen['healthy']:self.rotation_probe=0
                        self.needs_video_reacquire=False
                        self._rpc('reset') # Only a successful coordinate-space change resets VIDEO tracking.
            if crop is None and chosen['healthy']:self.active_crop=None
            if chosen['healthy'] and chosen['box']:
                self.recent_box=[chosen['box'][0]*packet.width,chosen['box'][1]*packet.height,chosen['box'][2]*packet.width,chosen['box'][3]*packet.height];self.recent_box_stamp=stamp
            self.diagnostics=dict(version=POLICY['version'],visible_joints=chosen['visible'],torso_joints=chosen['torso'],
                healthy=chosen['healthy'],strategy=strategy,rotation_degrees=self.rotation_turns*90,crop_bounds=list(crop) if crop else None,
                confirmation_misses=self.confirmation_misses,
                confirmation_rotation_degrees=None if self.confirmation_turn is None else self.confirmation_turn*90,
                probe_evidence=probe_evidence,
                recovery_attempted=attempted,transport_errors=self.transport_errors,transport_fallbacks=self.transport_fallbacks,
                independent_support_age_ms=stamp-self.support_stamp if self.support_stamp is not None else None,
                person_evidence=dict(self.person_guard.diagnostics) if self.person_guard else None,
                timestamp_ms=stamp,source_frame_id=packet.frame_id,accuracy_verified=False)
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
            for candidate in candidates:
                candidate['inference_rotation_ccw_degrees']=self.rotation_turns*90
                candidate['inference_recovery']=dict(self.diagnostics)
            return PoseOutput(tuple(candidates),stamp,sdk_call_started=True)
        except Exception as exc:
            raise AdapterCallError(f'MediaPipe GPU call/result conversion failed: {exc}', sdk_call_started=True) from exc

    def close(self):
        if self.person_guard is not None:self.person_guard.close();self.person_guard=None
        process, self.process = self.process, None
        self.last_timestamp = None
        self.inference_url = None
        self._clear_pose_state()
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
