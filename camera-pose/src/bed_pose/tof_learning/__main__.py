"""python -m bed_pose.tof_learning: collector, review helper, training and GPU checks."""
import argparse,json,math,time
from pathlib import Path
from .data import LABELS,load_rows,write_json


def doctor(args):
    import torch,numpy as np
    from .models import ToFClassifier
    from lightgbm import LGBMClassifier
    from xgboost import XGBClassifier
    report=dict(torch=torch.__version__,cuda_available=torch.cuda.is_available(),cuda_build=torch.version.cuda)
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable on this training environment')
    report.update(gpu=torch.cuda.get_device_name(),capability=torch.cuda.get_device_capability(),arch_list=torch.cuda.get_arch_list())
    for kind in ('cnn','cnn_gru'):
        m=ToFClassifier(kind).cuda();x=torch.rand(4,4,4,8,8,device='cuda');y=m(x);y.sum().backward();torch.cuda.synchronize()
        report[kind+'_cuda_forward_backward']=list(y.shape)
    rng=np.random.default_rng(11);x=rng.normal(size=(120,8));y=np.arange(120)%6
    xgb=XGBClassifier(n_estimators=3,max_depth=2,device='cuda',tree_method='hist');xgb.fit(x,y)
    config=json.loads(xgb.get_booster().save_config());report['xgboost_actual_device']=config['learner']['generic_param']['device']
    if report['xgboost_actual_device']=='cpu':raise RuntimeError('XGBoost silently fell back to CPU')
    try:
        lgb=LGBMClassifier(n_estimators=3,device_type='gpu',verbosity=-1,min_child_samples=2,n_jobs=4);lgb.fit(x,y)
        report['lightgbm_recommended_device']='gpu'
    except Exception as e:
        LGBMClassifier(n_estimators=3,device_type='cpu',verbosity=-1,n_jobs=4).fit(x,y)
        report['lightgbm_recommended_device']='cpu';report['lightgbm_gpu_reason']=str(e)
    report['random_forest_device']='cpu';report['synthetic_smoke_only']=True
    write_json(args.out,report);print(json.dumps(report,ensure_ascii=False,indent=2))


def review_template(args):
    rows=load_rows(args.inputs)
    target=Path(args.out)
    if target.exists():raise ValueError('Review file already exists')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(dict(sample_id=r['sample_id'],session_id=r['session_id'],timestamp_unix=r['timestamp_unix'],
                camera_prediction=r['label'],label=None,confirmed=False,reviewer=None,notes='Inspect independent evidence before confirming'),ensure_ascii=False)+'\n')
    print(f'Review template (not confirmed labels): {target}')


def review_confirm(args):
    from .data import label
    target=Path(args.out)
    if target.exists():raise ValueError('Review output already exists; choose a new file')
    if not args.evidence.strip() or not args.reviewer.strip():raise ValueError('reviewer and evidence required')
    chosen=[r for r in load_rows(args.inputs) if r['session_id']==args.session and args.start<=r['timestamp_unix']<=args.end]
    if not chosen:raise ValueError('No samples in this session/time range')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8') as f:
        for r in chosen:
            f.write(json.dumps(dict(sample_id=r['sample_id'],label=label(args.label),confirmed=True,
                reviewer=args.reviewer,evidence=args.evidence,reviewed_unix=time.time(),
                provenance='operator_visual_review_declaration'),ensure_ascii=False)+'\n')
    print(f'Review declarations saved: {len(chosen)}; software does not certify actual human review')


def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('collect');p.add_argument('--stream-url',required=True);p.add_argument('--camera-url',required=True)
    p.add_argument('--subject',required=True);p.add_argument('--session',required=True);p.add_argument('--out',default='data/paired')
    p.add_argument('--label-policy',choices=['verified','pseudo'],default='verified');p.add_argument('--seconds',type=float,default=0)
    from .collect import collect
    p.set_defaults(func=collect)
    p=sub.add_parser('check');p.add_argument('--pi',default='192.168.0.4');p.add_argument('--camera-url',required=True)
    p.add_argument('--seconds',type=float,default=10);p.add_argument('--temporary-camera-label',choices=[*LABELS,'empty'])
    p.add_argument('--out',required=True)
    from .check import run as check
    p.set_defaults(func=check)
    p=sub.add_parser('review-template');p.add_argument('inputs',nargs='+');p.add_argument('--out',required=True);p.set_defaults(func=review_template)
    p=sub.add_parser('review-confirm');p.add_argument('inputs',nargs='+');p.add_argument('--session',required=True)
    p.add_argument('--start',type=float,required=True);p.add_argument('--end',type=float,required=True)
    p.add_argument('--label',choices=LABELS,required=True);p.add_argument('--reviewer',required=True);p.add_argument('--evidence',required=True);p.add_argument('--out',required=True)
    p.set_defaults(func=review_confirm)
    p=sub.add_parser('train');p.add_argument('inputs',nargs='+');p.add_argument('--reviews',required=True);p.add_argument('--split',required=True)
    p.add_argument('--out',required=True);p.add_argument('--models',default='cnn,cnn_gru');p.add_argument('--window',type=int,default=16);p.add_argument('--stride',type=int,default=4)
    p.add_argument('--max-gap',type=float,default=.3);p.add_argument('--epochs',type=int,default=60);p.add_argument('--patience',type=int,default=10)
    p.add_argument('--batch',type=int,default=128);p.add_argument('--lr',type=float,default=.001);p.add_argument('--trees',type=int,default=300)
    p.add_argument('--seed',type=int,default=42);p.add_argument('--device',choices=['cuda','cpu'],default='cuda');p.add_argument('--no-amp',action='store_true')
    p.add_argument('--lightgbm-device',choices=['cpu','gpu'],default='cpu');p.add_argument('--allow-pseudo-train',action='store_true')
    from .train import run
    p.set_defaults(func=run)
    p=sub.add_parser('doctor');p.add_argument('--out',default='outputs/training-doctor.json');p.set_defaults(func=doctor)
    args=ap.parse_args()
    if getattr(args,'session','') and (Path(args.session).name!=args.session or args.session in ('.','..')):ap.error('session must be a plain identifier')
    if args.command=='train':
        if any(m not in ('cnn','cnn_gru') for m in args.models.split(',')):ap.error('models: cnn,cnn_gru')
        if any(not math.isfinite(v) or v<=0 for v in (args.epochs,args.patience,args.batch,args.trees,args.lr,args.max_gap,args.window,args.stride)):ap.error('finite positive hyperparameters required')
        if len(set(args.models.split(',')))!=len(args.models.split(',')):ap.error('duplicate model names')
    if args.command=='collect' and (not math.isfinite(args.seconds) or args.seconds<0):ap.error('seconds must be finite and nonnegative')
    if args.command=='check' and (not math.isfinite(args.seconds) or not 1<=args.seconds<=120):ap.error('check seconds must be between 1 and 120')
    if args.command=='review-confirm' and (not math.isfinite(args.start) or not math.isfinite(args.end) or args.start>args.end):ap.error('finite ordered review times required')
    args.func(args)


if __name__=='__main__':main()
