"""뒤척임 판정 — 순수 계산(입출력 없음). tof_server._posture_fields가 매 판정마다 부른다(2026-10-14 재설계).

뒤척임의 뜻(사용자 정의, 10-13·10-14): 자세가 아니라 '누운 채 자세를 바꿀 때의 몇 초짜리 움직임'.
예전 방식(자세 이름이 1.5초 넘게 바뀌면 뒤척임)은 사람이 가만히 있어도 모델이 왼쪽↔오른쪽을 헷갈리면 뒤척임이 됐다.

방법(사용자 제안: 자세마다 ToF 값이 다르고, 같은 자세에서는 값이 거의 같다):
  ① 움직임 = 지금 ToF 모양이 '마지막으로 멈춰 있던 모양'과 MOVE_MM 넘게 다름
     (침대 칸만, 두 센서의 칸별 |차이| 평균 → 센서 평균, 칸 하나는 800mm까지만 셈)
  ② 멈춤   = 직전 판정과의 차이가 STILL_MM 미만인 상태가 STILL_SEC 이어짐 → 그때 모양이 새 '멈춰 있던 모양'
  ③ 움직임이 끝나 멈췄을 때, 움직이기 전 자세 ≠ 멈춘 뒤 자세이고 둘 다 누운 자세 → 그 움직인 구간이 뒤척임
  ④ 움직임 없이 자세 이름만 바뀐 것(모델 헷갈림)은 뒤척임이 아니다. 움직였는데 자세가 같으면 꼼지락(뒤척임 아님).
기준값(10-13 S01·S02 녹화 181회차에서 잼, 침대 칸 평균 차이):
  같은 자세 30초 안(30초마다 살짝 고친 것 포함) 95% ≤ 47mm(발쪽)·63mm(머리쪽), 다른 자세끼리 중간값 93·119mm
  → MOVE_MM 60. 1초 간격 연속 프레임 차이(잡음) 중간값 19mm·95% 31mm → STILL_MM 35.
"""
from __future__ import annotations

from collections import deque

import numpy as np

MOVE_MM = 45.0
STILL_MM = 35.0
STILL_SEC = 1.5
SHAPE_CHANGE_MM = 35.0     # 자세가 정말 바뀌었으면 멈춘 모양도 달라야 한다(실측 자세끼리 최소 46mm: S01 왼↔오)
MAX_MOVE_SEC = 30.0          # 이보다 길게 움직이면(침대에서 내려옴·다른 활동) 뒤척임으로 보지 않는다
SHOW_SEC = 5.0               # 화면에 '뒤척임'을 남겨 두는 시간
CLIP_MM = 800.0
MIN_ZONES = 8
BED_MAX_MM = 2300
LYING = ("supine", "side_left", "side_right")


def _bed_mask(base):
    return np.array([(b is not None and 0 < b <= BED_MAX_MM) for b in (list(base or []) + [-1] * 64)[:64]])


def shape_diff(a: dict, b: dict, masks: dict) -> float | None:
    """두 프레임 묶음({'tof1': 64칸, 'tof2': 64칸})의 침대 칸 평균 |차이|(mm), 센서 평균."""
    ds = []
    for s, m in masks.items():
        if s not in a or s not in b:
            continue
        x, y = a[s], b[s]
        ok = m & (x > 0) & (y > 0)
        if ok.sum() >= MIN_ZONES:
            ds.append(float(np.mean(np.clip(np.abs(x[ok] - y[ok]), 0, CLIP_MM))))
    return float(np.mean(ds)) if ds else None


class TossDetector:
    def __init__(self):
        self.ref = None            # 마지막으로 멈춰 있던 모양
        self.ref_posture = None    # 그때 자세
        self.prev = None           # 직전 판정 때 모양
        self.still_since = None
        self.moving_since = None
        self.labels = deque()      # (t, 자세) 멈춤 구간 동안 — 멈춘 뒤 자세는 다수결
        self.last = None           # 마지막 뒤척임 {'at','from','to','sec'}
        self.events = deque()

    def update(self, now: float, frames: dict, label: str | None, baseline: dict) -> dict:
        cur = {s: np.array([(-1 if v is None else v) for v in (list(d) + [-1] * 64)[:64]], float)
               for s, d in frames.items() if d}
        masks = {s: _bed_mask(baseline.get(s)) for s in cur}
        step = shape_diff(cur, self.prev, masks) if self.prev else None
        self.prev = cur
        if step is not None and step < STILL_MM:
            self.still_since = self.still_since or now
        else:
            self.still_since = None
            self.labels.clear()
        if label:
            self.labels.append((now, label))
        while self.labels and now - self.labels[0][0] > STILL_SEC + 1.0:
            self.labels.popleft()
        settled = self.still_since is not None and now - self.still_since >= STILL_SEC
        if self.ref is None:
            if settled:
                self._settle(cur)
        else:
            d = shape_diff(cur, self.ref, masks)
            if self.moving_since is None and d is not None and d > MOVE_MM:
                self.moving_since = now
            if self.moving_since is not None and settled:
                before, start, old_ref = self.ref_posture, self.moving_since, self.ref
                after = self._settle(cur)
                dur = now - STILL_SEC - start
                changed = shape_diff(cur, old_ref, masks)   # 움직이기 전·후 멈춘 모양 차이(꼼지락은 원래 모양으로 돌아온다)
                if (before in LYING and after in LYING and before != after and 0 < dur <= MAX_MOVE_SEC
                        and changed is not None and changed >= SHAPE_CHANGE_MM):
                    self.last = {"at": now, "from": before, "to": after, "sec": round(max(dur, 0.5), 1)}
                    self.events.append(now)
            elif self.moving_since is None and settled:
                # 가만히 있는 동안 기준 모양·자세를 지금 것으로(천천히 바뀌는 이불·센서 값 따라감).
                # 움직임 없이 자세 이름만 바뀐 것(모델 헷갈림)은 여기서 기준 자세만 바뀌고 뒤척임은 아니다.
                self.ref = cur
                self.ref_posture = self._majority() or self.ref_posture
        while self.events and now - self.events[0] > 600:
            self.events.popleft()
        active = bool(self.last and now - self.last["at"] <= SHOW_SEC)
        return {"active": active, "moving": self.moving_since is not None,
                "from": self.last["from"] if active else None, "to": self.last["to"] if active else None,
                "sec": self.last["sec"] if active else None, "count_10min": len(self.events)}

    def _majority(self):
        if not self.labels:
            return None
        c = {}
        for _, l in self.labels:
            c[l] = c.get(l, 0) + 1
        return max(c, key=c.get)

    def _settle(self, cur):
        self.ref = cur
        self.ref_posture = self._majority()
        self.moving_since = None
        return self.ref_posture
