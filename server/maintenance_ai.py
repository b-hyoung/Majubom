"""Bounded ADWIN1-style mean-shift monitor; diagnostic only, no automatic retraining.

Bifet & Gavalda, Learning from Time-Changing Data with Adaptive Windowing,
SDM 2007, doi:10.1137/1.9781611972771.42. Uses a Hoeffding cut test on
bounded [0,1] values, NOT the compressed, variance-adaptive ADWIN2 algorithm.
The 256-sample cap and per-update testing are engineering adaptations; no
paper-level false-positive guarantee or clinical deterioration claim is made.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import math


class AdaptiveWindow:
    def __init__(self, delta=.01, min_subwindow=16, max_window=256):
        if not 0 < delta < 1 or min_subwindow < 2 or max_window < 2*min_subwindow:
            raise ValueError("Invalid window/delta configuration")
        self.delta, self.minimum = delta, min_subwindow
        self.values = deque(maxlen=max_window)

    def update(self, value):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("ADWIN input must be finite and within [0,1]")
        self.values.append(float(value))
        drift, change = False, None
        # Repeat shrinking until the retained suffix passes every eligible cut.
        while len(self.values) >= self.minimum*2:
            values = list(self.values)
            total, partial, n = sum(values), 0.0, len(values)
            cut_at = None
            for cut in range(1, n):
                partial += values[cut-1]
                if cut < self.minimum or n-cut < self.minimum:
                    continue
                before, after = partial/cut, (total-partial)/(n-cut)
                epsilon = math.sqrt(.5*(1/cut+1/(n-cut))*math.log(4*n/self.delta))
                if abs(before-after) > epsilon:
                    cut_at = cut
                    change = {"before": before, "after": after, "epsilon": epsilon}
                    break
            if cut_at is None:
                break
            drift = True
            for _ in range(cut_at):
                self.values.popleft()
        return {"drift": drift, "change": change, "width": len(self.values),
                "mean": sum(self.values)/len(self.values), "method": "bounded_adwin1_hoeffding_v1",
                "action": "review_data_and_calibration" if drift else None,
                "clinical_deterioration": None, "automatic_baseline_update": False}


class MaintenanceMonitor:
    """Separate subject/sensor/metric streams; skip held, poor, stale and demo data."""
    def __init__(self, bounds, max_streams=128, max_gap_sec=300, **window_options):
        for lo, hi in bounds.values():
            if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
                raise ValueError("Fixed, finite physical metric bounds required")
        self.bounds, self.options = dict(bounds), window_options
        self.max_streams, self.max_gap_sec = max_streams, max_gap_sec
        self.streams, self.last_seen = {}, {}

    def observe(self, subject, sensor, metric, value, timestamp, *, reliable=True, held=False, simulated=False):
        if simulated or str(subject).startswith("bed_sim_"):
            return {"status": "skipped", "reason": "simulation"}
        if reliable is not True or held or value is None:
            return {"status": "skipped", "reason": "unreliable_or_held"}
        if not subject or not sensor or metric not in self.bounds:
            return {"status": "skipped", "reason": "unknown_stream"}
        try:
            ts = float(timestamp) if isinstance(timestamp, (int, float)) else datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
            v = float(value)
        except (ValueError, TypeError, OverflowError):
            return {"status": "skipped", "reason": "invalid_value_or_time"}
        if not math.isfinite(ts) or not math.isfinite(v):
            return {"status": "skipped", "reason": "nonfinite"}
        lo, hi = self.bounds[metric]
        if not lo <= v <= hi:
            return {"status": "skipped", "reason": "out_of_bounds"}
        key = (subject, sensor, metric)
        previous = self.last_seen.get(key)
        if previous is not None and ts <= previous:
            return {"status": "skipped", "reason": "duplicate_or_out_of_order"}
        if key not in self.streams and len(self.streams) >= self.max_streams:
            return {"status": "skipped", "reason": "stream_capacity"}
        reset = previous is not None and ts-previous > self.max_gap_sec
        if key not in self.streams or reset:
            self.streams[key] = AdaptiveWindow(**self.options)
        self.last_seen[key] = ts
        return {"status": "observed", "stream": list(key), "reset_after_gap": reset,
                **self.streams[key].update((v-lo)/(hi-lo))}
