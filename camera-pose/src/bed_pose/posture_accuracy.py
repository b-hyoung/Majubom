"""Evaluate saved predictions against separate, declared-reviewed annotations.

Annotation provenance is declared by the reviewer, not verified by this tool.
No model training, relabeling, sensor agreement or synthetic human accuracy.
"""
from pathlib import Path

from .io_utils import load_json,strict_loads,file_hash,atomic_json
from .live_recording import verify_live

LABELS=('supine','side','edge_sitting')
OUTSIDE=('sitting','standing','other')
EVIDENCE_LABELS=LABELS+('sitting','prone')
FORCED_LABELS=EVIDENCE_LABELS+('lying','standing','other')
STATES=('lying','sitting','standing','other')


def load_annotations(path,measurement_id):
    value=load_json(path)
    if (not isinstance(value,dict) or set(value)!={'version','measurement_id','label_source','review_protocol_id','frames'}
            or value['version']!='posture-annotations-0.1' or value['measurement_id']!=measurement_id
            or value['label_source']!='reviewed_video'
            or not isinstance(value['review_protocol_id'],str) or not value['review_protocol_id'].strip()
            or not isinstance(value['frames'],list)):
        raise ValueError('Invalid posture annotation identity/provenance')
    frames={}
    for row in value['frames']:
        if (not isinstance(row,dict) or set(row)!={'stream_frame_id','label','annotation_id','uncertainty_reason'}
                or type(row['stream_frame_id']) is not int or row['stream_frame_id']<0
                or row['stream_frame_id'] in frames or row['label'] not in FORCED_LABELS+OUTSIDE+('uncertain',)
                or not isinstance(row['annotation_id'],str) or not row['annotation_id'].strip()
                or (row['label']=='uncertain' and (not isinstance(row['uncertainty_reason'],str) or not row['uncertainty_reason'].strip()))
                or (row['label']!='uncertain' and row['uncertainty_reason'] is not None)):
            raise ValueError('Invalid, duplicate or ambiguous posture annotation')
        frames[row['stream_frame_id']]=row
    return value,frames


def _rate(n,d):return n/d if d else None


def summarize(rows,annotations,labels=LABELS):
    confusion={gt:dict.fromkeys(labels+('held',),0) for gt in labels+('out_of_scope',)}
    coarse={gt:dict.fromkeys(STATES+('held',),0) for gt in STATES}
    seen=set();total=unannotated=uncertain=0
    for row in rows:
        total+=1;annotation=annotations.get(row['stream_frame_id'])
        if annotation is None:unannotated+=1;continue
        seen.add(row['stream_frame_id']);gt=annotation['label']
        if gt=='uncertain':uncertain+=1;continue
        a=row.get('posture_assessment') or {}
        label=a.get('label') if row['processing_status']=='ok' and a.get('status')=='predicted' else None
        if label not in labels:label='held'
        confusion[gt if gt in labels else 'out_of_scope'][label]+=1
        state=a.get('rule_features',{}).get('body_state') or {}
        predicted=state.get('state') if row['processing_status']=='ok' and state.get('status')=='predicted' else 'held'
        if predicted not in STATES:predicted='held'
        coarse_gt='lying' if gt in ('supine','side','prone') else 'sitting' if gt in ('sitting','edge_sitting') else gt
        coarse[coarse_gt][predicted]+=1
    if set(annotations)-seen:raise ValueError('Annotations reference frames missing from this measurement')
    metrics={}
    for label in labels:
        tp=confusion[label][label];support=sum(confusion[label].values());fn=support-tp
        fp=sum(counts[label] for gt,counts in confusion.items() if gt!=label)
        metrics[label]={'support':support,'tp':tp,'fp':fp,'fn_including_held':fn,
                        'precision':_rate(tp,tp+fp),'recall':_rate(tp,support),
                        'f1':_rate(2*tp,2*tp+fp+fn) if support else None}
    target_count=sum(sum(confusion[k].values()) for k in labels)
    correct=sum(confusion[k][k] for k in labels)
    accepted=sum(sum(confusion[k][p] for p in labels) for k in labels)
    values=[m['f1'] for m in metrics.values() if m['support']]
    outside=sum(confusion['out_of_scope'].values())
    coarse_total=sum(sum(c.values()) for c in coarse.values())
    coarse_accepted=sum(sum(c[p] for p in STATES) for c in coarse.values())
    coarse_correct=sum(coarse[k][k] for k in STATES)
    return {'frame_count':total,'unannotated_frames':unannotated,'uncertain_excluded_frames':uncertain,
            'evaluated_target_frames':target_count,'out_of_scope_frames':outside,
            'confusion':confusion,'per_class':metrics,
            'full_three_class_macro_f1':sum(values)/3 if labels==LABELS and len(values)==3 else None,
            'observed_class_macro_f1':sum(values)/len(values) if values else None,
            'macro_labels':[k for k in labels if metrics[k]['support']],
            'coverage':_rate(accepted,target_count),'held_rate':_rate(target_count-accepted,target_count),
            'accuracy_on_accepted':_rate(correct,accepted),'correct_fraction_including_held':_rate(correct,target_count),
            'out_of_scope_false_acceptance_rate':_rate(outside-confusion['out_of_scope']['held'],outside),
            'body_state':{'confusion':coarse,'evaluated_frames':coarse_total,'coverage':_rate(coarse_accepted,coarse_total),
                          'accuracy_on_accepted':_rate(coarse_correct,coarse_accepted),
                          'correct_fraction_including_held':_rate(coarse_correct,coarse_total)}}


def evaluate(directory,output,annotations=None):
    directory=Path(directory).resolve();output=Path(output).resolve()
    if output==directory or directory in output.parents:raise ValueError('Write evaluation outside the original measurement')
    verify_live(directory)
    manifest_path=directory/'manifest.json';frames_path=directory/'live_frames.jsonl'
    manifest_hash=file_hash(manifest_path);frames_hash=file_hash(frames_path);manifest=load_json(manifest_path)
    annotation_hash=file_hash(annotations) if annotations is not None else None
    provenance,labels=load_annotations(annotations,manifest['measurement_id']) if annotations is not None else (None,{})
    version=(manifest.get('automatic_posture_profile') or {}).get('version')
    classes=FORCED_LABELS if version=='automatic-posture-rules-0.12' else EVIDENCE_LABELS if version=='automatic-posture-rules-0.11' else LABELS
    with frames_path.open(encoding='utf-8') as stream:metrics=summarize((strict_loads(line) for line in stream),labels,classes)
    if (file_hash(manifest_path)!=manifest_hash or file_hash(frames_path)!=frames_hash
            or annotations is not None and file_hash(annotations)!=annotation_hash):
        raise ValueError('Measurement or annotations changed during evaluation')
    has_labels=metrics['evaluated_target_frames']+metrics['out_of_scope_frames']>0
    report={'version':'posture-accuracy-0.1','execution_kind':manifest['execution_kind'],
            'measurement_id':manifest['measurement_id'],'source_manifest_sha256':manifest_hash,
            'source_frames_sha256':frames_hash,'annotations_sha256':annotation_hash,
            'evaluation_status':'computed_against_declared_reviewed_annotations' if has_labels else 'not_evaluated_no_reviewed_labels',
            'annotation_provenance_verified_by_software':False,
            'annotation_source':{k:provenance[k] for k in ('label_source','review_protocol_id')} if provenance else None,
            'human_accuracy_validated':False,'metrics':metrics if has_labels else None,
            'counts':{k:metrics[k] for k in ('frame_count','unannotated_frames','uncertain_excluded_frames')},
            'policy':'current_frame_predictions_only; held counts as misses; planned goals and sensor outputs excluded'}
    output.mkdir(parents=True,exist_ok=False)
    try:atomic_json(output/'summary.json',report)
    except Exception:output.rmdir();raise
    return output,report
