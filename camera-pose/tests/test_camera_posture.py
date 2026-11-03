from copy import deepcopy
import unittest
from unittest.mock import patch
from bed_pose.camera_posture import LivePostureEstimator


def packet(stamp=1_000_000_000,frame=1,state='sitting',camera='A'):
    points=[[100.,90.,.9] for _ in range(17)]
    points[5]=[30.,40.,.9];points[6]=[40.,40.,.9]
    points[11]=[25.,80.,.9];points[12]=[35.,80.,.9]
    points[13]=[10.,100.,.9];points[14]=[15.,100.,.9]
    points[15]=[5.,130.,.9];points[16]=[10.,130.,.9]
    scores={n:.02 for n in ('lying','sitting','standing','other')};scores[state]=.94
    return dict(time_ns=stamp,frame_id=frame,input_id=camera,source_camera=camera,track_id='track-1',
                processing_ok=True,people_count=1,width=200,height=200,joints=points,
                image_body_state=dict(status='predicted',scores=scores,details=dict(direction_scores=dict(supine=.8,side=.1,prone=.1))))


ANNOTATION=dict(resolutions=[[200,200],[200,200]],points=[[[20,20],[180,20],[180,180],[20,180]]]*2)


class CameraPostureTests(unittest.TestCase):
    def test_sparse_geometry_cannot_override_image_direction(self):
        p=packet(state='lying')
        for i in (1,2,3,4,9,10):p['joints'][i]=None
        with patch('bed_pose.camera_posture.geometry',return_value=({'prone':1.},'prone')):
            r=LivePostureEstimator().update([p,None],ANNOTATION,None,1_010_000_000)
        self.assertEqual(r['label'],'supine')

    def test_brief_flip_is_rejected_but_sustained_change_is_accepted(self):
        e=LivePostureEstimator()
        for i in range(6):e.update([packet(1_000_000_000+i*100_000_000,i,'standing'),None],None,None,1_000_000_000+i*100_000_000)
        r=e.update([packet(1_600_000_000,7,'other'),None],None,None,1_600_000_000)
        self.assertEqual(r['label'],'standing')
        for i in range(8):r=e.update([packet(1_700_000_000+i*100_000_000,8+i,'other'),None],None,None,1_700_000_000+i*100_000_000)
        self.assertEqual(r['label'],'other')

    def test_supported_geometry_is_not_discarded_by_generic_clip_other(self):
        for label in ('supine','prone','edge_sitting','right_side'):
            p=packet(state='other')
            with patch('bed_pose.camera_posture.geometry',return_value=({label:1.},label)):
                r=LivePostureEstimator().update([p,None],ANNOTATION,None,1_010_000_000)
            self.assertEqual(r['label'],label)
            self.assertIn('clip_geometry_disagreement',r['warnings'])
            self.assertFalse(r['eligible_for_recording'])

    def test_geometry_without_bed_membership_cannot_override_image(self):
        with patch('bed_pose.camera_posture.geometry',return_value=({'supine':1.},'supine')):
            r=LivePostureEstimator().update([packet(state='standing'),None],None,None,1_010_000_000)
        self.assertEqual(r['label'],'standing')

    def test_clip_and_registered_2d_bed_detect_edge_without_metric_3d(self):
        r=LivePostureEstimator().update([packet(),None],ANNOTATION,None,1_010_000_000)
        self.assertEqual(r['label'],'edge_sitting');self.assertEqual(r['status'],'estimated')
        self.assertFalse(r['accuracy_verified']);self.assertFalse(r['eligible_for_recording'])
        self.assertFalse(r['score_is_probability'])

    def test_no_bed_does_not_suppress_visible_sitting(self):
        r=LivePostureEstimator().update([packet(),None],None,None,1_010_000_000)
        self.assertEqual(r['label'],'sitting');self.assertIn('bed_region_unavailable',r['warnings'])

    def test_lying_prone_and_standing_are_not_forced_to_sitting(self):
        for expected in ('supine','prone','standing','other'):
            p=packet(state='lying' if expected in ('supine','prone') else expected)
            if expected=='prone':p['image_body_state']['details']['direction_scores']=dict(supine=.1,side=.1,prone=.8)
            self.assertEqual(LivePostureEstimator().update([p,None],None,None,1_010_000_000)['label'],expected)

    def test_missing_side_geometry_is_not_arbitrarily_called_left(self):
        p=packet(state='lying');p['image_body_state']['details']['direction_scores']=dict(supine=.05,side=.9,prone=.05)
        self.assertEqual(LivePostureEstimator().update([p,None],None,None,1_010_000_000)['label'],'side')

    def test_empty_stale_and_multiple_people_clear_the_estimate(self):
        for mode in ('empty','stale','multi'):
            e=LivePostureEstimator();p=packet();e.update([p,None],ANNOTATION,None,1_010_000_000)
            q=deepcopy(p)
            if mode=='empty':q['people_count']=0
            if mode=='multi':q['people_count']=2
            r=e.update([q,None],ANNOTATION,None,1_500_000_000 if mode=='stale' else 1_010_000_000)
            self.assertIsNone(r['label']);self.assertEqual(e.smoothed,{})

    def test_repeated_poll_is_not_additional_stability_evidence(self):
        e=LivePostureEstimator();p=packet()
        for _ in range(30):r=e.update([p,None],ANNOTATION,None,1_010_000_000)
        self.assertEqual(r['status'],'estimated');self.assertEqual(e.count,1)

    def test_new_observations_stabilize_without_initial_dwell(self):
        e=LivePostureEstimator()
        for i in range(4):
            p=packet(1_000_000_000+i*200_000_000,i+1)
            r=e.update([p,None],ANNOTATION,None,p['time_ns']+1_000_000)
            self.assertEqual(r['label'],'edge_sitting')
        self.assertEqual(r['status'],'stable')

    def test_malformed_or_other_resolution_annotation_is_ignored(self):
        for a in ({'resolutions':[]},{'resolutions':[[640,480]]*2,'points':[]},dict(ANNOTATION,points=[[]]*2)):
            self.assertEqual(LivePostureEstimator().update([packet(),None],a,None,1_010_000_000)['label'],'sitting')

    def test_empty_camera_b_does_not_block_camera_a(self):
        b=packet(camera='B');b['people_count']=0
        self.assertEqual(LivePostureEstimator().update([packet(),b],ANNOTATION,None,1_010_000_000)['label'],'edge_sitting')

    def test_person_identity_change_does_not_inherit_previous_class(self):
        e=LivePostureEstimator();e.update([packet(),None],ANNOTATION,None,1_010_000_000)
        p=packet(1_100_000_000,2,'standing');p['track_id']='new-track'
        self.assertEqual(e.update([p,None],ANNOTATION,None,1_110_000_000)['label'],'standing')

    def test_no_image_no_geometry_never_invents_a_posture(self):
        p=packet();p['image_body_state']=None
        r=LivePostureEstimator().update([p,None],ANNOTATION,None,1_010_000_000)
        self.assertIsNone(r['label']);self.assertEqual(r['status'],'observing')


if __name__=='__main__':unittest.main()
