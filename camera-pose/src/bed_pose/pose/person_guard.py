"""Optional, explicit near-bed person evidence. No downloads or hidden CPU fallback."""
import base64,json,math,queue,subprocess,sys,threading
from pathlib import Path

import numpy as np


def supports_pose(boxes,q,width,height):
    if not q['center'] or not q['box']:return False
    cx,cy=q['center'][0]*width,q['center'][1]*height
    a,b,c,d=q['box'];a*=width;c*=width;b*=height;d*=height
    area=max(1,(c-a)*(d-b))
    return any(x0<=cx<=x1 and y0<=cy<=y1 and
               max(0,min(c,x1)-max(a,x0))*max(0,min(d,y1)-max(b,y0))/area>=.5
               for x0,y0,x1,y1 in boxes)


class PersonGuard:
    def __init__(self,config,*,max_people=1):
        self.process=None;self.cache_stamp=None;self.boxes=[];self.diagnostics={};self.sequence=0
        self.max_people=max_people;self.views_used=0
        values=json.loads(Path(config).read_text(encoding='utf-8'))
        executable=Path(values['python_path'])
        if not executable.is_absolute() or not executable.is_file():raise ValueError('Person guard Python is unavailable')
        self.replies=queue.Queue()
        self.process=subprocess.Popen([str(executable),str(Path(__file__).resolve()),'--worker',str(Path(config).resolve())],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        threading.Thread(target=self._read,daemon=True).start()
        try:self.profile=self._response(timeout=30)['profile']
        except Exception:self.close();raise

    def _read(self):
        for line in self.process.stdout:self.replies.put(line)
        self.replies.put(None)

    def _response(self,timeout=10):
        try:line=self.replies.get(timeout=timeout)
        except queue.Empty as exc:raise RuntimeError('Person guard GPU response timed out') from exc
        if line is None:raise RuntimeError('Person guard GPU process exited')
        value=json.loads(line)
        if not value.get('ok'):raise RuntimeError(value.get('error','Person guard failed'))
        return value

    def confirm(self,rgb,stamp,turns,q):
        h,w=rgb.shape[:2]
        if self.cache_stamp is None or stamp-self.cache_stamp>=500:
            self.sequence+=1
            request=dict(id=self.sequence,width=w,height=h,turns=turns,rgb=base64.b64encode(rgb.tobytes()).decode('ascii'),
                         pose_quality=q if self.max_people==1 else None)
            self.process.stdin.write(json.dumps(request,separators=(',',':'))+'\n');self.process.stdin.flush()
            value=self._response()
            if value['id']!=self.sequence:raise RuntimeError('Person guard response sequence mismatch')
            self.boxes=value['boxes'];self.cache_stamp=stamp;self.views_used=value['views_used']
        accepted=supports_pose(self.boxes,q,w,h)
        self.diagnostics=dict(confirmed=accepted,reason=None if accepted else 'person_not_confirmed_at_configured_scale',
                              detector_age_ms=stamp-self.cache_stamp,boxes=len(self.boxes),views_used=self.views_used,accuracy_verified=False)
        if q.get('box') and q.get('center'):
            a,b,c,d=q['box'];a*=w;c*=w;b*=h;d*=h
            area=max(1,(c-a)*(d-b));cx,cy=q['center'][0]*w,q['center'][1]*h
            self.diagnostics.update(pose_box=q['box'],pose_center=q['center'],
                center_in_detector_box=any(x0<=cx<=x1 and y0<=cy<=y1 for x0,y0,x1,y1 in self.boxes),
                maximum_pose_box_coverage=max((max(0,min(c,x1)-max(a,x0))*max(0,min(d,y1)-max(b,y0))/area
                                              for x0,y0,x1,y1 in self.boxes),default=0.))
        return accepted

    def reset(self):self.cache_stamp=None;self.boxes=[];self.diagnostics={};self.views_used=0

    def close(self):
        if self.process is None:return
        process=self.process
        try:process.stdin.close();process.wait(timeout=5)
        except (OSError,subprocess.TimeoutExpired):process.kill();process.wait(timeout=5)
        finally:
            process.stdout.close();self.process=None


def person_boxes(detector,bgr,turns,minimum,pose_quality=None):
    """Keep the opposite direction when needed; a supported single target is conclusive for this OR gate."""
    h,w=bgr.shape[:2];boxes=[];views_used=0
    directions=(turns%4,(turns+2)%4)
    if pose_quality is not None:directions+=((turns+1)%4,(turns+3)%4)
    for turn in directions:
        views_used+=1
        detections=detector(np.ascontiguousarray(np.rot90(bgr,turn)))
        for x0,y0,x1,y1 in detections:
            corners=np.array([[x0,y0],[x0,y1],[x1,y0],[x1,y1]],float)
            x,y=corners[:,0].copy(),corners[:,1].copy()
            if turn==1:corners[:,0]=w-y;corners[:,1]=x
            elif turn==2:corners[:,0]=w-x;corners[:,1]=h-y
            elif turn==3:corners[:,0]=y;corners[:,1]=h-x
            low=np.maximum(corners.min(axis=0),[0,0]);high=np.minimum(corners.max(axis=0),[w,h])
            if np.prod(np.maximum(0,high-low))/(w*h)>=minimum:boxes.append([*low.tolist(),*high.tolist()])
        if pose_quality is not None and supports_pose(boxes,pose_quality,w,h):break
    return boxes,views_used


def worker(config):
    import contextlib,hashlib,tempfile
    from importlib.metadata import version
    import cv2,onnxruntime as ort
    values=json.loads(Path(config).read_text(encoding='utf-8'))
    minimum=values['min_area_fraction'];model=Path(values['detector_model'])
    if type(minimum) not in (int,float) or not math.isfinite(minimum) or not 0<=minimum<1:raise ValueError('Invalid minimum person footprint')
    if hashlib.sha256(model.read_bytes()).hexdigest()!=values['sha256']:raise ValueError('Person detector asset changed')
    if version('rtmlib')!='0.0.16' or ort.__version__!='1.23.2':raise ValueError('Use the pinned pose comparison environment')
    ort.preload_dlls(directory=values['cuda_dll_dir'])
    constructor=ort.InferenceSession
    with tempfile.TemporaryDirectory() as temp:
        def session(*a,**kw):
            options=ort.SessionOptions();options.intra_op_num_threads=2;options.log_severity_level=3
            options.enable_profiling=True;options.profile_file_prefix=str(Path(temp)/'cuda')
            kw.update(sess_options=options,providers=[('CUDAExecutionProvider',{'cudnn_conv_algo_search':'HEURISTIC','gpu_mem_limit':512*1024**2})])
            result=constructor(*a,**kw)
            if result.get_providers()[0]!='CUDAExecutionProvider':raise RuntimeError('Person guard requires CUDA; CPU fallback rejected')
            return result
        ort.InferenceSession=session
        with contextlib.redirect_stdout(sys.stderr):
            from rtmlib import YOLOX
            detector=YOLOX(onnx_model=str(model),model_input_size=(640,640),backend='onnxruntime',device='cuda')
            detector(np.zeros((480,640,3),np.uint8))
        ort.InferenceSession=constructor
        events=json.loads(Path(detector.session.end_profiling()).read_text());providers={}
        for event in events:
            provider=event.get('args',{}).get('provider')
            if provider:providers[provider]=providers.get(provider,0)+1
        if not providers.get('CUDAExecutionProvider'):raise RuntimeError('Person detector executed no CUDA nodes')
    profile=dict(model='YOLOX-m HumanArt person detector',actual_device='cuda',sha256=values['sha256'],
                 executed_nodes=providers,min_area_fraction=minimum,scope='configured_near_bed_not_general_person_detection',
                 accuracy_verified=False)
    print(json.dumps(dict(ok=True,profile=profile)),flush=True)
    for line in sys.stdin:
        request=json.loads(line);w,h=request['width'],request['height']
        if type(w) is not int or type(h) is not int or min(w,h)<1 or w*h>4096*2160:raise ValueError('Invalid frame geometry')
        rgb=np.frombuffer(base64.b64decode(request['rgb'],validate=True),np.uint8).reshape(h,w,3)
        bgr=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
        boxes,views_used=person_boxes(detector,bgr,request['turns'],minimum,request.get('pose_quality'))
        print(json.dumps(dict(ok=True,id=request['id'],boxes=boxes,views_used=views_used)),flush=True)


if __name__=='__main__':
    try:worker(sys.argv[2])
    except Exception as exc:
        print(json.dumps(dict(ok=False,error=str(exc))),flush=True);sys.exit(1)
