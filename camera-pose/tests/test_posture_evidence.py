"""Geometry/provenance checks with controlled SDK outputs, not model accuracy."""
import copy
import csv
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from bed_pose.contracts import PoseOutput,IntegrityError
from bed_pose.io_utils import atomic_json,file_hash,load_json
from bed_pose.mapping import map_keypoints,raw_json_value
from bed_pose.pose.mediapipe import MediaPipeAdapter,COCO_INDICES,mapping_profile
from bed_pose.posture import assess_frame
from bed_pose.posture_evidence import compare_frame,PROFILE
from bed_pose.automatic_posture import AutomaticClassifier
from bed_pose.live_recording import verify_live
from test_mediapipe_adapter import Landmark
from test_automatic_posture import points
import test_web_workspace as web


def sample():
    image=[Landmark(.3,.3,0) for _ in range(33)]
    for source,(x,y) in zip(COCO_INDICES,points('supine')):image[source]=Landmark(x/640,y/360,0)
    world=[Landmark() for _ in range(33)]
    for i,p in {11:(-.2,-.5,0),12:(.2,-.5,0),23:(-.15,0,0),24:(.15,0,0)}.items():world[i]=Landmark(*p)
    raw=MediaPipeAdapter._candidates(SimpleNamespace(pose_landmarks=[image],pose_world_landmarks=[world]),640,360)
    mapped=map_keypoints(PoseOutput(raw,0,True),640,360,mapping_profile())
    row={'processing_status':'ok','stream_frame_id':7,'clock_id':'test','received_ns':'100',
         'raw_candidates':raw,'mapped_people':mapped,
         'target_posture':'side','verified_gt':'side','sensor_results':{'label':'side'}}
    return row


def compare(row):return compare_frame(row,640,360,assess_frame(row,640,360,None,AutomaticClassifier()))


class EvidenceTests(unittest.TestCase):
    def test_same_frame_features_without_labels_or_goal_leak(self):
        row=sample();before=copy.deepcopy(row);result=compare(row)
        self.assertEqual(row,before);self.assertEqual(result['stream_frame_id'],7)
        self.assertEqual(result['body_2d']['status'],'available')
        self.assertAlmostEqual(result['body_2d']['features']['shoulders']['transverse'],.5)
        self.assertAlmostEqual(result['world_3d']['features']['shoulders']['transverse'],.8)
        self.assertAlmostEqual(result['world_3d']['features']['shoulder_depth_fraction'],0.)
        self.assertEqual(result['world_3d']['source_indices'],[11,12,23,24])
        self.assertEqual(result['world_3d']['scores']['image'][0]['visibility'],.7)
        self.assertEqual(result['combined']['status'],'available');self.assertIsNone(result['combined']['label'])
        self.assertEqual(result['baseline']['reasons'],['bed_region_required'])
        row.update(target_posture='supine',verified_gt=None,sensor_results=None)
        self.assertEqual(compare(row),result)

    def test_body_features_are_invariant_to_in_plane_rotation_and_scale(self):
        row=sample();expected=compare(row)['body_2d']['features']
        for j in row['mapped_people'][0]['joints']:
            x,y=j['x_px'],j['y_px'];j.update(x_px=300-(y-180)*.5,y_px=180+(x-300)*.5)
        actual=compare(row)['body_2d']['features']
        for name in ('shoulders','hips'):
            for key in ('transverse','axial'):self.assertAlmostEqual(actual[name][key],expected[name][key])

    def test_world_depth_is_separate_from_pixel_coordinates_and_has_no_bed_height(self):
        row=sample();before=compare(row)['body_2d']
        for p in row['raw_candidates'][0]['sdk_landmarks']['world']:
            x,z=p['x'],p['z'];p.update(x=z,z=-x)
        result=compare(row)
        self.assertEqual(result['body_2d'],before)
        self.assertAlmostEqual(abs(result['world_3d']['features']['shoulder_depth_fraction']),1.)
        self.assertNotIn('bed_height',result['world_3d']['features'])

    def test_missing_world_and_missing_2d_are_not_zero_filled(self):
        row=sample();del row['raw_candidates'][0]['sdk_landmarks']['world']
        result=compare(row);self.assertEqual(result['body_2d']['status'],'available')
        self.assertIsNone(result['world_3d']['features']);self.assertEqual(result['combined']['status'],'held')
        row=sample();row['mapped_people'][0]['joints'][5].update(x_px=None,y_px=None,availability='missing')
        result=compare(row);self.assertIsNone(result['body_2d']['features'])
        self.assertEqual(result['world_3d']['status'],'available');self.assertEqual(result['combined']['status'],'held')

    def test_invalid_world_mapping_nonfinite_and_collapsed_geometry_hold(self):
        for mutation in ('mapping','nonfinite','collapsed','metadata'):
            row=sample();raw=row['raw_candidates'][0]
            if mutation=='mapping':row['mapped_people'][0]['joints'][5]['source_index']=5
            if mutation=='nonfinite':raw['sdk_landmarks']['world'][11]['x']=float('nan');row['raw_candidates']=raw_json_value(row['raw_candidates'])
            if mutation=='collapsed':raw['sdk_landmarks']['world'][11]=copy.deepcopy(raw['sdk_landmarks']['world'][12])
            if mutation=='metadata':raw['world_coordinate_semantics']='unknown'
            result=compare(row);self.assertEqual(result['world_3d']['status'],'held',mutation)
            self.assertIsNone(result['world_3d']['features'])
            json.dumps(result,allow_nan=False)

    def test_same_candidate_matching_and_failure_tracking_gates(self):
        row=sample();raw=copy.deepcopy(row['raw_candidates'][0]);raw['candidate_id']='other'
        raw['sdk_landmarks']['world'][11]['z']=.4
        row['raw_candidates'].insert(0,raw)
        self.assertAlmostEqual(compare(row)['world_3d']['features']['shoulder_depth_fraction'],0.)
        for failure in ('processing','tracking','ambiguous'):
            r=copy.deepcopy(row)
            if failure=='processing':r['processing_status']='failed'
            if failure=='tracking':r['target_tracking']={'status':'held'}
            if failure=='ambiguous':
                other=copy.deepcopy(r['mapped_people'][0]);other['candidate_id']='other';r['mapped_people'].append(other)
            result=compare(r);self.assertIsNone(result['body_2d']['features']);self.assertIsNone(result['world_3d']['features'])
        assessment=assess_frame(row,640,360,None,AutomaticClassifier());assessment['stream_frame_id']=6
        self.assertEqual(compare_frame(row,640,360,assessment)['body_2d']['reasons'],['assessment_frame_mismatch'])

    def test_selected_second_candidate_uses_its_own_world_and_optional_scores(self):
        row=sample();other=copy.deepcopy(row['mapped_people'][0]);other['candidate_id']='other'
        row['mapped_people'].append(other)
        raw=copy.deepcopy(row['raw_candidates'][0]);raw['candidate_id']='other'
        for point in raw['sdk_landmarks']['world']:
            x,z=point['x'],point['z'];point.update(x=z,z=-x,visibility=None,presence=False)
        del raw['sdk_landmarks']['image']
        row['raw_candidates'].append(raw)
        row['target_tracking']={'status':'tracking_provisional','candidate_id':'other','track_id':'track'}
        result=compare(row)
        self.assertEqual(result['candidate_id'],'other');self.assertEqual(result['track_id'],'track')
        self.assertAlmostEqual(abs(result['world_3d']['features']['shoulder_depth_fraction']),1.)
        self.assertTrue(all(score=={'visibility':None,'presence':None} for score in result['world_3d']['scores']['world']))


class EvidenceWebTests(unittest.TestCase):
    setUp=web.WebTests.setUp
    cleanup=web.WebTests.cleanup
    request=web.WebTests.request
    wait=web.WebTests.wait
    demo=web.WebTests.demo
    control=web.WebTests.control

    def world_input(self):
        sid=self.demo();engine=self.workspace.sessions[sid].worker.engine
        raw=sample()['raw_candidates'];engine.profile=mapping_profile()
        engine.adapter.estimate=lambda model_input:PoseOutput(copy.deepcopy(raw),model_input.model_timestamp_ms,True)
        self.wait(lambda x:x['sessions'][0].get('posture_evidence',{}).get('world_3d',{}).get('status')=='available')
        return sid

    def finish(self,sid):
        self.control(sid,'region',box=[1,1,630,350],selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'start');self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.control(sid,'stop');state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        return Path(state['directory'])

    def test_world_features_replay_and_csv_cli_export(self):
        from bed_pose.cli import main
        sid=self.world_input();root=self.finish(sid)
        self.assertEqual(verify_live(root)['integrity'],'verified')
        path=root/'live_frames.jsonl';before=file_hash(path)
        output=self.root/'comparison';stdout=io.StringIO()
        with contextlib.redirect_stdout(stdout):code=main(['posture-evidence','--run',str(root),'--output',str(output)])
        self.assertEqual(code,0);self.assertEqual(file_hash(path),before)
        result=json.loads(stdout.getvalue());summary=load_json(output/'summary.json')
        self.assertEqual(result['frame_count'],summary['frame_count'])
        with (output/'features.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),summary['frame_count']);self.assertTrue(all(r['world_3d_status']=='available' for r in rows))
        self.assertAlmostEqual(float(rows[0]['shoulder_transverse_3d']),.8)
        self.assertEqual(summary['evaluation_status'],'features_only_no_ground_truth_accuracy')
        from bed_pose.evidence_report import export_evidence
        with self.assertRaises(FileExistsError):export_evidence(root,output)
        with self.assertRaises(ValueError):export_evidence(root,root/'comparison')

    def test_preview_record_recompute_and_semantic_tamper_rejection(self):
        sid=self.demo();status,view=self.request('/api/view/'+sid)
        self.assertEqual(status,200);self.assertEqual(view['posture_evidence']['stream_frame_id'],view['frame_id'])
        self.assertEqual(view['posture_evidence']['world_3d']['status'],'held')
        self.control(sid,'region',box=[1,1,200,200],selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'start');self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.control(sid,'stop');state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);self.assertEqual(verify_live(root)['integrity'],'verified')
        manifest=load_json(root/'manifest.json');self.assertEqual(manifest['posture_evidence_profile'],PROFILE)
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        rows[0]['posture_evidence']['body_2d']['features']['shoulders']['transverse']+=1
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaisesRegex(IntegrityError,'Posture evidence differs'):verify_live(root)

    def test_record_without_new_evidence_fields_remains_compatible(self):
        sid=self.demo();self.control(sid,'region',box=[1,1,200,200],selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'start');self.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
        self.control(sid,'stop');state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        root=Path(state['directory']);manifest=load_json(root/'manifest.json');del manifest['posture_evidence_profile']
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        for row in rows:del row['posture_evidence']
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        self.assertEqual(verify_live(root)['integrity'],'verified')
        from bed_pose.evidence_report import export_evidence
        _,summary=export_evidence(root,self.root/'old-comparison')
        self.assertEqual(summary['frame_count'],len(rows))

    def test_partial_evidence_extension_is_rejected(self):
        sid=self.world_input();root=self.finish(sid)
        path=root/'live_frames.jsonl';manifest=load_json(root/'manifest.json')
        rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        for missing in ('profile','evidence','assessment'):
            modified=copy.deepcopy(rows);header=copy.deepcopy(manifest)
            if missing=='profile':del header['posture_evidence_profile']
            if missing=='evidence':del modified[0]['posture_evidence']
            if missing=='assessment':
                del header['posture_assessment_profile'];del modified[0]['posture_assessment']
            path.write_text(''.join(json.dumps(r)+'\n' for r in modified),encoding='utf-8')
            header['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',header)
            with self.assertRaisesRegex(IntegrityError,'Missing posture'):verify_live(root)
