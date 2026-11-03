"""실측 기록만 논문용 Markdown으로 옮긴다. 누락값은 빈칸으로 보존한다."""
import json
import math
from pathlib import Path

MODELS = {
    'raw_rf': ('RF 원시', '거리 128개'),
    'cnn': ('CNN', '센서별 3채널 8×8'),
    'cnn_rf': ('CNN 특징 + RF', 'CNN 특징'),
    'cnn_xgboost': ('CNN 특징 + XGBoost', 'CNN 특징'),
    'cnn_lightgbm': ('CNN 특징 + LightGBM', 'CNN 특징'),
}


def parameter_snapshot(value):
    """라이브러리의 NaN 결측 지정도 추정 없이 JSON에 기록한다."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: parameter_snapshot(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [parameter_snapshot(v) for v in value]
    return value


def cell(value):
    if value is None:
        return ''
    if isinstance(value, float):
        return format(value, '.10g') if math.isfinite(value) else ''
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return str(value).replace('|', '\\|').replace('\n', ' ')


def render_record(record=None):
    r = record or {}
    settings = r.get('settings', {})
    data = r.get('data', {})
    labels = r.get('labels', ['empty', 'supine', 'side_left', 'side_right', 'sitting', 'edge_sit'])
    lines = ['# 논문용 실측 기록', '',
             '빈칸은 미측정 또는 미확인 항목이다. 이 파일의 준비만으로 녹화·학습·Pi 배포 완료를 의미하지 않는다.', '',
             '| 기록 항목 | 값 |', '|---|---|',
             '| 학습 완료 | '+cell(r.get('completed'))+' |',
             '| 평가 자료 출처 | '+cell(r.get('source'))+' |', '',
             '## 실측 설정', '', '| 항목 | 값 |', '|---|---|']
    for key, title in (
        ('convolutions', '센서별 conv 층 수·채널'), ('feature_dimension', '이어 붙인 특징 차원'),
        ('epochs', 'epoch: 최대·묶음별 선택·최종'), ('optimizer', 'optimizer·학습률·기타 설정'),
        ('batch', 'batch'), ('early_stopping', 'early stopping 기준'), ('augmentation', '실제 적용 증강'),
        ('tree_parameters', 'RF·XGBoost·LightGBM 전체 설정'), ('tree_weighting', '트리 학습 가중치'),
        ('packages', '사용 라이브러리 버전'), ('gpu', '학습에 사용한 GPU 이름'),
        ('device', '학습 장치'), ('seed', '난수 seed')):
        lines.append(f'| {title} | {cell(settings.get(key))} |')
    lines += ['', '## 데이터', '', '| 항목 | 값 |', '|---|---|']
    for key, title in (('participants', '참여자 수'), ('sessions', '입력 세션 수'),
                       ('sessions_with_pairs', '평가 프레임 쌍이 있는 세션 수'), ('pairs', '전체 프레임 쌍 수'),
                       ('validation', '검증 방식'), ('same_person', '같은 사람 평가 여부'),
                       ('pairing', '짝짓기·구간 제외 규칙')):
        lines.append(f'| {title} | {cell(data.get(key))} |')
    lines += ['', '| 클래스 | 프레임 쌍 수 |', '|---|---:|']
    lines += [f'| {label} | {cell(data.get("class_pairs", {}).get(label))} |' for label in labels]
    lines += ['', '## 분류기 비교', '',
              '정확도·macro-F1·재현율은 0–1 척도이며, 완료된 학습의 외부 시험 묶음 예측만 집계한다.', '',
              '| 모델 | 입력 | 정확도 | macro-F1 | Pi 5 CPU ms/프레임 쌍 |', '|---|---|---:|---:|---:|']
    for key, (name, inputs) in MODELS.items():
        metrics = r.get('models', {}).get(key, {})
        lines.append(f'| {name} | {inputs} | {cell(metrics.get("accuracy"))} | {cell(metrics.get("macro_f1"))} | {cell(r.get("pi_cpu_ms", {}).get(key))} |')
    lines += ['', '### 클래스별 재현율', '', '| 모델 | '+' | '.join(labels)+' |',
              '|---|'+'---:|'*len(labels)]
    for key, (name, _) in MODELS.items():
        recalls = r.get('models', {}).get(key, {}).get('per_class_recall', {})
        lines.append('| '+name+' | '+' | '.join(cell(recalls.get(label)) for label in labels)+' |')
    lines += ['', '### 혼동 행렬', '', '행은 지시 라벨, 열은 예측 라벨이며 순서는 위 클래스 표와 같다.']
    for key, (name, _) in MODELS.items():
        lines += ['', f'#### {name}', '', '| 지시 라벨 / 예측 | '+' | '.join(labels)+' |',
                  '|---|'+'---:|'*len(labels)]
        matrix = r.get('models', {}).get(key, {}).get('confusion_matrix')
        for i, label in enumerate(labels):
            values = matrix[i] if matrix is not None else [None]*len(labels)
            lines.append('| '+label+' | '+' | '.join(cell(v) for v in values)+' |')
    camera = r.get('camera', {})
    if camera.get('status', 'evaluated') != 'evaluated':
        camera = {}
    lines += ['', '## 카메라 라벨과 말로 붙인 라벨의 일치율', '', '| 항목 | 값 |', '|---|---|']
    for key, title in (('accuracy', '시간 정렬된 비교의 일치율'), ('matched_observations', '시간 정렬된 비교 수'),
                       ('coverage', '비교 중 분류 가능한 비율'), ('missing_tof_pairs', '카메라 비교가 없는 ToF 쌍 수'),
                       ('confusion_matrix', '혼동 행렬'), ('prediction_labels', '혼동 행렬 열 순서')):
        lines.append(f'| {title} | {cell(camera.get(key))} |')
    lines += ['', '미분류는 일치율 계산에서 오답으로 처리한다. 카메라 비교가 없으면 일치율을 0으로 대체하지 않는다.', '',
              '## Pi 5 CPU 추론 측정 조건', '', '| 항목 | 값 |', '|---|---|',
              '| 장치·스레드·워밍업·측정 횟수·전처리 포함 여부 | '+cell(r.get('pi_benchmark_conditions'))+' |', '',
              '노트북 CPU 측정이나 TorchScript 내보내기 검사는 Pi 실측 추론 시간으로 쓰지 않는다.', '']
    return '\n'.join(lines)


def write_record(folder, record=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'paper_record.md'
    temporary = path.with_suffix('.md.tmp')
    temporary.write_text(render_record(record), encoding='utf-8')
    temporary.replace(path)
    return path
