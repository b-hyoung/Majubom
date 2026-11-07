"""ROI 마스킹 비교: 노트북 session_train.py와 같은 7개 모델·사람 단위 교차검증을 마스크만 바꿔 돌린다.

사용: python run_roi.py <데이터이름> <변형> <시드> <출력json>
데이터: v2 | v3 | v4 (DATASETS), 변형: VARIANTS 참고.
"""
import json, sys, time, random, importlib.util
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tl.session_data import load_dataset, outer_folds, validation_split, channels, LABELS

REPO = HERE.parents[2]   # 저장소 루트
SCR = HERE.parent
DATASETS = {
    'v2': (REPO / 'TOF/dataset_v2', HERE / 'baseline_v2.json'),
    'v3': (SCR / 'ds3p', REPO / 'TOF/ml/posture_rf_bedC_20261104_3p/training_baseline.json'),
    'v4': (REPO / 'TOF/dataset_v4', REPO / 'TOF/ml/incoming/2026-11-05/v4-150cm-6p-5seeds-6lE7WB/training_baseline.json'),
}
THREADS = 3
EPOCHS, PATIENCE, BATCH, TREES = 60, 10, 128, 200


def cache(name):
    path = HERE / f'cache_{name}.npz'
    if not path.exists():
        d, b = DATASETS[name]
        s, base, _ = load_dataset(d, b)
        np.savez_compressed(path, raw=np.stack([a['raw'] for a in s]), y=np.array([a['y'] for a in s]),
                            subject=np.array([a['subject'] for a in s]), rep=np.array([a['rep'] for a in s]),
                            session=np.array([a['session'] for a in s]), cond=np.array([a['cond'] for a in s]),
                            baseline=base)
    z = np.load(path)
    return {k: z[k] for k in z.files}


def load_roi_module():
    spec = importlib.util.spec_from_file_location('roi_old', REPO / 'TOF/ml/roi.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def roi_py_keep(raw):
    """기존 roi.py(예전 설치 기하) 프레임별 마스크. raw (N,2,64) → keep (N,2,64)."""
    m = load_roi_module()
    keep = np.ones(raw.shape, bool)
    for i, sensor in enumerate(('tof1', 'tof2')):
        # 같은 (칸, 거리)는 결과가 같으므로 고유값만 계산
        for z in range(64):
            col = raw[:, i, z]
            vals = np.unique(col)
            ok = {v: m.on_bed(sensor, z, int(v) if v > 0 else -1) for v in vals}
            keep[:, i, z] = np.array([ok[v] for v in col])
    return keep


def geo_fit_keep(margin):
    """v3 빈 침대 기준으로 역산한 기하(바닥 1.18/1.17m, 하향 43.7/40.3°, 침대 2.0×0.8m)에서
    광선이 매트리스 윗면(가로 ±0.4m+여유, 길이 0~2.0m)에 닿는 칸만 남긴다(정적)."""
    sys.path.insert(0, str(SCR)); import geom
    keep = np.zeros((2, 64), bool)
    for i, (h, t) in enumerate(((0.48, 43.7), (0.47, 40.3))):
        R = geom.rays(t, flip_rows=True)
        for r in range(8):
            for c in range(8):
                d = R[r, c]
                if d[2] >= 0: continue
                p = (h / -d[2]) * d
                keep[i, r * 8 + c] = abs(p[0]) <= 0.4 + margin and -margin <= p[1] - geom.OFF <= 2.0 + margin
    return keep


def base_keep(baseline, outliers):
    """빈 침대 기준이 유효한 칸만(빈 침대에서도 측정 안 되는 칸 = 침대 밖·먼 곳 제외).
    outliers=True면 같은 줄 중앙값보다 300mm 넘게 먼 칸(바닥·벽)도 제외."""
    b = np.asarray(baseline, float)
    keep = b > 0
    if outliers:
        for i in range(2):
            g = b[i].reshape(8, 8)
            for r in range(8):
                v = g[r][g[r] > 0]
                if len(v):
                    med = np.median(v)
                    for c in range(8):
                        if g[r, c] > med + 300: keep[i, r * 8 + c] = False
    return keep


def act_keep(raw, baseline, y, train, frac):
    """학습 묶음(시험 사람 제외)의 사람 자세 프레임에서 기준보다 100mm 넘게 가까워진 비율이
    frac 이상인 칸만 남긴다. 사람이 거의 닿지 않는 칸 = 침대 밖으로 보고 제외."""
    tr = train[y[train] != 0]
    r = raw[tr]; b = np.asarray(baseline, float)[None]
    near = (r > 0) & (b > 0) & (b - r > 100)
    return near.mean(0) >= frac


def cols_keep():
    keep = np.ones((2, 8, 8), bool); keep[:, :, 0] = keep[:, :, 7] = False
    return keep.reshape(2, 64)


VARIANTS = ['none', 'roi_py', 'base_valid', 'base_off', 'act2', 'act5', 'cols', 'geo_fit', 'geo_fit_m']


def main():
    name, variant, seed, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
    models_arg = sys.argv[5] if len(sys.argv) > 5 else 'all'
    import torch
    from torch import nn
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.ensemble import RandomForestClassifier
    from xgboost import XGBClassifier
    from lightgbm import LGBMClassifier
    from tl.session_models import DualSensorCNN
    torch.set_num_threads(THREADS)
    D = cache(name)
    raw0, y, base0 = D['raw'].astype(np.float32), D['y'].astype(np.int64), D['baseline'].astype(np.float32)
    samples = [dict(subject=s, rep=int(r), y=int(t)) for s, r, t in zip(D['subject'], D['rep'], y)]
    mode, folds = outer_folds(samples)
    static = None; dynamic = None
    if variant == 'roi_py':
        kp = HERE / f'cache_{name}_roipy.npy'
        if not kp.exists(): np.save(kp, roi_py_keep(raw0))
        dynamic = np.load(kp)
        bkeep = roi_py_keep(base0[None])[0]
    elif variant == 'base_valid': static = base_keep(base0, False)
    elif variant == 'base_off': static = base_keep(base0, True)
    elif variant == 'cols': static = cols_keep()
    elif variant == 'geo_fit': static = geo_fit_keep(0.0)
    elif variant == 'geo_fit_m': static = geo_fit_keep(0.15)
    elif variant != 'none' and not variant.startswith('act'): raise SystemExit('알 수 없는 변형')

    def masked(keep_frames, keep_base):
        raw = np.where(keep_frames, raw0, -1).astype(np.float32)
        base = np.where(keep_base, base0, -1).astype(np.float32)
        x = np.stack([channels(r, base) for r in raw]).astype(np.float32)
        return raw, x

    if dynamic is not None: RAW, X = masked(dynamic, bkeep)
    elif static is not None: RAW, X = masked(static[None], static)
    else: RAW, X = masked(np.ones((1, 2, 64), bool), np.ones((2, 64), bool))
    use = ['raw_rf', 'raw_xgboost', 'raw_lightgbm', 'cnn', 'cnn_rf', 'cnn_xgboost', 'cnn_lightgbm'] if models_arg == 'all' else models_arg.split(',')
    preds = {m: np.full(len(y), -1, np.int8) for m in use}
    info = dict(dataset=name, variant=variant, seed=seed, mode=mode, folds=[], kept_zones={})
    if static is not None: info['kept_zones'] = dict(tof1=int(static[0].sum()), tof2=int(static[1].sum()), mask=static.astype(int).tolist())
    if dynamic is not None:
        info['kept_zones'] = dict(mean_removed_valid_per_frame=float(((raw0 > 0) & ~dynamic).reshape(len(y), -1).sum(1).mean()))
    t0 = time.time()

    def fit_encoder(Xf, train, valid=None, fixed=None):
        random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
        model = DualSensorCNN()
        tx, ty = torch.from_numpy(Xf[train]), torch.from_numpy(y[train])
        gen = torch.Generator().manual_seed(seed)
        loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(tx, ty), batch_size=BATCH, shuffle=True, generator=gen)
        cnt = np.bincount(y[train], minlength=6)
        lossf = nn.CrossEntropyLoss(weight=torch.tensor(len(train) / (6 * cnt), dtype=torch.float32))
        opt = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.001)
        best, best_ep, stale = -1., 1, 0
        for ep in range(fixed or EPOCHS):
            model.train()
            for bx, by in loader:
                opt.zero_grad(set_to_none=True); loss = lossf(model(bx), by); loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.); opt.step()
            if valid is not None:
                p = infer(model, Xf, valid)[0]
                sc = f1_score(y[valid], p, labels=range(6), average='macro', zero_division=0)
                if sc > best: best, best_ep, stale = sc, ep + 1, 0
                else: stale += 1
                if stale >= PATIENCE: break
        return model, best_ep

    def infer(model, Xf, idx):
        model.eval(); P, F = [], []
        with torch.inference_mode():
            for s in range(0, len(idx), 1024):
                z = model.features(torch.from_numpy(Xf[idx[s:s + 1024]])); F.append(z.numpy()); P.append(model.classifier(z).argmax(1).numpy())
        return np.concatenate(P), np.concatenate(F)

    def trees():
        return dict(rf=lambda: RandomForestClassifier(n_estimators=200, class_weight='balanced', n_jobs=THREADS, random_state=seed),
                    xgboost=lambda: XGBClassifier(n_estimators=TREES, max_depth=4, tree_method='hist', device='cpu', objective='multi:softprob',
                                                  num_class=6, n_jobs=THREADS, random_state=seed),
                    lightgbm=lambda: LGBMClassifier(n_estimators=TREES, num_leaves=15, min_child_samples=5, verbosity=-1, n_jobs=THREADS,
                                                    device_type='cpu', random_state=seed))

    for key, train, test in folds:
        if variant.startswith('act'):
            k = act_keep(raw0, base0, y, train, int(variant[3:]) / 100)
            RAW, X = masked(k[None], k)
            info['kept_zones'][key] = dict(tof1=int(k[0].sum()), tof2=int(k[1].sum()), mask=k.astype(int).tolist())
        rawf = RAW.reshape(len(y), 128)
        cnt = np.bincount(y[train], minlength=6); w = len(train) / (6 * cnt[y[train]])
        T = trees(); fold = dict(held_out=key)
        for kind in ('rf', 'xgboost', 'lightgbm'):
            m = 'raw_' + kind
            if m not in use: continue
            clf = T[kind]()
            if kind == 'rf': clf.fit(rawf[train], y[train])
            else: clf.fit(rawf[train], y[train], sample_weight=w)
            preds[m][test] = clf.predict(rawf[test])
        if any(m.startswith('cnn') for m in use):
            itr, iva = validation_split(samples, train)
            cand, ep = fit_encoder(X, np.asarray(itr), np.asarray(iva)); del cand
            model, _ = fit_encoder(X, train, fixed=ep); fold['epoch'] = ep
            ptest, ftest = infer(model, X, test); _, ftrain = infer(model, X, train)
            preds['cnn'][test] = ptest
            for kind in ('rf', 'xgboost', 'lightgbm'):
                m = 'cnn_' + kind
                if m not in use: continue
                clf = T[kind](); clf.fit(ftrain, y[train], sample_weight=w); preds[m][test] = clf.predict(ftest)
        fold['acc'] = {m: float(accuracy_score(y[test], preds[m][test])) for m in use}
        info['folds'].append(fold)
        print(name, variant, seed, 'fold', key, {m: round(v, 3) for m, v in fold['acc'].items()}, f'{time.time() - t0:.0f}s', flush=True)
    info['acc'] = {m: float(accuracy_score(y, preds[m])) for m in use}
    info['f1'] = {m: float(f1_score(y, preds[m], labels=range(6), average='macro', zero_division=0)) for m in use}
    info['seconds'] = time.time() - t0
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out.with_suffix('.npz'), **{m: p for m, p in preds.items()})
    out.write_text(json.dumps(info, ensure_ascii=False))
    print('DONE', name, variant, seed, {m: round(v, 4) for m, v in info['acc'].items()}, flush=True)


if __name__ == '__main__':
    main()
