"""Step 5 of the pre-fall mock-data work: generate radar-like patient tracks for fall scenarios and look-alikes.

Every episode is a sequence of tracker frames (t, x, y, z, speed, npts) at the radar's ~18 frames/s, like the
viewer recordings. Gait features are NOT generated here; step 6 runs these frames through the reader's own gait code.

Where the numbers come from:
  * walking (speed, its oscillation and turn dips, position jitter, body-centre height, points, dropouts, straight runs):
    normal_gait_stats.json (lee/kim recordings)
  * the fall itself (drop time, landing height, shift, floor noise): fall_moment_stats.json (10-12/10-13 staged falls)
  * how walking changes before each kind of fall: literature_effects.json (our assumption, stated there)
Each virtual person gets their own height, speed, cadence and radar noise so the evaluation can hold people out.
Usage: python mock_generator.py [--people 60] [--seed 7]   → mock_episodes.pkl
"""
import argparse
import json
import os
import pickle

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FS = 1 / 0.055                     # frameCfg 55 ms
ROOM = (-2.6, 1.0, -2.0, 2.0)      # walkable x/y box (m), measured room is about 3.6 × 4 m
FALLS = ("unsteady_fall", "freeze_collapse", "dizzy_collapse", "trip_fall")
NEGATIVES = ("walk_normal", "walk_slow", "walk_shuffle", "stop_stand", "sit_chair", "crouch")
# How the radar behaves at low speed (real_still_behaviour.json, lee and kim acted trials, 1 s smoothed speed):
# below ~0.2 m/s most points disappear (static reflections are removed) and the reported body height rises.
STILL_SPEED = (0.025, 0.075, 0.125, 0.175, 0.25, 0.375, 0.575, 1.0)
STILL_NPTS_RATIO = (0.0, 0.047, 0.127, 0.27, 0.567, 0.705, 0.665, 1.086)   # median points / walking points
STILL_ZERO_FRAC = (0.55, 0.295, 0.15, 0.04, 0.015, 0.0035, 0.002, 0.0)    # share of frames with no points
STILL_Z_RATIO = (1.23, 1.16, 1.18, 1.15, 1.16, 1.10, 0.955, 0.985)        # median height / walking height


def load_walk_params():
    st = json.load(open(os.path.join(HERE, "normal_gait_stats.json"), encoding="utf-8"))["frame"]
    def pool(kind, key):
        vals = [st[k][key]["mean"] for k in st if k.endswith(kind) and st[k][key]]
        return float(np.mean(vals)), float(np.std(vals)) if len(vals) > 1 else 0.0
    out = {}
    for kind in ("normal", "slow", "shuffle"):
        out[kind] = {k: pool(kind, k) for k in ("speed_mean", "speed_std", "speed_osc_std", "cadence_hz",
                                                 "jitter_lateral_std", "jitter_along_std", "z_osc_std",
                                                 "npts_mean", "npts_zero_frac")}
        runs = [st[k]["straight_run_sec"]["p50"] for k in st if k.endswith(kind) and st[k]["straight_run_sec"]]
        out[kind]["run_p50"] = float(np.mean(runs))
    # kim's "shuffle" was walked at normal speed; use lee's for the shuffle speed
    out["shuffle"]["speed_mean"] = (st["lee:shuffle"]["speed_mean"]["mean"], 0.05)
    out["z_mean"] = float(np.mean([st[k]["z_mean"]["mean"] for k in st]))
    drops = sum(([st[k]["dropout_runs_frames"]["p50"]] for k in st if st[k]["dropout_runs_frames"]), [])
    out["dropout_run_frames"] = float(np.median(drops))
    return out


class Person:
    def __init__(self, rng, walk):
        self.z_walk = rng.normal(walk["z_mean"], 0.05)               # body centre while walking (this ceiling setup)
        self.speed_scale = rng.normal(1.08, 0.12)                    # calibrated: first run walked 7% slower than lee/kim
        self.cadence = rng.normal(walk["normal"]["cadence_hz"][0], 0.07)
        self.jitter = rng.uniform(0.32, 0.65)                       # radar noise level (calibrated: first run swayed 30% more than real)
        self.zero_frac = rng.uniform(0.0, 0.02)


class Track:
    """Builds one episode frame by frame."""

    def __init__(self, rng, person, walk):
        self.r, self.p, self.w = rng, person, walk
        self.t = 0.0
        self.x = rng.uniform(ROOM[0] + 0.5, ROOM[1] - 0.5)
        self.y = rng.uniform(ROOM[2] + 0.5, ROOM[3] - 0.5)
        self.h = rng.uniform(-np.pi, np.pi)
        self.h_goal = self.h
        self.next_turn = 0.0
        self.turn_t = -9.0
        self.phase = rng.uniform(0, 2 * np.pi)
        self.lat = self.along = self.zn = 0.0
        self.amp_mod = 1.0      # step-to-step change of the speed oscillation (calibrated to real stride_cv)
        self.cad_mod = 0.0      # step-to-step change of cadence
        self.spd_mod = 0.0      # step-to-step change of walking speed (calibrated to real stride_cv)
        self.drop_left = 0
        self.rows = []
        self.zoff = 0.0
        self.v_smooth = 0.8

    def _noise_step(self, sd, prev, rho=0.85):
        return rho * prev + np.sqrt(1 - rho ** 2) * self.r.normal(0, sd)

    def emit(self, z_body, speed_obs, kind, lat_scale=1.0, z_noise=None, v_true=None):
        w = self.w[kind]
        self.v_smooth += (max(0.0, speed_obs if v_true is None else v_true) - self.v_smooth) / FS   # ~1 s smoothing
        v = self.v_smooth
        self.lat = self._noise_step(w["jitter_lateral_std"][0] * self.p.jitter * lat_scale, self.lat)
        self.along = self._noise_step(w["jitter_along_std"][0] * self.p.jitter, self.along)
        self.zn = self._noise_step(w["z_osc_std"][0] if z_noise is None else z_noise, self.zn, 0.7)
        if self.drop_left <= 0 and self.r.random() < self.p.zero_frac / max(self.w["dropout_run_frames"], 1):
            self.drop_left = int(self.r.geometric(1 / self.w["dropout_run_frames"]))
        ratio = float(np.interp(v, STILL_SPEED, STILL_NPTS_RATIO))
        zero = float(np.interp(v, STILL_SPEED, STILL_ZERO_FRAC))
        if self.drop_left > 0 or self.r.random() < zero:
            npts = 0
        else:
            npts = max(1, int(self.r.poisson(w["npts_mean"][0] * max(ratio, 0.04))))
        self.drop_left -= 1
        target = (float(np.interp(v, STILL_SPEED, STILL_Z_RATIO)) - 1) * self.p.z_walk if z_noise is None else 0.0
        self.zoff += (target - self.zoff) * 0.05
        ox, oy = -np.sin(self.h), np.cos(self.h)
        self.rows.append((self.t, self.x + ox * self.lat + np.cos(self.h) * self.along,
                          self.y + oy * self.lat + np.sin(self.h) * self.along,
                          z_body + self.zoff + self.zn, max(0.0, speed_obs), npts))
        self.t += 1 / FS

    def walk(self, sec, kind, speed_ratio=lambda u: 1.0, osc_ratio=lambda u: 1.0, sway_ratio=lambda u: 1.0):
        """Walk for sec seconds; the ratio functions take progress u in [0, 1] (used for the pre-fall ramps)."""
        w = self.w[kind]
        base = w["speed_mean"][0] * self.p.speed_scale
        osc_amp = w["speed_osc_std"][0] * np.sqrt(2)
        t0 = self.t
        while self.t - t0 < sec:
            u = (self.t - t0) / sec
            inside = ROOM[0] < self.x < ROOM[1] and ROOM[2] < self.y < ROOM[3]
            to_c = np.arctan2((ROOM[2] + ROOM[3]) / 2 - self.y, (ROOM[0] + ROOM[1]) / 2 - self.x)
            heading_out = np.cos(self.h_goal - to_c) < 0.3
            if self.t >= self.next_turn or (not inside and heading_out):
                self.h_goal = to_c + self.r.normal(0, 0.7) if not inside \
                    else self.h + self.r.choice([-1, 1]) * self.r.uniform(np.pi / 3, np.pi)
                self.turn_t = self.t
                self.next_turn = self.t + self.r.lognormal(np.log(w["run_p50"]), 0.6)
            turn = (self.h_goal - self.h + np.pi) % (2 * np.pi) - np.pi     # people turn at about 1.5 rad/s (matches real sway), not instantly
            self.h += float(np.clip(turn, -1.5 / FS, 1.5 / FS))
            dip = 1 - 0.35 * np.exp(-((self.t - self.turn_t) / 0.35) ** 2)          # slow down in turns
            self.amp_mod = 1 + 0.97 * (self.amp_mod - 1) + self.r.normal(0, 0.09 * osc_ratio(u))
            self.cad_mod = 0.97 * self.cad_mod + self.r.normal(0, 0.03 * osc_ratio(u))
            self.phase += 2 * np.pi * self.p.cadence * (1 + self.cad_mod) / FS
            self.spd_mod = 0.9 * self.spd_mod + self.r.normal(0, 0.06 * osc_ratio(u))
            v = base * speed_ratio(u) * dip * (1 + self.spd_mod)
            v_obs = v + osc_amp * osc_ratio(u) * self.amp_mod * np.sin(self.phase) + self.r.normal(0, 0.03)
            self.x += np.cos(self.h) * v / FS
            self.y += np.sin(self.h) * v / FS
            self.emit(self.p.z_walk, v_obs, kind, lat_scale=sway_ratio(u), v_true=v)

    def stand(self, sec, z=None, sway_ratio=lambda u: 1.0, speed=0.03):
        t0 = self.t
        while self.t - t0 < sec:
            u = (self.t - t0) / sec
            self.emit(self.p.z_walk if z is None else z, abs(self.r.normal(speed, 0.02)), "normal", lat_scale=sway_ratio(u) * 0.6)

    def move_z(self, sec, z_from, z_to, shift=0.0, kind="normal"):
        """Smooth height change (smoothstep), optionally shifting forward; returns the time the change started."""
        t0, n = self.t, max(2, int(round(sec * FS)))
        for i in range(n):
            s = i / (n - 1)
            e = s * s * (3 - 2 * s)
            de = (6 * s * (1 - s)) / sec
            self.x += np.cos(self.h) * shift * de / FS
            self.y += np.sin(self.h) * shift * de / FS
            self.emit(z_from + (z_to - z_from) * e, shift * de + abs(self.r.normal(0, 0.05)), kind, z_noise=0.03)
        return t0


def fall_drop(rng, kind, person):
    """(drop seconds, landing height, horizontal shift) from the staged-fall measurements."""
    if kind in ("unsteady_fall", "trip_fall"):
        sec = rng.uniform(0.6, 1.0) if rng.random() < 0.6 else rng.uniform(1.0, 1.5)
    elif kind == "freeze_collapse":
        sec = rng.uniform(1.2, 3.0)
    else:
        sec = rng.uniform(0.9, 1.5)
    floor = rng.uniform(-0.2, 0.2)
    shift = rng.uniform(0.15, 0.5)
    return sec, floor, shift


def load_profile(path, exclude=()):
    """Real acted trials (real_scenario_profiles.json) grouped by kind, without the held-out sessions."""
    rows = json.load(open(path, encoding="utf-8"))["trials"]
    out = {}
    for r in rows:
        if "note" not in r and r["session"] not in exclude:
            out.setdefault(r["kind"], []).append(r)
    return out


def mixed_trial(rng, trials, weaken):
    """Blend two real trials of the same kind (random weight) and optionally scale the change toward 'no change'
    (factor 0.5-1), so milder actors than the recorded ones are covered (kim's unsteady walking changed rhythm ×1.32,
    lee's ×2.05)."""
    a, b = trials[rng.integers(len(trials))], trials[rng.integers(len(trials))]
    w = rng.uniform(0, 1)
    r = {k: (w * a[k] + (1 - w) * b[k]) if isinstance(a.get(k), float) and isinstance(b.get(k), float) else a.get(k)
         for k in a}
    if weaken:
        f = rng.uniform(0.5, 1.0)
        for k in ("speed_ratio", "osc_ratio", "sway_ratio"):
            if k in r and r[k] is not None:
                r[k] = 1 + f * (r[k] - 1)
        if "slow_frac" in r:
            r["slow_frac"] *= f
    r["session"], r["trial"] = f"mix:{a['session']}#{a['trial']}+{b['session']}#{b['trial']}", ""
    return r


def episode_from_profile(rng, person, walk, kind, prof, mix=False):
    """Same frame model as episode(), but the scenario change is copied from one real acted trial of that kind
    (picked at random) with ±15% jitter, instead of the literature ranges."""
    tr = Track(rng, person, walk)
    J = lambda v: v * rng.uniform(0.85, 1.15)
    info = {"kind": kind, "fall": kind in FALLS, "drop_start": None, "landed": None, "source_trial": None}
    gait = rng.choice(["normal", "slow"], p=[0.7, 0.3])
    tr.walk(rng.uniform(12, 25), gait)
    if kind in FALLS and kind != "trip_fall":
        r = mixed_trial(rng, prof[kind], weaken=rng.random() < 0.5) if mix else prof[kind][rng.integers(len(prof[kind]))]
        info["source_trial"] = f"{r['session']}#{r['trial']}"
        pre = max(2.0, J(r["warning_sec"] - 2.0))                 # cue speech (~2 s) comes before the acting
        hold = lambda k: (lambda u, v=k: 1 + (v - 1) * min(1.0, u / 0.3))
        info["warn_start"] = tr.t                                  # the warning behaviour starts here
        if kind == "unsteady_fall":
            tr.walk(pre, gait, speed_ratio=hold(J(r["speed_ratio"])), osc_ratio=hold(J(r["osc_ratio"])),
                    sway_ratio=hold(J(r["sway_ratio"])))
        elif kind == "freeze_collapse":
            slow = min(0.8, J(r["slow_frac"]))
            walk_ratio = min(1.0, max(0.1, (r["speed_ratio"] - slow * 0.07) / (1 - slow)))
            tr.walk(pre * (1 - slow), gait, speed_ratio=hold(J(walk_ratio)), osc_ratio=hold(J(r["osc_ratio"])),
                    sway_ratio=hold(J(r["sway_ratio"])))
            tr.stand(pre * slow, speed=0.07, sway_ratio=lambda u, v=J(r["sway_ratio"]): v / 0.6)
        else:  # dizzy: standing, tracker speed stays at the measured share of walking speed
            stand_speed = J(r["speed_ratio"]) * walk[gait]["speed_mean"][0] * person.speed_scale
            tr.stand(pre, speed=stand_speed, sway_ratio=lambda u, v=J(r["sway_ratio"]): v / 0.6)
        floor = J(r["floor_z"]) if r["floor_z"] is not None else rng.uniform(-0.05, 0.2)
        info["drop_start"] = tr.move_z(max(0.35, J(r["drop_sec"])), person.z_walk, floor, J(r["shift_m"]))
        info["landed"] = tr.t
        tr.stand(rng.uniform(4, 6), z=floor, speed=0.02)
    elif kind == "trip_fall":
        sec, floor, shift = fall_drop(rng, kind, person)
        info["drop_start"] = tr.move_z(sec, person.z_walk, floor, shift)
        info["warn_start"] = info["drop_start"]                    # no warning before a trip
        info["landed"] = tr.t
        tr.stand(rng.uniform(4, 6), z=floor, speed=0.02)
    elif kind.startswith("walk_"):
        tr.walk(rng.uniform(15, 30), kind.split("_")[1])
    elif kind == "stop_stand":
        tr.stand(rng.uniform(3, 10)); tr.walk(rng.uniform(5, 10), gait)
    else:  # sit_chair / crouch: lowest body-centre height measured in the real control trials
        r = prof[kind][rng.integers(len(prof[kind]))]
        low = person.z_walk * J(r["min_z"] / r["walk_z"])
        tr.stand(rng.uniform(0.5, 1.5))
        tr.move_z(rng.uniform(0.6, 1.4), person.z_walk, low); tr.stand(rng.uniform(1, 8), z=low)
        tr.move_z(rng.uniform(0.6, 1.4), low, person.z_walk); tr.walk(rng.uniform(4, 8), gait)
    info["frames"] = np.array(tr.rows, dtype=float)
    return info


def episode(rng, person, walk, kind):
    tr = Track(rng, person, walk)
    lit = json.load(open(os.path.join(HERE, "literature_effects.json"), encoding="utf-8"))["scenarios"]
    U = lambda a: rng.uniform(*a)
    info = {"kind": kind, "fall": kind in FALLS, "drop_start": None, "landed": None}
    lead_walk = rng.uniform(12, 25)
    gait = rng.choice(["normal", "slow"], p=[0.7, 0.3])
    if kind in FALLS:
        if kind == "dizzy_collapse":
            tr.walk(lead_walk, gait)
            tr.stand(rng.uniform(2, 5))                      # stops (e.g. after getting up / turning)
        else:
            tr.walk(lead_walk, gait)
        pre = U(lit[kind]["pre_fall_sec"]) if lit[kind]["pre_fall_sec"][1] > 0 else 0.0
        c = lit[kind]["changes"]
        ramp = lambda lo_hi, target: (lambda u, k=target: 1 + (k - 1) * min(1.0, u / 0.6))
        if kind == "unsteady_fall":
            tr.walk(pre, gait, speed_ratio=ramp(None, U(c["speed_ratio"])),
                    osc_ratio=ramp(None, U(c["speed_osc_ratio"])), sway_ratio=ramp(None, U(c["lateral_sway_ratio"])))
        elif kind == "freeze_collapse":
            freeze = U(c["freeze_sec"])
            tr.walk(max(0.5, pre - freeze), gait, speed_ratio=ramp(None, U(c["speed_ratio_shuffle"])),
                    osc_ratio=ramp(None, U(c["speed_osc_ratio"])))
            tr.stand(freeze, speed=0.05, sway_ratio=lambda u: 1.5)
        elif kind == "dizzy_collapse":
            sway = U(c["lateral_sway_ratio"])
            tr.stand(pre, speed=U(c["standing_speed_mps"]), sway_ratio=lambda u, s=sway: 1 + (s - 1) * u)
        sec, floor, shift = fall_drop(rng, kind, person)
        info["drop_start"] = tr.move_z(sec, person.z_walk, floor, shift)
        info["landed"] = tr.t
        tr.stand(rng.uniform(4, 6), z=floor, speed=0.02)
    else:
        if kind.startswith("walk_"):
            tr.walk(rng.uniform(25, 45), kind.split("_")[1])
        elif kind == "stop_stand":
            tr.walk(lead_walk, gait); tr.stand(rng.uniform(3, 10)); tr.walk(rng.uniform(5, 10), gait)
        elif kind == "sit_chair":
            tr.walk(lead_walk, gait); tr.stand(rng.uniform(0.5, 1.5))
            seat = person.z_walk * rng.uniform(0.62, 0.75)
            tr.move_z(rng.uniform(0.8, 1.5), person.z_walk, seat); tr.stand(rng.uniform(4, 10), z=seat)
            tr.move_z(rng.uniform(0.8, 1.5), seat, person.z_walk); tr.walk(rng.uniform(4, 8), gait)
        elif kind == "crouch":
            tr.walk(lead_walk, gait); tr.stand(rng.uniform(0.5, 1.5))
            low = rng.uniform(-0.1, 0.35)
            tr.move_z(rng.uniform(0.6, 1.2), person.z_walk, low); tr.stand(rng.uniform(1, 4), z=low)
            tr.move_z(rng.uniform(0.6, 1.2), low, person.z_walk); tr.walk(rng.uniform(4, 8), gait)
    info["frames"] = np.array(tr.rows, dtype=float)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, default=60)
    ap.add_argument("--per-kind", type=int, default=5, help="episodes per scenario per person")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--profile", help="real_scenario_profiles.json: copy scenario changes from real acted trials")
    ap.add_argument("--exclude-session", action="append", default=[], help="real session to hold out from the profile")
    ap.add_argument("--out", default="mock_episodes.pkl")
    ap.add_argument("--mix", action="store_true", help="blend two real trials and weaken half of the episodes")
    ap.add_argument("--skip-kind", action="append", default=[], help="scenario to leave out (e.g. dizzy_collapse)")
    a = ap.parse_args()
    prof = load_profile(a.profile, a.exclude_session) if a.profile else None
    rng = np.random.default_rng(a.seed)
    walk = load_walk_params()
    eps = []
    for pid in range(a.people):
        person = Person(rng, walk)
        for kind in [k for k in FALLS + NEGATIVES if k not in a.skip_kind]:
            for _ in range(a.per_kind):
                e = episode_from_profile(rng, person, walk, kind, prof, a.mix) if prof else episode(rng, person, walk, kind)
                e["person"] = pid
                eps.append(e)
    pickle.dump({"walk_params": walk, "episodes": eps, "seed": a.seed, "profile": a.profile, "excluded": a.exclude_session},
                open(os.path.join(HERE, a.out), "wb"))
    secs = sum(e["frames"][-1, 0] for e in eps)
    print(f"{len(eps)} episodes, {a.people} people, {secs / 3600:.1f} h of frames")


if __name__ == "__main__":
    main()
