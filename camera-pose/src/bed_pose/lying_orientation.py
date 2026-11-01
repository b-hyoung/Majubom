"""Current-frame lying direction rules after the existing posture/bed gates.

All thresholds are experimental initial settings, not calibrated accuracy bounds.
World z is a monocular model estimate, never bed height or measured gravity.
"""
from .posture_evidence import body_geometry


def _world_orientation(node,cfg):
    if not node or node.get('status')!='available':return None,'world_unavailable'
    f=node['features'];shoulder=f['shoulder_depth_fraction'];hip=f['hip_depth_fraction']
    if (max(f[p]['axial'] for p in ('shoulders','hips'))>cfg['body_axial_max']
            or f['line_angle_degrees'] is None or f['line_angle_degrees']>cfg['body_line_angle_max']
            or abs(shoulder-hip)>cfg['world_depth_difference_max']):
        return None,'world_body_inconsistent'
    if min(abs(shoulder),abs(hip))>=cfg['world_side_depth_min'] and shoulder*hip>0:
        return 'side',None
    if max(abs(shoulder),abs(hip))<=cfg['world_front_depth_max']:return 'supine',None
    return None,'world_orientation_ambiguous'


def classify_lying(obs,cfg):
    p=obs.points
    f=body_geometry([p[i] for i in (5,6,11,12)],2)
    node=obs.world_body
    direction3d,world_reason=_world_orientation(node,cfg)
    e={'version':'body-relative-orientation-0.1','body_2d':f,
       'world_3d':node or {'status':'held','features':None,'reasons':['world_not_provided']},
       'orientation_2d':None,'orientation_3d':direction3d,
       'world_orientation_reason':world_reason,'status':'held','source':'body_2d',
       'quality_status':'not_assessed','bed_height_measured':False}
    def held(reason):return None,reason,e
    # Reject a twisted/crossed torso before interpreting a narrow projection as side.
    ds=(p[5][0]-p[6][0],p[5][1]-p[6][1]);dh=(p[11][0]-p[12][0],p[11][1]-p[12][1])
    if (max(f[n]['axial'] for n in ('shoulders','hips'))>cfg['body_axial_max']
            or f['line_angle_degrees'] is None or f['line_angle_degrees']>cfg['body_line_angle_max']
            or sum(a*b for a,b in zip(ds,dh))<=0):
        return held('body_axis_inconsistent')
    widths=[f[n]['transverse'] for n in ('shoulders','hips')]
    narrow=cfg['side_span_max'];broad=cfg['front_span_min']
    if min(widths)<=narrow and max(widths)>=broad:return held('shoulder_hip_orientation_conflict')
    direction2d='side' if max(widths)<=narrow else 'supine' if min(widths)>=broad else None
    e['orientation_2d']=direction2d
    if direction2d and direction3d and direction2d!=direction3d:
        e['source']='body_2d_world_3d'
        return held('orientation_evidence_conflict')
    label=direction2d
    if direction3d and (label==direction3d or label is None):
        # 3D resolves only the 2D uncertainty band, never a strong opposite cue.
        label=direction3d;e['source']='body_2d_world_3d'
    if label is None:return held('lying_orientation_ambiguous')
    if label=='supine':
        if any(p[i] is None for i in (0,1,2)):return held('front_facing_evidence_missing')
        s=tuple((p[5][i]+p[6][i])/2 for i in (0,1))
        h=tuple((p[11][i]+p[12][i])/2 for i in (0,1))
        down=(h[0]-s[0],h[1]-s[1])
        if ds[0]*down[1]-ds[1]*down[0]<=0:return held('front_back_ambiguous')
    e['status']='predicted'
    return label,None,e
