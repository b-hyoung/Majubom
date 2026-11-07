"""Two-view input preparation only. No coordinate averaging or posture inference."""
from collections import Counter
import os
from pathlib import Path
from contextlib import ExitStack
from .contracts import IntegrityError
from .io_utils import load_json, strict_loads, json_text, file_hash, object_hash
from .joint_inspection import inspect_joints
from .live_recording import verify_live

LEGACY_SCHEMA='two-view-input-0.1-draft'
SCHEMA='two-view-input-0.2-draft'
LEGACY_POLICY={'method':'separate_view_features_v1','identity_verified':False,
        'normalization':'image_dimensions_and_manual_bed_rectangle',
        'missing_value_policy':'null_with_masks','joint_definition':'coco17',
        'bed_coordinate_units':'dimensionless_image_rectangle_not_metric_or_perspective_rectified','body_normalization':'not_configured',
        'coordinate_fusion':'none','view_order':'group_member_order_not_anatomical_left_right','posture_classifier':'not_implemented'}

POLICY=dict(LEGACY_POLICY,method='separate_view_features_v2',
            bed_mask_meaning='in_image_coordinate_and_reference_rectangle_available',
            inside_bed_meaning='rectangle_membership_including_boundary_null_if_unavailable')

QUAD_SCHEMA='two-view-input-0.3-draft'
QUAD_POLICY=dict(POLICY,method='separate_view_features_v3',
    normalization='image_dimensions_and_per_region_rectangle_or_quadrilateral_homography',
    bed_coordinate_units='dimensionless_unit_square_click_order_axes_not_metric_or_3d',
    bed_mask_meaning='in_image_coordinate_and_finite_region_transform',
    inside_bed_meaning='polygon_or_legacy_rectangle_membership_including_boundary_null_if_unavailable')

def input_schema(group):
    for member in group['members']:
        root=Path(member['run_directory']);manifest=load_json(root/'manifest.json')
        if any(r and r.get('method')=='manual_quadrilateral' for r in bed_regions(root,manifest).values()):return QUAD_SCHEMA
    return SCHEMA

def next_json(stream):
    try:return strict_loads(next(stream))
    except StopIteration as exc:raise IntegrityError('Missing input/source row') from exc


def confirmations(group):
    ids=[m['input_id'] for m in group['members']]
    initial={t['input_id']:t.get('tracking_at_group_start',{}).get('track_id') for t in group.get('target_selections',[])}
    result=[]
    if group.get('same_person_assertion')=='operator_report_not_verified' and all(initial.get(i) for i in ids):
        result.append((int(group.get('operator_initial_confirmation_perf_counter_ns') or group['requested_start_perf_counter_ns']),initial))
    for event in group.get('target_pair_confirmations',[]):
        if event['assertion']!='operator_report_not_verified':raise IntegrityError('Unsupported identity assertion')
        tracks={m['input_id']:m['track_id'] for m in event['members']}
        if set(tracks)!=set(ids) or any(not tracks[i] for i in ids):raise IntegrityError('Invalid target confirmation')
        result.append((int(event['requested_perf_counter_ns']),tracks))
    return sorted(result,key=lambda item:item[0])


def bed_regions(directory,manifest):
    regions={manifest['initial_bed_revision']:manifest['initial_bed_region']}
    with (directory/'control_events.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            event=strict_loads(line)
            if event['type']=='bed_region_changed':regions[event['revision']]=event['region']
    return regions


def view_features(row,manifest,regions,input_id,bed_auto=False):
    tracking=row.get('target_tracking',{'status':'not_configured','candidate_id':None})
    reasons=[]
    if row['processing_status']!='ok':reasons.append('processing_failed')
    if not bed_auto and tracking['status']!='tracking_provisional':reasons.append('target_'+tracking['status'])
    region=regions[row['bed_region_revision']]
    if region is None:reasons.append('bed_region_missing')
    from .bed_association import eligible
    is_eligible=bed_auto and eligible(row.get('candidate_count'),row.get('posture_assessment'),region,selection=row.get('bed_target_selection'),policy=bed_auto if isinstance(bed_auto,str) else 'unique-person-in-bed-v1')
    if bed_auto and not is_eligible:reasons.append('bed_person_unavailable_or_ambiguous')
    candidate_id=(row.get('posture_assessment') or {}).get('candidate_id') if bed_auto else tracking.get('candidate_id')
    candidate=next((p for p in row['mapped_people'] or [] if p['candidate_id']==candidate_id),None)
    features=None
    if (is_eligible or not bed_auto and tracking['status']=='tracking_provisional') and candidate is not None:
        inspected=inspect_joints({'people':[candidate]},{'candidates':row['raw_candidates']},
                                 manifest['mapping_profile']['image_block_id'])[0]['joints']
        features=[]
        for joint in inspected:
            numeric=joint['availability']=='predicted' and joint['x_px'] is not None and joint['y_px'] is not None
            inside=numeric and 0<=joint['x_px']<manifest['width'] and 0<=joint['y_px']<manifest['height']
            from .bed_geometry import contains,relative_xy
            bed_xy=relative_xy(region,(joint['x_px'],joint['y_px'])) if numeric and region else None
            features.append(dict(joint,
                image_xy=[joint['x_px']/manifest['width'],joint['y_px']/manifest['height']] if numeric else [None,None],
                image_mask=bool(inside),
                bed_xy=bed_xy if bed_xy is not None else [None,None],
                bed_mask=bool(inside and bed_xy is not None),
                inside_bed=bool(contains(region,(joint['x_px'],joint['y_px']))) if numeric and region else None))
        if not any(j['image_mask'] for j in features):reasons.append('no_in_image_joints')
    elif not bed_auto and tracking['status']=='tracking_provisional':reasons.append('tracked_candidate_missing')
    result={'input_id':input_id,'measurement_id':manifest['measurement_id'],'frame_id':row['frame_id'],
            'stream_frame_id':row['stream_frame_id'],'clock_id':row['clock_id'],'received_ns':row['received_ns'],
            'time_ns':str(int(manifest['clock_origin_perf_counter_ns'])+int(row['received_ns'])),
            'width':manifest['width'],'height':manifest['height'],
            'mapping_profile_id':manifest['mapping_profile']['profile_id'],
            'score_source_block':manifest['mapping_profile']['image_block_id'],'bed_region_revision':row['bed_region_revision'],
            'bed_region':region,'tracking':tracking,'joints':features,'hold_reasons':reasons,
            'identity_verified':False,'input_quality_status':'not_assessed'}
    if bed_auto:result['unique_bed_candidate']=bool(is_eligible and candidate is not None)
    if manifest.get('reference_posture_profile') or manifest.get('automatic_posture_profile'):
        result['posture_assessment']=row['posture_assessment']
    return result


def iter_inputs(group,directory,*,legacy=False):
    directory=Path(directory)
    alignment=group['alignment']
    if alignment['status']!='receive_time_approximation':raise IntegrityError('Unsupported alignment time basis')
    if alignment['path']!='alignment.jsonl' or file_hash(directory/'alignment.jsonl')!=alignment['sha256']:
        raise IntegrityError('Alignment checksum mismatch')
    if len(group['members'])!=2:raise IntegrityError('Exactly two views required')
    plan=group.get('measurement_plan')
    if plan:
        from .measurement_plan import validate_plan
        validated=validate_plan({'goal_posture':plan['goal_posture'],'preparation_mode':plan['preparation_mode'],
            'preparation_seconds':plan['preparation_seconds'] if plan['preparation_mode']=='timer' else 0,
            'duration_seconds':plan['duration_seconds']})
        if any(plan.get(key)!=value for key,value in validated.items()):raise IntegrityError('Plan instruction metadata mismatch')
    schema=input_schema(group)
    if legacy and schema==QUAD_SCHEMA:raise IntegrityError('Legacy schema cannot describe quadrilaterals')
    timeline=confirmations(group)
    with ExitStack() as stack:
        manifests=[];regions=[];streams=[];consumed=[0,0]
        for member in group['members']:
            root=Path(member['run_directory'])
            verify_live(root)
            if file_hash(root/'manifest.json')!=member['manifest_sha256']:raise IntegrityError('Original run manifest changed')
            manifest=load_json(root/'manifest.json')
            manifests.append(manifest);regions.append(bed_regions(root,manifest))
            streams.append(stack.enter_context((root/'live_frames.jsonl').open(encoding='utf-8')))
        source=stack.enter_context((directory/'alignment.jsonl').open(encoding='utf-8'))
        for index,line in enumerate(source):
            aligned=strict_loads(line)
            if aligned['alignment_id']!=index:raise IntegrityError('Alignment order mismatch')
            views=[];reasons=[]
            for side,key in enumerate(('left','right')):
                ref=aligned[key]
                if ref is None:views.append(None);continue
                row=next_json(streams[side])
                manifest=manifests[side]
                if (row['frame_id']!=consumed[side] or ref['measurement_id']!=manifest['measurement_id']
                    or any(ref[k]!=row[k] for k in ('frame_id','stream_frame_id','clock_id','received_ns','processing_status'))
                    or int(ref['time_ns'])!=int(manifest['clock_origin_perf_counter_ns'])+int(row['received_ns'])):
                    raise IntegrityError('Alignment source reference mismatch')
                consumed[side]+=1
                view=view_features(row,manifest,regions[side],group['members'][side]['input_id'],bed_auto=group.get('association_policy') if group.get('association_policy') in ('unique-person-in-bed-v1','bed-priority-v2') else False)
                if legacy and view['joints'] is not None:
                    for joint in view['joints']:joint.pop('inside_bed')
                views.append(view);reasons.extend(key+':'+r for r in view['hold_reasons'])
            if aligned['status']=='matched':
                if any(v is None for v in views):raise IntegrityError('Matched row missing a view')
                delta=int(views[1]['time_ns'])-int(views[0]['time_ns'])
                if str(delta)!=aligned['delta_ns'] or abs(delta)>int(alignment['max_skew_ns']):
                    raise IntegrityError('Alignment delta mismatch')
                eligible=[entry for entry in timeline if entry[0]<=min(int(v['time_ns']) for v in views)]
                confirmed=eligible[-1][1] if eligible else {}
                if group.get('association_policy') not in ('unique-person-in-bed-v1','bed-priority-v2') and any(not v['tracking'].get('track_id') or confirmed.get(v['input_id'])!=v['tracking'].get('track_id') for v in views):
                    reasons.append('operator_pair_confirmation_missing')
            elif aligned['status']=='unmatched' and sum(v is not None for v in views)==1:
                reasons.append('unmatched_time')
            else:raise IntegrityError('Invalid alignment status/shape')
            result={'schema_version':LEGACY_SCHEMA if legacy else schema,'group_id':group['group_id'],'alignment_id':index,
                   'status':'held' if reasons else 'candidate_input','hold_reasons':reasons,
                   'views':views,'delta_ns':aligned['delta_ns'],'time_basis':'receive_time_approximation',
                   'exposure_synchronized':False,'identity_verified':False,'input_quality_status':'not_assessed',
                   'posture_prediction':{'status':'not_implemented','label':None},
                   'target_posture':group.get('measurement_plan',{}).get('goal_posture') if group.get('measurement_plan') else None,
                   'verified_gt':None,'sensor_results':None}
            if group.get('measurement_plan'):result['target_label_source']='operator_instruction_not_ground_truth'
            yield result
        if any(consumed[i]!=manifests[i]['recorded_frames'] or next(streams[i],None) is not None for i in range(2)):
            raise IntegrityError('Fusion input coverage mismatch')


def build_inputs(group,directory):
    if group.get('alignment',{}).get('status')!='receive_time_approximation':
        return {'status':'blocked_alignment_unavailable','posture_classifier':'not_implemented'}
    directory=Path(directory);temp=directory/'fusion_inputs.jsonl.tmp';target=directory/'fusion_inputs.jsonl'
    counts=Counter(candidate_input=0,held=0);reasons=Counter()
    schema=input_schema(group);policy=QUAD_POLICY if schema==QUAD_SCHEMA else POLICY
    try:
        with temp.open('x',encoding='utf-8',newline='\n') as stream:
            for row in iter_inputs(group,directory):
                counts[row['status']]+=1;reasons.update(row['hold_reasons']);stream.write(json_text(row)+'\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,target)
        return {'status':'generated','schema_version':schema,'path':target.name,'sha256':file_hash(target),
                'counts':dict(counts),'hold_reason_counts':dict(reasons),'policy':policy,'policy_sha256':object_hash(policy),
                'posture_classifier':'not_implemented'}
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _inspect_group(directory):
    directory=Path(directory);group=load_json(directory/'manifest.json')
    if group['phase'] not in ('completed','failed') or not group['ledger_complete']:
        raise IntegrityError('Group recording is incomplete')
    result=group.get('fusion_inputs',{})
    if result.get('status')!='generated':
        raise IntegrityError('No generated two-view input artifact')
    legacy=result.get('schema_version')==LEGACY_SCHEMA
    if result.get('schema_version') not in (LEGACY_SCHEMA,SCHEMA,QUAD_SCHEMA):raise IntegrityError('Unsupported fusion schema')
    if not legacy and result['schema_version']!=input_schema(group):raise IntegrityError('Region coordinate schema mismatch')
    policy=LEGACY_POLICY if legacy else QUAD_POLICY if result['schema_version']==QUAD_SCHEMA else POLICY
    if (result['path']!='fusion_inputs.jsonl' or file_hash(directory/result['path'])!=result['sha256']
            or result['policy']!=policy or object_hash(result['policy'])!=result['policy_sha256']):
        raise IntegrityError('Fusion input artifact/policy checksum mismatch')
    counts=Counter(candidate_input=0,held=0);reasons=Counter()
    with (directory/result['path']).open(encoding='utf-8') as stream:
        for expected in iter_inputs(group,directory,legacy=legacy):
            actual=next_json(stream)
            if actual!=expected:raise IntegrityError('Two-view feature/reference mismatch')
            counts[actual['status']]+=1;reasons.update(actual['hold_reasons'])
        if next(stream,None) is not None:raise IntegrityError('Extra fusion rows')
    if dict(counts)!=result['counts'] or dict(reasons)!=result['hold_reason_counts']:
        raise IntegrityError('Fusion counts mismatch')
    if group.get('posture_predictions',{}).get('status')=='generated':
        from .group_posture import verify_predictions
        verify_predictions(group,directory)
    return {'group_id':group['group_id'],'integrity':'verified','input_counts':dict(counts),
            'hold_reason_counts':dict(reasons),'posture_classifier':('experimental_posture_combination' if group.get('posture_predictions',{}).get('status')=='generated' else 'not_implemented'),
            'identity_verified':False,'exposure_synchronized':False}


def inspect_group(directory):
    try:return _inspect_group(directory)
    except IntegrityError:raise
    except Exception as exc:raise IntegrityError('Group verification failed: '+type(exc).__name__) from exc
