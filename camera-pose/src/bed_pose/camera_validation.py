"""Separate reviewed six-posture evaluation, including abstentions in errors."""
from collections import Counter
from .bed_posture_3d import LABELS


def evaluate(predictions,annotations,*,minimum_per_class=30,minimum_episodes=3):
    if type(minimum_per_class) is not int or minimum_per_class<1:raise ValueError('Invalid minimum sample count')
    if type(minimum_episodes) is not int or minimum_episodes<1:raise ValueError('Invalid minimum episode count')
    rows={}
    for p in predictions:
        key=p.get('observation_id')
        if not isinstance(key,str) or not key or key in rows:raise ValueError('Missing or duplicate prediction observation_id')
        rows[key]=p
    counts=Counter();confusion={k:Counter() for k in LABELS};episodes={k:set() for k in LABELS}
    used=set();subjects=set();sessions=set();setups=set();covered=correct=0
    for a in annotations:
        key=a.get('observation_id');label=a.get('label')
        if key in used or key not in rows:raise ValueError('Duplicate annotation or unmatched observation')
        if label not in LABELS:raise ValueError('Only the six reviewed body postures are evaluated')
        if a.get('reviewed') is not True or any(not isinstance(a.get(k),str) or not a[k].strip() for k in ('reviewer','evidence','subject_id','session_id','episode_id')):
            raise ValueError('Independent review, evidence and grouping fields are required')
        used.add(key);counts[label]+=1;subjects.add(a['subject_id']);sessions.add(a['session_id'])
        signature=rows[key].get('setup_signature')
        if not isinstance(signature,str) or len(signature)!=64 or any(c not in '0123456789abcdef' for c in signature):
            raise ValueError('Camera setup signature is required')
        setups.add(signature)
        episodes[label].add((a['subject_id'],a['session_id'],a['episode_id']))
        pred=rows[key].get('predicted_label');pred=pred if pred in LABELS else 'unclassified'
        confusion[label][pred]+=1;correct+=pred==label;covered+=pred!='unclassified'
    n=sum(counts.values());accuracy=correct/n if n else None;coverage=covered/n if n else None
    recall={k:confusion[k][k]/counts[k] if counts[k] else None for k in LABELS}
    reasons=[]
    if len(setups)>1:raise ValueError('Camera/model/bed setup changed during evaluation')
    if len(used)!=len(rows):reasons.append('unreviewed_predictions')
    for k in LABELS:
        if counts[k]<minimum_per_class:reasons.append(f'{k}:insufficient_reviewed_samples')
        if len(episodes[k])<minimum_episodes:reasons.append(f'{k}:insufficient_separate_episodes')
        if recall[k] is None or recall[k]<.95:reasons.append(f'{k}:recall_below_95_percent')
    if accuracy is None or accuracy<.95:reasons.append('overall_accuracy_below_95_percent')
    if coverage is None or coverage<1:reasons.append('incomplete_six_class_coverage')
    return dict(version='camera-validation-0.1',threshold=.95,passed=not reasons,
                reviewed_samples=n,total_predictions=len(rows),unreviewed_predictions=len(rows)-len(used),
                setup_signature=next(iter(setups),None),accuracy=accuracy,coverage=coverage,
                counts=dict(counts),per_class_recall=recall,confusion={k:dict(v) for k,v in confusion.items()},
                episodes={k:len(v) for k,v in episodes.items()},subjects=sorted(subjects),sessions=sorted(sessions),
                acceptance_policy=dict(minimum_per_class=minimum_per_class,minimum_episodes=minimum_episodes,
                                       minimum_per_class_recall=.95,required_coverage=1.),
                reasons=reasons,scope='reviewed_dataset_only_not_population_accuracy',
                review_is_operator_attestation=True)
