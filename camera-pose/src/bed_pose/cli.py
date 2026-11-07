"""Commands for prepared input, explicit adapters and result integrity."""
import argparse
import json
from pathlib import Path
import sys
from jsonschema import ValidationError
from .contracts import IntegrityError, SourceError
from .io_utils import atomic_json, file_hash, load_json
from .pose.fake import FakeOptions
from .recording import verify_records
from .runner import run_benchmark, validate_run
from .video import prepare_input


def _testing_args(parser):
    parser.add_argument('--fake-fail-at', type=int, default=None,
                        help='Intentionally fail at this zero-based frame (synthetic only)')
    parser.add_argument('--fake-no-detection-at', type=int, action='append', default=[])
    parser.add_argument('--fake-people', type=int, default=1)
    parser.add_argument('--fake-missing-joint', type=int, action='append', default=[])


def _options(args):
    return FakeOptions(tuple(args.fake_no_detection_at), args.fake_fail_at,
                       args.fake_people, tuple(args.fake_missing_joint))


def _summary(path, manifest):
    return {'run_directory': str(path), 'execution_kind': manifest['execution_kind'],
            'run_status': manifest['run_status'], 'ledger_complete': manifest['ledger_complete'],
            'counts': manifest['counts'], 'errors': manifest['errors']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepared-video pose benchmark and synthetic pipeline checks.')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='Decode a video and fix its frame/PTS manifest')
    prepare.add_argument('--video', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
    prepare.add_argument('--run-output-root', type=Path, default=None)
    run = commands.add_parser('run', help='Run the configured adapter on prepared video frames')
    run.add_argument('--config', type=Path, required=True)
    _testing_args(run)
    demo = commands.add_parser('demo', help='Generate a nonhuman test video and run the synthetic adapter')
    demo.add_argument('--output', type=Path, required=True)
    _testing_args(demo)
    inspect = commands.add_parser('inspect', help='Verify result hashes, frame identities and record references')
    inspect.add_argument('--run', type=Path, required=True)
    configure = commands.add_parser('configure-mediapipe', help='Create a hashed MediaPipe config; does not download models')
    configure.add_argument('--config', type=Path, required=True)
    configure.add_argument('--model', type=Path, required=True)
    configure.add_argument('--output', type=Path, required=True)
    configure.add_argument('--num-poses', type=int, default=1)
    configure.add_argument('--execution-mode', choices=('independent_frames', 'ordered_sequence'), default='independent_frames')
    evaluate = commands.add_parser('evaluate', help='Evaluate verified records; no GT-based accuracy yet')
    evaluate.add_argument('--run', type=Path, required=True)
    evaluate.add_argument('--scope', choices=('full', 'diagnostic_partial'), default='full')
    compare = commands.add_parser('compare', help='Report all requested runs, including missing or failed runs')
    compare.add_argument('--runs', type=Path, nargs='+', required=True)
    compare.add_argument('--output', type=Path, required=True)
    compare.add_argument('--scope', choices=('full', 'diagnostic_partial'), default='full')
    render = commands.add_parser('render', help='Render verified frame overlays to SVG images and an HTML index')
    render.add_argument('--run', type=Path, required=True)
    render.add_argument('--video', type=Path, required=True)
    render.add_argument('--output', type=Path, required=True)
    render.add_argument('--max-frames', type=int, default=None, help='Render only the first N frames; inference records remain unchanged')
    live = commands.add_parser('live', help='Browser workspace for cameras, videos and measurements')
    live_source = live.add_mutually_exclusive_group()
    live.add_argument('--model', type=Path)
    live.add_argument('--pose-device', choices=('cpu','browser_gpu'), default='cpu', help='Experimental Windows browser GPU MediaPipe backend')
    live.add_argument('--pose-max-people',type=int,choices=(1,2,3),default=3,help='Use 1 for a single-target GPU tracking session; default retains 3 candidates')
    live_source.add_argument('--demo', action='store_true', help='Explicit synthetic input; no camera or real inference')
    live_source.add_argument('--camera', type=int)
    live_source.add_argument('--video', type=Path)
    live.add_argument('--output-root', type=Path, default=Path('recordings/live'))
    live.add_argument('--clip-model',type=Path,help='Prepared CLIP folder; defaults to checkpoints/clip-vit-base-patch16')
    live.add_argument('--clip-device',choices=('auto','cpu','cuda','mps'),default='auto',help='auto: CUDA, then Apple MPS when available, otherwise CPU')
    live.add_argument('--clip-precision',choices=('fp32','amp-fp16'),default='fp32',help='Optional CUDA mixed precision; scores stay float32')
    live.add_argument('--no-clip-share',action='store_true',help='Use separate CLIP models for comparison')
    live.add_argument('--clip-batch-size',type=int,choices=range(1,5),default=1,help='Optional shared runtime batching, 1 disables batching')
    live.add_argument('--clip-batch-wait-ms',type=float,default=2.,help='Maximum ready-request batch collection wait, 0..10 ms')
    live.add_argument('--clip-state-min',type=float,default=.5,help='Unvalidated initial relative state score threshold')
    live.add_argument('--clip-direction-min',type=float,default=.5,help='Unvalidated initial relative lying-direction threshold')
    clip_bench=commands.add_parser('clip-benchmark',help='Compare FP32 and candidate on identical local images; not posture accuracy')
    clip_bench.add_argument('--model',type=Path,default=Path('checkpoints/clip-vit-base-patch16'))
    clip_bench.add_argument('--images',type=Path,nargs='+',required=True)
    clip_bench.add_argument('--device',choices=('auto','cpu','cuda','mps'),default='auto')
    clip_bench.add_argument('--precision',choices=('fp32','amp-fp16'),default='fp32')
    clip_bench.add_argument('--batch-size',type=int,choices=range(1,5),default=1)
    clip_bench.add_argument('--iterations',type=int,default=20)
    clip_bench.add_argument('--output',type=Path,required=True)
    person_setup=commands.add_parser('person-setup',help='Prepare optional YOLOS-tiny person crop detector')
    person_setup.add_argument('--output',type=Path,default=Path('checkpoints/person-yolos-tiny'))
    person_setup.add_argument('--source',type=Path,help='Offline YOLOS-tiny folder instead of download')
    clip_setup=commands.add_parser('clip-setup',help='Explicitly download or import CLIP ViT-B/16 and record asset hashes')
    clip_setup.add_argument('--output',type=Path,default=Path('checkpoints/clip-vit-base-patch16'))
    clip_setup.add_argument('--source',type=Path,help='Offline Hugging Face-format folder instead of downloading')
    clip_setup.add_argument('--revision',default='main',help='HF revision; resolved to an immutable commit before downloading')
    live_inspect = commands.add_parser('live-inspect', help='Verify the separate live measurement record contract')
    live_inspect.add_argument('--run', type=Path, required=True)
    evidence=commands.add_parser('posture-evidence',help='Export same-frame body 2D and estimated 3D features; not accuracy')
    evidence.add_argument('--run',type=Path,required=True)
    evidence.add_argument('--output',type=Path,required=True)
    posture_eval=commands.add_parser('posture-evaluate',help='Evaluate saved posture predictions against separate reviewed annotations')
    posture_eval.add_argument('--run',type=Path,required=True)
    posture_eval.add_argument('--annotations',type=Path)
    posture_eval.add_argument('--output',type=Path,required=True)
    group_inspect=commands.add_parser('group-inspect',help='Verify original records, alignment and two-view input features')
    group_inspect.add_argument('--run',type=Path,required=True)
    stereo=commands.add_parser('stereo-evidence', help='Attach calibrated stereo joint estimates to existing group posture results')
    stereo.add_argument('--run',type=Path,required=True)
    stereo.add_argument('--calibration',type=Path,required=True)
    stereo.add_argument('--output',type=Path,required=True)
    stereo.add_argument('--depth-check',action='store_true',help='Require agreement with existing 3D geometry rules')
    args = parser.parse_args(argv)
    try:
        if args.command=='stereo-evidence':
            from .stereo_depth import export
            print(json.dumps(export(args.run,args.calibration,args.output,depth_check=args.depth_check),ensure_ascii=False))
            return 0
        if args.command=='person-setup':
            from .person_detector import setup
            print(json.dumps(setup(args.output,args.source),ensure_ascii=False))
            return 0
        if args.command=='clip-setup':
            from .clip_assets import setup_assets
            path,assets=setup_assets(args.output,source=args.source,revision=args.revision)
            print(json.dumps({'model_directory':str(path),'model_id':assets['model_id'],'revision':assets['revision'],
                              'weights_sha256':assets['files']['pytorch_model.bin'],'accuracy_validated':False},ensure_ascii=False))
            return 0
        if args.command == 'posture-evaluate':
            from .posture_accuracy import evaluate as evaluate_posture
            path,report=evaluate_posture(args.run,args.output,args.annotations)
            print(json.dumps({'report_directory':str(path),'evaluation_status':report['evaluation_status'],
                              'human_accuracy_validated':report['human_accuracy_validated']},ensure_ascii=False))
            return 0
        if args.command == 'posture-evidence':
            from .evidence_report import export_evidence
            path,report=export_evidence(args.run,args.output)
            print(json.dumps({'report_directory':str(path),'frame_count':report['frame_count'],
                              'evaluation_status':report['evaluation_status']},ensure_ascii=False))
            return 0
        if args.command == 'group-inspect':
            from .fusion_inputs import inspect_group
            print(json.dumps(inspect_group(args.run),ensure_ascii=False))
            return 0
        if args.command == 'clip-benchmark':
            from .clip_benchmark import run
            if args.output.exists():raise ValueError('Choose a new benchmark output path')
            report=run(args.model,args.images,device=args.device,precision=args.precision,
                       batch_size=args.batch_size,iterations=args.iterations)
            atomic_json(args.output,report)
            print(json.dumps(report,ensure_ascii=False))
            return 0
        if args.command == 'live':
            if args.camera is not None and args.camera < 0:
                raise ValueError('카메라 번호를 확인하세요. 합성 모드는 카메라 번호를 사용하지 않습니다.')
            from .live_gui import launch_live
            return launch_live(args.model, args.camera, args.output_root, args.demo, args.video,
                               clip_model=args.clip_model,clip_device=args.clip_device,
                               clip_state_min=args.clip_state_min,clip_direction_min=args.clip_direction_min,
                               clip_precision=args.clip_precision,clip_shared=not args.no_clip_share,
                               clip_batch_size=args.clip_batch_size,clip_batch_wait_ms=args.clip_batch_wait_ms,
                               pose_device=args.pose_device,pose_max_people=args.pose_max_people)
        if args.command == 'live-inspect':
            from .live_recording import verify_live
            print(json.dumps(verify_live(args.run), ensure_ascii=False))
            return 0
        if args.command in ('evaluate', 'compare'):
            from .evaluation import evaluate_run, compare_runs
            path, report = (evaluate_run(args.run, args.scope) if args.command == 'evaluate' else
                            compare_runs(args.runs, args.output, args.scope))
            print(json.dumps({'report_directory': str(path), 'integrity': report['integrity'],
                              'evaluation_status': report['evaluation_status']}, ensure_ascii=False))
            return 0 if report['evaluation_status'] == 'completed' else 4
        if args.command == 'render':
            from .rendering import render_overlay
            path, report = render_overlay(args.video, args.run, args.output, max_frames=args.max_frames)
            print(json.dumps({'render_directory': str(path), 'frame_count': report['frame_count'],
                              'execution_kind': report['execution_kind']}, ensure_ascii=False))
            return 0
        if args.command == 'configure-mediapipe':
            from .pose.mediapipe import prepare_configuration
            path = prepare_configuration(args.config, args.model, args.output,
                        execution_mode=args.execution_mode, num_poses=args.num_poses)
            print(json.dumps({'config': str(path)}, ensure_ascii=False))
            return 0
        if args.command == 'prepare':
            path = prepare_input(args.video, args.output, args.run_output_root)
            print(json.dumps({'input_manifest': str(path), 'sha256': file_hash(path),
                              'fake_config': str(path.parent / 'run_config.fake.json')}, ensure_ascii=False))
            return 0
        if args.command == 'inspect':
            manifest = load_json(args.run / 'manifest.json')
            if not manifest['ledger_complete']:
                raise IntegrityError('Run ledger is incomplete; inspect manifest errors before using any results')
            counts = verify_records(args.run, manifest)
            if counts != manifest['counts']:
                raise IntegrityError('Persisted counters differ from the manifest')
            print(json.dumps(dict(_summary(args.run, manifest), integrity='verified'), ensure_ascii=False))
            return 0
        if args.command == 'demo':
            from .demo import create_demo_video
            args.output = args.output.resolve()
            args.output.mkdir(parents=True, exist_ok=False)
            video = create_demo_video(args.output / 'synthetic_input.mkv')
            prepared = prepare_input(video, args.output / 'input', args.output / 'runs')
            config_path = prepared.parent / 'run_config.fake.json'
        else:
            config_path = args.config
        validated = validate_run(config_path, _options(args))
        path, manifest = run_benchmark(validated)
        print(json.dumps(_summary(path, manifest), ensure_ascii=False))
        if not manifest['ledger_complete']:
            return 4
        return {'succeeded': 0, 'failed': 3, 'cancelled': 130}[manifest['run_status']]
    except IntegrityError as exc:
        print(f'Integrity error: {exc}', file=sys.stderr)
        return 4
    except (SourceError, ValueError, OSError, ValidationError, KeyError, TypeError) as exc:
        print(f'Input/configuration error: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'Execution error ({type(exc).__name__}): {exc}', file=sys.stderr)
        return 3
