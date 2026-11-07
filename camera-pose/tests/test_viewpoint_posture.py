"""Projected synthetic geometry tests, not human viewpoint accuracy measurements."""
import copy
import math
import unittest
from dataclasses import replace
import cv2
import numpy as np
from bed_pose.automatic_posture import AutomaticClassifier,AUTO_DEFAULTS,DEFAULTS
from bed_pose.bed_geometry import make_quadrilateral
from bed_pose.posture import PostureInput


def scene(label='supine',roll=0,tilt=0,swap=0,yaw=0):
    p=np.zeros((17,3));p[:,1]=-.65;p[:,2]=-.12
    for i,x,y,z in ((0,0,-.8,-.22),(1,.05,-.84,-.22),(2,-.05,-.84,-.22),
                     (5,.22,-.5,-.12),(6,-.22,-.5,-.12),(11,.16,.3,-.12),(12,-.16,.3,-.12),
                     (7,.45,-.1,-.15),(8,-.45,-.1,-.15),(9,.45,.3,-.15),(10,-.45,.3,-.15),
                     (13,.16,.8,-.12),(14,-.16,.8,-.12),(15,.16,1.3,-.12),(16,-.16,1.3,-.12)):
        p[i]=(x,y,z)
    if label=='side':
        # Rotate about the longitudinal axis, keeping body above the bed.
        x=p[:,0].copy();z=p[:,2].copy()+.12;p[:,0]=z;p[:,2]=-x-.35
    elif label=='prone':
        p[:,0]*=-1;p[:,2]=-.24-p[:,2]
    elif label in ('edge_sitting','standing'):
        p[5]=(.22,1.2,-.92);p[6]=(-.22,1.2,-.92)
        p[11]=(.16,1.2,-.12);p[12]=(-.16,1.2,-.12)
        for i,x in ((13,.16),(14,-.16)):p[i]=(x,2.0,-.12) if label=='edge_sitting' else (x,1.2,.4)
        for i,x in ((15,.16),(16,-.16)):p[i]=(x,2.0,.5) if label=='edge_sitting' else (x,1.2,.92)
        p[:5]=[[0,1.2,-1.12],[.05,1.2,-1.14],[-.05,1.2,-1.14],[.1,1.2,-1.1],[-.1,1.2,-1.1]]
    w,h=1000,1000;K=np.array([[1000.,0,500],[0,1000.,500],[0,0,1]])
    rz=cv2.Rodrigues(np.array([0.,0.,math.radians(roll)]))[0]
    rx=cv2.Rodrigues(np.array([math.radians(tilt),0.,0.]))[0]
    ry=cv2.Rodrigues(np.array([0.,math.radians(yaw),0.]))[0];R=rz@ry@rx
    t=np.array([0.,-.2,5.]);rv=cv2.Rodrigues(R)[0]
    def project(a):return cv2.projectPoints(a,rv,t,K,None)[0].reshape(-1,2)
    bed=np.array([[-1.,-1.,0],[1.,-1.,0],[1.,1.5,0],[-1.,1.5,0]])
    vertices=project(bed).tolist();vertices=vertices[swap:]+vertices[:swap]
    # PnP must register an arbitrarily rotated/translated model coordinate frame.
    Q=cv2.Rodrigues(np.array([.2,-.4,.3]))[0];model=p@Q.T+np.array([.3,-.1,.2])
    return PostureInput(tuple(map(tuple,project(p))),w,h,make_quadrilateral(vertices,w,h),None,tuple(tuple(float(v) for v in p) for p in model))


class ViewpointTests(unittest.TestCase):
    def test_roll_tilt_and_corner_start_do_not_set_posture(self):
        model=AutomaticClassifier(AUTO_DEFAULTS)
        for label in ('supine','side','edge_sitting'):
            for roll in (0,35,90,170,270):
                for tilt in (0,25,55):
                    for start in (0,1,3):
                        obs=scene(label,roll,tilt,start,yaw=30 if tilt else 0)
                        with self.subTest(label=label,roll=roll,tilt=tilt,start=start):
                            result=model.evaluate(obs)
                            self.assertEqual(result[0],label,result)
                            self.assertFalse(result[2]['camera_registration']['calibration_verified'])

    def test_standing_prone_missing_and_bad_correspondence(self):
        model=AutomaticClassifier(AUTO_DEFAULTS)
        for roll in (0,90,180):
            self.assertEqual(model.evaluate(scene('standing',roll,25))[1],'standing_or_upright_out_of_scope')
            self.assertIsNone(model.predict(scene('prone',roll,25)))
        obs=scene()
        self.assertEqual(model.evaluate(replace(obs,world_points=None))[1],'auto_view_world_required')
        self.assertEqual(model.evaluate(replace(obs,bed_rectangle=(0,0,1000,1000)))[1],'auto_view_bed_corners_required')
        p=list(obs.points);p[7]=(50.,60.);p[8]=(900.,900.)
        self.assertEqual(model.evaluate(replace(obs,points=tuple(p)))[1],'auto_view_registration_uncertain')
        p=list(obs.points);p[5]=None
        self.assertEqual(model.evaluate(replace(obs,points=tuple(p)))[1],'automatic_torso_joints_missing')

    def test_registration_sources_and_invalid_world(self):
        model=AutomaticClassifier(AUTO_DEFAULTS)
        for yaw,source in ((0,'assumed_focal_prior'),(30,'rectangle_orthogonality')):
            result=model.evaluate(scene('supine',35,25,yaw=yaw))
            self.assertEqual(result[0],'supine')
            self.assertEqual(result[2]['camera_registration']['intrinsics_source'],source)
        obs=scene();p=list(obs.world_points);p[5]=(float('nan'),0,0)
        self.assertEqual(model.evaluate(replace(obs,world_points=tuple(p)))[1],'auto_view_world_required')

    def test_legacy_profiles_and_weight_selection_unchanged(self):
        from test_automatic_posture import observation
        old=AutomaticClassifier(DEFAULTS)
        for label in ('supine','side','edge_sitting'):self.assertEqual(old.predict(observation(label)),label)
        for k in DEFAULTS:
            if k not in ('version','view'):self.assertEqual(AUTO_DEFAULTS[k],DEFAULTS[k])
        bad=copy.deepcopy(AUTO_DEFAULTS);bad['view']='oblique'
        with self.assertRaises(ValueError):AutomaticClassifier(bad)



def raw_scene(obs):
    from test_posture_evidence import sample
    from bed_pose.pose.mediapipe import COCO_INDICES
    raw=copy.deepcopy(sample()['raw_candidates'])
    raw[0]['pose_blocks'][0]['transform_to_canonical']['matrix']=[[1000,0,0],[0,1000,0],[0,0,1]]
    for src,xy,xyz in zip(COCO_INDICES,obs.points,obs.world_points):
        raw[0]['pose_blocks'][0]['values'][src]=[float(xy[0])/1000,float(xy[1])/1000]
        raw[0]['sdk_landmarks']['image'][src].update(x=float(xy[0])/1000,y=float(xy[1])/1000)
        raw[0]['sdk_landmarks']['world'][src].update(zip(('x','y','z'),xyz))
    return raw


class ViewpointWebTests(unittest.TestCase):
    import test_web_workspace as web
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    control=web.WebTests.control

    def test_auto_api_current_frame_and_recorded_result(self):
        from bed_pose.live_engine import LiveEngine
        from bed_pose.pose.mediapipe import mapping_profile
        from bed_pose.pose.fake import FakeAdapter
        from bed_pose.contracts import PoseOutput,IntegrityError
        from bed_pose.live_recording import verify_live
        from bed_pose.io_utils import load_json,file_hash,atomic_json
        from bed_pose.posture import assess_frame
        import time,json
        from pathlib import Path
        obs=scene('supine',35,25);raw=raw_scene(obs)
        class Adapter(FakeAdapter):
            def estimate(self,m):return PoseOutput(copy.deepcopy(raw),m.model_timestamp_ms,True)
        def factory(source,model,output_root,options_root,**kwargs):
            def read():
                time.sleep(.03);return np.zeros((1000,1000,3),dtype=np.uint8)
            source.read=read
            return LiveEngine(source,Adapter(),{'execution_mode':'ordered_sequence'},output_root,profile=mapping_profile(),execution_kind='synthetic')
        self.workspace.factory=factory
        status,value=self.request('/api/inputs',{'kind':'demo'});self.assertEqual(status,200);sid=value['id']
        self.wait(lambda x:x['sessions'][0]['frame_id'] is not None)
        self.assertEqual(self.control(sid,'region',points=obs.bed_rectangle['vertices'],selected_frame_id=0)[0],200)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        # No manual view selector is sent.
        self.assertEqual(self.control(sid,'posture_mode',mode='automatic',view='bed_auto')[0],200)
        state=self.wait(lambda x:x['sessions'][0]['posture_assessment']['label']=='supine')['sessions'][0]
        self.assertEqual(state['automatic_posture_profile'],AUTO_DEFAULTS)
        self.assertEqual(self.control(sid,'start')[0],200)
        self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.assertEqual(self.control(sid,'stop')[0],200)
        state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0];root=Path(state['directory'])
        self.assertEqual(verify_live(root)['integrity'],'verified')
        rows=[json.loads(l) for l in (root/'live_frames.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['posture_assessment']['label']=='supine' for r in rows))
        row=copy.deepcopy(rows[0]);row.update(target_posture='side',verified_gt='side',sensor_result='side')
        result=assess_frame(row,1000,1000,obs.bed_rectangle,AutomaticClassifier(AUTO_DEFAULTS))
        self.assertEqual(result['label'],'supine')
        row['raw_candidates'][0]['candidate_id']='different'
        self.assertEqual(assess_frame(row,1000,1000,obs.bed_rectangle,AutomaticClassifier(AUTO_DEFAULTS))['reasons'],['auto_view_world_required'])
        from bed_pose.evidence_report import export_evidence
        export_evidence(root,self.root/'report')
        path=root/'live_frames.jsonl';rows[0]['posture_assessment']['rule_features']['torso_plane_normal_cos']=1.
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest=load_json(root/'manifest.json');manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(root)

if __name__=='__main__':unittest.main()
