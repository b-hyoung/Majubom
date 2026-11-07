"""회차/사람 외부 평가, 내부 검증 epoch 선택, Pi CPU용 TorchScript·트리 저장."""
import csv
import hashlib
import json
from pathlib import Path
import random
import time
import numpy as np
from .data import write_json
from .paper_record import write_record, render_record, parameter_snapshot
from .session_data import (LABELS, load_dataset, outer_folds, validation_split,
                           orientation_verified, mirror, camera_accuracy, with_training_derivatives)


def run(args):
    import torch
    from torch import nn
    from sklearn.metrics import accuracy_score, f1_score, recall_score, confusion_matrix
    from sklearn.ensemble import RandomForestClassifier
    from xgboost import XGBClassifier
    from lightgbm import LGBMClassifier
    import joblib
    from .session_models import DualSensorCNN
    if args.epochs < 1 or args.batch < 1: raise ValueError('학습 횟수와 배치는 양수여야 합니다.')
    root = Path(args.out)
    if root.exists():
        # 오늘 미리 준비한 빈 논문 기록표만 있는 폴더는 실제 학습에 사용할 수 있다.
        entries=list(root.iterdir())
        if len(entries)!=1 or entries[0].name!='paper_record.md' or entries[0].read_text(encoding='utf-8')!=render_record():
            raise ValueError('기존 학습 결과를 보존하도록 새 출력 폴더를 지정하세요.')
    samples, baseline, audit = load_dataset(args.dataset, args.baseline)
    real_count = len(samples)
    real_session_count = len(audit)
    mode, folds = outer_folds(samples)
    synthetic_path = getattr(args, 'synthetic_dataset', None)
    if synthetic_path:
        derived, _, derived_audit = load_dataset(synthetic_path, args.baseline, synthetic=True,
                                               source_sessions={row['session']:row for row in audit})
        samples.extend(derived)
        audit.extend(derived_audit)
    roi = None
    roi_module = getattr(args, 'roi_module', None)
    if roi_module:
        from .session_roi import apply_roi
        baseline, roi = apply_roi(samples, baseline, roi_module)
    augment = orientation_verified(args.orientation) if args.augment else False
    if args.augment and not augment: raise ValueError('좌우 증강에는 --orientation 검증 파일이 필요합니다.')
    device = torch.device(args.device)
    if device.type == 'cuda' and not torch.cuda.is_available(): raise RuntimeError('CUDA를 사용할 수 없습니다.')
    torch.set_num_threads(min(8, torch.get_num_threads()))
    root.mkdir(parents=True,exist_ok=True)
    if roi_module:
        (root/'roi.py').write_bytes(Path(roi_module).read_bytes())
        write_json(root/'roi_audit.json', roi)
    x = np.stack([s['x'] for s in samples]); raw = np.stack([s['raw'].reshape(128) for s in samples])
    y = np.array([s['y'] for s in samples], dtype=np.int64)
    all_predictions = {name:np.full(real_count,-1,dtype=int) for name in ('raw_rf','cnn','cnn_rf','cnn_xgboost','cnn_lightgbm')}
    epochs = []; split_audit = []; fold_reports = []
    training_runs = []; optimizer_parameters = {}; fitted_tree_parameters = {}
    gpu_name = torch.cuda.get_device_name(device) if device.type == 'cuda' else None

    def metrics(truth, prediction):
        return dict(accuracy=float(accuracy_score(truth,prediction)),
            macro_f1=float(f1_score(truth,prediction,labels=range(6),average='macro',zero_division=0)),
            per_class_recall=dict(zip(LABELS,recall_score(truth,prediction,labels=range(6),average=None,zero_division=0).tolist())),
            confusion_matrix=confusion_matrix(truth,prediction,labels=range(6)).tolist())

    def fit_encoder(train, valid=None, fixed_epochs=None):
        seed=args.seed;random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
        if device.type=='cuda':torch.cuda.manual_seed_all(seed)
        model=DualSensorCNN().to(device)
        tx,ty=x[train],y[train]
        if augment:
            flipped,flipped_y=mirror(tx,ty);tx=np.concatenate([tx,flipped]);ty=np.concatenate([ty,flipped_y])
        tx=torch.from_numpy(tx);ty=torch.from_numpy(ty)
        generator=torch.Generator().manual_seed(seed)
        loader=torch.utils.data.DataLoader(torch.utils.data.TensorDataset(tx,ty),batch_size=args.batch,
            shuffle=True,generator=generator,num_workers=0)
        counts=np.bincount(y[train],minlength=6)
        loss_fn=nn.CrossEntropyLoss(weight=torch.tensor(len(train)/(6*counts),device=device,dtype=torch.float32))
        optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
        optimizer_parameters.update(name=type(optimizer).__name__, defaults=dict(optimizer.defaults),
                                    loss='class_weighted_cross_entropy', gradient_clip_norm=1.)
        best=-1.;best_epoch=1;stale=0
        for epoch in range(fixed_epochs or args.epochs):
            model.train()
            for bx,by in loader:
                optimizer.zero_grad(set_to_none=True);loss=loss_fn(model(bx.to(device)),by.to(device))
                if not torch.isfinite(loss):raise RuntimeError('학습 손실이 유한하지 않습니다.')
                loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
            if valid is not None:
                pred=infer(model,valid)[0];score=metrics(y[valid],pred)['macro_f1']
                if score>best:best=score;best_epoch=epoch+1;stale=0
                else:stale+=1
                if stale>=args.patience:break
        training_runs.append(dict(role='inner_validation' if valid is not None else 'refit',
                                  completed_epochs=epoch+1, train_pairs=len(train),
                                  selected_epoch=best_epoch if valid is not None else None))
        return model,best_epoch

    def infer(model, indices):
        model.eval();pred=[];features=[]
        with torch.inference_mode():
            for start in range(0,len(indices),args.batch):
                bx=torch.from_numpy(x[indices[start:start+args.batch]]).to(device)
                z=model.features(bx);features.append(z.cpu().numpy());pred.append(model.classifier(z).argmax(1).cpu().numpy())
        return np.concatenate(pred),np.concatenate(features)

    def trees():
        return dict(cnn_rf=RandomForestClassifier(n_estimators=200,class_weight='balanced',n_jobs=8,random_state=args.seed),
            cnn_xgboost=XGBClassifier(n_estimators=args.trees,max_depth=4,tree_method='hist',device='cpu',
                objective='multi:softprob',num_class=6,n_jobs=8,random_state=args.seed),
            cnn_lightgbm=LGBMClassifier(n_estimators=args.trees,num_leaves=15,min_child_samples=5,
                verbosity=-1,n_jobs=8,device_type='cpu',random_state=args.seed))

    def fit_models(train, test, model, folder):
        folder.mkdir()
        if roi_module:
            (folder/'roi.py').write_bytes(Path(roi_module).read_bytes())
        feature_train=infer(model,train)[1]
        cnn_pred,feature_test=infer(model,test)
        results=dict(cnn=cnn_pred)
        reference=RandomForestClassifier(n_estimators=200,class_weight='balanced',n_jobs=8,random_state=args.seed)
        reference.fit(raw[train],y[train]);results['raw_rf']=reference.predict(raw[test]);joblib.dump(reference,folder/'raw_rf.joblib')
        fitted_tree_parameters['raw_rf']=reference.get_params()
        # CNN 뒤 트리는 모두 CPU로 학습·배포. CNN 특징 학습은 노트북 CUDA.
        count=np.bincount(y[train],minlength=6);weight=len(train)/(6*count[y[train]])
        for name,tree in trees().items():
            tree.fit(feature_train,y[train],sample_weight=weight)
            fitted_tree_parameters[name]=tree.get_params()
            results[name]=tree.predict(feature_test).astype(int)
            if name=='cnn_xgboost':tree.save_model(folder/(name+'.json'))
            else:joblib.dump(tree,folder/(name+'.joblib'))
        cpu=model.to('cpu').eval();script=torch.jit.script(cpu);script.save(str(folder/'cnn_cpu.ts'))
        with torch.inference_mode():
            example=torch.from_numpy(x[test[:min(4,len(test))]])
            if not torch.allclose(script(example),cpu(example),atol=1e-5,rtol=1e-5):
                raise RuntimeError('CPU 내보내기 검증 실패')
        write_json(folder/'preprocessing.json',dict(version='dual-sensor-3channel-1',labels=LABELS,
            sensor_order=['tof1_foot','tof2_head'],input_shape=['batch',2,3,8,8],
            channels=['distance_div_4000','baseline_minus_distance_div_4000','valid_mask'],
            baseline={s:baseline[i].tolist() for i,s in enumerate(('tof1','tof2'))},
            delta_valid_only_when_baseline_and_current_valid=True,raw_rf_invalid=-1,roi_mask=bool(roi),roi=roi))
        return results

    for key,train,test in folds:
        inner_train,inner_valid=validation_split(samples,train)
        inner_augmented=with_training_derivatives(samples,inner_train)
        augmented_train=with_training_derivatives(samples,train)
        candidate,best_epoch=fit_encoder(inner_augmented,inner_valid);del candidate
        # 검증에서 epoch를 정한 뒤 바깥 학습 전체로 CNN을 새로 학습한다.
        model,_=fit_encoder(augmented_train,fixed_epochs=best_epoch);epochs.append(best_epoch)
        predictions=fit_models(augmented_train,test,model,root/('fold-'+key));del model
        report={name:metrics(y[test],pred) for name,pred in predictions.items()}
        for name,pred in predictions.items():all_predictions[name][test]=pred
        fold_reports.append(dict(held_out=key,test_samples=len(test),selected_epochs=best_epoch,models=report,
                                 real_train_samples=len(train),synthetic_train_samples=len(augmented_train)-len(train)))
        split_audit.append(dict(held_out=key,
            train_sample_ids=[samples[i]['sample_id'] for i in augmented_train],test_sample_ids=[samples[i]['sample_id'] for i in test],
            epoch_selection_train_ids=[samples[i]['sample_id'] for i in inner_augmented],
            epoch_selection_validation_ids=[samples[i]['sample_id'] for i in inner_valid]))
        print('묶음 평가 완료: '+key,flush=True)
    if any((p<0).any() for p in all_predictions.values()):raise RuntimeError('시험 예측이 누락됐습니다.')
    overall={name:metrics(y[:real_count],pred) for name,pred in all_predictions.items()}
    final_epochs=max(1,int(np.median(epochs)));indices=np.arange(len(y))
    final,_=fit_encoder(indices,fixed_epochs=final_epochs)
    fit_models(indices,indices,final,root/'final')  # 반환값은 평가 지표로 사용하지 않는다.
    camera=camera_accuracy(samples[:real_count],args.camera)
    write_json(root/'metrics.json',dict(version='bed-session-training-1',labels=LABELS,split_mode=mode,
        same_person_evaluation=mode=='rep',test_used_for_model_selection=False,
        label_source='operator_spoken_session_declaration',samples=real_count,folds=fold_reports,
        synthetic_samples=len(samples)-real_count,synthetic_test_samples=0,
        models=overall,camera=camera,augmentation=augment,final_epochs=final_epochs,
        trained_device=str(device),deployment_device='cpu',production_ready=False,roi_mask=bool(roi),roi=roi))
    write_json(root/'split_audit.json',dict(sessions=audit,folds=split_audit,
        sample_lineage=[{key:sample[key] for key in ('sample_id','session','collection_subject','synthetic','source_session')}
                        for sample in samples]))
    write_json(root/'run_manifest.json',dict(seed=args.seed,baseline_sha256=hashlib.sha256(Path(args.baseline).read_bytes()).hexdigest(),
        sessions_csv_sha256=hashlib.sha256((Path(args.dataset)/'sessions.csv').read_bytes()).hexdigest(),
        synthetic_sessions_csv_sha256=hashlib.sha256((Path(synthetic_path)/'sessions.csv').read_bytes()).hexdigest() if synthetic_path else None,
        orientation_sha256=hashlib.sha256(Path(args.orientation).read_bytes()).hexdigest() if args.orientation else None,
        camera_sha256={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in args.camera},
        packages={p:__import__('importlib.metadata',fromlist=['version']).version(p) for p in ('torch','numpy','scikit-learn','xgboost','lightgbm')}))
    with (root/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['모델','정확도','macro-F1',*LABELS])
        for name,r in overall.items():writer.writerow([name,r['accuracy'],r['macro_f1'],*[r['per_class_recall'][k] for k in LABELS]])
    lines=['# 새 침상 학습 결과','',f'평가 분할: {mode} / 같은 사람 평가: {mode == "rep"}',
           '정답 출처: 운영자가 말로 지정한 녹화 구간. 독립 영상 정답 인증은 아님.','',
           '| 모델 | 정확도 | macro-F1 |','|---|---:|---:|']
    lines += [f'| {name} | {r["accuracy"]:.4f} | {r["macro_f1"]:.4f} |' for name,r in overall.items()]
    lines += ['', 'Pi 운영 모델·baseline·DB는 변경하지 않았습니다. 배포는 majubom-c1이 담당합니다.',
              '최종 모델 경로: '+str((root/'final').resolve()),'카메라 평가는 metrics.json의 camera 항목을 확인하세요.']
    (root/'majubom-c1-summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    # 완료된 실행의 설정과 외부 시험 예측만 논문 기록에 사용한다.
    people={s['subject'] for s in samples[:real_count] if s['subject'] and s['subject'] not in ('없음','none','empty')}
    record=dict(completed=True, source='Pi dataset_v3 운영자 지시 라벨; 외부 시험 묶음 예측 집계',
        labels=LABELS, settings=dict(
            convolutions={name:[dict(in_channels=m.in_channels,out_channels=m.out_channels,
                                    kernel_size=list(m.kernel_size),stride=list(m.stride),padding=list(m.padding))
                                for m in branch.modules() if isinstance(m,nn.Conv2d)]
                          for name,branch in (('tof1_foot',final.foot),('tof2_head',final.head_sensor))},
            feature_dimension=final.classifier.in_features,
            epochs=dict(maximum=args.epochs,fold_selected=epochs,final=final_epochs,runs=training_runs),
            optimizer=optimizer_parameters,batch=args.batch,
            early_stopping=dict(metric='inner_validation_macro_f1',direction='maximize',
                                strict_improvement=True,patience=args.patience,
                                final_refit='fixed_selected_epochs_without_early_stopping'),
            augmentation='두 센서 가로 반전 + side_left/side_right 교환, 학습 자료에만 적용' if augment else '미적용',
            tree_parameters=fitted_tree_parameters,
            tree_weighting=dict(raw_rf='balanced class_weight',cnn_rf='balanced class_weight × inverse-frequency sample_weight',
                                cnn_xgboost='inverse-frequency sample_weight',cnn_lightgbm='inverse-frequency sample_weight'),
            packages={p:__import__('importlib.metadata',fromlist=['version']).version(p)
                      for p in ('torch','numpy','scikit-learn','xgboost','lightgbm','joblib')},
            gpu=gpu_name,device=str(device),seed=args.seed),
        data=dict(participants=len(people) if people else None,sessions=real_session_count,
                  sessions_with_pairs=sum(a['pairs']>0 for a in audit[:real_session_count]),pairs=real_count,
                  class_pairs={label:sum(s['label']==label for s in samples[:real_count]) for label in LABELS},
                  validation='사람 단위 (Leave-One-Subject-Out)' if mode=='subject' else '회차 단위',
                  same_person=mode=='rep',pairing='수신 시각 차이 ≤250ms, 1:1 짝짓기, 세션 앞뒤 1초 제외'),
        models=overall,camera=camera,folds=fold_reports,roi_mask=bool(roi),roi=roi,
        synthetic=dict(sessions=len(audit)-real_session_count,pairs=len(samples)-real_count,
                       test_pairs=0,actual_participants_exclude_synthetic=True,
                       source_session_excluded_from_inner_validation_and_outer_test=True),
        camera_used_for_training=False, camera_used_to_correct_tof=False,
        experiment_conditions='카메라 2대, 침대 같은 긴 면의 양 끝 위쪽에서 촬영, 반대쪽 긴 면은 보이지 않음',
        empty_split_policy='session_metadata.json의 촬영 묶음 참여자에 귀속' if
            (Path(args.dataset)/'session_metadata.json').exists() else '회차 기준 분배')
    record=parameter_snapshot(record)
    write_json(root/'paper_record.json',record)
    write_record(root,record)
    print('학습·CPU 내보내기 완료: '+str(root.resolve()),flush=True)
