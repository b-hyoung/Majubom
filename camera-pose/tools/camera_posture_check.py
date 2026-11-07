"""Capture independent camera predictions or evaluate separately reviewed labels."""
import argparse,hashlib,json,time,urllib.request
from pathlib import Path
from bed_pose.camera_validation import evaluate


def read(path):return [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capture');c.add_argument('--seconds',type=float,default=10);c.add_argument('--out',type=Path,required=True)
    e=sub.add_parser('evaluate');e.add_argument('--predictions',type=Path,required=True);e.add_argument('--reviews',type=Path,required=True);e.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.command=='capture':
        if not 0<a.seconds<=120:raise ValueError('Capture duration must be 0–120 seconds')
        conn=json.loads(Path('outputs/live-connection.json').read_text(encoding='utf-8'));seen=set()
        a.out.parent.mkdir(parents=True,exist_ok=True)
        with a.out.open('x',encoding='utf-8') as f:
            end=time.monotonic()+a.seconds
            while time.monotonic()<end:
                req=urllib.request.Request(conn['origin']+'/api/room/status',headers={'X-BedPose-Token':conn['token']})
                data=json.loads(urllib.request.urlopen(req,timeout=5).read());r=data.get('camera_posture') or {}
                key=r.get('observation_id') or 'missing-input:'+str(time.monotonic_ns())
                if key and key not in seen:
                    seen.add(key);f.write(json.dumps(dict(observation_id=key,timestamp_unix=time.time(),
                        predicted_label=r.get('label'),method=r.get('method'),status=r.get('status'),
                        setup_signature=r.get('setup_signature'),warnings=r.get('warnings',[]),accuracy_verified=False),ensure_ascii=False)+'\n')
                time.sleep(.1)
        print(json.dumps(dict(captured_predictions=len(seen),ground_truth_created=False),ensure_ascii=False))
    else:
        result=evaluate(read(a.predictions),read(a.reviews))
        result.update(predictions_sha256=hashlib.sha256(a.predictions.read_bytes()).hexdigest(),
                      reviews_sha256=hashlib.sha256(a.reviews.read_bytes()).hexdigest())
        a.out.parent.mkdir(parents=True,exist_ok=True)
        with a.out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        if not result['passed']:raise SystemExit(2)


if __name__=='__main__':main()
