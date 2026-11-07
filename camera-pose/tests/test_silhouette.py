"""Controlled mask/SDK samples; not human segmentation or posture accuracy."""
import copy
import csv
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from bed_pose.contracts import ModelInput,FramePacket,PoseOutput,IntegrityError
from bed_pose.silhouette import PROFILE,anchors,capture,assess
from bed_pose.posture import assess_frame
from bed_pose.automatic_posture import AutomaticClassifier
from bed_pose.live_recording import verify_live
from bed_pose.io_utils import atomic_json,load_json,file_hash
from bed_pose.pose.mediapipe import MediaPipeAdapter,mapping_profile
from bed_pose.mapping import map_keypoints
from test_posture_evidence import sample
import test_mediapipe_adapter as sdk
import test_weighted_posture as weighted_tests


class Mask:
    def __init__(self,values):self.values=values
    def numpy_view(self):return self.values


def mask(kind='consistent',width=640,height=360):
    a=np.full((height,width),.05,dtype=np.float32)
    if kind=='conflict':a[10:80,10:80]=.95
    else:a[100:260,100:550]=.5 if kind=='uncertain' else .95
    return Mask(a)


def row_with_mask(kind='consistent'):
    row=sample();row.update(stream_id='stream',model_timestamp_ms='1')
    row['posture_assessment']=assess_frame(row,640,360,{'x1':100,'y1':100,'x2':550,'y2':250},AutomaticClassifier())
    m=ModelInput(FramePacket('stream',row['stream_frame_id'],np.zeros((360,640,3),dtype=np.uint8),{}),1)
    row['silhouette_observation']=capture(SimpleNamespace(segmentation_masks=[mask(kind)]),row['raw_candidates'],m)
    return row


class SilhouetteTests(unittest.TestCase):
    def test_consistent_conflict_and_ambiguous_never_change_posture(self):
        expected=None
        for kind,status in (('consistent','consistent'),('conflict','conflict'),('uncertain','unavailable')):
            row=row_with_mask(kind);before=copy.deepcopy(row)
            actual=assess(row,640,360)
            self.assertEqual(actual['status'],status)
            self.assertFalse(actual['affects_posture']);self.assertFalse(actual['independent_evidence'])
            self.assertEqual(row,before)
            posture=assess_frame(row,640,360,{'x1':100,'y1':100,'x2':550,'y2':250},AutomaticClassifier())
            if expected is None:expected=posture
            self.assertEqual(posture,expected);self.assertEqual(posture['label'],'supine')
            if kind=='consistent':self.assertEqual(actual['inside_count'],7)
            if kind=='conflict':self.assertEqual(actual['outside_count'],7)
        row.update(target_posture='side',verified_gt='side',sensor_results={'label':'side'})
        self.assertEqual(assess(row,640,360),assess(before,640,360))

    def test_sparse_samples_are_detached_and_not_full_masks(self):
        row=row_with_mask();packet=row['silhouette_observation']
        self.assertEqual(len(packet['candidates'][0]['samples']),7)
        self.assertTrue(all(len(p['pixels'])<=9 for p in packet['candidates'][0]['samples']))
        self.assertLess(len(json.dumps(packet,allow_nan=False)),12000)
        self.assertNotIn('mask_pixels',packet);self.assertEqual(len(packet['candidates'][0]['mask_sha256']),64)
        for saved in packet['candidates'][0]['samples']:self.assertTrue(all(type(p[2]) is float for p in saved['pixels']))

    def test_uninformative_nonfinite_missing_and_mismatched_masks(self):
        row=row_with_mask();m=ModelInput(FramePacket('stream',7,np.zeros((360,640,3),dtype=np.uint8),{}),1)
        for value,reason in ((None,'mask_not_provided'),([], 'mask_candidate_mismatch'),
                ([Mask(np.zeros((360,640),dtype=np.float32))],'mask_not_informative'),
                ([Mask(np.ones((360,640),dtype=np.float32))],'mask_not_informative'),
                ([Mask(np.full((360,640),float('nan'),dtype=np.float32))],'mask_values_invalid'),
                ([Mask(np.ones((180,320),dtype=np.float32))],'mask_geometry_mismatch')):
            row['silhouette_observation']=capture(SimpleNamespace(segmentation_masks=value),row['raw_candidates'],m)
            a=assess(row,640,360);self.assertEqual(a['status'],'unavailable');self.assertEqual(a['reasons'],[reason])

    def test_frame_identity_tracking_and_sample_mapping_gates(self):
        original=row_with_mask()
        for change,reason in (('frame','mask_frame_mismatch'),('clock','mask_frame_mismatch'),
                              ('target','target_held'),('failed','processing_failed'),
                              ('point','mask_samples_invalid'),('probability','mask_samples_invalid'),
                              ('mapped_point','mask_mapping_mismatch'),('source_index','mask_mapping_mismatch')):
            row=copy.deepcopy(original)
            if change=='frame':row['silhouette_observation']['stream_frame_id']+=1
            if change=='clock':row['posture_assessment']['clock_id']='other'
            if change=='target':row['target_tracking']={'status':'held'}
            if change=='failed':row['processing_status']='failed'
            if change=='point':row['silhouette_observation']['candidates'][0]['samples'][0]['xy'][0]+=1
            if change=='probability':row['silhouette_observation']['candidates'][0]['samples'][0]['pixels'][0][2]=True
            if change=='mapped_point':row['mapped_people'][0]['joints'][5]['x_px']+=5
            if change=='source_index':row['mapped_people'][0]['joints'][5]['source_index']=5
            self.assertEqual(assess(row,640,360)['reasons'],[reason],change)

    def test_selected_candidate_is_matched_by_id_not_position(self):
        row=row_with_mask();other=copy.deepcopy(row['raw_candidates'][0]);other['candidate_id']='other'
        row['raw_candidates'].append(other)
        row['mapped_people']=map_keypoints(PoseOutput(row['raw_candidates'],1,True),640,360,mapping_profile())
        row['target_tracking']={'status':'tracking_provisional','candidate_id':'other','track_id':'track'}
        row['posture_assessment']=assess_frame(row,640,360,{'x1':100,'y1':100,'x2':550,'y2':250},AutomaticClassifier())
        m=ModelInput(FramePacket('stream',7,np.zeros((360,640,3),dtype=np.uint8),{}),1)
        row['silhouette_observation']=capture(SimpleNamespace(segmentation_masks=[mask('conflict'),mask()]),row['raw_candidates'],m)
        row['silhouette_observation']['candidates'].reverse()
        actual=assess(row,640,360);self.assertEqual(actual['candidate_id'],'other');self.assertEqual(actual['status'],'consistent')
        del row['target_tracking'];row['posture_assessment']=assess_frame(row,640,360,None,AutomaticClassifier())
        self.assertEqual(assess(row,640,360)['status'],'unavailable')


class SilhouetteAdapterTests(unittest.TestCase):
    setUp=sdk.AdapterTests.setUp
    input=sdk.AdapterTests.input

    def test_opt_in_sdk_output_preserves_original_landmarks_and_offline_default(self):
        result=sdk.result();a=np.full((100,200),.05,dtype=np.float32);a[:30,:80]=.95
        result.segmentation_masks=[Mask(a)]
        plain=MediaPipeAdapter();plain.open(self.settings)
        enabled=MediaPipeAdapter(silhouette=True);metadata=enabled.open(self.settings)
        with patch.object(self.tasks[0],'detect',return_value=result):old=plain.estimate(self.input())
        with patch.object(self.tasks[1],'detect',return_value=result):new=enabled.estimate(self.input())
        self.assertFalse(plain.sdk_options.output_segmentation_masks)
        self.assertTrue(enabled.sdk_options.output_segmentation_masks);self.assertTrue(metadata['segmentation_masks'])
        self.assertEqual(old.candidates,new.candidates);self.assertIsNone(old.silhouette_observation)
        self.assertIsNone(old.silhouette_preview)
        self.assertTrue(new.silhouette_preview['candidates'][0]['image'].startswith('data:image/png;base64,'))
        self.assertEqual(new.silhouette_observation['candidates'][0]['status'],'available')
        with patch.object(self.tasks[1],'detect',return_value=result), patch('bed_pose.silhouette_overlay.capture_preview',side_effect=RuntimeError('display error')):
            degraded=enabled.estimate(self.input())
        self.assertEqual(degraded.candidates,new.candidates);self.assertIsNone(degraded.silhouette_preview)
        self.assertEqual(degraded.silhouette_observation,new.silhouette_observation)
        plain.close();enabled.close()

    def test_auxiliary_read_failure_does_not_fail_pose_inference(self):
        class BrokenMask:
            def numpy_view(self):raise RuntimeError('mask unavailable')
        result=sdk.result();result.segmentation_masks=[BrokenMask()]
        enabled=MediaPipeAdapter(silhouette=True);enabled.open(self.settings)
        with patch.object(self.tasks[0],'detect',return_value=result):output=enabled.estimate(self.input())
        self.assertEqual(len(output.candidates),1);self.assertEqual(output.silhouette_observation['candidates'][0]['reason'],'mask_read_failed')
        enabled.close()

    def test_live_factory_opt_in_enables_mask_without_adding_a_model(self):
        from bed_pose.live_factory import make_engine
        from bed_pose.live_sources import CameraSource
        engine=make_engine(CameraSource(0),self.model,self.root/'records',self.root,silhouette=True)
        self.assertTrue(engine.adapter.silhouette);self.assertEqual(len(engine.settings['assets']),1)


class SilhouetteWebTests(unittest.TestCase):
    setUp=weighted_tests.WeightedWebTests.setUp
    cleanup=weighted_tests.WeightedWebTests.cleanup
    request=weighted_tests.WeightedWebTests.request
    wait=weighted_tests.WeightedWebTests.wait
    demo=weighted_tests.WeightedWebTests.demo
    control=weighted_tests.WeightedWebTests.control
    world_input=weighted_tests.WeightedWebTests.world_input
    weighted_input=weighted_tests.WeightedWebTests.weighted_input
    finish=weighted_tests.WeightedWebTests.finish

    def visual_input(self):
        sid=self.weighted_input();engine=self.workspace.sessions[sid].worker.engine
        self.mask=mask()
        def estimate(m):
            raw=copy.deepcopy(self.raw);current_mask=self.mask
            observation=capture(SimpleNamespace(segmentation_masks=None if current_mask is None else [current_mask]),raw,m)
            return PoseOutput(raw,m.model_timestamp_ms,True,observation)
        engine.adapter.estimate=estimate
        self.wait(lambda x:x['sessions'][0].get('silhouette_assessment',{}).get('status')=='consistent')
        return sid

    def test_preview_final_label_record_replay_history_csv_and_tamper(self):
        sid=self.visual_input();self.mask=mask('conflict')
        self.wait(lambda x:x['sessions'][0]['silhouette_assessment']['status']=='conflict')
        _,view=self.request('/api/view/'+sid)
        self.assertEqual(view['posture_assessment']['label'],'side')
        self.assertEqual(view['silhouette_assessment']['stream_frame_id'],view['frame_id'])
        self.assertFalse(view['silhouette_assessment']['affects_posture'])
        root=self.finish(sid);self.assertEqual(verify_live(root)['integrity'],'verified')
        manifest=load_json(root/'manifest.json');self.assertEqual(manifest['silhouette_profile'],PROFILE)
        self.assertEqual(self.request('/api/records/open',{'record_id':root.name})[0],200)
        _,page=self.request('/api/records/frames?record_id='+root.name)
        self.assertTrue(all(r['silhouette_assessment']['status']=='conflict' and r['assessment']['label']=='side' for r in page['frames']))
        from bed_pose.evidence_report import export_evidence
        output,_=export_evidence(root,self.root/'silhouette-report')
        with (output/'features.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertTrue(all(r['silhouette_status']=='conflict' and r['baseline_label']=='side' for r in rows))
        path=root/'live_frames.jsonl';original=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        original[0]['silhouette_assessment']['status']='consistent'
        path.write_text(''.join(json.dumps(r)+'\n' for r in original),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaisesRegex(IntegrityError,'Silhouette evidence differs'):verify_live(root)

    def test_previous_record_without_silhouette_fields_remains_readable(self):
        sid=self.visual_input();root=self.finish(sid);manifest=load_json(root/'manifest.json');manifest.pop('silhouette_profile')
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        for row in rows:row.pop('silhouette_observation');row.pop('silhouette_assessment')
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        self.assertEqual(verify_live(root)['integrity'],'verified')
        self.request('/api/records/open',{'record_id':root.name})
        _,page=self.request('/api/records/frames?record_id='+root.name)
        self.assertTrue(all(r['silhouette_assessment'] is None for r in page['frames']))

    def test_partial_extension_missing_samples_and_profile_is_rejected(self):
        sid=self.visual_input();root=self.finish(sid);manifest=load_json(root/'manifest.json')
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        for missing in ('observation','assessment','profile'):
            changed=copy.deepcopy(rows);header=copy.deepcopy(manifest)
            if missing=='profile':del header['silhouette_profile']
            else:del changed[0]['silhouette_'+missing]
            path.write_text(''.join(json.dumps(r)+'\n' for r in changed),encoding='utf-8')
            header['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',header)
            with self.assertRaisesRegex(IntegrityError,'Missing silhouette'):verify_live(root)
