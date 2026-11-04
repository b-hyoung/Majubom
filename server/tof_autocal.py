"""ToF 빈 침대 기준값 자동 재캘리브레이션 — 순수 계산(입출력 없음). tof_server의 작업 스레드가 호출한다(2026-10-13).

왜: 베개·이불 위치가 바뀌면 빈 침대 모양이 기준값과 달라져 재실 판정이 틀어진다.
언제(사용자 결정 2026-10-13, 10-08 조건을 구체화): ToF가 침상 이탈을 확정하고 10초 뒤, 그동안 레이더가 사람을 추적 중이며
  그 사람이 0.3m 이상 움직였을 때(침대 위에 가만히 누운 환자를 ToF가 놓친 경우 환자 모양이 기준에 섞이는 것을 막는 안전장치).
어떻게: 이어서 5초 동안의 프레임에서 칸별 중앙값. 수집 중 크게 흔들린 칸(누가 침구를 만짐)은 예전 값을 유지.
안전장치: 기존 기준과 비교해 30cm 넘게 바뀐 칸이 25%(16칸)를 넘으면 자동 적용하지 않고 '재확인 필요'로 남긴다
  (침대를 옮겼거나 물건을 올려둔 경우 — 사람이 보고 수동으로 다시 잡는다).
"""
from __future__ import annotations

import statistics as st

from bed_position import bed_center

WAIT_AFTER_EXIT_SEC = 10.0     # 사용자 결정: 이탈 확정 후 10초
COLLECT_SEC = 5.0              # 그 뒤 5초 동안의 프레임 중앙값(한 프레임 저장 시 측정 잡음이 기준에 들어갔다)
RADAR_MOVE_M = 0.3             # 레이더 추적이 이탈 후 이만큼 움직였어야 '사람이 침대를 떠났다'고 본다
GIVE_UP_SEC = 600.0            # 이탈 후 10분 안에 조건이 안 맞으면 이번 이탈은 건너뛴다
UNSTABLE_MM = 150              # 수집 중 칸 값의 (최대−최소)가 이보다 크면 흔들린 칸 → 예전 값 유지
VALID_RATIO = 0.6              # 수집 프레임 중 이 비율 이상 유효해야 그 칸 값을 정한다(아니면 -1)
BIG_CHANGE_MM = 300            # 이보다 크게 바뀐 칸을 '큰 변화'로 센다
BIG_CHANGE_MAX = 16            # 큰 변화 칸이 이보다 많으면(64칸의 25%) 자동 적용하지 않는다


def radar_moved(history: list[tuple[float, list[tuple]]], since: float) -> dict:
    """history: [(시각, [(tid, x, y), ...]), ...]. since 이후 어떤 추적이든 RADAR_MOVE_M 이상 움직였나."""
    first, best = {}, (0.0, None)
    for t, targets in history:
        if t < since:
            continue
        for tid, x, y in targets:
            if tid not in first:
                first[tid] = (x, y)
            d = ((x - first[tid][0]) ** 2 + (y - first[tid][1]) ** 2) ** 0.5
            if d > best[0]:
                best = (d, tid)
    return {"moved": best[0] >= RADAR_MOVE_M, "max_move_m": round(best[0], 2), "tid": best[1],
            "seen_targets": len(first)}


# 침대 가운데: 2026-10-31부터 bed_position.bed_center()(mmWave/bed_center.json). 예전 값 (1.0, 1.4) — 10-29 30° 누운 자리 실측 · 레이더 바닥 좌표의 침대 가운데(mmWave/send_mmw.py BED_CENTER와 같게 — 10-22 부스 실측, 랩실 (0.1, 1.2))
BED_CLEAR_M = 1.5              # 2026-10-22: 이 안에(1.0m는 침대 옆 1m에 서 있던 사람을 못 막음) 레이더 추적이 하나라도 있으면 갱신하지 않는다
BED_CLEAR_SEC = 5.0            # 수집 직전 이 시간 동안 계속 비어 있어야 한다
# 10-22 라이브: 걸터앉은 사람을 ToF가 '이탈'로 깜빡일 때마다, 랩실의 다른 사람이 0.3m 움직이면 조건이 맞아
# 15분 동안 8번 갱신 → 앉은 몸이 '빈 침대 기준'에 들어가 ToF가 사람을 더 못 보는 악순환. '누가 움직였나'가 아니라
# '침대 근처가 비었나'를 본다.


def bed_clear(history: list[tuple[float, list[tuple]]], now: float, window: float = BED_CLEAR_SEC) -> dict:
    """history: [(시각, [(tid, x, y), ...])]. 최근 window초 동안 침대 가운데 BED_CLEAR_M 안에 추적이 없었나."""
    recent = [(t, tg) for t, tg in history if now - t <= window]
    if len(recent) < max(3, int(window / 0.5) - 2):
        return {"clear": False, "reason": "레이더 기록 부족", "nearest_m": None}
    bc = bed_center()
    if bc is None:   # 2026-10-31: 이 설치에서 침대 위치를 안 쟀으면 '비었는지' 알 수 없다 → 갱신하지 않음
        return {"clear": False, "reason": "침대 위치 미측정(tools/set_bed_center.py)", "nearest_m": None}
    nearest = min((((x - bc[0]) ** 2 + (y - bc[1]) ** 2) ** 0.5 for _, tg in recent for _, x, y in tg),
                  default=None)
    clear = nearest is None or nearest > BED_CLEAR_M
    return {"clear": clear, "nearest_m": round(nearest, 2) if nearest is not None else None,
            "reason": "" if clear else f"침대 {nearest:.2f}m 안에 사람(추적)"}


def build_baseline(frames: list[list[int]], old: list[int] | None) -> tuple[list[int], dict]:
    """5초 동안의 64칸 프레임들 → 새 기준값과 통계. 흔들린 칸·유효 부족 칸은 예전 값(없으면 -1)."""
    n_zone = max(len(f) for f in frames)
    new, unstable, invalid = [], 0, 0
    for z in range(n_zone):
        vals = [f[z] for f in frames if z < len(f) and f[z] is not None and f[z] > 0]
        keep = old[z] if old and z < len(old) else -1
        if len(vals) < VALID_RATIO * len(frames):
            new.append(keep if keep and keep > 0 else -1)
            invalid += 1
        elif max(vals) - min(vals) > UNSTABLE_MM:
            new.append(keep if keep and keep > 0 else int(st.median(vals)))
            unstable += 1
        else:
            new.append(int(st.median(vals)))
    return new, {"frames": len(frames), "unstable_zones": unstable, "invalid_zones": invalid}


def compare(old: list[int] | None, new: list[int]) -> dict:
    """기존 기준과 새 기준 비교: 바뀐 칸 수·평균 변화·큰 변화 칸 수, 자동 적용 가능 여부."""
    if not old:
        return {"changed": None, "big_changes": 0, "mean_abs_mm": None, "apply": True, "note": "기존 기준 없음 → 새로 만듦"}
    diffs = [abs(n - o) for n, o in zip(new, old) if n and n > 0 and o and o > 0]
    big = sum(d > BIG_CHANGE_MM for d in diffs)
    return {"changed": sum(d > 20 for d in diffs), "big_changes": big,
            "mean_abs_mm": round(st.mean(diffs), 1) if diffs else None, "apply": big <= BIG_CHANGE_MAX}


# ── 자세 모델 기준 자동 갱신(2026-11-04 사용자 결정: ToF만 쓸 때, 모델의 '빈 침대' 판정으로 계속 다시 잡는다) ──
# 레이더 없이(USE_RADAR=False) 쓰는 경로. 자세 모델은 원시 거리만 보고 판정하므로 빈 침대 기준과 무관하다
# (기준에 의존하는 '사람 칸 0 → empty' 게이트 전의 모델 출력만 쓴다 — 기준이 틀어지면 판정도 틀어지는 순환 방지).
MODEL_WINDOW_SEC = 60.0        # 최근 이 시간 동안의 모델 판정을 본다
MODEL_EMPTY_RATIO = 0.8        # 그중 empty 비율이 이 이상이어야 한다. 11-04 실측: 이불·베개 있는 빈 침대 0.87(나머지는
                               # 걸터앉음 오판 — 학습 빈 침대에 이불·베개가 없었음). 사람 단위 시험에서 실제 걸터앉음이
                               # empty로 나온 비율은 27%·9%라 0.8이면 구분된다(회차별로는 더 높을 수 있음)
MODEL_EMPTY_MEDIAN_CONF = 0.8  # empty 판정 확신의 중앙값(11-04 빈 침대·이불·베개: 0.8~1.0)
MODEL_RECAL_INTERVAL_SEC = 600.0   # 갱신 후 다음 갱신까지 최소 간격(빈 침대가 오래 이어지면 10분마다)
MODEL_HINT_MAX_AGE_SEC = 2.0   # 판정 사이가 이보다 벌어지면(센서 끊김) 창을 처음부터 다시 채운다


def model_empty_ready(history: list[tuple[float, str | None, float | None]], now: float,
                      window: float = MODEL_WINDOW_SEC) -> dict:
    """history: [(시각, 모델 라벨, 확신)] 시간순(같은 프레임 중복 없음). 최근 window초가 끊김 없이 채워졌고
    empty 비율·확신 중앙값이 기준 이상이면 ready. 시험(11-04 사람 단위): 걸터앉음의 약 27%가 empty로 나와
    한두 번 튀는 것은 막을 수 없으므로 '연속'이 아니라 '비율'로 본다."""
    rec = [h for h in history if now - h[0] <= window]
    out = {"ready": False, "ratio": None, "median_conf": None, "frames": len(rec)}
    if not rec or now - rec[-1][0] > MODEL_HINT_MAX_AGE_SEC:
        return out
    start = rec[0][0]
    for a, b in zip(rec, rec[1:]):
        if b[0] - a[0] > MODEL_HINT_MAX_AGE_SEC:
            start = b[0]
    rec = [h for h in rec if h[0] >= start]
    empties = [c for _, l, c in rec if l == "empty" and c is not None]
    out.update(ratio=round(len(empties) / len(rec), 3), frames=len(rec),
               median_conf=round(st.median(empties), 3) if empties else None)
    out["ready"] = (rec[-1][0] - start >= window - MODEL_HINT_MAX_AGE_SEC and out["ratio"] >= MODEL_EMPTY_RATIO
                    and empties and out["median_conf"] >= MODEL_EMPTY_MEDIAN_CONF)
    out["ready"] = bool(out["ready"])
    return out
