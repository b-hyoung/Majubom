"""Separate storage for measurements intended for future rolling retention.

This module does not delete recordings or impose a storage limit.
"""
from pathlib import Path
import re


def validate_segment(segment):
    if (not isinstance(segment,dict) or set(segment) not in ({'series_id','index','previous_measurement_id'},
                                                               {'series_id','index','previous_measurement_id','group_id'})
            or not isinstance(segment['series_id'],str) or re.fullmatch(r'[0-9a-f]{32}',segment['series_id']) is None
            or ('group_id' in segment and (not isinstance(segment['group_id'],str)
                    or re.fullmatch(r'[0-9a-f]{32}',segment['group_id']) is None))
            or type(segment['index']) is not int or segment['index']<0
            or (segment['index']==0 and segment['previous_measurement_id'] is not None)
            or (segment['index']>0 and (not isinstance(segment['previous_measurement_id'],str)
                or re.fullmatch(r'[0-9a-f]{32}',segment['previous_measurement_id']) is None))):
        raise ValueError('블랙박스 구간 정보가 올바르지 않습니다.')
    return dict(segment)


def blackbox_root(output_root, *, create=False):
    root = Path(output_root).resolve()
    path = root / 'blackbox'
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError('블랙박스 저장 위치가 올바른 폴더가 아닙니다.')
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError('블랙박스 저장 위치를 확인하세요.')
    return path
