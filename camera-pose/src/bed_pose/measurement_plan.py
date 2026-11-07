"""Single-posture instruction plan, independent of prediction and ground truth."""
import math

POSTURES={'supine':'바로 누움','side':'옆으로 누움','edge_sitting':'걸터앉음'}


def validate_plan(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)-{'goal_posture','preparation_mode','preparation_seconds','duration_seconds'}:
        raise ValueError('단일 자세 계획 형식을 확인하세요.')
    goal=value.get('goal_posture');mode=value.get('preparation_mode')
    if type(goal) is not str or goal not in POSTURES or mode not in ('timer','manual'):
        raise ValueError('목표 자세와 준비 방식을 선택하세요.')
    preparation=value.get('preparation_seconds',0)
    duration=value.get('duration_seconds')
    if type(preparation) not in (int,float) or not math.isfinite(preparation) or preparation<0 or preparation>86400:
        raise ValueError('준비 시간은 0~86400초로 입력하세요.')
    if duration is not None and (type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=86400):
        raise ValueError('측정 시간은 0초 초과~86400초, 수동 종료는 빈칸으로 두세요.')
    return {'kind':'single_posture','goal_posture':goal,'goal_label':POSTURES[goal],
            'preparation_mode':mode,'preparation_seconds':preparation if mode=='timer' else None,
            'duration_seconds':duration,'label_source':'operator_instruction_not_ground_truth',
            'verified_gt':None}
