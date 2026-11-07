"""Group-separated supervised encoders and frozen-feature tree comparison."""
import json,random,time,hashlib,platform
from pathlib import Path
import numpy as np
from .data import LABELS,load_rows,make_windows,write_json


def run(args):
    import torch
    from torch import nn
    from torch.utils.data import DataLoader,TensorDataset
    from sklearn.metrics import accuracy_score,balanced_accuracy_score,f1_score,confusion_matrix,classification_report
    from sklearn.ensemble import RandomForestClassifier
    from lightgbm import LGBMClassifier
    from xgboost import XGBClassifier
    import joblib
    from .models import ToFClassifier
    out=Path(args.out)
    if out.exists():raise ValueError('Output run already exists; choose a new run directory')
    rows=load_rows(args.inputs,args.reviews);split=json.loads(Path(args.split).read_text(encoding='utf-8'))
    synthetic=any(r.get('synthetic') for r in rows)
    if synthetic and not all(r.get('synthetic') for r in rows):raise ValueError('Never mix synthetic smoke data with real participant data')
    windows=make_windows(rows,split,args.window,args.stride,args.max_gap,args.allow_pseudo_train)
    out.mkdir(parents=True)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA requested but unavailable; run doctor first')
    device=torch.device('cuda' if args.device=='cuda' else 'cpu');seed=args.seed
    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
    torch.set_num_threads(min(8,torch.get_num_threads()))
    if device.type=='cuda':torch.cuda.manual_seed_all(seed)
    tensors={k:(torch.from_numpy(np.stack([e['x'] for e in v])),np.array([e['y'] for e in v])) for k,v in windows.items()}
    write_json(out/'split_audit.json',dict(group_by=split['group_by'],split=split,
        windows={part:[dict(group=e['group'],sample_ids=e['sample_ids']) for e in examples] for part,examples in windows.items()},
        counts={k:{LABELS[i]:int(np.sum(y==i)) for i in range(6)} for k,(x,y) in tensors.items()}))
    class_counts=np.bincount(tensors['train'][1],minlength=6)
    weights=torch.tensor(len(tensors['train'][1])/(6*class_counts),dtype=torch.float32,device=device)
    loaders={k:DataLoader(TensorDataset(x,torch.tensor(y,dtype=torch.long)),batch_size=args.batch,
        shuffle=k=='train',num_workers=0,pin_memory=device.type=='cuda',generator=torch.Generator().manual_seed(seed)) for k,(x,y) in tensors.items()}
    results=[]
    def metrics(y,pred):
        return dict(accuracy=float(accuracy_score(y,pred)),balanced_accuracy=float(balanced_accuracy_score(y,pred)),
            macro_f1=float(f1_score(y,pred,labels=list(range(6)),average='macro',zero_division=0)),
            confusion_matrix=confusion_matrix(y,pred,labels=list(range(6))).tolist(),
            per_class=classification_report(y,pred,labels=list(range(6)),target_names=LABELS,output_dict=True,zero_division=0))
    amp=device.type=='cuda' and not args.no_amp
    for kind in args.models.split(','):
        random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
        model=ToFClassifier(kind).to(device);optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=.001)
        scaler=torch.amp.GradScaler('cuda',enabled=amp);lossfn=nn.CrossEntropyLoss(weight=weights)
        best=-1.;best_state=None;stale=0;history=[];began=time.perf_counter()
        def infer(part,features=False):
            model.eval();outputs=[]
            with torch.inference_mode():
                for x,y in loaders[part]:
                    x=x.to(device)
                    with torch.autocast(device_type=device.type,enabled=amp):z=model.features(x) if features else model(x)
                    outputs.append(z.float().cpu().numpy())
            return np.concatenate(outputs)
        for epoch in range(args.epochs):
            model.train();loss_total=0.;count=0
            for x,y in loaders['train']:
                x=x.to(device);y=y.to(device);optimizer.zero_grad(set_to_none=True)
                with torch.autocast(device_type=device.type,enabled=amp):loss=lossfn(model(x),y)
                if not torch.isfinite(loss):raise RuntimeError('Nonfinite training loss; inspect data')
                scaler.scale(loss).backward();scaler.unscale_(optimizer);torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
                scaler.step(optimizer);scaler.update();loss_total+=float(loss.detach())*len(y);count+=len(y)
            validation=metrics(tensors['validation'][1],infer('validation').argmax(1));score=validation['macro_f1']
            history.append(dict(epoch=epoch+1,train_loss=loss_total/count,validation_macro_f1=score))
            if score>best:
                best=score;best_state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()};stale=0
            else:stale+=1
            print(json.dumps(dict(model=kind,**history[-1])),flush=True)
            if stale>=args.patience:break
        model.load_state_dict(best_state);model.to(device)
        torch.save(dict(state_dict=best_state,kind=kind,labels=LABELS,window=args.window,channels=['tof1_distance','tof2_distance','tof1_valid','tof2_valid'],scale_mm=4000),out/f'{kind}.pt')
        write_json(out/f'{kind}_history.json',history)
        # Feature extraction always uses ordered loaders, including the training partition.
        feature_sets={}
        for part,(x,y) in tensors.items():
            model.eval();chunks=[]
            with torch.inference_mode():
                for i in range(0,len(x),args.batch):
                    with torch.autocast(device_type=device.type,enabled=amp):z=model.features(x[i:i+args.batch].to(device))
                    chunks.append(z.float().cpu().numpy())
            feature_sets[part]=np.concatenate(chunks)
        for part in ('validation','test'):
            y=tensors[part][1];pred=infer(part).argmax(1)
            results.append(dict(model=kind,partition=part,**metrics(y,pred)))
        trees={'random_forest':RandomForestClassifier(n_estimators=args.trees,random_state=seed,n_jobs=8),
               'lightgbm':LGBMClassifier(n_estimators=args.trees,num_leaves=15,verbosity=-1,random_state=seed,n_jobs=8,device_type=args.lightgbm_device,min_child_samples=5),
               'xgboost':XGBClassifier(n_estimators=args.trees,max_depth=4,learning_rate=.05,tree_method='hist',device='cuda' if device.type=='cuda' else 'cpu',random_state=seed,n_jobs=8,objective='multi:softprob',num_class=6)}
        for name,tree in trees.items():
            start=time.perf_counter();train_y=tensors['train'][1]
            sample_weight=len(train_y)/(6*class_counts[train_y])
            tree.fit(feature_sets['train'],train_y,sample_weight=sample_weight)
            elapsed=time.perf_counter()-start
            actual_device='cpu'
            if name=='xgboost':
                actual_device=json.loads(tree.get_booster().save_config())['learner']['generic_param']['device']
                if device.type=='cuda' and actual_device=='cpu':raise RuntimeError('XGBoost unexpectedly fell back to CPU')
            elif name=='lightgbm':actual_device=args.lightgbm_device
            for part in ('validation','test'):
                start=time.perf_counter();pred=tree.predict(feature_sets[part]);latency=(time.perf_counter()-start)/len(pred)*1000
                results.append(dict(model=f'{kind}_features+{name}',partition=part,fit_device=actual_device,fit_seconds=elapsed,predict_ms_per_window=latency,**metrics(tensors[part][1],pred)))
            if name=='xgboost':tree.save_model(out/f'{kind}_{name}.json')
            else:joblib.dump(tree,out/f'{kind}_{name}.joblib')
        write_json(out/f'{kind}_runtime.json',dict(total_seconds=time.perf_counter()-began,device=str(device),amp=amp,
            peak_cuda_mb=torch.cuda.max_memory_allocated()/2**20 if device.type=='cuda' else None))
        del model
        if device.type=='cuda':torch.cuda.empty_cache()
    write_json(out/'metrics.json',dict(labels=LABELS,results=results,test_used_for_model_selection=False,
        evaluation='synthetic_smoke_only' if synthetic else 'reviewed_labels_only',pseudo_train_enabled=args.allow_pseudo_train))
    import csv
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8-sig') as f:
        fields=['model','partition','accuracy','balanced_accuracy','macro_f1'];w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(results)
    packages={}
    from importlib.metadata import version
    for p in ['torch','numpy','scikit-learn','lightgbm','xgboost']:packages[p]=version(p)
    write_json(out/'run_manifest.json',dict(seed=seed,settings={k:v for k,v in vars(args).items() if k!='func'},packages=packages,
        gpu=torch.cuda.get_device_name() if device.type=='cuda' else None,platform=platform.platform(),
        input_hashes={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in args.inputs},
        split_hash=hashlib.sha256(Path(args.split).read_bytes()).hexdigest(),dataset_windows={k:len(v) for k,v in windows.items()},
        reviews_hash=hashlib.sha256(Path(args.reviews).read_bytes()).hexdigest(),synthetic_smoke_only=synthetic,
        training_labels_are_pseudo=args.allow_pseudo_train,production_ready=False))
    print(f'Completed: {out.resolve()}',flush=True)
