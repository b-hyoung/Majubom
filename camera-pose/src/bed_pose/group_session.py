"""Two-input lifecycle, durable references and post-recording receive-time alignment."""
from pathlib import Path
import copy
import queue
import threading
import time
import uuid
from .io_utils import atomic_json, file_hash, load_json
from .live_recording import verify_live


class MeasurementGroup:
    def __init__(self, members, root, max_skew_ns=None, targets=(), plan=None, record_video=False, association_policy=None, blackbox=False, segment_seconds=None):
        if type(blackbox) is not bool:raise ValueError('저장 방식을 확인하세요.')
        if segment_seconds is not None and (not blackbox or type(segment_seconds) is not int or not 1<=segment_seconds<=86400):raise ValueError('구간 길이는 1~86400초여야 합니다.')
        self.blackbox=blackbox
        self.segment_seconds=segment_seconds
        self.segment_due=None
        self.rotating=None
        self.segment_jobs={}
        self.segment_tail=None
        self.segment_carry=None
        self.segment_carry_index=-1
        if type(record_video) is not bool:raise ValueError('녹화 설정을 확인하세요.')
        if record_video and any(s.kind not in ('camera','demo') for s in members):
            raise ValueError('두 입력 모두 카메라여야 영상 녹화를 사용할 수 있습니다.')
        self.record_video=record_video
        from .bed_association import POLICY as BED_POLICY, PRIORITY_POLICY
        if association_policy not in (None,BED_POLICY,PRIORITY_POLICY):raise ValueError('지원하지 않는 대상 연결 방식입니다.')
        self.association_policy=association_policy
        from .measurement_plan import validate_plan
        plan=validate_plan(plan)
        self.members = list(members)
        self.id = uuid.uuid4().hex
        self.directory = Path(root)/'groups'/self.id
        self.directory.mkdir(parents=True, exist_ok=False)
        created=str(time.perf_counter_ns())
        self.data = {'schema_version':'group-0.1-draft', 'group_id':self.id,
                     'measurement_mode':'planned_single' if plan else 'free',
                     'video_recording_requested':record_video,
                     'phase':'preparing' if plan else 'starting', 'ledger_complete':False,
                     'created_perf_counter_ns':created,
                     'operator_initial_confirmation_perf_counter_ns':created,
                     'measurement_plan':dict(plan,status='preparing',preparation_started_perf_counter_ns=created,
                         ready_perf_counter_ns=None,measurement_started_perf_counter_ns=None,measurement_stopped_perf_counter_ns=None) if plan else None,
                     'alignment_max_skew_ns':str(max_skew_ns) if max_skew_ns is not None else None,
                     'requested_start_perf_counter_ns':None if plan else created,
                     'requested_stop_perf_counter_ns':None,
                     'members':[{'input_id':s.id,'title':s.title,'run_directory':None,
                                 'started_ack_perf_counter_ns':None,'stopped_ack_perf_counter_ns':None} for s in members],
                     'errors':[], 'alignment_status':'pending_until_stop' if max_skew_ns is not None else 'not_implemented',
                     'target_association_status':'operator_initialized_continuity_unverified' if targets else 'not_configured',
                     'target_selections':list(targets),
                     'target_pair_status':'operator_initialized_not_verified' if targets else 'not_configured',
                     'same_person_assertion':'operator_report_not_verified' if targets else None,
                     'posture_prediction':{'status':'not_implemented','label':None}}
        if blackbox:self.data['storage_class']='blackbox'
        if segment_seconds is not None:self.data.update(segment_seconds=segment_seconds,segments=[])
        if association_policy is not None:self.data['association_policy']=association_policy
        if association_policy==PRIORITY_POLICY:
            self.data['target_association_status']='bed_priority_per_frame_unverified'
            self.data['target_pair_status']='bed_priority_not_identity_verified'
        from .multiview_posture import POLICY,EVIDENCE_POLICY,FORCED_POLICY,supported
        profiles=[getattr(s.worker.engine,'body_state_profile',None) for s in members]
        if supported(profiles) and all(s.worker.engine.posture_classifier and s.worker.engine.posture_classifier.profile['version'] in ('automatic-posture-rules-0.8','automatic-posture-rules-0.9','automatic-posture-rules-0.10','automatic-posture-rules-0.11','automatic-posture-rules-0.12') for s in members):
            strict=any(s.worker.engine.posture_classifier.profile['version']=='automatic-posture-rules-0.11' for s in members)
            forced=any(s.worker.engine.posture_classifier.profile['version']=='automatic-posture-rules-0.12' for s in members)
            self.data['posture_fusion_policy']=copy.deepcopy(FORCED_POLICY if forced else EVIDENCE_POLICY if strict else POLICY)
        from .decision_evidence import PROFILE as DECISION_PROFILE
        self.data['decision_evidence_profile']=copy.deepcopy(DECISION_PROFILE)
        from .posture_stability import Stabilizer,merged
        self.stability=Stabilizer(merged([s.worker.engine.stability.profile for s in members]))
        self.data['stability_profile']=dict(self.stability.profile)
        self._stable_ids=None;self._stable_gate=None;self._stable_sequence=0;self._stable_latest=None
        self.finished, self.failed = False, False
        self._finalize_job=None
        self.save()

    def save(self):
        atomic_json(self.directory/'manifest.json', self.data)

    def start(self):
        if self.data['measurement_plan'] is None:self._begin()

    def _begin(self):
        if self.finished or self.data['phase'] not in ('preparing','starting'):return
        if any(s.state['closed'] or s.worker.stop_requested.is_set() or s.state['recording'] or s.state['pending'] or (not s.state['region'] and (self.association_policy is not None or not self.data.get('posture_fusion_policy'))) for s in self.members):
            self.fail('PLAN_START_INPUT_UNAVAILABLE')
            return
        self.data['phase']='starting'
        self.data['requested_start_perf_counter_ns']=str(time.perf_counter_ns())
        if self.data['measurement_plan']:
            self.data['measurement_plan']['status']='starting'
        self.save()
        for session in self.members:
            session.state['pending']='start'
            segment={'series_id':session.worker.engine.stream_id,'index':0,'previous_measurement_id':None,'group_id':self.id} if self.segment_seconds is not None else None
            session.worker.submit('start',record_video=self.record_video,reset_tracking=self.association_policy is not None,blackbox=self.blackbox,group_segment=segment)

    def ready(self,trigger='operator_ready'):
        if self.finished or self.data['phase']!='preparing':raise ValueError('준비 중인 계획 측정이 없습니다.')
        self.data['measurement_plan']['ready_trigger']=trigger
        self.data['measurement_plan']['ready_perf_counter_ns']=str(time.perf_counter_ns())
        self._begin()

    def cancel(self,reason='user_cancelled'):
        self.data['phase']='failed' if self.failed else 'cancelled'
        self.data['measurement_plan'].update(status='cancelled_before_start',cancel_reason=reason,
            cancelled_perf_counter_ns=str(time.perf_counter_ns()))
        self.save();self.finished=True

    def stop(self, close_inputs=False):
        if self.finished:return
        if self._finalize_job is not None:
            # Closing devices must remain possible while immutable records finalize.
            if close_inputs:
                for session in self.members:session.worker.request_close()
            return
        if self.data['phase']=='preparing':
            self.cancel('input_closed' if close_inputs else 'user_cancelled')
            if close_inputs:
                for session in self.members:session.worker.request_close()
            return
        self.data['phase']='stopping'
        if self.data['measurement_plan']:
            self.data['measurement_plan'].setdefault('stop_trigger','group_error' if self.failed else 'input_ended' if close_inputs else 'operator_stop')
        if self.data['requested_stop_perf_counter_ns'] is None:
            self.data['requested_stop_perf_counter_ns']=str(time.perf_counter_ns())
            if self.data['measurement_plan']:
                self.data['measurement_plan']['measurement_stopped_perf_counter_ns']=self.data['requested_stop_perf_counter_ns']
        self.save()
        for session in self.members:
            if close_inputs:
                session.worker.request_close()
            elif not session.state['closed']:
                session.state['pending']='stop'
                session.worker.submit('stop')

    def message(self, session, kind, value):
        if self.finished or self._finalize_job is not None:return
        member=next(m for m in self.data['members'] if m['input_id']==session.id)
        if member['stopped_ack_perf_counter_ns'] is not None and kind in ('error','command_rejected'):
            return  # Preview errors after this member's interval do not rewrite its recording outcome.
        if kind=='started':
            member['measurement_started_perf_counter_ns']=str(session.worker.engine.origin+int(value['started_ns']))
            member['run_directory']=value['directory']
            member['started_ack_perf_counter_ns']=str(time.perf_counter_ns())
            if all(m['run_directory'] for m in self.data['members']) and self.data['phase']=='starting':
                self.data['phase']='recording'
                if self.segment_seconds is not None:self.segment_due=time.monotonic()+self.segment_seconds
                if self.data['measurement_plan']:
                    self.data['measurement_plan'].update(status='recording',measurement_started_perf_counter_ns=str(max(int(m['measurement_started_perf_counter_ns']) for m in self.data['members'])))
            if self.data['measurement_plan'] and all(m['run_directory'] for m in self.data['members']):
                plan=self.data['measurement_plan']
                plan['measurement_started_perf_counter_ns']=str(max(int(m['measurement_started_perf_counter_ns']) for m in self.data['members']))
                plan['stop_before_both_started']=bool(self.data['requested_stop_perf_counter_ns'] and
                    int(self.data['requested_stop_perf_counter_ns'])<int(plan['measurement_started_perf_counter_ns']))
            self.save()
        elif kind=='segment_rotated' and self.segment_seconds is not None:
            if self.rotating is None or session.id not in self.rotating['pending']:
                self.fail('UNEXPECTED_SEGMENT_ROTATION',session.id);return
            if value['previous_directory']!=member['run_directory'] or value['segment_index']!=self.rotating['index']+1:
                self.fail('SEGMENT_ROTATION_MISMATCH',session.id);return
            self.rotating['pending'].remove(session.id)
            member['run_directory']=value['directory']
            if not self.rotating['pending']:
                self._finish_segment(self.rotating)
                self.rotating=None
                self.segment_due=time.monotonic()+self.segment_seconds
            self.save()
        elif kind=='target_selected':
            self.data.setdefault('target_reselections',[]).append({'input_id':session.id,
                'tracking':value['tracking'],'received_ack_perf_counter_ns':str(time.perf_counter_ns())})
            self.data['target_pair_status']='reconfirmation_required'
            self.save()
        elif kind=='stopped':
            member['stopped_ack_perf_counter_ns']=str(time.perf_counter_ns())
            if value['manifest']['run_status']!='succeeded':
                self.fail('MEMBER_RECORDING_FAILED',session.id)
            self.save()
        elif kind in ('error','command_rejected') and value.get('action')!='target_select':
            self.fail('MEMBER_'+kind.upper(),session.id)
        elif kind in ('eof','closed') and self.data['phase'] in ('preparing','starting','recording'):
            # One input ending also ends the partner interval.
            self.stop(close_inputs=True)

    def fail(self, code, input_id=None):
        self.failed=True
        self.data['errors'].append({'code':code,'input_id':input_id})
        self.stop(close_inputs=True)

    def tick(self):
        if self.finished:return
        if self.rotating is not None and any(s.id in self.rotating['pending'] and s.state['closed'] for s in self.members):
            self.rotating=None
            self.failed=True
            self.data['errors'].append({'code':'SEGMENT_ROTATION_INTERRUPTED'})
            self.stop(close_inputs=True)
        for index,job in list(self.segment_jobs.items()):
            try:result=job.get_nowait()
            except queue.Empty:continue
            del self.segment_jobs[index]
            segment=next(item for item in self.data['segments'] if item['index']==index)
            segment.update(status=result['phase'],ledger_complete=result['ledger_complete'])
            if index>self.segment_carry_index and result.get('posture_predictions',{}).get('status')=='generated':
                self.segment_carry=result['posture_predictions']
                self.segment_carry_index=index
            if result['phase']!='completed' or not result['ledger_complete']:
                self.failed=True;self.data['errors'].append({'code':'SEGMENT_FINALIZATION_FAILED','segment_index':index})
                if self.data['phase'] in ('starting','recording'):self.stop(close_inputs=True)
            self.save()
        plan=self.data['measurement_plan']
        if self.data['phase']=='preparing':
            if any(s.state['closed'] or s.state['phase']=='closing' for s in self.members):self.fail('PLAN_INPUT_CLOSED')
            elif plan['preparation_mode']=='timer' and time.perf_counter_ns()-int(plan['preparation_started_perf_counter_ns'])>=plan['preparation_seconds']*1e9:self.ready('timer_elapsed')
            return
        if self.data['phase']=='recording' and plan and plan['duration_seconds'] is not None:
            if time.perf_counter_ns()-int(plan['measurement_started_perf_counter_ns'])>=plan['duration_seconds']*1e9:
                plan['stop_trigger']='duration_elapsed'
                self.stop()

        if self.data['phase']=='recording':self.public(update_stability=True)

        if self.data['phase']=='recording' and self.segment_due is not None and self.rotating is None and time.monotonic()>=self.segment_due:
            index=len(self.data['segments'])
            self.rotating={'index':index,'pending':{s.id for s in self.members},'members':copy.deepcopy(self.data['members'])}
            for session in self.members:session.worker.submit('rotate_group_segment')

        if self._finalize_job is not None:
            try:self.data=self._finalize_job.get_nowait()
            except queue.Empty:return
            self.finished=True
            self.failed=self.data['phase']=='failed'
            return
        done=all(m['stopped_ack_perf_counter_ns'] is not None or member.state['closed']
                 for member,m in zip(self.members,self.data['members']))
        if not done or self.rotating is not None or self.segment_jobs:return
        self.data['phase']='finalizing'
        self.save()
        self._finalize_job=queue.Queue(maxsize=1)
        work=copy.copy(self)
        work.data=copy.deepcopy(self.data)
        if self.segment_seconds is not None:
            work.data['group_segment_index']=len(self.data['segments'])
            self._apply_segment_carry(work.data,self.segment_carry)
        def finalize():
            try:work._finish_record()
            except Exception as exc:
                work.data.update(phase='failed',ledger_complete=False)
                work.data['errors'].append({'code':'GROUP_FINALIZATION_FAILED','exception_type':type(exc).__name__})
                try:work.save()
                except Exception:pass
            self._finalize_job.put(work.data)
        threading.Thread(target=finalize,daemon=True).start()

    def _finish_segment(self, rotation):
        index=rotation['index']
        directory=self.directory/'segments'/str(index)
        directory.mkdir(parents=True,exist_ok=False)
        work=copy.copy(self)
        work.directory=directory
        work.data=copy.deepcopy(self.data)
        work.data.pop('segments',None)
        work.data['members']=rotation['members']
        work.data['group_segment_index']=index
        work.data['phase']='finalizing'
        work.failed=False;work.finished=False
        item={'index':index,'directory':str(directory),'status':'finalizing',
              'members':[m['run_directory'] for m in rotation['members']]}
        self.data['segments'].append(item)
        job=queue.Queue(maxsize=1);self.segment_jobs[index]=job
        previous=self.segment_tail
        tail={'done':threading.Event(),'result':None}
        self.segment_tail=tail
        def finalize():
            try:
                if previous is not None:
                    previous['done'].wait()
                    prior=previous['result']
                    if prior['phase']!='completed' or not prior['ledger_complete']:
                        raise RuntimeError('Previous group segment failed')
                    self._apply_segment_carry(work.data,prior.get('posture_predictions'))
                work._finish_record()
            except Exception as exc:
                work.data.update(phase='failed',ledger_complete=False)
                work.data['errors'].append({'code':'SEGMENT_FINALIZATION_FAILED','exception_type':type(exc).__name__})
                try:work.save()
                except Exception:pass
            tail['result']=work.data
            tail['done'].set()
            job.put(work.data)
        threading.Thread(target=finalize,daemon=True).start()

    @staticmethod
    def _apply_segment_carry(data,artifact):
        if isinstance(artifact,dict) and artifact.get('status')=='generated':
            checkpoint=artifact.get('final_stability_checkpoint')
            if checkpoint is not None:data['initial_group_stability_checkpoint']=copy.deepcopy(checkpoint)
            data['group_stability_sequence_offset']=artifact['next_stability_sequence']

    def _finish_record(self):
        for session,member in zip(self.members,self.data['members']):
            directory=member['run_directory']
            if directory is None:
                self.failed=True
                self.data['errors'].append({'code':'MEMBER_NOT_STARTED','input_id':session.id})
                continue
            try:
                summary=verify_live(directory)
                manifest=load_json(Path(directory)/'manifest.json')
                member['record_validation']=summary
                member['manifest_sha256']=file_hash(Path(directory)/'manifest.json')
                member['timing']={k:manifest[k] for k in ('clock_id','clock_origin_perf_counter_ns','started_ns','finished_ns','first_stream_frame_id','recorded_frames')}
                if summary['run_status']!='succeeded':self.failed=True
            except Exception as exc:
                self.failed=True
                self.data['errors'].append({'code':'MEMBER_VERIFICATION_FAILED','input_id':session.id,'exception_type':type(exc).__name__})
        if not self.failed and self.data['alignment_max_skew_ns'] is not None:
            try:
                from .time_alignment import align_group
                self.data['alignment']=align_group(self.data['members'],self.directory,int(self.data['alignment_max_skew_ns']),self.data['target_selections'])
                self.data['alignment_status']=self.data['alignment']['status']
            except Exception as exc:
                self.failed=True
                self.data['alignment_status']='failed'
                self.data['errors'].append({'code':'ALIGNMENT_FAILED','exception_type':type(exc).__name__})
        if not self.failed:
            try:
                from .fusion_inputs import build_inputs
                self.data['fusion_inputs']=build_inputs(self.data,self.directory)
                from .group_posture import build_predictions
                self.data['posture_predictions']=build_predictions(self.data,self.directory)
            except Exception as exc:
                self.failed=True
                self.data['errors'].append({'code':'FUSION_INPUT_FAILED','exception_type':type(exc).__name__})
        self.data['phase']='failed' if self.failed else 'completed'
        self.data['ledger_complete']=not any(e['code'] in ('MEMBER_NOT_STARTED','MEMBER_VERIFICATION_FAILED','ALIGNMENT_FAILED','FUSION_INPUT_FAILED','SEGMENT_FINALIZATION_FAILED','SEGMENT_ROTATION_INTERRUPTED') for e in self.data['errors'])
        self.data['finished_perf_counter_ns']=str(time.perf_counter_ns())
        if self.data['measurement_plan']:
            self.data['measurement_plan']['status']=self.data['phase']
        self.save()
        self.finished=True

    def public(self, update_stability=False):
        observations=[]
        if self.data['target_selections']:
            for member in self.members:
                people=member.view['people'] if member.view else None
                status=('input_unavailable' if member.state['closed'] or people is None else
                        'target_missing' if not people else 'target_ambiguous' if len(people)>1 else 'continuity_unverified')
                tracking=member.worker.engine.target_tracker.current()
                observations.append({'input_id':member.id,'status':status if status=='input_unavailable' else tracking['status'] if tracking['status']!='not_configured' else status,
                                     'reason':tracking.get('reason'),'track_id':tracking.get('track_id')})
        progress={}
        plan=self.data['measurement_plan']
        if plan:
            now=time.perf_counter_ns()
            if self.data['phase']=='preparing' and plan['preparation_seconds'] is not None:
                progress['preparation_remaining_seconds']=max(0,plan['preparation_seconds']-(now-int(plan['preparation_started_perf_counter_ns']))/1e9)
            if plan['measurement_started_perf_counter_ns']:
                end=plan['measurement_stopped_perf_counter_ns'] or str(now)
                progress['measurement_elapsed_seconds']=max(0,(int(end)-int(plan['measurement_started_perf_counter_ns']))/1e9)
        from .reference_posture import combine_views
        from .fusion_inputs import confirmations
        views=[]
        from .bed_association import eligible as bed_eligible
        for s in self.members:
            a=s.state.get('posture_assessment')
            tracking=s.state.get('target_tracking') or {}
            views.append({'input_id':s.id,'title':s.title,'closed':s.state['closed'],'assessment':a,
                'image_profile':getattr(s.worker.engine,'body_state_profile',None),
                'reference_profile':s.state.get('automatic_posture_profile') or s.state.get('posture_reference_profile'),
                'tracking_provisional':tracking.get('status')=='tracking_provisional',
                'unique_bed_candidate':bed_eligible(s.state.get('candidate_count'),a,s.state.get('region'),selection=s.state.get('bed_target_selection'),policy=self.association_policy or 'unique-person-in-bed-v1'),
                'time_ns':str(s.worker.engine.origin+int(a['received_ns'])) if a else '0',
                'max_age_ns':int(tracking.get('config',{}).get('max_gap_ms',500)*1e6)})
        posture=combine_views(views,int(self.data['alignment_max_skew_ns']) if self.data['alignment_max_skew_ns'] is not None else None,
            confirmations(self.data),self.data['phase']=='recording' and all(s.kind!='video_file' for s in self.members),time.perf_counter_ns(),fusion_policy=self.data.get('posture_fusion_policy'),association_policy=self.association_policy)
        from .multiview_posture import closest_candidate
        posture['live_closest_candidate']=closest_candidate(posture)
        from .decision_evidence import group as group_evidence
        posture['decision_evidence']=group_evidence(views,posture)
        from .posture_stability import project
        if update_stability:self._stable_latest=self.stable_public(views,posture)
        posture['stable_posture']=project(self._stable_latest,time.perf_counter_ns(),closed=self.data['phase']!='recording')
        if posture['stable_posture'] is not None and posture['status']!='predicted' and self.data['phase']=='recording':
            posture['stable_posture'].update(status='held',label=None,reasons=['instantaneous_unavailable'])
        return dict(self.data,directory=str(self.directory),target_observations=observations,plan_progress=progress,posture_assessment=posture)

    def stable_public(self, views, posture):
        """Live latest-pair preview, not the saved FIFO alignment sequence."""
        from .posture_stability import project,group_context,presentation_label
        label=lambda value:presentation_label(value,self.stability.profile['version'])
        ids=tuple((v.get('assessment') or {}).get('stream_frame_id',-1) for v in views)
        gate=(posture['status'],label(posture.get('label')),tuple(posture.get('reasons') or []))
        now=time.perf_counter_ns()
        context=group_context(views,posture,[s.state.get('bed_region_revision') for s in self.members])
        from .io_utils import object_hash
        context_changed=self.stability.context is not None and self.stability.context!=object_hash(context)
        same=ids==self._stable_ids and gate==self._stable_gate and not context_changed
        fresh=self._stable_ids is None or all(a>b for a,b in zip(ids,self._stable_ids))
        if not same and (context_changed or posture['status']!='predicted' or fresh or self._stable_gate is None or self._stable_gate[0]!='predicted'):
            if posture['status']!='predicted':self.stability.reset()
            self._stable_sequence+=1
            timestamp=max((int(v.get('time_ns','0')) for v in views),default=0)
            self.stability.observe(posture,timestamp,self._stable_sequence,context)
            self._stable_ids=ids;self._stable_gate=gate
        result=project(self.stability.result,now,closed=self.data['phase']!='recording')
        if result is not None:
            result['time_basis']='receive_time_approximation'
            result['sampling']='live_distinct_latest_pairs'
            if not fresh and not same and posture['status']=='predicted' and label(posture.get('label'))!=label(self.stability.result.get('instantaneous_label')):
                result.update(status='waiting_for_pair',label=None,reasons=['fresh_pair_required'])
        return result
