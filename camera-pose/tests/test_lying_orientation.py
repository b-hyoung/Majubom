"""Synthetic orientation decisions and replay; not human accuracy measurements."""
import copy
import math
import json
from dataclasses import replace
from pathlib import Path
import unittest

from bed_pose.automatic_posture import AutomaticClassifier,V4_DEFAULTS as DEFAULTS,OLD_DEFAULTS,TORSO_METHOD,LEGACY_METHOD,PREVIOUS_METHOD
from bed_pose.posture import assess_frame
from bed_pose.posture_evidence import world_body_evidence
from bed_pose.pose.mediapipe import COCO_INDICES
from bed_pose.contracts import PoseOutput,IntegrityError
from bed_pose.live_recording import verify_live
from bed_pose.io_utils import atomic_json,load_json,file_hash
from test_automatic_posture import observation,BOX,points
from test_posture_evidence import sample
import test_posture_evidence as evidence_tests


def raw_world(depth):
    raw=copy.deepcopy(sample()['raw_candidates'])
    for left,right,width,y in ((11,12,.4,-.5),(23,24,.3,0)):
        x=width*math.sqrt(1-depth**2)/2;z=width*depth/2
        for index,sign in ((left,-1),(right,1)):
            raw[0]['sdk_landmarks']['world'][index].update(x=sign*x,y=y,z=sign*z)
    return raw


def world(depth):
    row=sample();row['raw_candidates']=raw_world(depth)
    return world_body_evidence(row,row['mapped_people'][0])


def gap():
    obs=observation('supine');p=list(obs.points)
    for i,point in {5:(200.,165.),6:(200.,195.),11:(300.,165.),12:(300.,195.)}.items():p[i]=point
    return replace(obs,points=tuple(p))


def sdk_gap(depth):
    raw=raw_world(depth)
    for source,(x,y) in zip(COCO_INDICES,gap().points):
        raw[0]['pose_blocks'][0]['values'][source]=[x/640,y/360]
        raw[0]['sdk_landmarks']['image'][source].update(x=x/640,y=y/360)
    return raw


class LyingOrientationTests(unittest.TestCase):
    def test_2d_axial_transverse_decomposition_changes_final_label(self):
        obs=gap();p=list(obs.points)
        for i,point in {5:(190.,172.5),6:(210.,187.5),11:(290.,172.5),12:(310.,187.5)}.items():p[i]=point
        obs=replace(obs,points=tuple(p))
        self.assertEqual(AutomaticClassifier(OLD_DEFAULTS).evaluate(obs)[1],'lying_orientation_ambiguous')
        label,reason,f=AutomaticClassifier(DEFAULTS).evaluate(obs)
        self.assertEqual((label,reason),('side',None))
        self.assertEqual(f['orientation_decision']['source'],'body_2d')

    def test_world_resolves_uncertainty_band_in_final_labels(self):
        model=AutomaticClassifier(DEFAULTS)
        self.assertEqual(model.evaluate(gap())[1],'lying_orientation_ambiguous')
        for depth,label in ((.9,'side'),(0.,'supine'),(-.9,'side')):
            actual=model.evaluate(replace(gap(),world_body=world(depth)))
            self.assertEqual(actual[:2],(label,None))
            self.assertEqual(actual[2]['orientation_decision']['source'],'body_2d_world_3d')

    def test_explicit_2d_world_conflicts_hold_instead_of_forcing_label(self):
        for pose,depth in (('supine',.9),('side',0.)):
            actual=AutomaticClassifier(DEFAULTS).evaluate(replace(observation(pose),world_body=world(depth)))
            self.assertEqual(actual[:2],(None,'orientation_evidence_conflict'))

    def test_world_uncertainty_or_absence_preserves_strong_2d_path(self):
        for depth in (None,.5):
            for label in ('supine','side'):
                obs=replace(observation(label),world_body=world(depth) if depth is not None else None)
                self.assertEqual(AutomaticClassifier(DEFAULTS).predict(obs),label)
        malformed=copy.deepcopy(world(.9));malformed.update(status='held',features=None,reasons=['world_coordinates_invalid'])
        self.assertEqual(AutomaticClassifier(DEFAULTS).evaluate(replace(gap(),world_body=malformed))[1],'lying_orientation_ambiguous')

    def test_torso_and_lying_eligibility_gates_cannot_be_overridden_by_world(self):
        model=AutomaticClassifier(DEFAULTS)
        for pose in ('standing','edge_sitting'):
            obs=replace(observation(pose),world_body=world(.9))
            self.assertEqual(model.predict(obs),'edge_sitting' if pose=='edge_sitting' else None)
            self.assertEqual(model.evaluate(obs)[2]['orientation_decision']['status'],'not_used')
        for region,reason in ((None,'bed_region_required'),((1,1,50,50),'body_outside_bed_region')):
            self.assertEqual(model.evaluate(replace(gap(),bed_rectangle=region,world_body=world(.9)))[1],reason)
        p=list(gap().points)
        for i in (11,12):p[i]=(p[i][0],p[i][1]+100)
        self.assertEqual(model.evaluate(replace(gap(),points=tuple(p),world_body=world(.9)))[1],'view_or_body_orientation_ambiguous')
        p=list(gap().points);p[5]=None
        self.assertEqual(model.evaluate(replace(gap(),points=tuple(p),world_body=world(.9)))[1],'automatic_torso_joints_missing')

    def test_supine_world_rescue_still_requires_anterior_cues(self):
        for change,reason in (('face','front_facing_evidence_missing'),('back','front_back_ambiguous')):
            p=list(gap().points)
            if change=='face':p[0]=None
            else:p[5],p[6],p[11],p[12]=p[6],p[5],p[12],p[11]
            self.assertEqual(AutomaticClassifier(DEFAULTS).evaluate(replace(gap(),points=tuple(p),world_body=world(0.)))[1],reason)

    def test_crossed_twisted_and_inconsistent_torso_is_not_side(self):
        for change,reason in (('crossed','body_axis_inconsistent'),('twisted','body_axis_inconsistent'),('mixed','shoulder_hip_orientation_conflict')):
            obs=observation('supine');p=list(obs.points)
            if change=='crossed':p[11],p[12]=p[12],p[11]
            if change=='twisted':p[5],p[6]=(175.,175.),(225.,185.)
            if change=='mixed':p[11],p[12]=(300.,175.),(300.,185.)
            self.assertEqual(AutomaticClassifier(DEFAULTS).evaluate(replace(obs,points=tuple(p),world_body=world(.9)))[1],reason)

    def test_body_frame_rotation_and_no_previous_frame_label_reuse(self):
        model=AutomaticClassifier(dict(DEFAULTS,view='overhead'))
        base=replace(gap(),bed_rectangle=(0,0,640,640),height=640,world_body=world(.9))
        for angle in (0,45,90,135):
            a=math.radians(angle)
            p=tuple((300+(x-300)*math.cos(a)-(y-300)*math.sin(a),300+(x-300)*math.sin(a)+(y-300)*math.cos(a)) for x,y in base.points)
            self.assertEqual(model.predict(replace(base,points=p)),'side')
        model=AutomaticClassifier(DEFAULTS)
        for depth,label in ((.9,'side'),(None,None),(0.,'supine')):
            self.assertEqual(model.predict(replace(gap(),world_body=world(depth) if depth is not None else None)),label)

    def test_old_rule_profiles_ignore_new_world_evidence_and_remain_frozen(self):
        for version in (LEGACY_METHOD,PREVIOUS_METHOD,TORSO_METHOD):
            model=AutomaticClassifier(dict(OLD_DEFAULTS,version=version))
            for obs in (gap(),observation('supine'),observation('side')):
                self.assertEqual(model.evaluate(obs),model.evaluate(replace(obs,world_body=world(.9))))
            self.assertNotIn('world_side_depth_min',model.profile)

    def test_selected_candidate_world_mapping_and_external_input_independence(self):
        from bed_pose.mapping import map_keypoints
        from bed_pose.pose.mediapipe import mapping_profile
        raw=sdk_gap(.9);other=copy.deepcopy(sdk_gap(0.)[0]);other['candidate_id']='other';raw.append(other)
        mapped=map_keypoints(PoseOutput(raw,0,True),640,360,mapping_profile())
        row=dict(sample(),raw_candidates=raw,mapped_people=mapped)
        region=dict(zip(('x1','y1','x2','y2'),BOX))
        self.assertEqual(assess_frame(row,640,360,region,AutomaticClassifier(DEFAULTS))['reasons'],['ambiguous_target'])
        row['target_tracking']={'status':'tracking_provisional','candidate_id':'other','track_id':'track'}
        result=assess_frame(row,640,360,region,AutomaticClassifier(DEFAULTS))
        self.assertEqual(result['label'],'supine');self.assertEqual(result['candidate_id'],'other')
        row.update(target_posture='side',verified_gt='side',sensor_results={'label':'side'})
        self.assertEqual(assess_frame(row,640,360,region,AutomaticClassifier(DEFAULTS)),result)
        row['mapped_people'][1]['joints'][5]['source_index']=5
        result=assess_frame(row,640,360,region,AutomaticClassifier(DEFAULTS))
        self.assertIsNone(result['label']);self.assertEqual(result['reasons'],['lying_orientation_ambiguous'])

    def test_new_thresholds_are_versioned_and_invalid_profiles_rejected(self):
        for key,value in (('body_axial_max',True),('body_line_angle_max',90),('world_side_depth_min',.2),('world_depth_difference_max',2)):
            with self.assertRaises(ValueError):AutomaticClassifier(dict(DEFAULTS,**{key:value}))
        with self.assertRaises(ValueError):AutomaticClassifier(dict(DEFAULTS,version=TORSO_METHOD))


class LyingOrientationWebTests(unittest.TestCase):
    setUp=evidence_tests.EvidenceWebTests.setUp
    cleanup=evidence_tests.EvidenceWebTests.cleanup
    request=evidence_tests.EvidenceWebTests.request
    wait=evidence_tests.EvidenceWebTests.wait
    demo=evidence_tests.EvidenceWebTests.demo
    control=evidence_tests.EvidenceWebTests.control
    world_input=evidence_tests.EvidenceWebTests.world_input

    def finish(self,sid):
        previous=self.workspace.sessions[sid].state.get('directory')
        self.assertEqual(self.control(sid,'start')[0],200)
        self.wait(lambda x:x['sessions'][0]['recording'] and x['sessions'][0]['directory']!=previous
                  and x['sessions'][0]['recorded_frames']>=2)
        self.assertEqual(self.control(sid,'stop')[0],200)
        state=self.wait(lambda x:x['sessions'][0]['integrity']=='verified')['sessions'][0]
        return Path(state['directory'])

    def orientation_input(self):
        sid=self.world_input();engine=self.workspace.sessions[sid].worker.engine
        self.raw=sdk_gap(.9)
        engine.adapter.estimate=lambda m:PoseOutput(copy.deepcopy(self.raw),m.model_timestamp_ms,True)
        self.control(sid,'region',box=BOX,selected_frame_id=0)
        self.wait(lambda x:x['sessions'][0]['region'] is not None)
        self.control(sid,'posture_mode',mode='automatic',view='oblique')
        self.wait(lambda x:x['sessions'][0]['posture_assessment']['label']=='side' and x['sessions'][0]['pending'] is None)
        engine.posture_classifier=AutomaticClassifier(DEFAULTS)
        self.wait(lambda x:x['sessions'][0]['posture_assessment']['classifier_id'].startswith(DEFAULTS['version']+':') and x['sessions'][0]['posture_assessment']['label']=='side')
        return sid

    def test_final_world_label_preview_record_replay_and_tampering(self):
        sid=self.orientation_input();_,view=self.request('/api/view/'+sid)
        self.assertEqual(view['posture_assessment']['label'],'side')
        self.assertEqual(view['posture_evidence']['combined']['classifier_status'],'used_by_automatic_rules')
        self.assertEqual(view['posture_assessment']['stream_frame_id'],view['frame_id'])
        root=self.finish(sid);self.assertEqual(verify_live(root)['integrity'],'verified')
        manifest=load_json(root/'manifest.json');self.assertEqual(manifest['automatic_posture_profile'],DEFAULTS)
        from bed_pose.evidence_report import export_evidence
        import csv
        output,_=export_evidence(root,self.root/'orientation-report')
        with (output/'features.csv').open(encoding='utf-8-sig',newline='') as stream:report=list(csv.DictReader(stream))
        self.assertTrue(all(r['baseline_label']=='side' and r['decision_source']=='body_2d_world_3d' for r in report))
        path=root/'live_frames.jsonl';rows=[json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(r['posture_prediction']['label']=='side' for r in rows))
        rows[0]['raw_candidates'][0]['sdk_landmarks']['world'][11]['z']=0
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        manifest['artifact_sha256'][path.name]=file_hash(path);atomic_json(root/'manifest.json',manifest)
        with self.assertRaises(IntegrityError):verify_live(root)

    def test_v3_world_record_remains_held_and_export_does_not_reclassify_it(self):
        sid=self.orientation_input();engine=self.workspace.sessions[sid].worker.engine
        engine.posture_classifier=AutomaticClassifier(OLD_DEFAULTS)
        self.wait(lambda x:x['sessions'][0]['posture_assessment']['classifier_id'].startswith(TORSO_METHOD+':')
                  and x['sessions'][0]['posture_assessment']['reasons']==['lying_orientation_ambiguous'])
        root=self.finish(sid);self.assertEqual(verify_live(root)['integrity'],'verified')
        from bed_pose.evidence_report import export_evidence
        import csv
        output,report=export_evidence(root,self.root/'old-orientation-report')
        self.assertEqual(report['source_automatic_posture_profile'],OLD_DEFAULTS)
        _,view=self.request('/api/view/'+sid)
        self.assertEqual(view['posture_evidence']['combined']['classifier_status'],'not_configured')
        with (output/'features.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertTrue(all(r['baseline_status']=='held' and r['baseline_label']=='' and r['decision_source']=='' for r in rows))
