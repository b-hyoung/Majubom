"""S01+S02 ToF 모델의 CPU 단일 프레임 쌍 추론."""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import urllib.request
import numpy as np


def stamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=9)))
    return parsed.timestamp()


def predict(latest, bundle, model_name='cnn_rf', require_fresh=False):
    bundle = Path(bundle)
    config = json.loads((bundle/'preprocessing.json').read_text(encoding='utf-8'))
    if config['roi_mask']:
        raise ValueError('이 추론기는 비마스킹 묶음 전용입니다.')
    times = [stamp(latest[sensor]['received_at']) for sensor in ('tof1', 'tof2')]
    if abs(times[0]-times[1]) > .25:
        raise ValueError('두 센서의 수신 시각 차이가 250ms를 넘습니다.')
    if require_fresh:
        now = datetime.now(timezone.utc).timestamp()
        if any(not -.1 <= now-value <= .5 for value in times):
            raise ValueError('센서 프레임이 500ms보다 오래됐거나 미래 시각입니다.')
    arrays = []
    for sensor in ('tof1', 'tof2'):
        values = latest[sensor]['distances_mm']
        if not isinstance(values, list) or len(values) != 64:
            raise ValueError('각 센서에 거리 64개가 필요합니다.')
        arrays.append([float(v) if type(v) in (int, float) and np.isfinite(v) and v > 0 else -1.
                       for v in values])
    raw = np.asarray(arrays, dtype=np.float32)
    import joblib
    if model_name == 'raw_rf':
        model = joblib.load(bundle/'raw_rf.joblib')
        probabilities = model.predict_proba(raw.reshape(1, 128))[0]
    else:
        import torch
        torch.set_num_threads(4)
        baseline = np.array([config['baseline'][sensor] for sensor in ('tof1', 'tof2')], dtype=np.float32)
        valid = np.isfinite(raw) & (raw > 0) & (raw <= 4000)
        base_valid = np.isfinite(baseline) & (baseline > 0) & (baseline <= 4000)
        distance = np.where(valid, np.clip(raw, 0, 4000)/4000, 0)
        delta = np.where(valid & base_valid, np.clip(baseline-raw, -4000, 4000)/4000, 0)
        inputs = np.stack([distance, delta, valid.astype(np.float32)], axis=1).reshape(1, 2, 3, 8, 8)
        cnn = torch.jit.load(str(bundle/'cnn_cpu.pt'), map_location='cpu').eval()
        with torch.inference_mode():
            tensor = torch.from_numpy(inputs)
            if model_name == 'cnn':
                probabilities = torch.softmax(cnn(tensor), dim=1)[0].numpy()
            else:
                features = cnn.features(tensor).numpy()
                model = joblib.load(bundle/(model_name+'.joblib'))
                probabilities = model.predict_proba(features)[0]
    labels = config['labels']
    if len(probabilities) != len(labels) or not np.isfinite(probabilities).all():
        raise ValueError('모델 출력 클래스 또는 점수가 잘못됐습니다.')
    index = int(np.argmax(probabilities))
    return dict(model=model_name, label=labels[index], score=float(probabilities[index]),
                scores=dict(zip(labels, map(float, probabilities))), roi_mask=False,
                pair_skew_s=abs(times[0]-times[1]), execution_device='cpu')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--model', choices=('raw_rf', 'cnn', 'cnn_rf', 'cnn_xgboost', 'cnn_lightgbm'), default='cnn_rf')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path, help='tof1/tof2 객체와 received_at·distances_mm이 있는 JSON')
    source.add_argument('--latest-url', help='예: http://127.0.0.1:5001/tof/latest')
    args = parser.parse_args()
    if args.latest_url:
        with urllib.request.urlopen(args.latest_url, timeout=3) as response:
            latest = json.load(response)
    else:
        latest = json.loads(args.input.read_text(encoding='utf-8-sig'))
    print(json.dumps(predict(latest, args.bundle, args.model, bool(args.latest_url)), ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
