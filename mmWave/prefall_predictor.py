"""Pre-fall predictor: "will this person fall within the next few seconds?" from the mmWave patient track (2026-11-16).

Feed it the followed person's track one frame at a time (time, x, y, z, speed, points). It needs the person's first
10 s of walking as a personal baseline, then every 0.5 s it scores the last 3 s with the model in
models/prefall_xgb.json + prefall_xgb_meta.json. Version 2 (analysis/20261116_prefall_mock/prefall_final8.py) is
trained to flag a drop within 8 s (horizon_sec in the metadata); its first alarm came 2.9 s before the drop on average
on new mock data and 2.9-4.6 s on real people left out of training. Version 1 (3 s horizon) is kept as
models/prefall_xgb_v1_3s.json.

Scenarios it was trained for: unsteady walking (비틀거림) and freezing / short shuffling steps (멈칫) before sitting or
falling down. Dizzy collapse while standing is NOT covered (gait barely changes). Trips without warning cannot be
predicted. Trained on 2 people's acted trials (lee, kim) plus mock data built from them: treat it as a prototype.

Alarm = mean of the last 3 window probabilities >= threshold, and only while the person is upright (body height at
least 80% of their walking height); once the body goes down, the existing fall check (fall_detect.py) takes over.
Call reset() when a different person is followed or the track was lost.

Usage:
    p = PreFallPredictor()
    for t, x, y, z, speed, npts in frames:
        r = p.update(t, x, y, z, speed, npts)   # None until a window is scored
        if r and r["alarm"]: ...
"""
import json
import os
from collections import deque

import numpy as np

import prefall_gait as M   # same gait code the model was trained with, independent of the reader version

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_KEYS = ("speed", "speed_cv", "sway", "freeze_ratio", "stride_length", "stride_cv")


def _window(frames, end, win):
    """Gait features of the frames in (end - win, end], computed with the reader's own gait code."""
    sel = [f for f in frames if end - win < f[0] <= end]
    if len(sel) < 10:
        return None
    g = M._gait_from_samples([(f[0], f[1], f[2], f[3], f[4], 0.0) for f in sel])
    if g is None:
        return None
    a = np.array(sel)
    g = {k: float(g[k]) for k in BASE_KEYS}
    g["straightness"] = M.straightness_by_part(a[:, 0], a[:, 1:3])
    return g


class JsonTrees:
    """Evaluates an XGBoost binary:logistic model saved as JSON with numpy only, for machines without xgboost
    (the Raspberry Pi reader). Same output as XGBClassifier.predict_proba (checked in tests)."""

    def __init__(self, path):
        d = json.load(open(path, encoding="utf-8"))["learner"]
        if d["objective"]["name"] != "binary:logistic":
            raise ValueError("only binary:logistic models are supported")
        base = float(str(d["learner_model_param"]["base_score"]).strip("[]"))
        self.margin0 = float(np.log(base / (1 - base)))
        self.trees = []
        for t in d["gradient_booster"]["model"]["trees"]:
            self.trees.append((np.array(t["left_children"]), np.array(t["right_children"]), np.array(t["split_indices"]),
                               np.array(t["split_conditions"], dtype=float), np.array(t["default_left"], dtype=bool)))

    def predict_proba(self, X):
        X = np.asarray(X, dtype=np.float32)
        out = np.full(len(X), self.margin0)
        for left, right, feat, cond, dflt in self.trees:
            for i, x in enumerate(X):
                n = 0
                while left[n] != -1:
                    v = x[feat[n]]
                    n = (left[n] if dflt[n] else right[n]) if np.isnan(v) else (left[n] if v < np.float32(cond[n]) else right[n])
                out[i] += cond[n]
        p = 1 / (1 + np.exp(-out))
        return np.column_stack([1 - p, p])


def load_model(path):
    """XGBClassifier when xgboost is installed, otherwise the numpy tree evaluator."""
    try:
        from xgboost import XGBClassifier
    except ImportError:
        return JsonTrees(path)
    m = XGBClassifier()
    m.load_model(path)
    return m


def features(g, base, prev15, prev30):
    v = [g["speed"] / max(base["speed"], 0.1), g["speed_cv"] / max(base["speed_cv"], 0.05),
         g["sway"] / max(base["sway"], 0.01), g["freeze_ratio"] - base["freeze_ratio"],
         g["stride_cv"] / max(base["stride_cv"], 0.03), g["straightness"], g["speed"]]
    for pg in (prev15, prev30):
        v += ([g["speed"] - pg["speed"], g["speed_cv"] - pg["speed_cv"], g["sway"] - pg["sway"],
               g["freeze_ratio"] - pg["freeze_ratio"]] if pg else [0.0, 0.0, 0.0, 0.0])
    return v


class PreFallPredictor:
    LOW_RESET_SEC = 1.0
    RECENT_WALK_FROM, RECENT_WALK_TO, RECENT_WALK_SPEED = 4.0, 1.5, 0.25

    def __init__(self, model_path=os.path.join(HERE, "models", "prefall_xgb.json"),
                 meta_path=os.path.join(HERE, "models", "prefall_xgb_meta.json")):
        self.meta = json.load(open(meta_path, encoding="utf-8"))
        self.model = load_model(model_path)
        self.win = float(self.meta["window_sec"])
        self.step = float(self.meta["step_sec"])
        self.base_sec = float(self.meta["baseline_sec"])
        self.thr = float(self.meta["threshold"])
        self.upright = float(self.meta["upright_ratio"])
        self.min_walk_speed = float(self.meta.get("min_baseline_speed", M.WALK_SPEED_MIN))
        self.reset()

    def reset(self, start=None):
        """Forget the person. start: time the baseline walking begins (default: the next frame's time)."""
        self.t0 = start
        self.first = []                 # frames of the first baseline_sec (personal baseline)
        self.recent = deque()           # last few seconds of frames
        self.base = None
        self.walk_z = None
        self.next_end = None
        self.rows = {}                  # window end (relative, rounded) -> gait dict
        self.probs = deque(maxlen=3)
        self.low_since = None           # body below the upright line since (relative time)
        self.rebase = False

    def _baseline(self):
        rows = [_window(self.first, end, self.win) for end in np.arange(self.win, self.base_sec + 1e-9, 1.0)]
        rows = [r for r in rows if r]
        if not rows:
            return None
        return {k: float(np.median([r[k] for r in rows])) for k in BASE_KEYS}

    def update(self, t, x, y, z, speed, npts=0):
        if self.rebase:
            # The body was low for over LOW_RESET_SEC (sat, crouched, lay down or fell). Standing up and walking off
            # again looks like a gait change against the old baseline (live replay 2026-11-16: alarms ~9 s after getting
            # up from the mat), so wait for 10 s of fresh walking.
            self.reset()
        if self.t0 is None:
            self.t0 = t
        rel = t - self.t0
        frame = (rel, float(x), float(y), float(z), float(speed), float(npts))
        if rel <= self.base_sec:
            self.first.append(frame)
        self.recent.append(frame)
        while self.recent and rel - self.recent[0][0] > self.win + 3.5:
            self.recent.popleft()
        if self.base is None:
            if rel < self.base_sec:
                return None
            self.base = self._baseline()
            zs = [f[3] for f in self.first if 1.0 <= f[0] <= self.base_sec]
            self.walk_z = float(np.median(zs)) if zs else None
            if self.base is None or self.walk_z is None or self.base["speed"] < self.min_walk_speed:
                self.reset()            # the first 10 s were not walking (e.g. lying in bed): start over
                return None
            self.next_end = self.base_sec + self.win
        result = None
        while self.next_end is not None and self.next_end <= rel:
            end = self.next_end
            self.next_end = round(end + self.step, 6)
            g = _window(self.recent, end, self.win)
            if g is None:
                continue
            key = round(end, 2)
            self.rows[key] = g
            for old in [k for k in self.rows if k < key - 3.5]:
                del self.rows[old]
            prob = float(self.model.predict_proba(np.array([features(g, self.base, self.rows.get(round(end - 1.5, 2)),
                                                                     self.rows.get(round(end - 3.0, 2)))]))[0, 1])
            self.probs.append(prob)
            zs = [f[3] for f in self.recent if end - 0.5 < f[0] <= end]
            upright = bool(zs) and float(np.median(zs)) >= self.upright * self.walk_z
            # A freeze before a fall comes straight out of walking. Someone who has been standing still and starts to walk
            # looks the same to the model (live replay 2026-11-16: alarms 0.2-1.5 s after kim started walking from a
            # standstill), so an alarm also needs walking 1.5-4 s before this window's end.
            past = [f[4] for f in self.recent if end - self.RECENT_WALK_FROM < f[0] <= end - self.RECENT_WALK_TO]
            moving = bool(past) and float(np.median(past)) >= self.RECENT_WALK_SPEED
            alarm = len(self.probs) == 3 and float(np.mean(self.probs)) >= self.thr and upright and moving
            self.low_since = None if upright else (self.low_since if self.low_since is not None else end)
            self.rebase = self.low_since is not None and end - self.low_since >= self.LOW_RESET_SEC
            result = {"t": self.t0 + end, "prob": prob, "mean3": float(np.mean(self.probs)), "upright": upright,
                      "walking_before": moving, "alarm": alarm, "threshold": self.thr, "rebaseline": self.rebase}
            if self.rebase:
                break
        return result


class PatientPreFall:
    """Runs PreFallPredictor on the reader's patient track (send_mmw.py). Starts over when the patient number changes
    or the track is missing for more than 1 s, and turns a new alarm into one event (at most one per 10 s)."""

    GAP_SEC = 1.0
    COOLDOWN_SEC = 10.0

    def __init__(self, predictor=None):
        self.p = predictor or PreFallPredictor()
        self.tid = None
        self.last_t = None
        self.last = None
        self.alarm_on = False
        self.last_event_at = float("-inf")

    def feed(self, now, target, pid=None):
        """target: the reader's patient_target (world posX/posY/posZ, velX/velY, linked_npts) or None.
        Returns (latest result or None, event dict or None)."""
        if target is None:
            if self.last_t is not None and now - self.last_t > self.GAP_SEC:
                self._restart(None)
            return self.last, None
        if target["tid"] != self.tid or (self.last_t is not None and now - self.last_t > self.GAP_SEC):
            self._restart(target["tid"])
        self.last_t = now
        speed = float(np.hypot(target.get("velX", 0.0), target.get("velY", 0.0)))
        r = self.p.update(now, target["posX"], target["posY"], target["posZ"], speed,
                          target.get("linked_npts", target.get("npts", 0)))
        event = None
        if r is not None:
            self.last = {**r, "tid": target["tid"]}
            if r["alarm"] and not self.alarm_on and now - self.last_event_at >= self.COOLDOWN_SEC:
                event = {"stage": "prefall", "pid": pid, "tid": target["tid"], "at": now, "x": round(float(target["posX"]), 3),
                         "y": round(float(target["posY"]), 3), "z": round(float(target["posZ"]), 3),
                         "prob": round(r["mean3"], 3), "threshold": round(r["threshold"], 3),
                         "horizon_sec": self.p.meta.get("horizon_sec", 3),
                         "model": f"prefall_xgb v{self.p.meta.get('version', 1)} {self.p.meta.get('created', '')} (unsteady, freeze)"}
                self.last_event_at = now
            self.alarm_on = r["alarm"]
        return self.last, event

    def _restart(self, tid):
        self.p.reset()
        self.tid, self.last_t, self.last, self.alarm_on = tid, None, None, False


def load_patient_prefall():
    """PatientPreFall, or None with a reason when the model file or xgboost is missing (the reader then runs without it)."""
    try:
        return PatientPreFall(), None
    except (OSError, ImportError, ValueError, KeyError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
