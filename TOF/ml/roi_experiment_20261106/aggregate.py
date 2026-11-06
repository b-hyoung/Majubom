"""res/<데이터>/<변형>_<시드>.json·npz를 모아 비교표(JSON)를 만든다."""
import json, sys
from pathlib import Path
import numpy as np
from sklearn.metrics import accuracy_score, f1_score, recall_score

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_roi import cache

MODELS = ['raw_rf', 'raw_xgboost', 'raw_lightgbm', 'cnn', 'cnn_rf', 'cnn_xgboost', 'cnn_lightgbm']
SEEDS = [42, 0, 1, 2, 3]
FOUR = np.array([0, 1, 1, 1, 2, 3])   # empty / 누움 3종 / 앉음 / 걸터앉음


def main():
    out = {}
    for ddir in sorted((HERE / 'res').iterdir()):
        name = ddir.name; D = cache(name); y = D['y']; subj = D['subject']
        people = sorted(s for s in set(subj) if s != '없음')
        variants = sorted({p.stem.rsplit('_', 1)[0] for p in ddir.glob('*.json')})
        out[name] = {}
        for v in variants:
            runs = {s: ddir / f'{v}_{s}' for s in SEEDS if (ddir / f'{v}_{s}.json').exists()}
            if not runs: continue
            res = {'seeds': sorted(runs), 'models': {}}
            info = json.loads(next(iter(runs.values())).with_suffix('.json').read_text())
            res['kept_zones'] = info.get('kept_zones')
            for m in MODELS:
                acc, f1, a4, rec, per = [], [], [], [], {p: [] for p in people}
                paired = []
                for s, base in runs.items():
                    p = np.load(base.with_suffix('.npz'))[m].astype(int)
                    acc.append(accuracy_score(y, p)); f1.append(f1_score(y, p, labels=range(6), average='macro', zero_division=0))
                    a4.append(accuracy_score(FOUR[y], FOUR[p]))
                    rec.append(recall_score(y, p, labels=range(6), average=None, zero_division=0))
                    for q in people:
                        k = subj == q; per[q].append(accuracy_score(y[k], p[k]))
                    nb = ddir / f'none_{s}.npz'
                    if v != 'none' and nb.exists():
                        paired.append(acc[-1] - accuracy_score(y, np.load(nb)[m].astype(int)))
                res['models'][m] = dict(
                    acc_mean=float(np.mean(acc)), acc_sd=float(np.std(acc)), acc_min=float(np.min(acc)), acc_max=float(np.max(acc)),
                    f1_mean=float(np.mean(f1)), f1_sd=float(np.std(f1)), acc4_mean=float(np.mean(a4)), acc4_sd=float(np.std(a4)),
                    recall_mean=np.mean(rec, 0).tolist(), per_subject={q: float(np.mean(v_)) for q, v_ in per.items()},
                    per_subject_sd={q: float(np.std(v_)) for q, v_ in per.items()},
                    paired_diff_vs_none=[float(d) for d in paired],
                    paired_wins=int(sum(d > 0 for d in paired)))
            out[name][v] = res
    (HERE / 'summary.json').write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for name, vs in out.items():
        print('==', name)
        for v, r in vs.items():
            print(f'  {v:10s} seeds={len(r["seeds"])}', ' '.join(f'{m}={r["models"][m]["acc_mean"]*100:.2f}' for m in MODELS))


if __name__ == '__main__':
    main()
