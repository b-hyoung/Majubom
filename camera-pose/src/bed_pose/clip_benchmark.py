"""Same-image FP32/candidate comparison, not a ground-truth accuracy test."""
from copy import deepcopy
import math
from pathlib import Path
import platform
import statistics
import time

from .clip_assets import read_assets
from .clip_posture import ClipRuntime, policy, decide, resolve_device, square_image
from .io_utils import file_hash


def run(model, images, *, device='auto', precision='fp32', batch_size=1, iterations=20):
    from PIL import Image
    import numpy as np
    from importlib.metadata import version
    from .clip_pool import validate_options
    validate_options(batch_size,0)
    if type(iterations) is not int or not 1<=iterations<=1000:raise ValueError('Iterations must be in [1,1000]')
    device=resolve_device(device);policy(device=device,precision=precision)
    assets=read_assets(model,verify=True)
    paths=[Path(p).resolve() for p in images]
    if not paths or len(paths)>256:raise ValueError('Provide 1..256 images')
    hashes=[file_hash(path) for path in paths]
    prepared=[]
    for path in paths:
        with Image.open(path) as image:prepared.append(square_image(np.array(image.convert('RGB'))))
    reports=[];observations=[]
    for mode,size in (('fp32',1),(precision,batch_size)):
        runtime=ClipRuntime(model,device) if mode=='fp32' else ClipRuntime(model,device,mode)
        cfg=dict(policy(device=device,precision=mode),assets_manifest=deepcopy(assets),
                 runtime_versions={k:version(k) for k in ('torch','transformers','Pillow')})
        try:
            for _ in range(3):runtime.similarities_many(prepared[:size])
            times=[];metrics=[];values=[]
            for repeat in range(iterations):
                current=[];started=time.perf_counter_ns()
                for offset in range(0,len(prepared),size):
                    current.extend(runtime.similarities_many(prepared[offset:offset+size]))
                    metrics.append(deepcopy(runtime.last_metrics))
                times.append((time.perf_counter_ns()-started)/1e6)
                # Compare every repeated observation; nondeterminism is not hidden.
                values.append([(v,scale,decide(v,scale,cfg)) for v,scale in current])
            ordered=sorted(times)
            reports.append({'precision':mode,'batch_size':size,'images_per_iteration':len(prepared),
                'mean_iteration_ms':statistics.mean(times),'p95_iteration_ms':ordered[max(0,math.ceil(.95*len(ordered))-1)],
                'images_per_second':len(prepared)*1000/statistics.mean(times),
                'runtime':deepcopy(runtime.execution_info),'last_metrics':metrics[-1]})
            observations.append(values)
        finally:runtime.close()
    max_delta=0.;changed=0;comparisons=0
    for baseline,candidate in zip(*observations):
        for (a,_,ad),(b,_,bd) in zip(baseline,candidate):
            comparisons+=1;max_delta=max(max_delta,max(abs(a[k]-b[k]) for k in a))
            changed+= (ad['state'],ad['lying_direction'])!=(bd['state'],bd['lying_direction'])
    if [file_hash(path) for path in paths]!=hashes:raise ValueError('Benchmark input changed during execution')
    return {'version':'clip-runtime-comparison-0.1','model_assets':assets,'device':device,
        'python':platform.python_version(),'platform':platform.platform(),
        'inputs':[{'sha256':digest} for digest in hashes],
        'input_policy':'provided_rgb_images_square_pad_224_no_person_detection',
        'warmup_iterations':3,'iterations':iterations,'baseline':reports[0],'candidate':reports[1],
        'comparison_count':comparisons,'changed_state_or_direction_count':changed,'max_cosine_absolute_delta':max_delta,
        'accuracy_validated':False,'scope':'image_runtime_only_excludes_camera_pose_recording_browser',
        'memory_scope':'process_allocator_samples_not_isolated_model_peak'}
