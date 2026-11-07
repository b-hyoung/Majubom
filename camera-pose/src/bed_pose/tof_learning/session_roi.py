"""기존 ToF 기하 ROI를 복사된 학습 표본에 적용하고 실험 설정을 기록한다."""
import hashlib
import importlib.util
from pathlib import Path
import numpy as np
from .session_data import channels


def apply_roi(samples, baseline, module_path):
    source = Path(module_path).resolve()
    spec = importlib.util.spec_from_file_location('bed_session_roi', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, 'mask_offbed', None)):
        raise ValueError('mask_offbed 함수가 있는 ToF ROI 모듈이 필요합니다.')

    def mask(raw):
        result = np.stack([module.mask_offbed(sensor, raw[index].tolist())
                           for index, sensor in enumerate(('tof1', 'tof2'))]).astype(np.float32)
        if result.shape != (2, 64) or not np.isfinite(result).all():
            raise ValueError('ROI 출력은 유한한 센서별 64칸이어야 합니다.')
        if not np.all((result == raw) | (result == -1)):
            raise ValueError('ROI는 원래 거리를 유지하거나 -1로 제외해야 합니다.')
        return result

    masked_baseline = mask(baseline)
    statistics = {}
    for sample in samples:
        before = sample['raw']
        after = mask(before)
        entry = statistics.setdefault(sample['label'], dict(pairs=0, removed_valid_cells=[0, 0], original_valid_cells=[0, 0]))
        entry['pairs'] += 1
        valid_before = (before > 0) & (before <= 4000)
        valid_after = (after > 0) & (after <= 4000)
        for index in range(2):
            entry['removed_valid_cells'][index] += int((valid_before[index] & ~valid_after[index]).sum())
            entry['original_valid_cells'][index] += int(valid_before[index].sum())
        sample['raw'] = after
        sample['x'] = channels(after, masked_baseline)
    parameters = {name: getattr(module, name) for name in (
        'SENSOR_H1', 'SENSOR_H2', 'TILT1_DEG', 'TILT2_DEG', 'SEP', 'BED_WID',
        'FOV_DEG', 'GRID', 'Y_MARGIN', 'YAW_DEG', 'Y_OFF') if hasattr(module, name)}
    return masked_baseline, dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        parameters=parameters, sensor_order=['tof1', 'tof2'], statistics=statistics,
        applied_to=['raw_distance_rf', 'cnn_distance', 'cnn_baseline_difference', 'cnn_valid_mask'],
        current_bed_geometry_verified=False,
        description='기존 ToF ROI의 침대 폭 기하 마스크. 현재 설치값 재실측 전 비교 실험.')
