"""Opt-in stereo bridge; independent of existing posture and recording decisions."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import time
import uuid
import threading
import numpy as np
from .calibration import validate
from .io_utils import atomic_json, load_json, object_hash
from .stereo_depth import triangulate


def default_config():
    return dict(dimensions_source='demo', room_size=[4, 2.7, 5],
                bed=dict(size=[1, .55, 2], position=[2, .275, 2.5], yaw_degrees=0),
                bindings=['', ''], installation_confirmed=False, same_person_confirmed=False,
                max_skew_ms=40., max_age_ms=1500.)


def validate_config(c):
    if not isinstance(c, dict) or set(c) != set(default_config()):
        raise ValueError('Room configuration keys do not match')
    def vector(v, positive=False):
        if not isinstance(v, list) or len(v) != 3 or any(type(x) not in (int, float) or not np.isfinite(x) or (positive and x <= 0) or abs(x)>100 for x in v):
            raise ValueError('Room/bed dimensions must be finite meters (maximum 100m)')
    vector(c['room_size'], True)
    if not isinstance(c['bed'], dict) or set(c['bed']) != {'size', 'position', 'yaw_degrees'}:
        raise ValueError('Invalid bed configuration')
    vector(c['bed']['size'], True); vector(c['bed']['position'])
    if type(c['bed']['yaw_degrees']) not in (int, float) or not np.isfinite(c['bed']['yaw_degrees']):
        raise ValueError('Invalid bed angle')
    if c['dimensions_source'] not in ('demo', 'measured'):
        raise ValueError('Dimensions must be marked demo or measured')
    if not isinstance(c['bindings'], list) or len(c['bindings']) != 2 or any(not isinstance(v, str) for v in c['bindings']) or (c['bindings'][0] and c['bindings'][0] == c['bindings'][1]):
        raise ValueError('Assign two distinct inputs')
    if any(type(c[k]) is not bool for k in ('installation_confirmed', 'same_person_confirmed')):
        raise ValueError('Explicit installation and same-person confirmation required')
    for k in ('max_skew_ms', 'max_age_ms'):
        if type(c[k]) not in (int, float) or not np.isfinite(c[k]) or not 0 < c[k] <= 5000:
            raise ValueError('Invalid time thresholds')
    return c


def reconstruct(pair, calibration):
    import cv2
    matrices = []
    for c in calibration['cameras']:
        k, r, t = (np.asarray(c[key], float) for key in ('intrinsic_matrix', 'rotation', 'translation'))
        matrices.append((k @ np.column_stack((r, t)), r, t, -r.T @ t))
    joints = []
    for index in range(17):
        samples = [v['joints'][index] for v in pair]
        valid = all(j is not None for j in samples)
        result = dict(id=index, valid=False, confidence=None, x=None, y=None, z=None, reason='joint_missing_or_low_visibility')
        if valid:
            pixels = []
            for j, c in zip(samples, calibration['cameras']):
                k = np.asarray(c['intrinsic_matrix'], float)
                pixels.append(cv2.undistortPoints(np.array([[j[:2]]], float), k,
                              np.asarray(c['distortion_coefficients'], float), P=k).reshape(2))
            estimate = triangulate(pixels, matrices, calibration['quality'])
            result['reason'] = estimate.get('reason')
            if estimate['status'] == 'estimated':
                result.update(valid=True, x=estimate['xyz'][0], y=estimate['xyz'][1], z=estimate['xyz'][2],
                              confidence=min(j[2] for j in samples),
                              reprojection_error_px=estimate['reprojection_error_px'])
        joints.append(result)
    return dict(personId='operator-confirmed-pair', timestampMs=time.time_ns() // 1000000,
                coordinateSystem='room_world', unit='m', jointDefinition='coco17',
                identity_verified=False, exposure_synchronized=False, joints=joints)


class RoomMonitor:
    # Caller holds WebWorkspace.lock for all operations.
    def __init__(self, directory):
        self.directory = Path(directory)
        self.config = default_config()
        self.calibration = None
        self.error = None
        self.setup=dict(method='automatic_background',pattern=dict(kind='charuco',columns=9,rows=6,width_m=None),manual_cameras=[])
        self.calibration_job=dict(state='idle',error=None,result=None)
        self.job_lock=threading.RLock()
        self.auto_cancel=threading.Event()
        self.auto_inputs=dict(detector='SIFT',baseline_m=None,cameras=None,room_transform=None)
        self.auto_samples=[];self.auto_deadline=None;self.auto_bindings=None
        self.auto_saved=None
        self.candidates=None
        self.preview_enabled=False
        self.preview_candidate=None
        self.preview_bindings=None
        self.preview_skeleton=None
        self.preview_reason='disabled'
        from .bed_posture_3d import PostureFilter
        self.posture_filter=PostureFilter()
        self.posture_3d=None
        self.preview_bed=None
        self.preview_annotation=None
        self.axis_up=None;self.axis_long=None
        self.axis_reference_info=dict(source='none')
        self.display_alignment='bed'
        self.bed_surface_height=.65
        self.axis_history=deque(maxlen=80)
        self.preview_stamp=None;self.preview_pose_key=None
        self.preview_mode=None;self.preview_switch=None;self.preview_switch_since=0.
        self.latest_observations=[None,None]
        self.preview_evidence=[]
        self.capture_records=[p.parent.name for p in sorted((self.directory/'captures').glob('*/pair.json'))][-50:]
        try:
            if (self.directory/'setup.json').exists():self.set_setup(load_json(self.directory/'setup.json'),save=False)
        except (OSError,ValueError,TypeError,KeyError):pass
        # Automatic is the new default; older pattern setup remains an explicit option.
        self.setup['method']='automatic_background'
        try:
            from .automatic_calibration import validate_inputs
            if (self.directory/'automatic-inputs.json').exists():
                self.auto_inputs=validate_inputs(load_json(self.directory/'automatic-inputs.json'))
            if (self.directory/'automatic-result.json').exists():
                saved=load_json(self.directory/'automatic-result.json')
                if saved['sha256']!=object_hash(saved['result']):raise ValueError('자동 보정 저장값의 해시가 맞지 않습니다.')
                if saved['result'].get('method')!='automatic_background':raise ValueError('Invalid automatic result method')
                self.auto_saved=saved['result']
        except (OSError,ValueError,TypeError,KeyError) as exc:self.error=str(exc)
        try:
            if (self.directory/'room.json').exists():
                self.config = validate_config(load_json(self.directory/'room.json'))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.error = str(exc)
        # New process must confirm physical camera bindings again.
        self.config.update(installation_confirmed=False, same_person_confirmed=False)
        try:
            if (self.directory/'calibration.json').exists():
                self.calibration = validate(load_json(self.directory/'calibration.json'))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.error = str(exc)
        try:
            measurements=load_json(self.directory/'physical-measurements.json')
            height=measurements.get('bed_surface_height_m',.65)
            if type(height) in (int,float) and np.isfinite(height) and .1<=height<=2:self.bed_surface_height=height
        except (OSError,ValueError,TypeError,KeyError):pass
        self.reset()

    def reset(self):
        self.preview_skeleton = None
        self.preview_stamp=None;self.preview_pose_key=None
        self.preview_mode=None;self.preview_switch=None;self.preview_switch_since=0.
        self.latest_observations=[None,None]
        self.axis_history.clear()
        self.posture_3d=None
        self.posture_filter.reset()
        self.buffers = [deque(), deque()]
        self.latest_pair = None
        self.last_capture = None
        self.skeleton = None
        self.received = [0, 0]
        self.dropped = [0, 0]
        self.paired = 0
        self.started = time.monotonic()
        self.reason = 'waiting_for_pair'

    def set_preview(self, values):
        enabled=values.get('enabled')
        if type(enabled) is not bool:
            raise ValueError('임시 표시 사용 여부를 확인하세요.')
        if enabled:
            from .experimental_preview import prepare
            path=self.directory/'guided-calibration-candidate-20.json'
            if not path.is_file():
                raise ValueError('저장된 임시 보정 후보가 없습니다.')
            self.preview_candidate=prepare(load_json(path))
            self.preview_bindings=self.config['bindings'][:]
            self.axis_up=None;self.axis_long=None;self.axis_reference_info=dict(source='none')
            axis_path=self.directory/'preview-axis-reference.json'
            if axis_path.is_file():
                try:
                    saved_axis=load_json(axis_path)
                    if saved_axis['candidate_hash']==object_hash(self.preview_candidate['cameras']) and saved_axis.get('up') is not None:
                        from .preview_alignment import rotation
                        up=np.asarray(saved_axis['up'],float)
                        long=np.asarray(saved_axis['long'],float) if saved_axis.get('long') is not None else None
                        if up.shape!=(3,) or not np.isfinite(up).all() or np.linalg.norm(up)<.5:raise ValueError('invalid up')
                        matrix=rotation(up,long)
                        if not np.isfinite(matrix).all():raise ValueError('invalid axis')
                        self.axis_up=up/np.linalg.norm(up);self.axis_long=matrix[2] if long is not None else None
                        self.axis_reference_info=saved_axis['reference_info']
                except (OSError,ValueError,KeyError,TypeError):self.error='저장된 표시 축 기준을 복원하지 못했습니다.'
            self.preview_bed=None
            self.preview_annotation=None
            bed_path=self.directory/'preview-bed.json'
            if bed_path.is_file():
                try:saved=load_json(bed_path)
                except (OSError,ValueError,TypeError):saved={}
                if saved.get('candidate_hash')==object_hash(self.preview_candidate['cameras']):
                    from .bed_posture_3d import bed_frame
                    try:
                        self.preview_bed=bed_frame(saved['bed']['corners'],'camera_a_preview',False,'manual_two_view_corners')
                        self.preview_annotation=deepcopy(saved.get('annotation'))
                    except (ValueError,TypeError,KeyError):self.error='저장된 침대 위치가 손상됐습니다. 다시 등록하세요.'
        self.preview_enabled=enabled
        self.preview_skeleton=None
        self.preview_stamp=None;self.preview_pose_key=None
        self.latest_observations=[None,None]
        self.preview_mode=None;self.preview_switch=None;self.preview_switch_since=0.
        self.axis_history.clear()
        self.preview_reason='waiting_for_pair' if enabled else 'disabled'
        self.posture_3d=None
        self.posture_filter.reset()
        return dict(enabled=enabled, accuracy_verified=False)

    def register_preview_bed(self,values):
        if not self.preview_enabled or self.preview_candidate is None:
            raise ValueError('실시간 임시 3D 모드를 먼저 켜 주세요.')
        from .bed_posture_3d import annotate_preview_bed
        if values.get('bindings',self.config['bindings'])!=self.config['bindings']:
            raise ValueError('영상 고정 후 카메라 연결이 바뀌었습니다. 새 영상을 고정하세요.')
        bed=annotate_preview_bed(values,self.preview_candidate)
        atomic_json(self.directory/'preview-bed.json',dict(bed=bed,
            candidate_hash=object_hash(self.preview_candidate['cameras']),annotation=deepcopy(values)))
        self.preview_bed=bed
        self.preview_annotation=deepcopy(values)
        self.display_alignment='bed'
        self.posture_3d=None;self.posture_filter.reset()
        return dict(bed=deepcopy(bed),accuracy_verified=False)

    def refresh_preview(self):
        if not self.preview_enabled:return
        from .experimental_preview import select_live_pose,reconstruct_preview,sanitize_pose
        from .bed_posture_3d import classify
        now=time.perf_counter_ns();fresh_limit=min(self.config['max_age_ms'],300)*1e6
        packets=[p if p is not None and 0<=now-p['time_ns']<=fresh_limit else None for p in self.latest_observations]
        stereo=None;pose=None
        self.preview_reason=None
        self.preview_evidence=[]
        if self.config['bindings']!=self.preview_bindings:self.preview_reason='bindings_changed'
        elif any(p and p['people_count']>1 for p in packets):self.preview_reason='multiple_people'
        else:
            pair=self.latest_pair
            if (pair is not None and all(0<=now-p['time_ns']<=fresh_limit and p['processing_ok']
                and p['people_count']==1 and p['source_kind'] in ('camera','pi_camera')
                and [p['width'],p['height']]==c['image_resolution'] for p,c in zip(pair,self.preview_candidate['cameras']))):
                try:
                    stereo=reconstruct_preview(pair,self.preview_candidate)
                    stereo['source_frame_ids']=[p['frame_id'] for p in pair]
                    stereo=sanitize_pose(stereo)
                except (ValueError,np.linalg.LinAlgError):pass
            monos=[sanitize_pose(p.get('mono_preview')) if p else None for p in packets]
            for candidate in [stereo,*monos]:
                if candidate is None:continue
                decision=classify(candidate,self.preview_bed)
                self.preview_evidence.append(dict(source=candidate['source'],camera=candidate.get('source_camera'),
                    label=decision['label'],quality_score=decision['quality_score'],reason=decision['reason']))
            pose=select_live_pose(stereo,monos)
            if pose:
                key=lambda p:(p['source'],p.get('source_camera'))
                proposed=key(pose)
                previous=next((p for p in [stereo,*monos] if p and key(p)==self.preview_mode
                               and sum(j['valid'] for j in p['joints'])>=8),None)
                if previous is not None and proposed!=self.preview_mode:
                    if self.preview_switch!=proposed:self.preview_switch=proposed;self.preview_switch_since=now
                    if now-self.preview_switch_since<600e6:pose=deepcopy(previous)
                else:self.preview_switch=None
                self.preview_mode=key(pose)
            if pose is None:self.preview_reason='no_reconstructable_joints'
        self.preview_skeleton=pose
        if pose:
            pose['observation_id']=str((pose['source'],pose.get('source_camera'),tuple(pose['source_frame_ids'])))
            pose['personId']='unverified-live-target'
            if pose.get('source_camera'):
                self.preview_stamp=packets['AB'.index(pose['source_camera'])]['time_ns']
            else:self.preview_stamp=min(p['time_ns'] for p in self.latest_pair)
            key=(pose['source'],pose.get('source_camera'),tuple(pose['source_frame_ids']))
            if key!=self.preview_pose_key:
                if self.preview_pose_key is not None and key[:2]!=self.preview_pose_key[:2]:self.posture_filter.reset()
                self.axis_history.append((time.monotonic(),deepcopy(pose)))
                from .bed_posture_3d import check_view_agreement
                decision=check_view_agreement(classify(pose,self.preview_bed),self.preview_evidence)
                self.posture_3d=self.posture_filter.update(decision,time.monotonic())
                self.preview_pose_key=key
        else:
            self.preview_stamp=None;self.preview_pose_key=None
            self.posture_3d=self.posture_filter.update(classify(None,self.preview_bed),time.monotonic())

    def align_preview(self,values):
        from .preview_alignment import reference,rotation
        kind=values.get('reference')
        if kind=='bed':
            if self.preview_bed is None:raise ValueError('침대 모서리를 먼저 등록하세요.')
            self.display_alignment='bed'
        elif kind=='reset':
            self.display_alignment='raw'
            self.axis_up=None;self.axis_long=None
            self.axis_reference_info=dict(source='none')
        elif kind=='camera_pitch':
            self.display_alignment='reference'
            if not self.preview_enabled:raise ValueError('임시 3D를 먼저 켜 주세요.')
            from .preview_alignment import tilt_reference
            self.axis_up,self.axis_reference_info=tilt_reference(values.get('pitch_degrees'),self.preview_candidate['display_cameras'])
            self.axis_long=None
            atomic_json(self.directory/'camera-pitch-reference.json',self.axis_reference_info)
        else:
            if not self.preview_enabled or self.preview_stamp is None or time.perf_counter_ns()-self.preview_stamp>300e6:
                raise ValueError('현재 몸통 관절이 보여야 축 기준을 등록할 수 있습니다.')
            axis=reference(self.axis_history,kind,time.monotonic(),self.axis_up)
            self.display_alignment='reference'
            if kind=='standing':
                self.axis_up=axis;self.axis_long=None
                self.axis_reference_info=dict(source='user_standing_reference')
            else:self.axis_long=axis
        self.posture_filter.reset();self.posture_3d=None
        if self.preview_candidate is not None and kind!='bed':
            atomic_json(self.directory/'preview-axis-reference.json',dict(
                candidate_hash=object_hash(self.preview_candidate['cameras']),
                up=self.axis_up.tolist() if self.axis_up is not None else None,
                long=self.axis_long.tolist() if self.axis_long is not None else None,
                reference_info=deepcopy(self.axis_reference_info)))
        return dict(standing_registered=self.axis_reference_info['source']=='user_standing_reference',up_registered=self.axis_up is not None,reference_info=deepcopy(self.axis_reference_info),lying_registered=self.axis_long is not None,
            rotation=rotation(self.axis_up,self.axis_long).tolist() if self.axis_up is not None else np.eye(3).tolist(),
            accuracy_verified=False,used_for_training=False)

    def configure(self, values):
        config = deepcopy(validate_config(values))
        if self.calibration_job['state'] in ('countdown','collecting','running'):
            self.cancel_calibration()
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json(self.directory/'room.json', config)
        if config['bindings']!=self.config['bindings']:
            self.axis_up=None;self.axis_long=None;self.axis_reference_info=dict(source='none')
        self.config = config
        self.reset()
        return deepcopy(config)

    def set_setup(self,values,save=True):
        if save and self.calibration_job['state'] in ('countdown','collecting','running'):
            raise ValueError('보정 중에는 방식을 변경할 수 없습니다. 먼저 취소하세요.')
        from .calibration_methods import pattern_config
        if values.get('method') not in ('automatic_background','screen_checkerboard','screen_charuco','screen_aruco','manual','measured_points','natural_features'):
            raise ValueError('Unsupported calibration method')
        pattern=pattern_config(values['pattern'])
        manual=values.get('manual_cameras',[])
        if not isinstance(manual,list) or len(manual) not in (0,2):raise ValueError('Manual camera list must contain two cameras')
        for c in manual:
            for key in ('position','look_at'):
                v=np.asarray(c[key],float)
                if v.shape!=(3,) or not np.isfinite(v).all():raise ValueError('Manual positions must be finite XYZ')
            if np.linalg.norm(np.asarray(c['position'])-c['look_at'])<1e-6:raise ValueError('Camera direction must differ from position')
        result=dict(method=values['method'],pattern=pattern,manual_cameras=deepcopy(manual))
        if save:
            self.directory.mkdir(parents=True,exist_ok=True);atomic_json(self.directory/'setup.json',result)
        self.setup=result
        return deepcopy(result)

    def save_auto_inputs(self,values):
        from .automatic_calibration import validate_inputs
        settings=deepcopy(validate_inputs(values))
        self.directory.mkdir(parents=True,exist_ok=True)
        atomic_json(self.directory/'automatic-inputs.json',settings)
        self.auto_inputs=settings
        return deepcopy(settings)

    def schedule_automatic(self,values,now_ns=None):
        with self.job_lock:
            if self.calibration_job['state'] in ('countdown','collecting','running'):
                raise ValueError('이미 보정 대기/수집/계산 중입니다.')
            self.save_auto_inputs(values)
            self.auto_cancel=threading.Event()
            self.auto_samples=[];self.auto_bindings=self.config['bindings'][:]
            now=time.perf_counter_ns() if now_ns is None else now_ns
            self.auto_deadline=now+10_000_000_000
            self.config.update(installation_confirmed=False,same_person_confirmed=False)
            self.candidates=None
            self.calibration_job=dict(state='countdown',error=None,result=None,remaining_seconds=10,
                message='10초 후 정적 배경을 수집합니다. 사람은 시야에서 비켜 주세요. 카메라는 고정하세요.')
            return deepcopy(self.calibration_job)

    def cancel_calibration(self):
        with self.job_lock:
            self.auto_cancel.set();self.auto_samples=[]
            self.calibration_job=dict(state='cancelled',error=None,result=None)
        return deepcopy(self.calibration_job)

    def tick_automatic(self,sessions,now_ns=None):
        now=time.perf_counter_ns() if now_ns is None else now_ns
        state=self.calibration_job['state']
        if state not in ('countdown','collecting'):return
        if self.auto_cancel.is_set():return
        if now<self.auto_deadline:
            self.calibration_job=dict(self.calibration_job,remaining_seconds=max(0,int(np.ceil((self.auto_deadline-now)/1e9))))
            return
        self.calibration_job=dict(self.calibration_job,state='collecting',remaining_seconds=0)
        if self.config['bindings']!=self.auto_bindings:
            self.cancel_calibration();return
        if now>self.auto_deadline+8_000_000_000:
            self.calibration_job=dict(state='failed',result=None,error='10초 대기 후 신선한 실제 카메라 세 쌍을 확보하지 못했습니다. 연결/시간 차이/추론 상태를 확인하세요.')
            self.auto_samples=[];return
        pair=self.latest_pair
        if pair is None or any(v['time_ns']<self.auto_deadline for v in pair):return
        if any(sid not in sessions or sessions[sid].state.get('closed') or sessions[sid].state.get('phase') in ('error','closing','ended') for sid in self.auto_bindings):return
        if any(v['source_kind'] not in ('camera','pi_camera') or now-v['time_ns']>self.config['max_age_ms']*1e6 for v in pair):return
        if abs(pair[0]['time_ns']-pair[1]['time_ns'])>self.config['max_skew_ms']*1e6:return
        if self.auto_samples and min(v['time_ns'] for v in pair)-max(v['time_ns'] for v in self.auto_samples[-1])<350_000_000:return
        self.auto_samples.append(pair)
        self.calibration_job=dict(self.calibration_job,collected_pairs=len(self.auto_samples))
        if len(self.auto_samples)<3:return
        samples=self.auto_samples[:];self.auto_samples=[]
        settings=deepcopy(self.auto_inputs);cancel=self.auto_cancel;bindings=self.auto_bindings[:]
        self.calibration_job=dict(state='running',error=None,result=None,collected_pairs=3)
        def work():
            try:
                from .automatic_calibration import solve_background
                result=solve_background(samples,settings)
                result['capture_bindings']=bindings
                with self.job_lock:
                    if cancel.is_set():return
                    atomic_json(self.directory/'automatic-result.json',dict(result=result,sha256=object_hash(result)))
                    self.auto_saved=result;self.candidates=result
                    atomic_json(self.directory/'calibration-candidate.json',result)
                    self.calibration_job=dict(state='needs_input' if result['status']=='needs_input' else 'complete',error=None,result=result)
            except Exception as exc:
                with self.job_lock:
                    if not cancel.is_set():self.calibration_job=dict(state='failed',error=str(exc),result=None,metrics=getattr(exc,'metrics',{}))
        threading.Thread(target=work,daemon=True,name='automatic-background-calibration').start()

    def restore_automatic(self):
        if self.calibration_job['state'] in ('countdown','collecting','running'):raise ValueError('진행 중인 보정을 먼저 취소하세요.')
        if self.auto_saved is None:raise ValueError('저장된 자동 보정 후보가 없습니다.')
        self.candidates=deepcopy(self.auto_saved)
        self.calibration_job=dict(state='needs_input' if self.candidates.get('status')=='needs_input' else 'complete',
                                  error=None,result=self.candidates)
        return deepcopy(self.calibration_job)

    def start_calibration(self,values):
        if self.setup['method']=='automatic_background':return self.schedule_automatic(values)
        if self.calibration_job['state'] in ('countdown','collecting','running'):raise ValueError('보정 계산이 이미 진행 중입니다.')
        method=self.setup['method'];pattern=deepcopy(self.setup['pattern'])
        pair=self.latest_pair
        self.calibration_job=dict(state='running',error=None,result=None)
        self.candidates=None
        def work():
            try:
                from .calibration_methods import screen_solve,measured_points_solve,natural_pose
                if method.startswith('screen_'):
                    result=screen_solve(self.directory/'captures',pattern,values['anchor_pair'],values.get('anchor_world'))
                elif method=='measured_points':result=measured_points_solve(values)
                elif method=='natural_features':
                    if pair is None:raise ValueError('동기화된 카메라 영상이 필요합니다.')
                    if time.perf_counter_ns()-min(v['time_ns'] for v in pair)>self.config['max_age_ms']*1e6:
                        raise ValueError('영상이 오래되었습니다. 신선한 동기화 쌍이 필요합니다.')
                    if len(values['cameras'])!=2 or any(v['source_kind'] not in ('camera','pi_camera') for v in pair):
                        raise ValueError('두 실제 카메라와 두 카메라의 내부 보정값이 필요합니다.')
                    for v,c in zip(pair,values['cameras']):
                        if [v['width'],v['height']]!=c['image_resolution']:raise ValueError('내부 보정값의 해상도가 현재 영상과 다릅니다.')
                    result=natural_pose([v['rgb'] for v in pair],values['cameras'])
                else:raise ValueError('수동 위치는 시각화 전용입니다. 검증된 보정으로 승격하지 않습니다.')
                self.candidates=result
                atomic_json(self.directory/'calibration-candidate.json',result)
                self.calibration_job=dict(state='complete',error=None,result=result)
            except Exception as exc:self.calibration_job=dict(state='failed',error=str(exc),result=None)
        threading.Thread(target=work,daemon=True,name='room-calibration').start()
        return dict(state='running')

    def install(self, value):
        calibration = deepcopy(validate(value))
        if self.calibration_job['state'] in ('countdown','collecting','running'):raise ValueError('진행 중인 보정을 먼저 취소하세요.')
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json(self.directory/'calibration.json', calibration)
        self.calibration, self.error = calibration, None
        self.config.update(installation_confirmed=False, same_person_confirmed=False)
        self.reset()
        return dict(status='loaded', calibration=calibration)

    def ingest(self, session, frame):
        if session.id not in self.config['bindings']:
            return
        slot = self.config['bindings'].index(session.id)
        row = frame['row']
        rgb = frame['rgb']
        hint=None
        if self.preview_enabled and self.config['bindings']==self.preview_bindings and self.preview_annotation:
            try:
                annotation=self.preview_annotation;points=np.asarray(annotation['points'][slot],float)
                if (annotation['resolutions'][slot]==[rgb.shape[1],rgb.shape[0]] and points.shape==(4,2)
                        and np.isfinite(points).all() and np.all(points>=0) and np.all(points<=[rgb.shape[1],rgb.shape[0]])):
                    hint=dict(bounds=[float(points[:,0].min()),float(points[:,1].min()),float(points[:,0].max()),float(points[:,1].max())],resolution=[rgb.shape[1],rgb.shape[0]])
            except (KeyError,TypeError,ValueError,IndexError):pass
        session.worker.engine.pose_region_hint=hint
        stamp = session.worker.engine.origin + int(row['received_ns'])
        threshold = self.calibration['quality']['min_visibility'] if self.calibration else .5
        # Require exactly one person in each image. Cross-view identity remains an operator assertion.
        joints = [None] * 17
        people = row.get('mapped_people') or []
        if row.get('processing_status') == 'ok' and len(people) == 1:
            from .joint_inspection import inspect_joints
            inspected = inspect_joints({'people': people}, {'candidates': row['raw_candidates']},
                                      session.worker.engine.profile.image_block_id)[0]['joints']
            for i, j in enumerate(inspected):
                v = j['visibility']['value']
                presence = j['presence']['value']
                if (j['availability'] == 'predicted' and j['x_px'] is not None and j['y_px'] is not None
                        and 0 <= j['x_px'] < rgb.shape[1] and 0 <= j['y_px'] < rgb.shape[0]
                        and v is not None and v >= threshold and (presence is None or presence >= threshold)):
                    joints[i] = [j['x_px'], j['y_px'], min(v, presence) if presence is not None else v]
        packet = dict(frame_id=row['stream_frame_id'], time_ns=stamp, width=rgb.shape[1], height=rgb.shape[0],
                      joints=joints, rgb=rgb, synthetic=session.kind == 'demo',
                      source_kind=session.kind)
        from .automatic_calibration import person_boxes
        packet.update(person_boxes=person_boxes(row,rgb.shape[1],rgb.shape[0]),processing_ok=row.get('processing_status')=='ok')
        packet['people_count']=len(people)
        packet['world_joints']=None
        packet['rotation_ccw_degrees']=next((c.get('inference_rotation_ccw_degrees',0) for c in row.get('raw_candidates',[]) if isinstance(c,dict)),0)
        if len(people)==1 and packet['processing_ok']:
            from .viewpoint_posture import world_points
            packet['world_joints']=world_points(row,people[0])
        if self.preview_enabled and self.preview_candidate is not None:
            from .experimental_preview import monocular_preview
            c=self.preview_candidate['cameras'][slot]
            if packet['source_kind'] in ('camera','pi_camera') and [packet['width'],packet['height']]==c['image_resolution']:
                packet['mono_preview']=monocular_preview(packet,self.preview_candidate,slot)
        self.latest_observations[slot]=packet
        self.refresh_preview()

        self.received[slot] += 1
        buf = self.buffers[slot]
        buf.append(packet)
        if len(buf) > 8:
            buf.popleft(); self.dropped[slot] += 1
        now = time.perf_counter_ns()
        for i, queue in enumerate(self.buffers):
            while queue and now - queue[0]['time_ns'] > self.config['max_age_ms'] * 1e6:
                queue.popleft(); self.dropped[i] += 1
        if not all(self.buffers):
            return
        a, b = min(((a, b) for a in self.buffers[0] for b in self.buffers[1]),
                   key=lambda pair: abs(pair[0]['time_ns'] - pair[1]['time_ns']))
        if abs(a['time_ns'] - b['time_ns']) > self.config['max_skew_ms'] * 1e6:
            self.reason = 'timestamp_skew_exceeded'
            return
        for i, selected in enumerate((a, b)):
            while self.buffers[i]:
                item = self.buffers[i].popleft()
                if item is selected:
                    break
                self.dropped[i] += 1
        self.latest_pair = (a, b)
        self.paired += 1
        self.refresh_preview()
        self.skeleton = None
        self.reason = self.gate()
        if self.reason is None:
            self.skeleton = reconstruct(self.latest_pair, self.calibration)
            if not any(j['valid'] for j in self.skeleton['joints']):
                self.reason = 'no_reconstructable_joints'
        if not self.preview_enabled:
            from .bed_posture_3d import classify,room_bed
            bed=room_bed(self.config) if self.config['dimensions_source']=='measured' else None
            self.posture_3d=self.posture_filter.update(classify(self.skeleton if self.reason is None else None,bed),time.monotonic())

    def gate(self):
        if self.latest_pair is None:
            return self.reason
        if self.calibration is None:
            return 'uncalibrated'
        if max(self.calibration['stereo_reprojection_error'], self.calibration['anchor_reprojection_error'],
               *(c['reprojection_error'] for c in self.calibration['cameras'])) > self.calibration['quality']['max_reprojection_error_px']:
            return 'calibration_error_exceeded'
        if not self.config['installation_confirmed']:
            return 'confirm_camera_installation'
        if not self.config['same_person_confirmed']:
            return 'confirm_same_person'
        if any(v['source_kind'] not in ('camera', 'pi_camera') for v in self.latest_pair):
            return 'live_camera_inputs_required'
        for v, c in zip(self.latest_pair, self.calibration['cameras']):
            if [v['width'], v['height']] != c['image_resolution']:
                return 'resolution_changed_recalibrate'
        return None

    def status(self, sessions):
        now = time.perf_counter_ns()
        pair = self.latest_pair
        age = max((now-v['time_ns']) / 1e6 for v in pair) if pair else None
        available = all(sid in sessions and not sessions[sid].state.get('closed') and
                        sessions[sid].state.get('phase') not in ('error', 'closing', 'ended') for sid in self.config['bindings'])
        reason = 'inputs_unavailable' if not available else 'stale_pair' if age is not None and age > self.config['max_age_ms'] else self.gate() or self.reason
        from .bed_posture_3d import classify
        posture=deepcopy(self.posture_3d) if self.posture_3d is not None else classify(None,None)
        def alive(index):
            session=sessions.get(self.config['bindings'][index])
            return session is not None and not session.state.get('closed') and session.state.get('phase') not in ('error','closing','ended')
        selected_camera=self.preview_skeleton.get('source_camera') if self.preview_skeleton else None
        preview_available=alive('AB'.index(selected_camera)) if selected_camera else all(alive(i) for i in (0,1))
        preview_fresh=(self.preview_stamp is not None and 0<=now-self.preview_stamp<=min(self.config['max_age_ms'],300)*1e6 and preview_available)
        if (self.preview_enabled and not preview_fresh) or (not self.preview_enabled and (not available or age is None or age>self.config['max_age_ms'])):
            posture=classify(None,None)
        preview_pose=deepcopy(self.preview_skeleton) if self.preview_enabled and preview_fresh and self.preview_reason is None else None
        preview_cameras=deepcopy(self.preview_candidate['display_cameras']) if self.preview_enabled else []
        preview_bed=deepcopy(self.preview_bed) if self.preview_enabled else None
        alignment=dict(standing_registered=self.axis_reference_info['source']=='user_standing_reference',up_registered=self.axis_up is not None,reference_info=deepcopy(self.axis_reference_info),lying_registered=self.axis_long is not None,used_for_training=False)
        from .preview_alignment import rotation,pose_in_display,cameras_in_display,bed_transform
        matrix=np.eye(3);offset=None
        alignment['mode']=self.display_alignment
        alignment['bed_surface_height_m']=self.bed_surface_height
        if self.display_alignment=='bed' and preview_bed:
            matrix,offset=bed_transform(preview_bed,self.bed_surface_height)
            alignment['source']='bed_plane';alignment['scale_verified']=False
            alignment['translation']=offset.tolist()
        elif self.display_alignment=='reference' and self.axis_up is not None:
            matrix=rotation(self.axis_up,self.axis_long)
            alignment['source']='body_or_pitch_reference'
        else:alignment['source']='camera_a'
        alignment['rotation']=matrix.tolist()
        preview_pose=pose_in_display(preview_pose,matrix,offset)
        preview_cameras=cameras_in_display(preview_cameras,matrix,offset)
        if preview_bed:
            from .bed_posture_3d import bed_frame
            corners=np.array(preview_bed['corners'])@matrix.T+(np.zeros(3) if offset is None else offset)
            if offset is not None:
                alignment['plane_fit_residual']=preview_bed['plane_fit_error']
                # Draw the fitted surface; preserve original raw corners for geometry/diagnostics.
                corners[:,1]=self.bed_surface_height
            preview_bed=bed_frame(corners,'bed_preview' if offset is not None else 'camera_a_preview_aligned',False,preview_bed['source'])
        return dict(config=deepcopy(self.config), calibration=deepcopy(self.calibration), error=self.error,
                    learning_observation=dict(
                        observation_id=self.preview_skeleton.get('observation_id') if self.preview_enabled and self.preview_skeleton else str([p['frame_id'] for p in pair]) if pair else None,
                        observed_unix=time.time()-(now-(self.preview_stamp if self.preview_enabled else min(p['time_ns'] for p in pair)))/1e9 if (self.preview_stamp is not None if self.preview_enabled else pair is not None) else None,
                        age_ms=(now-self.preview_stamp)/1e6 if self.preview_enabled and self.preview_stamp is not None else age,
                        time_basis='host_receive_not_exposure',source=self.preview_skeleton.get('source') if self.preview_skeleton else None,
                        calibration_verified=not self.preview_enabled and self.calibration is not None and reason is None),
                    posture_3d=posture,
                    experimental_preview=dict(enabled=self.preview_enabled,accuracy_verified=False,
                        input_rotations=[p.get('rotation_ccw_degrees',0) if p else 0 for p in self.latest_observations],
                        bed=preview_bed,alignment=alignment,
                        reason=('stale_pair' if self.preview_skeleton is not None and not preview_fresh else self.preview_reason),
                        cameras=preview_cameras,skeleton=preview_pose),
                    setup=deepcopy(self.setup),calibration_job=deepcopy(self.calibration_job),
                    automatic_inputs=deepcopy(self.auto_inputs),automatic_saved=self.auto_saved is not None,
                    captures=self.capture_records[:],
                    reason=reason, skeleton=deepcopy(self.skeleton) if reason is None else None,
                    synchronization=dict(pairs=self.paired, received=self.received[:], dropped=self.dropped[:],
                        pair_rate_fps=self.paired/max(.001, time.monotonic()-self.started),
                        pair_fraction=2*self.paired/sum(self.received) if sum(self.received) else None,
                        skew_ms=abs(pair[0]['time_ns']-pair[1]['time_ns'])/1e6 if pair else None,
                        age_ms=age, time_basis='host_monotonic_receive_not_exposure',
                        frame_ids=[v['frame_id'] for v in pair] if pair else None))

    def capture(self, sessions):
        import cv2
        pair = self.latest_pair
        if (pair is None or self.status(sessions)['reason'] == 'inputs_unavailable'
                or self.status(sessions)['synchronization']['age_ms'] > self.config['max_age_ms']):
            raise ValueError('신선한 동기화 프레임 쌍이 필요합니다.')
        if any(v['source_kind'] not in ('camera', 'pi_camera') for v in pair):
            raise ValueError('보정 촬영에는 실제 카메라 입력을 사용하세요.')
        identity = '-'.join(str(v['frame_id']) for v in pair)
        if self.last_capture == identity:
            raise ValueError('이미 저장한 프레임 쌍입니다. 체커보드를 이동하고 다음 쌍을 촬영하세요.')
        directory = self.directory/'captures'/identity
        # UUID prevents collisions across input sessions and preserves existing captures.
        directory = directory.with_name(identity+'-'+uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        for cid, packet in zip(('camera_a', 'camera_b'), pair):
            ok, encoded = cv2.imencode('.png', cv2.cvtColor(packet['rgb'], cv2.COLOR_RGB2BGR))
            if not ok:
                raise ValueError('보정 이미지를 저장하지 못했습니다.')
            (directory/(cid+'.png')).write_bytes(encoded.tobytes())
        atomic_json(directory/'pair.json', dict(bindings=self.config['bindings'],
                    pattern=deepcopy(self.setup['pattern']),
                    frames=[{k:v for k,v in p.items() if k not in ('rgb', 'joints')} for p in pair],
                    exposure_synchronized=False))
        self.last_capture = identity
        self.capture_records.append(directory.name);self.capture_records=self.capture_records[-50:]
        return dict(pair_id=directory.name, directory=str(directory))
