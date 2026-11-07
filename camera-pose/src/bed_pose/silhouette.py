"""Auxiliary silhouette membership only; never a posture or correctness verdict.

Store sparse original mask samples, not full images/masks. Replay can check the
recorded samples and decision, not reconstruct the SDK's original full mask.
"""
import math
import hashlib
import numpy as np

PROFILE={'version':'silhouette-support-0.1','policy':'auxiliary_only_no_posture_changes',
         'source':'mediapipe_pose_segmentation_same_model_not_independent',
         'anchors':['left_shoulder','right_shoulder','left_hip','right_hip','shoulder_mid','hip_mid','torso_mid'],
         'source_indices':[11,12,23,24],'mapped_indices':[5,6,11,12],
         'radius_fraction':.005,'radius_min':1,'radius_max':8,
         'foreground_threshold':.5,'coverage_min':.001,'coverage_max':.98,
         'inside_min':.6,'outside_max':.4,'agreement_min':1.,'conflict_min':.5,
         'storage':'sparse_mask_probabilities_no_full_mask','quality':'not_assessed'}


def number(v):return type(v) in (float,int) and math.isfinite(v)


def anchors(candidate,width,height):
    from .pose.mediapipe import MP33
    blocks=[b for b in candidate.get('pose_blocks',[]) if b.get('block_id')=='image']
    if (len(blocks)!=1 or blocks[0].get('source_joint_names')!=list(MP33)
            or blocks[0].get('joint_definition_id')!='mediapipe33'
            or blocks[0].get('coordinate_space')!='normalized_image'):
        raise ValueError('mask_mapping_mismatch')
    image=candidate['sdk_landmarks']['image']
    if len(image)!=33:raise ValueError('mask_mapping_mismatch')
    points=[]
    for index in PROFILE['source_indices']:
        x,y=image[index]['x'],image[index]['y']
        if not number(x) or not number(y) or not 0<=x<1 or not 0<=y<1:raise ValueError('body_joints_missing')
        points.append((x*width,y*height))
    s=tuple((points[0][i]+points[1][i])/2 for i in (0,1))
    h=tuple((points[2][i]+points[3][i])/2 for i in (0,1))
    if math.dist(s,h)<=0:raise ValueError('degenerate_body_geometry')
    return points+[s,h,tuple((s[i]+h[i])/2 for i in (0,1))]


def pixels(point,width,height):
    radius=max(PROFILE['radius_min'],min(PROFILE['radius_max'],round(min(width,height)*PROFILE['radius_fraction'])))
    x,y=(round(v) for v in point)
    return sorted({(max(0,min(width-1,x+dx)),max(0,min(height-1,y+dy)))
                   for dx in (-radius,0,radius) for dy in (-radius,0,radius)})


def sample_mask(mask,candidate,width,height):
    result={'candidate_id':candidate['candidate_id'],'status':'unavailable','reason':None}
    try:
        a=mask.numpy_view()
        result['native_shape']=list(a.shape)
        if a.shape==(height,width,1):a=a[:,:,0]
        if a.shape!=(height,width) or a.dtype.kind!='f':raise ValueError('mask_geometry_mismatch')
        if not np.isfinite(a).all() or np.any(a<0) or np.any(a>1):raise ValueError('mask_values_invalid')
        coverage=float(np.mean(a>=PROFILE['foreground_threshold']))
        result.update(width=width,height=height,foreground_fraction=coverage,
                      mask_sha256=hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest(),
                      mask_dtype=str(a.dtype))
        if not PROFILE['coverage_min']<=coverage<=PROFILE['coverage_max']:raise ValueError('mask_not_informative')
        points=anchors(candidate,width,height)
        result.update(status='available',samples=[{'anchor':name,'xy':list(point),
            'pixels':[[x,y,float(a[y,x])] for x,y in pixels(point,width,height)]}
            for name,point in zip(PROFILE['anchors'],points)])
    except (ValueError,KeyError,IndexError,TypeError,AttributeError) as exc:
        reason=str(exc) if isinstance(exc,ValueError) else 'mask_read_failed'
        result.update(status='unavailable',reason=reason if reason in (
            'mask_geometry_mismatch','mask_values_invalid','mask_not_informative','mask_mapping_mismatch',
            'body_joints_missing','degenerate_body_geometry') else 'mask_read_failed')
    return result


def capture(result,candidates,model_input):
    """SDK result side channel; an auxiliary failure must not fail pose inference."""
    frame=model_input.frame
    packet={'version':PROFILE['version'],'stream_id':frame.video_id,'stream_frame_id':frame.frame_id,
            'model_timestamp_ms':None if model_input.model_timestamp_ms is None else str(model_input.model_timestamp_ms),
            'width':frame.width,'height':frame.height,'candidates':[]}
    masks=getattr(result,'segmentation_masks',None)
    if masks is None or len(masks)!=len(candidates):
        reason='mask_not_provided' if masks is None else 'mask_candidate_mismatch'
        packet['candidates']=[{'candidate_id':c['candidate_id'],'status':'unavailable','reason':reason} for c in candidates]
    else:
        for mask,candidate in zip(masks,candidates):
            try:packet['candidates'].append(sample_mask(mask,candidate,frame.width,frame.height))
            except Exception:packet['candidates'].append({'candidate_id':candidate['candidate_id'],'status':'unavailable','reason':'mask_read_failed'})
    return packet


def assess(row,width,height):
    a=row.get('posture_assessment') or {}
    result={'version':PROFILE['version'],'stream_frame_id':row['stream_frame_id'],
            'clock_id':row['clock_id'],'received_ns':row['received_ns'],'candidate_id':a.get('candidate_id'),
            'status':'unavailable','reasons':[],'samples':[],'inside_count':None,'outside_count':None,
            'uncertain_count':None,'support_ratio':None,'experimental':True,'quality_status':'not_assessed',
            'independent_evidence':False,'affects_posture':False}
    def unknown(reason):result['reasons']=[reason];return result
    if row['processing_status']!='ok':return unknown('processing_failed')
    if row.get('target_tracking',{}).get('status')=='held':return unknown('target_held')
    if (a.get('stream_frame_id'),a.get('clock_id'),a.get('received_ns'))!=(row['stream_frame_id'],row['clock_id'],row['received_ns']):
        return unknown('mask_frame_mismatch')
    raw=[c for c in row.get('raw_candidates') or [] if c.get('candidate_id')==a.get('candidate_id')]
    if a.get('candidate_id') is None or len(raw)!=1:return unknown('selected_candidate_unavailable')
    packet=row.get('silhouette_observation')
    if packet is None:return unknown('mask_not_provided')
    try:
        if (packet['version'],packet['stream_id'],packet['stream_frame_id'],packet['model_timestamp_ms'],packet['width'],packet['height'])!=(
                PROFILE['version'],row['stream_id'],row['stream_frame_id'],row['model_timestamp_ms'],width,height):
            return unknown('mask_frame_mismatch')
        matches=[c for c in packet['candidates'] if c['candidate_id']==a['candidate_id']]
        if len(matches)!=1:return unknown('mask_candidate_mismatch')
        node=matches[0]
        if node['status']=='unavailable':return unknown(node.get('reason') or 'mask_not_provided')
        if node['status']!='available' or node['width']!=width or node['height']!=height:return unknown('mask_geometry_mismatch')
        if not isinstance(node.get('mask_sha256'),str) or len(node['mask_sha256'])!=64:return unknown('mask_samples_invalid')
        coverage=node['foreground_fraction']
        if not number(coverage) or not PROFILE['coverage_min']<=coverage<=PROFILE['coverage_max']:return unknown('mask_not_informative')
        if len(node['samples'])!=len(PROFILE['anchors']):return unknown('mask_samples_invalid')
        points=anchors(raw[0],width,height)
        mapped=[p for p in row.get('mapped_people') or [] if p.get('candidate_id')==a['candidate_id']]
        if len(mapped)!=1 or len(mapped[0]['joints'])!=17:return unknown('mask_mapping_mismatch')
        for mapped_index,source_index,point in zip(PROFILE['mapped_indices'],PROFILE['source_indices'],points):
            joint=mapped[0]['joints'][mapped_index]
            if joint.get('availability')!='predicted':return unknown('body_joints_missing')
            if (joint.get('source_index')!=source_index or not number(joint.get('x_px')) or not number(joint.get('y_px'))
                    or abs(joint['x_px']-point[0])>1e-6 or abs(joint['y_px']-point[1])>1e-6):
                return unknown('mask_mapping_mismatch')
        counts={'inside':0,'outside':0,'uncertain':0}
        for name,point,sample in zip(PROFILE['anchors'],points,node['samples']):
            expected=pixels(point,width,height)
            if sample['anchor']!=name or sample['xy']!=list(point) or len(sample['pixels'])!=len(expected):return unknown('mask_samples_invalid')
            scores=[]
            for (x,y),saved in zip(expected,sample['pixels']):
                if len(saved)!=3 or type(saved[0]) is not int or type(saved[1]) is not int or saved[:2]!=[x,y] or not number(saved[2]) or not 0<=saved[2]<=1:
                    return unknown('mask_samples_invalid')
                scores.append(saved[2])
            probability=max(scores)
            state='inside' if probability>=PROFILE['inside_min'] else 'outside' if probability<=PROFILE['outside_max'] else 'uncertain'
            counts[state]+=1;result['samples'].append({'anchor':name,'xy':list(point),'probability':probability,'state':state})
        total=len(points);result.update(inside_count=counts['inside'],outside_count=counts['outside'],uncertain_count=counts['uncertain'],support_ratio=counts['inside']/total)
        if counts['inside']/total>=PROFILE['agreement_min']:result['status']='consistent'
        elif counts['outside']/total>=PROFILE['conflict_min']:result['status']='conflict'
        else:return unknown('mask_membership_ambiguous')
        return result
    except (KeyError,TypeError,IndexError,AttributeError,ValueError):return unknown('mask_samples_invalid')
