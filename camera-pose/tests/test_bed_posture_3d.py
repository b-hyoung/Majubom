"""Synthetic six-posture cases; not real-device accuracy measurements."""
from copy import deepcopy
import unittest
import numpy as np
from bed_pose.bed_posture_3d import bed_frame,classify,PostureFilter,room_bed,annotate_preview_bed,check_view_agreement
from bed_pose.room_monitor import default_config
from bed_pose.experimental_preview import prepare
from test_room_monitor import fixture,projected_pair


def bed():return bed_frame([[-.5,0,-1],[.5,0,-1],[.5,0,1],[-.5,0,1]],'camera_a_preview')


def pose(label):
    p=np.zeros((17,3));p[:,1]=.15
    p[5]=[.2,.15,-.4];p[6]=[-.2,.15,-.4];p[11]=[.13,.15,.15];p[12]=[-.13,.15,.15]
    p[13]=[.13,.15,.5];p[14]=[-.13,.15,.5];p[15]=[.13,.15,.85];p[16]=[-.13,.15,.85]
    p[0]=[0,.25,-.6]
    if label in ('prone','left_side','right_side'):
        angle={'prone':np.pi,'left_side':-np.pi/2,'right_side':np.pi/2}[label]
        r=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
        p=(p-[0,.15,0])@r.T+[0,.15,0]
    if label in ('sitting','edge_sitting'):
        p[5]=[.2,.55,0];p[6]=[-.2,.55,0];p[11]=[.13,0,0];p[12]=[-.13,0,0]
        p[13]=[.13,0,.4];p[14]=[-.13,0,.4];p[15]=[.13,-.4,.4];p[16]=[-.13,-.4,.4]
        if label=='edge_sitting':
            p[:,0]+=.48;p[13:,0]+=.2
    return dict(coordinateSystem='camera_a_preview',unit='unverified_candidate_units',timestampMs=123,
                joints=[dict(id=i,valid=True,x=float(v[0]),y=float(v[1]),z=float(v[2]),confidence=.92) for i,v in enumerate(p)])


class SixPostureTests(unittest.TestCase):
    def test_disagreement_is_not_a_majority_vote_or_probability(self):
        decision=classify(pose('supine'),bed())
        evidence=[dict(label='supine',quality_score=.9),dict(label='prone',quality_score=.85)]
        r=check_view_agreement(decision,evidence)
        self.assertIsNone(r['label']);self.assertEqual(r['reason'],'conflicting_pose_evidence')
        self.assertEqual(decision['label'],'supine')
        evidence[1]['quality_score']=.55
        r=check_view_agreement(decision,evidence)
        self.assertEqual(r['label'],'supine');self.assertEqual(r['required_stable_seconds'],1.2)
        self.assertIn('cross_view_disagreement',r['warnings'])
        self.assertFalse(r['score_is_probability'])
        evidence[1]['label']=None
        self.assertFalse(check_view_agreement(decision,evidence)['warnings'])

    def test_six_labels_and_anatomical_left_right(self):
        for label in ('supine','prone','left_side','right_side','sitting','edge_sitting'):
            with self.subTest(label=label):
                r=classify(pose(label),bed())
                self.assertEqual(r['label'],label,r)
                self.assertFalse(r['accuracy_verified']);self.assertFalse(r['eligible_for_recording'])
                self.assertFalse(r['score_is_probability'])

    def test_missing_torso_face_knees_and_ankles_hold(self):
        for label,index,reason in [('supine',5,'torso_joints_missing')]:
            p=pose(label);p['joints'][index]['valid']=False
            self.assertEqual(classify(p,bed())['reason'],reason)
        p=pose('edge_sitting')
        for i in (15,16):p['joints'][i]['valid']=False
        self.assertEqual(classify(p,bed())['reason'],'ankles_required_at_edge')
        p=pose('prone')
        for i in (0,1,2):p['joints'][i]['valid']=False
        self.assertEqual(classify(p,bed())['reason'],'face_required_for_front_back')
        p=pose('sitting')
        for i in (13,14):p['joints'][i]['valid']=False
        self.assertEqual(classify(p,bed())['reason'],'knees_required_for_sitting')

    def test_empty_outside_mismatched_and_low_quality_do_not_classify(self):
        self.assertEqual(classify(None,bed())['reason'],'skeleton_unavailable')
        self.assertEqual(classify(pose('supine'),None)['reason'],'bed_registration_required')
        p=pose('supine');p['coordinateSystem']='room_world'
        self.assertEqual(classify(p,bed())['reason'],'coordinate_system_mismatch')
        p=pose('supine')
        for j in p['joints']:j['x']+=4
        self.assertEqual(classify(p,bed())['reason'],'body_outside_bed')
        p=pose('supine');p['joints'][5]['confidence']=.1
        self.assertEqual(classify(p,bed())['status'],'held')

    def test_arbitrary_rigid_frame_preserves_label_and_yaw_bed(self):
        theta=.8;r=np.array([[np.cos(theta),0,np.sin(theta)],[0,1,0],[-np.sin(theta),0,np.cos(theta)]])
        offset=np.array([2,1,3]);b=bed_frame(np.array(bed()['corners'])@r.T+offset,'camera_a_preview')
        p=pose('left_side')
        for j in p['joints']:
            q=r@np.array([j[k] for k in ('x','y','z')])+offset;j.update(zip(('x','y','z'),q.tolist()))
        self.assertEqual(classify(p,b)['label'],'left_side')
        config=default_config();config['bed']['yaw_degrees']=45
        b=room_bed(config);self.assertFalse(b['verified']);self.assertEqual(b['coordinate_system'],'room_world')

    def test_temporal_stability_no_stale_or_missing_label_carryover(self):
        f=PostureFilter();r=classify(pose('supine'),bed())
        self.assertEqual(f.update(r,0)['status'],'candidate')
        self.assertEqual(f.update(r,.3)['status'],'candidate')
        self.assertEqual(f.update(r,.7)['status'],'stable')
        self.assertEqual(f.update(r,2)['status'],'candidate')
        self.assertEqual(f.update(classify(None,bed()),2.1)['status'],'held')
        self.assertEqual(f.update(r,2.2)['status'],'candidate')
        self.assertEqual(f.update(classify(pose('prone'),bed()),2.3)['status'],'candidate')

    def test_malformed_or_crossed_bed_rejected(self):
        with self.assertRaises(ValueError):bed_frame([[0,0,0]]*4,'camera_a_preview')
        corners=bed()['corners'];corners[1],corners[2]=corners[2],corners[1]
        with self.assertRaises(ValueError):bed_frame(corners,'camera_a_preview')

    def test_dual_image_bed_registration_and_bad_correspondence(self):
        cal=fixture();candidate=prepare(cal)
        xyz=np.array([[-.4,-.3,3],[.4,-.3,3],[.4,.3,3],[-.4,.3,3]])
        # In camera-A display these points are a vertical plane, but still a valid shared surface.
        pair=projected_pair(cal,np.vstack([xyz,np.tile(xyz[0],(13,1))]))
        values=dict(points=[[j[:2] for j in v['joints'][:4]] for v in pair],resolutions=[[640,480]]*2)
        b=annotate_preview_bed(values,candidate)
        self.assertFalse(b['verified']);self.assertEqual(b['coordinate_system'],'camera_a_preview')
        bad=deepcopy(values);bad['resolutions'][0]=[1280,720]
        with self.assertRaises(ValueError):annotate_preview_bed(bad,candidate)
