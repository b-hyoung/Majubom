"""Explicitly prepare/compare RTMPose candidates. No automatic runtime model replacement."""
import argparse,hashlib,io,json,sys,time,urllib.request,zipfile
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
URL='https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/'
ASSETS={
 'lightweight':dict(det='yolox_tiny_8xb8-300e_humanart-6f3252f9.zip',pose='rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip',det_size=[416,416],pose_size=[192,256]),
 'balanced':dict(det='yolox_m_8xb8-300e_humanart-c2c7a14a.zip',pose='rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip',det_size=[640,640],pose_size=[192,256])}


def prepare(directory):
    directory=Path(directory)
    if directory.exists():raise ValueError('Choose a new asset directory; previous models are preserved')
    directory.mkdir(parents=True);manifest=dict(version='rtmpose-comparison-1',rtmlib='0.0.16',models={})
    for mode,spec in ASSETS.items():
        item=dict(det_size=spec['det_size'],pose_size=spec['pose_size'])
        for role in ('det','pose'):
            url=URL+spec[role];print('Downloading official model: '+spec[role],flush=True)
            with urllib.request.urlopen(url,timeout=30) as r:blob=r.read(256*1024**2+1)
            if len(blob)>256*1024**2:raise ValueError('Model archive too large')
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                models=[entry for entry in archive.infolist() if entry.filename.endswith('.onnx')]
                if len(models)!=1 or models[0].file_size>256*1024**2:raise ValueError('Unexpected model archive')
                data=archive.read(models[0])
            name=f'{mode}_{role}.onnx';(directory/name).write_bytes(data)
            item[role]=dict(file=name,sha256=hashlib.sha256(data).hexdigest(),source_url=url)
        manifest['models'][mode]=item
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Prepared: '+str(directory.resolve()),flush=True)


def configure_guard(args):
    assets=Path(args.assets);manifest=json.loads((assets/'manifest.json').read_text(encoding='utf-8'))
    item=manifest['models']['balanced']['det'];model=assets/item['file'];executable=Path(args.python)
    dll=Path(args.cuda_dll_dir) if args.cuda_dll_dir else PROJECT.parent/'.venv-training/Lib/site-packages/torch/lib'
    if not executable.is_file() or not dll.is_dir():raise ValueError('Pose environment and CUDA DLL directory required')
    if hashlib.sha256(model.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Person detector asset changed')
    if not 0<=args.min_area_fraction<1:raise ValueError('Invalid minimum footprint')
    config=PROJECT/'checkpoints/person-guard.json'
    if config.exists():raise ValueError('Person guard configuration already exists; preserve or explicitly edit it')
    config.write_text(json.dumps(dict(python_path=str(executable.resolve()),detector_model=str(model.resolve()),
        sha256=item['sha256'],cuda_dll_dir=str(dll.resolve()),min_area_fraction=args.min_area_fraction),indent=2),encoding='utf-8')
    print('Explicit near-bed GPU person evidence configured. Small/distant people may be held. '+str(config))


def compare(args):
    import cv2,numpy as np,onnxruntime as ort
    from importlib.metadata import version
    if version('rtmlib')!='0.0.16':raise ValueError('Use the pinned pose comparison environment')
    assets=Path(args.assets);manifest=json.loads((assets/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version')!='rtmpose-comparison-1':raise ValueError('Unsupported asset manifest')
    out=Path(args.out)
    if out.exists():raise ValueError('Choose a new output directory to preserve earlier evidence')
    out.mkdir(parents=True)
    if args.device=='cuda':
        dll=Path(args.cuda_dll_dir) if args.cuda_dll_dir else PROJECT.parent/'.venv-training/Lib/site-packages/torch/lib'
        if sys.platform=='win32' and not dll.is_dir():raise ValueError('CUDA DLL directory required; run training setup or pass --cuda-dll-dir')
        ort.preload_dlls(directory=str(dll.resolve()) if dll.is_dir() else None)
    original=ort.InferenceSession;sessions=[]
    def session(*a,**kw):
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.log_severity_level=3
        options.enable_profiling=True;options.profile_file_prefix=str((out/f'execution-{len(sessions)}').resolve());kw['sess_options']=options
        kw['providers']=[('CUDAExecutionProvider',{'cudnn_conv_algo_search':'HEURISTIC','gpu_mem_limit':768*1024**2})] if args.device=='cuda' else ['CPUExecutionProvider']
        result=original(*a,**kw)
        if args.device=='cuda' and result.get_providers()[0]!='CUDAExecutionProvider':raise RuntimeError('CUDA unavailable; CPU fallback is not reported as GPU')
        sessions.append(result);return result
    ort.InferenceSession=session
    rows=[]
    try:
        from rtmlib import Body
        item=manifest['models'][args.mode];files={}
        for role in ('det','pose'):
            name=item[role]['file'];path=assets/name
            if Path(name).name!=name or hashlib.sha256(path.read_bytes()).hexdigest()!=item[role]['sha256']:raise ValueError('Model asset missing or changed')
            files[role]=str(path.resolve())
        body=Body(**files,det_input_size=tuple(item['det_size']),pose_input_size=tuple(item['pose_size']),backend='onnxruntime',device=args.device)
        for source in args.images:
            path=Path(source);data=path.read_bytes();image=cv2.imdecode(np.frombuffer(data,np.uint8),1)
            if image is None:raise ValueError('Unreadable input image: '+path.name)
            for turn in (0,1,3,2) if args.rotations else (0,):
                view=np.ascontiguousarray(np.rot90(image,turn));durations=[]
                for i in range(args.repeats+1):
                    start=time.perf_counter();points,scores=body(view)
                    if i:durations.append((time.perf_counter()-start)*1000)
                counts=[]
                for person,score in zip(points,scores):
                    usable=(score>=.5)&np.isfinite(person).all(axis=1)&(person[:,0]>=0)&(person[:,0]<view.shape[1])&(person[:,1]>=0)&(person[:,1]<view.shape[0])
                    counts.append(dict(usable_joints=int(usable.sum()),torso_joints=int(usable[[5,6,11,12]].sum())))
                row=dict(image=str(path),image_sha256=hashlib.sha256(data).hexdigest(),input_rotation_ccw_degrees=turn*90,
                    coordinate_space='rotated_input_pixels',people=counts,keypoints=points.tolist(),keypoint_scores=scores.tolist(),
                    score_semantics='uncalibrated_simcc_model_score_not_mediapipe_visibility',median_ms=float(np.median(durations)))
                rows.append(row);print(json.dumps({k:v for k,v in row.items() if k not in ('keypoints','keypoint_scores')}),flush=True)
    finally:ort.InferenceSession=original
    execution=[]
    for s in sessions:
        path=s.end_profiling();events=json.loads(Path(path).read_text());counts={}
        for e in events:
            p=e.get('args',{}).get('provider')
            if p:counts[p]=counts.get(p,0)+1
        if args.device=='cuda' and counts and not counts.get('CUDAExecutionProvider'):raise RuntimeError('No CUDA nodes executed')
        execution.append(dict(configured_providers=s.get_providers(),executed_nodes=counts))
    if args.device=='cuda' and not any(e['executed_nodes'].get('CUDAExecutionProvider') for e in execution):raise RuntimeError('No CUDA execution was observed')
    report=dict(model=args.mode,assets=item,device=args.device,execution=execution,rows=rows,
        accuracy_measured=False,ground_truth_available=False,depth_available=False,repeated_static_images=True,
        automatic_runtime_model_switch=False,versions={p:version(p) for p in ('rtmlib','onnxruntime-gpu' if args.device=='cuda' else 'onnxruntime','numpy')})
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('prepare');p.add_argument('--assets',default=str(PROJECT/'checkpoints/pose-candidates'))
    p=commands.add_parser('guard');p.add_argument('--assets',default=str(PROJECT/'checkpoints/pose-candidates'))
    p.add_argument('--python',required=True);p.add_argument('--cuda-dll-dir');p.add_argument('--min-area-fraction',type=float,required=True)
    p=commands.add_parser('compare');p.add_argument('images',nargs='+');p.add_argument('--assets',default=str(PROJECT/'checkpoints/pose-candidates'))
    p.add_argument('--out',required=True);p.add_argument('--mode',choices=ASSETS,default='lightweight');p.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    p.add_argument('--cuda-dll-dir');p.add_argument('--repeats',type=int,default=5);p.add_argument('--rotations',action='store_true')
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.assets)
    elif args.command=='guard':configure_guard(args)
    else:
        if not 1<=args.repeats<=100:parser.error('repeats must be 1..100')
        compare(args)
