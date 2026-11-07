"""Current-frame pose recovery proposals; never fill hidden joints from history."""
from copy import deepcopy
import math
from .mediapipe import COCO_INDICES
from .rotation_rescue import restore_result

POLICY={'version':'pose-recovery-0.1','visibility_min':.5,'healthy_visible_joints':14,
        'probe_interval_ms':500,'crop_full_frame_recheck_ms':1000,'history_roi_max_age_ms':1000,
        'max_extra_inferences_per_probe':1,'validation':'experimental_not_accuracy_calibrated'}
POLICY.update(crop_requires_current_torso=True,cropped_primary='stateless_image',
              crop_expires_without_full_frame_support=True,video_acquisition_requires_image_confirmation=True,
              independent_full_frame_recheck_ms=1000,extra_inference_budget_scope='mediapipe_only',
              optional_person_detector_max_views=4,optional_person_detector_interval_ms=500)


def quality(result):
    best=dict(visible=0,body=0,torso=0,healthy=False,box=None,center=None)
    for person in result.get('landmarks',[]):
        if len(person)!=33:continue
        good=[]
        for i,native in enumerate(COCO_INDICES):
            p=person[native];x,y,v=p.get('x'),p.get('y'),p.get('visibility');presence=p.get('presence')
            if (all(type(n) in (int,float) and math.isfinite(n) for n in (x,y,v))
                    and 0<=x<1 and 0<=y<1 and v>=.5
                    and (presence is None or type(presence) in (int,float) and math.isfinite(presence) and presence>=.5)):
                good.append((i,x,y))
        ids={p[0] for p in good};torso=len(ids&{5,6,11,12});body=len(ids&set(range(5,17)))
        q=dict(visible=len(good),body=body,torso=torso,healthy=len(good)>=14 and torso==4,box=None,center=None)
        if good:
            xs=[p[1] for p in good];ys=[p[2] for p in good]
            q['box']=[min(xs),min(ys),max(xs),max(ys)]
            anchors=[p for p in good if p[0] in (5,6,11,12)]
            if anchors:q['center']=[sum(p[k] for p in anchors)/len(anchors) for k in (1,2)]
        if (q['torso'],q['body'],q['visible'])>(best['torso'],best['body'],best['visible']):best=q
    return best


def improves(candidate,baseline):
    if candidate['visible']<6 or candidate['torso']<3:return False
    # Retrying a crop must not switch to a distant, better-scoring bystander.
    if baseline['torso']>=3 and baseline['center'] and candidate['center'] and math.dist(baseline['center'],candidate['center'])>.2:return False
    return (candidate['torso']>baseline['torso'] or candidate['body']>=baseline['body']+2
            or candidate['torso']>=baseline['torso'] and candidate['visible']>=baseline['visible']+2)


def padded_box(bounds,width,height,padding=.2):
    if not isinstance(bounds,(tuple,list)) or len(bounds)!=4 or not all(type(x) in (int,float) and math.isfinite(x) for x in bounds):return None
    x0,y0,x1,y1=bounds
    if x1<=x0 or y1<=y0:return None
    dx=(x1-x0)*padding;dy=(y1-y0)*padding
    box=(max(0,int(x0-dx)),max(0,int(y0-dy)),min(width,math.ceil(x1+dx)),min(height,math.ceil(y1+dy)))
    if min(box[2]-box[0],box[3]-box[1])<64:return None
    area=(box[2]-box[0])*(box[3]-box[1])/(width*height)
    return box if .2<=area<.98 else None


def proposals(width,height,turns,hint=None,recent=None,active=None,allow_crops=True):
    full=(0,0,width,height)
    result=[(t,None,'rotation') for t in (0,1,3,2) if (t,None)!=(turns,active)]
    if not allow_crops:return result
    boxes=[]
    for name,bounds,padding in [('recent',recent,.25),('bed_hint',hint,.2),
                              ('upper',(0,0,width,height*.8),0),('lower',(0,height*.2,width,height),0),
                              ('center',(width*.1,height*.1,width*.9,height*.9),0)]:
        box=padded_box(bounds,width,height,padding)
        if box is None or box==full or box in [b for _,b in boxes]:continue
        boxes.append((name,box))
    for name,box in boxes:
        for t in (turns,(turns+1)%4,(turns+3)%4,(turns+2)%4):
            if (t,box)!=(turns,active):result.append((t,box,name))
    return result


def restore(result,turns,crop,width,height):
    bounds=crop or (0,0,width,height);x0,y0,x1,y1=bounds
    result=restore_result(result,turns,x1-x0,y1-y0)
    if crop is None:return result
    result=deepcopy(result)
    for person in result.get('landmarks',[]):
        for p in person:
            p['x']=(x0+p['x']*(x1-x0))/width;p['y']=(y0+p['y']*(y1-y0))/height
            if p.get('z') is not None:p['z']*=(x1-x0)/width
    return result
