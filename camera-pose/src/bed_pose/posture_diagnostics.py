"""Bounded counts of processed frames, not camera frames or accuracy estimates."""
from collections import Counter,deque

class PostureDiagnostics:
    def __init__(self,window=300):
        self.rows=deque(maxlen=window)
        self.scope=None
        self.latest=None

    def observe(self,assessment,region_revision):
        scope=(assessment['classifier_id'],region_revision)
        if scope!=self.scope:self.rows.clear();self.scope=scope
        self.rows.append((assessment['status'],tuple(assessment['reasons'])))
        statuses=Counter(s for s,_ in self.rows);reasons=Counter(r for s,rs in self.rows if s=='held' for r in rs)
        self.latest={'window_limit':self.rows.maxlen,'processed_frames':len(self.rows),
                'predicted':statuses['predicted'],'held':statuses['held'],'unavailable':statuses['unavailable'],
                'image_provider_errors':reasons['body_state_provider_failed'],
                'image_input_quality_holds':reasons['body_state_crop_quality_insufficient'],
                'hold_reason_counts':dict(reasons),'hold_fraction':statuses['held']/len(self.rows),
                'last_stream_frame_id':assessment['stream_frame_id'],'classifier_id':assessment['classifier_id'],
                'bed_region_revision':region_revision,'scope':'since_classifier_or_region_change_bounded_processed_frames'}

        return self.latest
