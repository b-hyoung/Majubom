"""Quadrilateral geometry and real recording integration; not bed recognition accuracy."""
import copy
import json
from pathlib import Path
import unittest
from bed_pose.bed_geometry import make_quadrilateral,contains,relative_xy
from bed_pose.live_recording import validate_region,verify_live,make_region
from bed_pose.posture import PostureInput
from bed_pose.automatic_posture import AutomaticClassifier
from bed_pose.io_utils import load_json,atomic_json,file_hash
from bed_pose.contracts import IntegrityError
import test_fusion_inputs as fusion
import test_automatic_posture as automatic

POINTS=[[150,70],[470,70],[600,330],[30,330]]
class GeometryTests(unittest.TestCase):
    def test_clockwise_counterclockwise_and_unit_corners(self):
        for pts in (POINTS,list(reversed(POINTS))):
            region=make_quadrilateral(pts,640,360);validate_region(region,640,360)
            for p,expected in zip(pts,((0,0),(1,0),(1,1),(0,1))):
                self.assertTrue(contains(region,p))
                for v,e in zip(relative_xy(region,p),expected):self.assertAlmostEqual(v,e)
            self.assertTrue(contains(region,[300,200]));self.assertFalse(contains(region,[40,80]))
            self.assertFalse(contains(region,[620,200]))

    def test_reject_bad_shapes(self):
        shapes=[POINTS[:3],[[1,1],[5,5],[1,5],[5,1]],[[1,1],[100,1],[20,20],[1,100]],
                [[1,1],[1,1],[3,3],[4,4]],[[0,0],[1,0],[1,1],[0,1]],
                [[0,0],[700,0],[600,300],[0,300]],[[False,1],[100,1],[100,100],[1,100]],
                [[float('nan'),1],[100,1],[100,100],[1,100]]]
        for pts in shapes:
            with self.assertRaises(ValueError):make_quadrilateral(pts,640,360)
        with self.assertRaises(ValueError):make_quadrilateral(POINTS,0,360)

    def test_metadata_tamper_and_legacy_rectangle(self):
        r=make_quadrilateral(POINTS,640,360);r['x1']=0
        with self.assertRaises(ValueError):validate_region(r,640,360)
        old=make_region([10,10,100,100],640,360);validate_region(old,640,360)
        self.assertEqual(relative_xy(old,[55,55]),[.5,.5]);self.assertTrue(contains(old,[10,100]))

    def test_automatic_uses_polygon_instead_of_bounding_box(self):
        p=automatic.points('supine');region=make_quadrilateral([[250,100],[350,100],[600,300],[100,300]],640,360)
        # The bounding rectangle contains the body, but the tapered polygon excludes one shoulder.
        p[5]=(150.,155.);p[6]=(150.,205.);p[11]=(300.,155.);p[12]=(300.,205.)
        a=AutomaticClassifier().evaluate(PostureInput(tuple(p),640,360,region))
        self.assertEqual(a[1],'body_outside_bed_region')

class QuadWebTests(unittest.TestCase):
    setUp=fusion.FusionTests.setUp
    cleanup=fusion.FusionTests.cleanup
    request=fusion.FusionTests.request
    wait=fusion.FusionTests.wait
    demo=fusion.FusionTests.demo
    control=fusion.FusionTests.control
    prepare_pair=fusion.FusionTests.prepare_pair
    start_pair=fusion.FusionTests.start_pair
    finish=fusion.FusionTests.finish

    def test_group_polygon_revision_coordinates_and_inspect(self):
        from bed_pose.fusion_inputs import inspect_group,view_features
        ids,gid=self.start_pair()
        for sid in ids:
            frame=self.workspace.sessions[sid].state['frame_id']
            self.assertEqual(self.control(sid,'region',points=POINTS,selected_frame_id=frame)[0],200)
        self.wait(lambda x:all(s['region']['method']=='manual_quadrilateral' for s in x['sessions']))
        before=[self.workspace.sessions[sid].state['recorded_frames'] for sid in ids]
        self.wait(lambda x:all(s['recorded_frames']>=n+3 for s,n in zip(x['sessions'],before)))
        root,group,rows=self.finish(gid)
        self.assertEqual(group['fusion_inputs']['schema_version'],'two-view-input-0.3-draft')
        self.assertEqual(inspect_group(root)['integrity'],'verified')
        self.assertTrue(any(v and v['bed_region']['method']=='manual_quadrilateral' for row in rows for v in row['views']))
        source=Path(group['members'][0]['run_directory']);manifest=load_json(source/'manifest.json')
        row=json.loads((source/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()[0]);person=row['mapped_people'][0]
        person['joints'][0].update(x_px=40.,y_px=80.)
        region=make_quadrilateral(POINTS,640,360)
        joint=view_features(row,manifest,{row['bed_region_revision']:region},'test')['joints'][0]
        self.assertTrue(joint['bed_mask']);self.assertFalse(joint['inside_bed'])
        self.assertTrue(any(v<0 or v>1 for v in joint['bed_xy']))

    def test_invalid_api_does_not_replace_region_or_stop_input(self):
        sid=self.demo();self.control(sid,'region',box=[1,1,200,200],selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        original=copy.deepcopy(self.workspace.sessions[sid].state['region'])
        status,_=self.control(sid,'region',points=[[1,1],[5,5],[1,5],[5,1]],selected_frame_id=0)
        self.assertEqual(status,400);self.assertEqual(self.workspace.sessions[sid].state['region'],original)
        self.assertFalse(self.workspace.sessions[sid].state['closed'])

class AutomaticQuadTests(unittest.TestCase):
    setUp=automatic.AutomaticWebTests.setUp
    cleanup=automatic.AutomaticWebTests.cleanup
    request=automatic.AutomaticWebTests.request
    wait=automatic.AutomaticWebTests.wait
    demo=automatic.AutomaticWebTests.demo
    control=automatic.AutomaticWebTests.control
    automatic=automatic.AutomaticWebTests.automatic

    def test_automatic_polygon_prediction_and_region_edit_replay(self):
        sid=self.automatic()
        quad=[[100,100],[550,100],[620,300],[30,300]]
        frame=self.workspace.sessions[sid].state['frame_id']
        self.assertEqual(self.control(sid,'region',points=quad,selected_frame_id=frame)[0],200)
        self.wait(lambda x:x['sessions'][0]['region']['method']=='manual_quadrilateral' and x['sessions'][0]['posture_assessment']['label']=='supine')
        self.control(sid,'start');self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        frame=self.workspace.sessions[sid].state['frame_id']
        self.control(sid,'region',points=[[120,100],[530,100],[600,300],[50,300]],selected_frame_id=frame)
        self.wait(lambda x:x['sessions'][0]['region']['vertices'][0][0]==120 and x['sessions'][0]['recorded_frames']>=5)
        self.control(sid,'stop');state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        self.assertEqual(verify_live(state['directory'])['integrity'],'verified')
        manifest=load_json(Path(state['directory'])/'manifest.json')
        self.assertEqual(manifest['initial_bed_region']['vertices'],quad)
