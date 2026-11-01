"""Causal label confirmation, separate from all instantaneous posture decisions."""
from copy import deepcopy
from .io_utils import object_hash

LEGACY = {'version':'posture-stability-0.1', 'confirm_ms':500, 'min_samples':3,
           'max_gap_ms':1000, 'expire_ms':1500, 'validation':'unvalidated_initial_values',
           'hold_policy':'clear_current_and_pending', 'future_frames':False}
DEFAULT = dict(LEGACY,version='posture-stability-0.2')
LABELS=('supine','side','edge_sitting','sitting','prone','lying','standing','other')


def presentation_label(label, version):
    return 'lying' if version==DEFAULT['version'] and label=='supine' else label


def profile(options=None):
    if options is not None and (not isinstance(options,dict) or set(options)-set(DEFAULT)):raise ValueError('Invalid stability settings')
    baseline=LEGACY if options and options.get('version')==LEGACY['version'] else DEFAULT
    p=dict(baseline)
    if options is not None:p.update(options)
    if (any(p[k]!=baseline[k] for k in ('version','validation','hold_policy','future_frames'))
            or p['future_frames'] is not False
            or any(type(p[k]) is not int for k in ('confirm_ms','min_samples','max_gap_ms','expire_ms'))
            or not 0<=p['confirm_ms']<=10000 or not 2<=p['min_samples']<=100
            or not 1<=p['max_gap_ms']<=p['expire_ms']<=60000):
        raise ValueError('안정화 설정: 확인 0~10000ms, 관측 2~100개, 최대 간격 ≤ 만료 시간(1~60000ms)')
    return p


def merged(profiles):
    configs=[profile(p) for p in profiles]
    version=DEFAULT['version'] if all(p['version']==DEFAULT['version'] for p in configs) else LEGACY['version']
    return profile({'version':version, 'confirm_ms':max(p['confirm_ms'] for p in configs),
                    'min_samples':max(p['min_samples'] for p in configs),
                    'max_gap_ms':min(p['max_gap_ms'] for p in configs),
                    'expire_ms':min(p['expire_ms'] for p in configs)})


class Stabilizer:
    def __init__(self, configuration=None):
        self.profile=profile(configuration)
        self.reset()

    def reset(self):
        self.context=None;self.time=None;self.sequence=None
        self.current=None;self.confirmed_ns=None;self.pending=None;self.since=None;self.count=0
        self.result=None

    def checkpoint(self):
        """State before the next observation, for independently replayable segments."""
        return {key:deepcopy(getattr(self,key)) for key in
                ('context','time','sequence','current','confirmed_ns','pending','since','count')}

    def restore(self, state):
        keys={'context','time','sequence','current','confirmed_ns','pending','since','count'}
        if not isinstance(state,dict) or set(state)!=keys:raise ValueError('Invalid stability checkpoint')
        def unsigned(value):return type(value) is int and value>=0
        empty=state['context'] is None
        if (empty != (state['time'] is None) or empty != (state['sequence'] is None)
                or (not empty and (not isinstance(state['context'],str) or len(state['context'])!=64
                                   or any(c not in '0123456789abcdef' for c in state['context'])
                                   or not unsigned(state['time']) or not unsigned(state['sequence'])))
                or state['current'] not in (None,*LABELS) or state['pending'] not in (None,*LABELS)
                or not unsigned(state['count'])
                or (state['pending'] is None and (state['since'] is not None or state['count']!=0))
                or (state['pending'] is not None and (not unsigned(state['since']) or state['count']<1))
                or (state['current'] is None) != (state['confirmed_ns'] is None)
                or (state['confirmed_ns'] is not None and not unsigned(state['confirmed_ns']))
                or (empty and (state['current'] is not None or state['pending'] is not None))
                or (not empty and ((state['since'] is not None and state['since']>state['time'])
                                   or (state['confirmed_ns'] is not None and state['confirmed_ns']>state['time'])))):
            raise ValueError('Invalid stability checkpoint')
        for key,value in state.items():setattr(self,key,deepcopy(value))
        self.result=None

    def observe(self, assessment, timestamp_ns, sequence, context):
        if type(timestamp_ns) is not int or timestamp_ns<0 or type(sequence) is not int or sequence<0:
            raise ValueError('Invalid stability observation identity/time')
        context=object_hash(context)
        # Polling the same frame cannot add evidence or wait out a confirmation.
        if context==self.context and sequence==self.sequence:
            return deepcopy(self.result)
        reset_reason=None
        if self.context is not None and context!=self.context:reset_reason='context_changed'
        elif self.sequence is not None and (sequence<=self.sequence or timestamp_ns<=self.time):reset_reason='observation_not_increasing'
        elif self.time is not None and timestamp_ns-self.time>self.profile['max_gap_ms']*1_000_000:reset_reason='observation_gap'
        if reset_reason:
            self.current=self.pending=self.since=self.confirmed_ns=None;self.count=0
        self.context=context;self.time=timestamp_ns;self.sequence=sequence
        raw=assessment.get('label') if assessment.get('status')=='predicted' else None
        raw=presentation_label(raw,self.profile['version'])
        if raw not in LABELS or reset_reason=='observation_not_increasing':
            self.current=self.pending=self.since=self.confirmed_ns=None;self.count=0
            status='held';label=None;reason=reset_reason or 'instantaneous_unavailable'
        elif raw==self.current:
            self.pending=None;self.since=None;self.count=0
            status='stable';label=self.current;reason=None
        else:
            if self.pending!=raw:self.pending=raw;self.since=timestamp_ns;self.count=0
            self.count+=1
            enough=self.count>=self.profile['min_samples'] and timestamp_ns-self.since>=self.profile['confirm_ms']*1_000_000
            if enough:
                self.current=raw;self.confirmed_ns=timestamp_ns;self.pending=None
                status='stable';label=raw;reason=None
            else:
                status='changing' if self.current is not None else 'confirming';label=None;reason='confirmation_pending'
        self.result={'version':self.profile['version'],'status':status,'label':label,
                     'instantaneous_label':assessment.get('label'), 'instantaneous_status':assessment.get('status'),
                     'time_ns':str(timestamp_ns),'sequence':sequence,'context_sha256':context,
                     'candidate_label':self.pending,'candidate_since_ns':str(self.since) if self.pending else None,
                     'candidate_samples':self.count if self.pending else 0,
                     'last_confirmed_label':self.current,'confirmed_ns':str(self.confirmed_ns) if self.confirmed_ns is not None else None,
                     'confirmation_delay_ms':(timestamp_ns-self.since)/1e6 if status=='stable' and self.since is not None else None,
                     'reasons':[reason] if reason else [],'reset_reason':reset_reason,
                     'expire_ms':self.profile['expire_ms'],'experimental':True}
        # A confirmation delay belongs to its confirmation frame, not later frames.
        if status=='stable':self.since=None;self.count=0
        return deepcopy(self.result)


def project(result, now_ns=None, *, closed=False):
    """Expire presentation without manufacturing a new recorded camera frame."""
    if result is None:return None
    result=deepcopy(result)
    expired=closed or (now_ns is not None and (now_ns<int(result['time_ns']) or now_ns-int(result['time_ns'])>result['expire_ms']*1_000_000))
    if expired:
        result.update(status='expired',label=None,candidate_label=None,candidate_samples=0,
                      reasons=['input_closed' if closed else 'input_expired'])
    return result


def row_context(row):
    a=row.get('posture_assessment') or {}
    return {'track_id':a.get('track_id'),'candidate_id':a.get('candidate_id'),
            'classifier_id':a.get('classifier_id'),'bed_region_revision':row.get('bed_region_revision'),
            'clock_id':row['clock_id']}


def row_time(row):
    media=row.get('source_media')
    return int(media['relative_time_ns']) if media is not None else int(row['received_ns'])


def frame(machine,row):
    result=machine.observe(row['posture_assessment'],row_time(row),row['stream_frame_id'],row_context(row))
    result['time_basis']='media_relative' if row.get('source_media') is not None else 'receive_time_approximation'
    return result


def group_context(views, assessment, region_revisions):
    evidence=assessment.get('fusion_evidence') or {}
    result={'members':[(v['input_id'],(v.get('assessment') or {}).get('track_id'),
                         (v.get('assessment') or {}).get('classifier_id')) for v in views],
            'regions':region_revisions,'method':assessment.get('method'),
            'contributors':[v['input_id'] for v in evidence.get('contributors',[]) if v['used']]}
    if assessment.get('association_policy')=='bed-priority-v2':
        result['bed_candidate_ids']=[(v.get('assessment') or {}).get('candidate_id') for v in views]
    return result
