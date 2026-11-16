"""Gait features of the Pi's room-walking fall forecast (port 5099), reused by the combined pre-fall model (2026-11-16).

Copied from another session's experiment on the Pi (~/.local/share/bed-fall-mmwave-experiment/walk_features.py and
walk_refine.py: WalkingWindow, RelativeWalkingWindow, extract). Only the feature computation is kept; their model and
alert policy are not. The features need the track at the server's live-snapshot rate (~3.9 samples/s), so
resample() thins an 18 frames/s track first; at 18 frames/s the speed and turning values are dominated by jitter.

Features (last 3 s): speed, speed_change (sd of point speeds), lateral (rms off the main axis), direction_change,
efficiency (straight / path), relative_height_drop (vs. the same track 2.6-9 s earlier), height_variation (range),
downward_speed, recent_lateral_gain (vs. 2.6-9 s earlier).
"""
from collections import deque

import numpy as np

NAMES = ("pi_speed", "pi_speed_change", "pi_lateral", "pi_direction_change", "pi_efficiency",
         "pi_relative_height_drop", "pi_height_variation", "pi_downward_speed", "pi_recent_lateral_gain")
LIVE_PERIOD = 0.26
MAX_GAP = .65


class WalkingWindow:
    def __init__(self):
        self.history = deque()
        self.track_id = None
        self.stamp = None
        self.at = None

    def observe(self, at, track):
        if not track:
            if self.at is not None and at - self.at > MAX_GAP:
                self.__init__()
            return None
        if self.track_id != track["id"] or self.at is None or at - self.at > MAX_GAP:
            self.__init__()
        if self.stamp == track["stamp"]:
            return None
        xyz = np.asarray(track["xyz"], dtype=float)
        if xyz.shape != (3,) or not np.isfinite(xyz).all():
            return None
        self.track_id, self.stamp, self.at = track["id"], track["stamp"], at
        self.history.append((at, xyz))
        while self.history and at - self.history[0][0] > 3.0:
            self.history.popleft()
        if len(self.history) < 7 or at - self.history[0][0] < 1.6:
            return None
        times = np.asarray([t for t, _ in self.history])
        xyzs = np.asarray([v for _, v in self.history])
        dt = np.diff(times)
        if np.any(dt <= 0):
            self.__init__()
            return None
        points = xyzs[:, :2]
        displacements = np.diff(points, axis=0)
        speed = np.linalg.norm(displacements, axis=1) / dt
        path = np.linalg.norm(displacements, axis=1).sum()
        straight = np.linalg.norm(points[-1] - points[0])
        if path < .32:
            return None
        centered = points - points.mean(axis=0)
        _, _, basis = np.linalg.svd(centered, full_matrices=False)
        lateral = centered @ basis[-1]
        steps = displacements[np.linalg.norm(displacements, axis=1) > .035]
        angles = np.arctan2(steps[:, 1], steps[:, 0])
        changes = np.angle(np.exp(1j * np.diff(angles)))
        z = xyzs[:, 2]
        c = times - times.mean()
        slope = float(c @ z / (c @ c))
        return (speed.mean(), speed.std(), np.sqrt(np.mean(lateral ** 2)),
                np.mean(np.abs(changes)) if len(changes) else 0.0, straight / path, z.mean(), np.ptp(z), slope)


class RelativeWalkingWindow:
    def __init__(self):
        self.estimator = WalkingWindow()
        self.reference = deque()

    def observe(self, at, track):
        if track and track["xyz"][2] < .30:          # body near the floor: start over (as in their extract())
            self.__init__()
            return None
        previous = self.estimator.track_id, self.estimator.at
        vector = self.estimator.observe(at, track)
        current = self.estimator.track_id, self.estimator.at
        if current == previous:
            return None
        if current[0] != previous[0] or previous[1] is None or at - previous[1] > .65:
            self.reference.clear()
        if vector is None:
            return None
        speed, variation, lateral, direction, efficiency, height, spread, slope = vector
        while self.reference and at - self.reference[0][0] > 9:
            self.reference.popleft()
        baseline = [(z, side) for ts, z, side in self.reference if at - ts >= 2.6]
        self.reference.append((at, height, lateral))
        if len(baseline) < 5:
            return None
        base_z, base_lateral = np.median(baseline, axis=0)
        return np.asarray((speed, variation, lateral, direction, efficiency, np.clip(base_z - height, -.45, .55), spread,
                           np.clip(-slope, -.5, .5), np.clip(lateral - base_lateral, -.25, .35)))


class LiveSampler:
    """Feeds RelativeWalkingWindow at the live-snapshot rate from an 18 frames/s track; keeps the latest vector."""

    def __init__(self):
        self.window = RelativeWalkingWindow()
        self.next_t = None
        self.latest = None      # (time, vector)

    def update(self, t, x, y, z):
        if self.next_t is not None and t < self.next_t:
            return self.latest
        self.next_t = t + LIVE_PERIOD
        v = self.window.observe(t, {"id": 0, "stamp": float(t), "xyz": [round(float(x), 3), round(float(y), 3), round(float(z), 3)]})
        if v is not None:
            self.latest = (t, v)
        return self.latest

    def vector_at(self, end, max_age=0.6):
        if self.latest is None or end - self.latest[0] > max_age:
            return [float("nan")] * len(NAMES)
        return [float(v) for v in self.latest[1]]


def series(frames, stop):
    """[(time, vector)] for a recorded track (t, x, y, z, ...) up to stop."""
    s, out = LiveSampler(), []
    for r in frames:
        if r[0] >= stop:
            break
        before = s.latest
        s.update(r[0], r[1], r[2], r[3])
        if s.latest is not None and s.latest is not before:
            out.append(s.latest)
    return out
