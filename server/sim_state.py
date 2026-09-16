"""Server-side mirror of site/dashboard-assets/simulation.js's phase/scenario tables.

Why this exists: the 5 bed_sim_* demo patients' sensor readings are purely client-side
theater (see simulation.js) — nothing is ever written to tof_readings/mmw_readings for
them, on purpose (those tables hold only real hardware readings). But the AI report
pipeline (ai_agent.gather_patient_state) needs *something* concrete to reason about for
these patients, or every report reduces to "정보 부족" — which defeats the point of a demo
that is supposed to show what a real report looks like. This module lets the backend
compute "which phase is bed_sim_X in right now" using the exact same deterministic,
wall-clock-seeded formula as the frontend, so a report generated server-side (by the
weekly scheduler, with no browser open) still lines up with whatever a viewer would see
on screen at that moment.

Keep PHASES/SCENARIOS in sync with simulation.js by hand — there are only two small
tables, so this has stayed easy to eyeball on every edit.
"""
from __future__ import annotations

import math
import time

PHASES: dict[str, dict] = {
    "sleep": {"name": "정상 수면", "dur": 30, "posture": "supine", "posture_ko": "바로 누움", "in_bed": True,
              "tof_risk": "normal", "speed": 0.0, "sway": 0.0, "stride": 0.0, "cv": 0.10, "freeze": 1.00,
              "mmw_risk": "normal", "reason": None},
    "toss": {"name": "수면 중 뒤척임", "dur": 20, "posture": "tossing", "posture_ko": "뒤척임", "in_bed": True,
             "tof_risk": "caution", "speed": 0.02, "sway": 0.01, "stride": 0.0, "cv": 0.22, "freeze": 0.90,
             "mmw_risk": "caution", "reason": None},
    "exit": {"name": "침상 이탈 감지", "dur": 25, "posture": None, "posture_ko": "침상 이탈 중", "in_bed": False,
             "tof_risk": "warning", "speed": 0.12, "sway": 0.03, "stride": 0.15, "cv": 0.18, "freeze": 0.60,
             "mmw_risk": "warning", "reason": "침상 가장자리 이탈 감지"},
    "walk": {"name": "이탈 후 이동", "dur": 20, "posture": None, "posture_ko": "보행 중", "in_bed": False,
             "tof_risk": "normal", "speed": 0.86, "sway": 0.02, "stride": 0.62, "cv": 0.11, "freeze": 0.05,
             "mmw_risk": "normal", "reason": None},
    "risk": {"name": "낙상 위험 임박", "dur": 20, "posture": None, "posture_ko": "불안정 보행", "in_bed": False,
             "tof_risk": "warning", "speed": 0.55, "sway": 0.09, "stride": 0.33, "cv": 0.38, "freeze": 0.34,
             "mmw_risk": "warning", "reason": "보행 변동성 증가 — 낙상 전조 패턴"},
    "fall": {"name": "낙상 감지됨", "dur": 20, "posture": None, "posture_ko": "낙상 감지", "in_bed": False,
             "tof_risk": "critical", "speed": 0.0, "sway": 0.15, "stride": 0.0, "cv": 0.55, "freeze": 0.98,
             "mmw_risk": "critical", "reason": "바닥 근접 감지 — 낙상 의심, 즉시 확인 필요"},
}

SCENARIOS: dict[str, list[str]] = {
    "bed_sim_calm": ["sleep", "toss"],
    "bed_sim_bedexit": ["sleep", "exit", "walk"],
    "bed_sim_gaitcaution": ["sleep", "toss", "toss"],
    "bed_sim_risk": ["sleep", "toss", "risk", "toss"],
    "bed_sim_fall": ["sleep", "toss", "exit", "walk", "risk", "fall"],
}


def is_simulated(bed_id: str) -> bool:
    return bed_id in SCENARIOS


def _seed_for(bed_id: str) -> float:
    h = 0
    for ch in bed_id:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return (h % 1000) / 1000 * math.pi * 2


def current_phase(bed_id: str, now_ts: float | None = None) -> dict | None:
    """Same math as simulation.js's scenarioState(): a deterministic function of wall-clock
    time and a per-bed_id hash seed, so backend and frontend agree without ever talking to
    each other. now_ts is seconds since epoch (time.time()), matching JS's Date.now()/1000."""
    keys = SCENARIOS.get(bed_id)
    if not keys:
        return None
    now_ts = time.time() if now_ts is None else now_ts
    total = sum(PHASES[k]["dur"] for k in keys)
    t = (now_ts + _seed_for(bed_id) * 10) % total
    acc = 0.0
    for key in keys:
        dur = PHASES[key]["dur"]
        if t < acc + dur:
            return {"key": key, **PHASES[key]}
        acc += dur
    first = keys[0]
    return {"key": first, **PHASES[first]}


def simulated_tof_behavior(bed_id: str) -> dict | None:
    phase = current_phase(bed_id)
    if not phase:
        return None
    return {
        "posture": phase["posture"], "posture_ko": phase["posture_ko"], "posture_conf": 0.9,
        "presence": {"in_bed": phase["in_bed"]},
        "risk": {"status": "anomaly" if phase["tof_risk"] in ("warning", "critical") else "normal",
                 "reason": phase["reason"]},
        "posture_baseline": {"alert_level": phase["tof_risk"], "source": "simulated_scenario"},
        "exit_episode": None,
        "note": "시뮬레이션 시나리오 기반 현재 상태 — 프런트엔드 데모용 가상 신호이며 실제 ToF 하드웨어 값이 아닙니다.",
    }


def simulated_mmw_latest(bed_id: str) -> dict | None:
    phase = current_phase(bed_id)
    if not phase:
        return None
    return {
        "raw": {"speed": phase["speed"], "sway": phase["sway"], "stride_length": phase["stride"],
                "speed_cv": phase["cv"], "freeze_ratio": phase["freeze"]},
        "quality": {"reliable": True, "walking": phase["speed"] > 0.1},
        "alert_level": phase["mmw_risk"], "measuring_held": False,
        "note": "시뮬레이션 시나리오 기반 현재 상태 — 프런트엔드 데모용 가상 신호이며 실제 mmWave 하드웨어 값이 아닙니다.",
    }
