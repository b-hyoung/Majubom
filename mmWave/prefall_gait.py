"""Gait features used by the pre-fall predictor (copied from send_mmw.py at commit d2c0bfd, 2026-11-16).

The model was trained with these exact functions (3 s straightness parts, sway along the walking direction). They are
kept here so the predictor gives the same numbers whatever version of send_mmw.py the reader runs (the Pi's reader
had older gait code with local changes). If send_mmw.py's gait code changes, this file must not follow it unless the
model is retrained.
"""
import numpy as np

FREEZE_SPEED_THRESH = 0.15
WALK_SPEED_MIN = 0.20
STRAIGHT_PART_SEC = 3.0
SWAY_SMOOTH_SEC = 1.0


def _estimate_stride(speed, t, speed_mean):
    """
    속도 시계열에서 FFT로 걸음 주기(cadence)를 내부적으로 추출하고,
    보폭(stride_length)과 보폭 변동성(stride_cv)을 추정한다.

    원리: 걸을 때 몸통 속도가 한 걸음마다 빨라졌다 느려졌다를 반복한다.
    이 주기적 변동의 지배 주파수 = cadence(Hz, 걸음/초).
    stride_length = speed / cadence.

    보폭 변동성: 속도 시계열을 걸음 주기 단위로 잘라 구간별 평균 속도를 구하고,
    각 구간의 보폭을 추정해 CV(변동계수)를 구한다.
    """
    n = len(speed)
    if n < 20 or speed_mean < 0.1:
        return 0.0, 0.0

    # 샘플링 주파수 추정
    dt = np.diff(t)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return 0.0, 0.0
    fs = 1.0 / np.median(dt)
    if fs < 1.0:
        return 0.0, 0.0

    # 속도에서 평균 제거 후 FFT
    sig = speed - speed_mean
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    fft_mag = np.abs(np.fft.rfft(sig))

    # 걸음 주파수 범위: 0.5~3.0 Hz (분당 30~180걸음)
    mask = (freqs >= 0.5) & (freqs <= 3.0)
    if not np.any(mask):
        return 0.0, 0.0

    valid_freqs = freqs[mask]
    valid_mag = fft_mag[mask]
    peak_idx = np.argmax(valid_mag)

    # 피크가 노이즈보다 충분히 커야 유의미 (평균 대비 2배 이상)
    if valid_mag[peak_idx] < np.mean(valid_mag) * 2:
        return 0.0, 0.0

    cadence_hz = float(valid_freqs[peak_idx])

    # 보폭 추정: stride = speed / cadence
    stride_length = speed_mean / cadence_hz

    # 보폭 변동성: 걸음 주기 단위로 구간을 나눠 각 구간 보폭을 추정
    step_period = 1.0 / cadence_hz  # 한 걸음에 걸리는 시간 (초)
    samples_per_step = max(int(fs * step_period), 1)

    if n >= samples_per_step * 2:
        n_steps = n // samples_per_step
        step_strides = []
        for i in range(n_steps):
            seg = speed[i * samples_per_step:(i + 1) * samples_per_step]
            seg_mean = float(np.mean(seg))
            if seg_mean > 0.05:
                step_strides.append(seg_mean / cadence_hz)
        if len(step_strides) >= 2:
            arr_s = np.array(step_strides)
            s_mean = np.mean(arr_s)
            if s_mean > 1e-6:
                stride_cv = float(np.std(arr_s) / s_mean)
            else:
                stride_cv = 0.0
        else:
            stride_cv = 0.0
    else:
        stride_cv = 0.0

    return stride_length, stride_cv


# ── 보행 지표 계산 (features.py 로직 통합) ─────────────────────────────


def _sway_along_heading(t, xy, smooth_sec=SWAY_SMOOTH_SEC):
    """1초 이동평균 경로를 진행 방향으로 보고, 각 점이 그 방향에서 옆으로 벗어난 거리의 표준편차(m).
    시작점→끝점 직선을 기준으로 하면 휘어진 길 자체가 흔들림으로 잡힌다."""
    if len(xy) < 3:
        return 0.0
    dt = np.diff(t)
    dt = dt[dt > 0]
    fs = 1.0 / float(np.median(dt)) if len(dt) else 18.0
    k = max(3, int(round(fs * smooth_sec)) | 1)          # 홀수 창
    if k > len(xy):
        k = len(xy) if len(xy) % 2 else len(xy) - 1
    if k < 3:
        return 0.0
    pad = np.pad(xy, ((k // 2, k // 2), (0, 0)), mode="edge")
    smooth = np.column_stack([np.convolve(pad[:, i], np.ones(k) / k, mode="valid") for i in range(2)])
    tangent = np.gradient(smooth, axis=0)
    norm = np.linalg.norm(tangent, axis=1, keepdims=True)
    tangent = tangent / np.where(norm > 1e-9, norm, 1.0)
    d = xy - smooth
    return float(np.std(d[:, 0] * tangent[:, 1] - d[:, 1] * tangent[:, 0]))


def straightness_by_part(t, xy, part_sec=STRAIGHT_PART_SEC):
    """창을 part_sec 조각으로 나눠 조각마다 순이동/경로 길이를 구하고 가장 낮은 값을 돌려준다.
    창이 part_sec보다 짧으면 창 전체 하나로 본다."""
    if len(xy) < 2:
        return 0.0
    span = t[-1] - t[0]
    parts = max(1, int(round(span / part_sec)))
    edges = [t[0] + span * i / parts for i in range(parts + 1)]
    values = []
    for a, b in zip(edges, edges[1:]):
        idx = np.where((t >= a) & (t <= b))[0]
        if len(idx) < 2:
            continue
        p = xy[idx]
        path = float(np.sum(np.hypot(*np.diff(p, axis=0).T)))
        values.append(float(np.hypot(*(p[-1] - p[0]))) / path if path > 1e-6 else 0.0)
    return min(values) if values else 0.0


def _gait_from_samples(samples):
    """샘플 리스트 [(t,x,y,z,vx,vy), ...] → gait raw dict. 10건 미만이면 None."""
    if len(samples) < 10:
        return None

    arr = np.array(samples)  # columns: t,x,y,z,vx,vy
    t, x, y, z, vx, vy = (arr[:, i] for i in range(6))

    # 속도 크기
    speed = np.sqrt(vx ** 2 + vy ** 2)
    speed_mean = float(np.mean(speed))
    speed_std = float(np.std(speed))
    speed_cv = float(speed_std / speed_mean) if speed_mean > 1e-6 else 0.0

    # sway: 진행 방향(1초 이동평균 경로)에서 옆으로 벗어난 거리의 표준편차
    sway = _sway_along_heading(t, np.column_stack([x, y]))

    # freeze: 거의 멈춰 있던 비율
    freeze_ratio = float(np.mean(speed < FREEZE_SPEED_THRESH))

    # height_drop: z 최대 하강폭 (낙상 신호)
    height_drop = float(np.max(z) - np.min(z)) if np.ptp(z) > 0 else 0.0

    # stride_length / stride_cv: 속도 FFT → cadence → 보폭 추정
    stride_length, stride_cv = _estimate_stride(speed, t, speed_mean)

    return {
        "speed": round(speed_mean, 3),
        "speed_cv": round(speed_cv, 3),
        "sway": round(sway, 3),
        "freeze_ratio": round(freeze_ratio, 3),
        "height_drop": round(height_drop, 3),
        "stride_length": round(stride_length, 3),
        "stride_cv": round(stride_cv, 3),
    }
