import tempfile
import unittest
from pathlib import Path
import numpy as np
from bed_pose.stereo_depth import validate_calibration, triangulate, depth_row, export
from unittest.mock import patch
import json
from bed_pose.io_utils import atomic_json


class StereoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.group = {'members':[]}
        cameras = []
        for i in range(2):
            root = Path(self.tmp.name)/str(i)
            root.mkdir()
            atomic_json(root/'manifest.json', {'width':640,'height':480})
            self.group['members'].append({'input_id':str(i),'run_directory':str(root)})
            cameras.append({'input_id':str(i),'image_size':[640,480],
                'intrinsic':[[500,0,320],[0,500,240],[0,0,1]],
                'rotation_world_to_camera':[[1,0,0],[0,1,0],[0,0,1]],
                'translation_world_to_camera':[-i,0,0]})
        self.cal = {'version':'stereo-pinhole-0.1','image_coordinates':'undistorted_pinhole_pixels',
                    'unit':'m','quality':{'max_reprojection_error_px':2,'min_ray_angle_degrees':1},'cameras':cameras}
        self.matrices = validate_calibration(self.cal,self.group)

    def test_reconstruct_and_invalid_pairs(self):
        point = np.array([.2,.1,4.,1.])
        images = []
        for p,_,_,_ in self.matrices:
            v=p@point;images.append((v[:2]/v[2]).tolist())
        result=triangulate(images,self.matrices,self.cal['quality'])
        self.assertEqual(result['status'],'estimated')
        np.testing.assert_allclose(result['xyz'],point[:3],atol=1e-8)
        self.assertEqual(triangulate([[345,252.5],[220,300]],self.matrices,self.cal['quality'])['reason'],'reprojection_error')
        self.assertEqual(triangulate([[320,240],[320,240]],self.matrices,self.cal['quality'])['status'],'held')
        self.assertEqual(triangulate([[320,240],[445,240]],self.matrices,self.cal['quality'])['reason'],'behind_camera')

    def test_calibration_mismatch_and_unrectified_rejected(self):
        self.cal['cameras'].reverse()
        with self.assertRaises(ValueError):validate_calibration(self.cal,self.group)
        self.cal['cameras'].reverse()
        self.cal['image_coordinates']='distorted_pixels'
        with self.assertRaises(ValueError):validate_calibration(self.cal,self.group)

    def test_export_keeps_posture_and_attaches_depth(self):
        root=Path(self.tmp.name)/'group';root.mkdir()
        group=dict(self.group,group_id='g',posture_predictions={'status':'generated'})
        atomic_json(root/'manifest.json',group)
        cal=Path(self.tmp.name)/'cal.json';atomic_json(cal,self.cal)
        joints1=[{'image_mask':True,'x_px':345,'y_px':252.5}]*17
        joints2=[{'image_mask':True,'x_px':220,'y_px':252.5}]*17
        row={'alignment_id':0,'hold_reasons':[],'time_basis':'receive_time_approximation',
             'views':[{'joints':joints1},{'joints':joints2}]}
        posture={'alignment_id':0,'status':'predicted','label':'supine'}
        output=Path(self.tmp.name)/'out'
        with patch('bed_pose.stereo_depth.inspect_group'), patch('bed_pose.stereo_depth.iter_inputs',return_value=iter([row])), patch('bed_pose.stereo_depth.predictions',return_value=iter([posture])):
            report=export(root,cal,output)
        saved=json.loads((output/'posture_with_depth.jsonl').read_text())
        self.assertEqual(saved['posture'],posture)
        self.assertEqual(saved['stereo_depth']['status'],'estimated')
        self.assertFalse(report['depth_used_in_classifier'])
        self.assertEqual(report['rows_with_estimated_joints'],1)

    def test_pair_gate_and_missing_joint(self):
        joint={'image_mask':True,'x_px':320,'y_px':240}
        row={'hold_reasons':['operator_pair_confirmation_missing'], 'time_basis':'receive_time_approximation',
             'views':[{'joints':[joint]*17},{'joints':[joint]*17}]}
        self.assertEqual(depth_row(row,self.matrices,self.cal['quality'])['joints'],[])
        row['hold_reasons']=[]
        row['views'][1]['joints']=[{'image_mask':False}]*17
        result=depth_row(row,self.matrices,self.cal['quality'])
        self.assertEqual(result['status'],'held')
        self.assertEqual(result['joints'][0]['reason'],'joint_not_observed_in_both_views')


if __name__=='__main__':unittest.main()

class StereoDecisionTests(unittest.TestCase):
    def scene_data(self, label):
        import cv2
        from test_viewpoint_posture import scene
        obs=scene(label)
        q=cv2.Rodrigues(np.array([.2,-.4,.3]))[0]
        xyz=(np.array(obs.world_points)-np.array([.3,-.1,.2]))@q
        joints=[{'image_mask':True,'x_px':p[0],'y_px':p[1]} for p in obs.points]
        depth={'status':'estimated','joints':[{'status':'estimated','xyz':p.tolist()} for p in xyz]}
        row={'views':[{'joints':joints,'width':obs.width,'height':obs.height,'bed_region':obs.bed_rectangle}]}
        return row,depth

    def test_stereo_geometry_uses_existing_rules(self):
        from bed_pose.stereo_depth import stereo_posture
        from bed_pose.automatic_posture import AUTO_DEFAULTS
        for label in ('supine','side','edge_sitting'):
            with self.subTest(label=label):
                row,depth=self.scene_data(label)
                result=stereo_posture(row,depth,np.array([0.,0.,-1.]),AUTO_DEFAULTS)
                self.assertEqual(result['label'],label,result)
        row,depth=self.scene_data('standing')
        result=stereo_posture(row,depth,np.array([0.,0.,-1.]),AUTO_DEFAULTS)
        self.assertEqual(result['reason'],'standing_or_upright_out_of_scope')
        depth['joints'][5]={'status':'held'}
        self.assertEqual(stereo_posture(row,depth,np.array([0.,0.,-1.]),AUTO_DEFAULTS)['reason'],'automatic_torso_joints_missing')

    def test_triangulated_observations_reach_posture_decision(self):
        from bed_pose.stereo_depth import stereo_posture, combine_depth, depth_row
        from bed_pose.automatic_posture import AUTO_DEFAULTS
        k=np.array([[1000.,0,500],[0,1000.,500],[0,0,1]])
        r=np.eye(3)
        matrices=[]
        for t in (np.array([0.,-.2,5.]),np.array([-1.,-.2,5.])):
            matrices.append((k@np.column_stack((r,t)),r,t,-t))
        for label in ('supine','side','edge_sitting'):
            with self.subTest(label=label):
                row,truth=self.scene_data(label)
                row.update(hold_reasons=[],time_basis='receive_time_approximation')
                row['views'].append({'joints':[]})
                for j in truth['joints']:
                    v=matrices[1][0]@np.append(j['xyz'],1.)
                    row['views'][1]['joints'].append({'image_mask':True,'x_px':v[0]/v[2],'y_px':v[1]/v[2]})
                depth=depth_row(row,matrices,{'max_reprojection_error_px':1,'min_ray_angle_degrees':1})
                candidate=stereo_posture(row,depth,np.array([0.,0.,-1.]),AUTO_DEFAULTS)
                self.assertEqual(candidate['label'],label,candidate)
                self.assertEqual(combine_depth({'status':'predicted','label':label,'reasons':[]},candidate)['label'],label)
                from bed_pose.io_utils import json_text
                self.assertIn(label,json_text(candidate))

    def test_fusion_agreement_conflict_and_unavailable(self):
        from bed_pose.stereo_depth import combine_depth
        posture={'status':'predicted','label':'supine','reasons':[]}
        self.assertEqual(combine_depth(posture,{'status':'predicted','label':'supine'})['label'],'supine')
        self.assertEqual(combine_depth(posture,{'status':'predicted','label':'side'})['reasons'],['image_stereo_posture_conflict'])
        self.assertIsNone(combine_depth(posture,{'status':'held','reason':'bed_region_required'})['label'])
        self.assertIsNone(combine_depth({'status':'held','reasons':['old_hold']},{'status':'predicted','label':'side'})['label'])
