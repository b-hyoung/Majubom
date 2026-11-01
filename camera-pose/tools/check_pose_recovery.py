"""Opt-in real-GPU negative controls and a dropped-response check; no accuracy score."""
import argparse,hashlib,json,tempfile
from pathlib import Path
from unittest.mock import patch
import http.client

import cv2,numpy as np
from bed_pose.contracts import FramePacket,ModelInput
from bed_pose.io_utils import atomic_json,file_hash
from bed_pose.pose.browser_gpu import BrowserGPUAdapter
from bed_pose.pose.mediapipe import DEFAULT_OPTIONS


def image(path):
    data=Path(path).read_bytes();bgr=cv2.imdecode(np.frombuffer(data,np.uint8),1)
    if bgr is None:raise ValueError('Unreadable image: '+str(path))
    return cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB),hashlib.sha256(data).hexdigest()


class LostResponse:
    """Inference really completes; its first response is lost before the client accepts it."""
    def __init__(self,connection):self.connection=connection
    def request(self,*args,**kw):return self.connection.request(*args,**kw)
    def getresponse(self):
        response=self.connection.getresponse();response.read()
        raise ConnectionAbortedError('injected loss of an already executed GPU response')
    def close(self):self.connection.close()


def check(args):
    output=Path(args.out)
    if output.exists():raise ValueError('Choose a new output directory')
    output.mkdir(parents=True)
    report=dict(accuracy_measured=False,ground_truth_joints_available=False,
                static_image_repetitions=True,negative_controls=[],transport=None)
    with tempfile.TemporaryDirectory() as temp:
        options=Path(temp)/'options.json';atomic_json(options,DEFAULT_OPTIONS)
        model=Path(args.model).resolve();adapter=BrowserGPUAdapter()
        settings=dict(execution_mode='ordered_sequence',requested_device='browser_gpu',
                      assets=[dict(role='pose_landmarker_task',path=str(model),sha256=file_hash(model))],
                      adapter_options_path=str(options),adapter_options_sha256=file_hash(options))
        report['profile']=adapter.open(settings)
        try:
            for source in args.negative_image:
                rgb,sha=image(source);adapter.reset_for_video();detections=0;strategies={}
                for i in range(args.repeats):
                    out=adapter.estimate(ModelInput(FramePacket(str(source),i,rgb,{}),i*40))
                    detections+=bool(out.candidates);strategy=adapter.diagnostics['strategy']
                    strategies[strategy]=strategies.get(strategy,0)+1
                report['negative_controls'].append(dict(image_sha256=sha,repetitions=args.repeats,
                                                        observations_with_a_person=detections,strategies=strategies))
            rgb,sha=image(args.positive_image);adapter.reset_for_video()
            if adapter.inference_connection is None:raise RuntimeError('HTTP transport unavailable for fault injection')
            adapter.inference_connection=LostResponse(adapter.inference_connection)
            errors=adapter.transport_errors
            out=adapter.estimate(ModelInput(FramePacket('positive-control',88,rgb,{}),10))
            report['transport']=dict(image_sha256=sha,pose_returned=bool(out.candidates),
                external_timestamp_ms=out.model_timestamp_ms,source_frame_id=adapter.diagnostics['source_frame_id'],
                replay_uses_fresh_internal_timestamp=adapter.bridge_timestamp>10,
                transport_errors_added=adapter.transport_errors-errors,diagnostics=adapter.diagnostics)
            # Lose two real HTTP responses; the third inference must use the same GPU via stdio.
            adapter.reset_for_video();constructor=http.client.HTTPConnection
            adapter.inference_connection=LostResponse(adapter.inference_connection)
            errors=adapter.transport_errors;fallbacks=adapter.transport_fallbacks
            with patch('bed_pose.pose.browser_gpu.http.client.HTTPConnection',
                       side_effect=lambda *a,**kw:LostResponse(constructor(*a,**kw))):
                out=adapter.estimate(ModelInput(FramePacket('positive-control',89,rgb,{}),20))
            report['fallback_transport']=dict(pose_returned=bool(out.candidates),
                external_timestamp_ms=out.model_timestamp_ms,source_frame_id=adapter.diagnostics['source_frame_id'],
                transport_errors_added=adapter.transport_errors-errors,fallbacks_added=adapter.transport_fallbacks-fallbacks,
                same_gpu_private_pipe_used=adapter.rpc_until>20,diagnostics=adapter.diagnostics)
        finally:adapter.close()
    passed=(all(r['observations_with_a_person']==0 for r in report['negative_controls'])
            and report['transport']['pose_returned'] and report['transport']['transport_errors_added']==1
            and report['transport']['external_timestamp_ms']==10 and report['transport']['source_frame_id']==88
            and report['transport']['replay_uses_fresh_internal_timestamp']
            and report['fallback_transport']['pose_returned'] and report['fallback_transport']['transport_errors_added']==2
            and report['fallback_transport']['fallbacks_added']==1 and report['fallback_transport']['same_gpu_private_pipe_used']
            and report['fallback_transport']['external_timestamp_ms']==20 and report['fallback_transport']['source_frame_id']==89)
    report['passed']=passed
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='profile'},ensure_ascii=False))
    if not passed:raise RuntimeError('GPU recovery smoke check failed; see saved report')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--negative-image',nargs='+',required=True,help='Visually verified empty scenes; no baseline is written')
    parser.add_argument('--positive-image',required=True,help='An image with a visible person, not joint ground truth')
    parser.add_argument('--model',default=str(Path(__file__).resolve().parents[1]/'checkpoints/pose_landmarker_full.task'))
    parser.add_argument('--repeats',type=int,default=160);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    if not 1<=args.repeats<=1000:parser.error('repeats must be 1..1000')
    check(args)
