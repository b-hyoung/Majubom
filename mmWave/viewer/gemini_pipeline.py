"""'제미나이' 탭의 처리 경로. 펌웨어 타겟(1010)을 그대로 쓰고 후처리한다.

제안받은 세 가지를 적용한다.
- 위치 필터: EMA 0.3. 녹화 비교에서 속도 연동 칼만보다 튐과 지연이 작았다.
- 반경 필터: 1011이 묶은 점 중 타겟 중심에서 먼 점은 뺀다.
- 유령 게이트: 반경 안 점이 연속해서 적은 타겟은 제외한다. 낮게 내려온 진짜 사람은 남긴다.
- 적응 높이: 점이 많으면 상위 85%, 적으면 상위 3개 평균을 쓰고 1초 중앙값으로 다듬는다.
- GMM 분할: 한 타겟의 반사점이 두 덩어리로 넓게 퍼지면 가상 번호 2개로 나눈다.
다른 탭의 전처리, 사람 ID, 낙상 판정 결과는 쓰지 않고 확정한 범위만 쓴다.
"""
from collections import deque

import numpy as np
from sklearn.mixture import GaussianMixture

from person_ids import in_poly

EMA_ALPHA = 0.3            # 위치 필터 계수
NEAR_RADIUS_M = 0.4        # 타겟 중심에서 이보다 먼 점은 그 타겟의 점으로 보지 않는다
GHOST_MIN_POINTS = 8       # 반경 안 점이 이보다 적은 프레임이 이어지면 유령으로 본다
GHOST_FRAMES = 3           # 이만큼 연속으로 많아야 진짜 점으로 본다
GHOST_HOLD_FRAMES = 545    # 약 30초 동안 많았던 적이 없으면 유령으로 본다(10초는 앉은 사람의 22%를 숨김)
LOW_Z_M = 0.5              # 진짜였던 타겟이 이보다 낮으면 쓰러진 사람일 수 있어 제외하지 않는다
HEAD_MANY_POINTS = 20
HEAD_PERCENTILE = 85
HEAD_MIN_POINTS = 8
HEAD_MEDIAN_FRAMES = 18    # 1초 중앙값
SPLIT_MIN_POINTS = 25      # 이보다 점이 적으면 나누지 않는다
SPLIT_MIN_STD_M = 0.45     # 수평 퍼짐이 이보다 작으면 한 사람으로 본다
SPLIT_MIN_DIST_M = 0.7     # 두 중심이 이보다 가까우면 한 사람으로 본다
SPLIT_MIN_WEIGHT = 0.3     # 작은 쪽 비중이 이보다 작으면 한 사람으로 본다


def near_points(own, x, y, radius=NEAR_RADIUS_M):
    """1011이 타겟에 묶은 점 중 타겟 중심 반경 안의 점만 남긴다."""
    if not len(own):
        return own
    return own[np.hypot(own[:, 0] - x, own[:, 1] - y) <= radius]


def adaptive_head(zs):
    """점이 많으면 상위 85%, 적으면 상위 3개 평균. 너무 적으면 None."""
    n = len(zs)
    if n >= HEAD_MANY_POINTS:
        return float(np.percentile(zs, HEAD_PERCENTILE))
    if n >= HEAD_MIN_POINTS:
        return float(np.sort(zs)[-3:].mean())
    return None


class GhostGate:
    """반경 안 점이 많았던 때가 최근에 있었는지로 타겟을 거른다.

    앉은 사람은 점 수가 프레임마다 크게 바뀌므로(중앙값 5개, 상위 10% 62개) 프레임별로 판단하지 않는다.
    진짜였던 타겟이 문 구역 밖에서 낮게 내려오면 쓰러진 사람일 수 있어 남긴다.
    매트리스 윗면이 0.7m라서 침상에 누운 사람은 높이로 걸러지지 않으므로 침상 안도 함께 남긴다.
    """

    def __init__(self, min_points=GHOST_MIN_POINTS, frames=GHOST_FRAMES, hold_frames=GHOST_HOLD_FRAMES, protect=True):
        self.min_points, self.frames, self.hold_frames, self.protect = min_points, frames, hold_frames, protect
        self.high = 0
        self.since_good = None   # 마지막으로 점이 많았던 뒤 지난 프레임 수
        self.was_real = False

    def update(self, count, z, in_door=False, on_bed=False):
        if count >= self.min_points:
            self.high += 1
        else:
            self.high = 0
        if self.high >= self.frames:
            self.was_real = True
            self.since_good = 0
        elif self.since_good is not None:
            self.since_good += 1
        if self.since_good is None:
            return True
        if self.since_good <= self.hold_frames:
            return False
        return not (self.protect and self.was_real and (z < LOW_Z_M or on_bed) and not in_door)


def split_centers(xy):
    """반사점이 두 덩어리이면 두 중심을, 아니면 None을 돌려준다."""
    if len(xy) < SPLIT_MIN_POINTS or np.hypot(*xy.std(axis=0)) < SPLIT_MIN_STD_M:
        return None
    gm = GaussianMixture(n_components=2, covariance_type="full", n_init=1, max_iter=30, random_state=0).fit(xy)
    if np.linalg.norm(gm.means_[0] - gm.means_[1]) < SPLIT_MIN_DIST_M or gm.weights_.min() < SPLIT_MIN_WEIGHT:
        return None
    # 두 덩어리가 한 덩어리보다 잘 설명해야 나눈다
    one = GaussianMixture(n_components=1, covariance_type="full", random_state=0).fit(xy)
    if gm.bic(xy) >= one.bic(xy):
        return None
    return gm.means_


class GeminiPipeline:
    def __init__(self):
        self.filters = {}
        self.trails = {}
        self.gates = {}
        self.heads = {}
        self.split_frames = 0

    def process(self, now, tracks, points, shapes, linked=None):
        linked = linked or {}
        room = shapes["room"]
        shown = []
        live = set()
        removed_room = 0
        removed_ghost = 0
        for t in tracks:
            tid = t["tid"]
            live.add(tid)
            xyz = t["world"]
            smooth = self.filters.get(tid)
            if smooth is None:
                smooth = self.filters[tid] = list(xyz)
                self.trails[tid] = deque(maxlen=40)
                self.gates[tid] = GhostGate()
                self.heads[tid] = deque(maxlen=HEAD_MEDIAN_FRAMES)
            smooth[:] = [EMA_ALPHA * v + (1 - EMA_ALPHA) * c for v, c in zip(xyz, smooth)]
            x, y, z = (round(v, 3) for v in smooth)
            self.trails[tid].append([round(x, 2), round(y, 2)])
            if not in_poly(x, y, room):
                removed_room += 1
                continue
            own = np.array(linked.get(tid, []), dtype=float).reshape(-1, 5)
            near = near_points(own, xyz[0], xyz[1])
            value = adaptive_head(near[:, 2]) if len(near) else None
            if value is not None:
                self.heads[tid].append(value)
            elif not len(near):
                self.heads[tid].clear()
            if self.gates[tid].update(len(near), xyz[2], in_poly(xyz[0], xyz[1], shapes["door"]), in_poly(xyz[0], xyz[1], shapes["bed"])):
                removed_ghost += 1
                continue
            head = round(float(np.median(self.heads[tid])), 2) if self.heads[tid] else None
            centers = split_centers(own[:, :2]) if len(own) else None
            base = {**t, "world": [x, y, z], "head_z": head, "trail": list(self.trails[tid]), "pid": None, "hidden_reason": None}
            if centers is None:
                shown.append(base)
            else:
                self.split_frames += 1
                for k, c in enumerate(centers):
                    shown.append({**base, "tid": tid * 1000 + k + 1, "world": [round(float(c[0]), 3), round(float(c[1]), 3), z], "label": f"{tid}번 분할", "kind": "person", "trail": []})
        for tid in [k for k in self.filters if k not in live]:
            del self.filters[tid], self.trails[tid], self.gates[tid], self.heads[tid]
        return {"tracks": shown, "points": points, "z_hist": None,
                "note": f"범위 필터 → EMA {EMA_ALPHA} → 반경 {NEAR_RADIUS_M}m 점 → 유령 게이트 → 적응 높이 → GMM 분할. 제외: 범위 밖 {removed_room}개, 유령 {removed_ghost}개. 분할 누적 {self.split_frames}프레임."}
