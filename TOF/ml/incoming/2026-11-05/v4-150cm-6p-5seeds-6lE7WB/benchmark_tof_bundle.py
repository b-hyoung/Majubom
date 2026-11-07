"""Pi에서 원시 RF 묶음의 수치 일치와 단일 프레임 추론 시간을 측정한다."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import time

import joblib
import numpy as np


def benchmark(directory):
    model = joblib.load(directory/'raw_rf.joblib')
    model.n_jobs = 1
    assert model.n_features_in_ == 128 and list(model.classes_) == list(range(6))
    frames = np.load(directory/'verification_input.npy')
    expected = json.loads((directory/'verification_probabilities.json').read_text(encoding='utf-8'))
    actual = model.predict_proba(frames)
    assert np.allclose(actual, expected, atol=1e-10, rtol=1e-10)
    for _ in range(10):
        model.predict_proba(frames[:1])
    durations = []
    for i in range(120):
        frame = frames[i%len(frames):i%len(frames)+1]
        started = time.perf_counter_ns()
        model.predict_proba(frame)
        durations.append((time.perf_counter_ns()-started)/1e6)
    device = Path('/proc/device-tree/model').read_bytes().decode().strip('\0')
    assert 'Raspberry Pi 5' in device
    result = dict(device=device,architecture=platform.machine(),python=platform.python_version(),
                  scikit_learn=importlib.metadata.version('scikit-learn'),model='raw_rf',n_jobs=1,
                  repetitions=120,warmup_calls=10,mean_ms=float(np.mean(durations)),
                  median_ms=float(np.median(durations)),p95_ms=float(np.percentile(durations,95)),
                  minimum_ms=float(min(durations)),maximum_ms=float(max(durations)),
                  probability_max_abs_diff=float(np.max(np.abs(actual-expected))),
                  scope='predict_proba for one received sensor pair; excludes network and acquisition')
    (directory/'pi_cpu_benchmark.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    args = parser.parse_args()
    benchmark(args.directory)
