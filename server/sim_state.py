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
from datetime import datetime

# tof_server.POSTURE_KO 색상표의 부분집합 — 순환 임포트를 피하려고 여기 따로 둔다(값은
# tof_server.py의 POSTURE_KO와 맞춰 유지).
_POSTURE_COLOR = {
    "empty": "#9aa0b4", "supine": "#22c55e", "side_left": "#38bdf8", "side_right": "#38bdf8",
    "tossing": "#fb923c", "edge_sit": "#f59e0b", "sitting": "#4aa8ff",
}

PHASES: dict[str, dict] = {
    "sleep": {"name": "정상 수면", "dur": 30, "posture": "supine", "posture_ko": "바로 누움", "in_bed": True,
              "tof_risk": "normal", "speed": 0.0, "sway": 0.0, "stride": 0.0, "cv": 0.10, "freeze": 1.00,
              "mmw_risk": "normal", "reason": None, "activity": "inBed", "pos": (1.1, 1.9)},
    "toss": {"name": "수면 중 뒤척임", "dur": 20, "posture": "tossing", "posture_ko": "뒤척임", "in_bed": True,
             "tof_risk": "caution", "speed": 0.02, "sway": 0.01, "stride": 0.0, "cv": 0.22, "freeze": 0.90,
             "mmw_risk": "caution", "reason": None, "activity": "inBed", "pos": (1.05, 2.6)},
    "edge": {"name": "기상 준비 · 걸터앉음", "dur": 12, "posture": None, "posture_ko": "걸터앉음", "in_bed": True,
             "tof_risk": "caution", "speed": 0.01, "sway": 0.02, "stride": 0.0, "cv": 0.15, "freeze": 0.75,
             "mmw_risk": "caution", "reason": None, "activity": "inBed", "pos": (1.5, 3.0)},
    "exit": {"name": "침상 이탈 감지", "dur": 25, "posture": None, "posture_ko": "침상 이탈 중", "in_bed": False,
             "tof_risk": "warning", "speed": 0.12, "sway": 0.03, "stride": 0.15, "cv": 0.18, "freeze": 0.60,
             "mmw_risk": "warning", "reason": "침상 가장자리 이탈 감지", "activity": "outOfBed", "pos": (1.75, 2.85)},
    "checkingNormal": {"name": "낙상 의심 · 교차검증 중", "dur": 10, "posture": None, "posture_ko": "움직임 없음 · 확인 중",
                        "in_bed": False, "tof_risk": "warning", "speed": 0.0, "sway": 0.0, "stride": 0.0, "cv": 0.0,
                        "freeze": 1.00, "mmw_risk": "warning",
                        "reason": "이탈 전 자세: 걸터앉음(정상) — 이탈 후 움직임 없음, AI 교차검증 중",
                        "activity": "checking", "pos": (1.75, 2.85)},
    "checkingAbnormal": {"name": "낙상 의심 · 교차검증 중", "dur": 10, "posture": None, "posture_ko": "움직임 없음 · 확인 중",
                          "in_bed": False, "tof_risk": "warning", "speed": 0.0, "sway": 0.0, "stride": 0.0, "cv": 0.0,
                          "freeze": 1.00, "mmw_risk": "warning",
                          "reason": "이탈 전 자세: 뒤척임(비정상) — 이탈 후 움직임 없음, AI 교차검증 중",
                          "activity": "checking", "pos": (1.75, 2.85)},
    "walk": {"name": "이탈 후 이동", "dur": 20, "posture": None, "posture_ko": "보행 중", "in_bed": False,
             "tof_risk": "normal", "speed": 0.86, "sway": 0.02, "stride": 0.62, "cv": 0.11, "freeze": 0.05,
             "mmw_risk": "normal", "reason": None, "activity": "walking", "pos": None,
             "pos_from": (1.9, 3.0), "pos_to": (3.6, 5.25)},
    "risk": {"name": "낙상 위험 임박", "dur": 20, "posture": None, "posture_ko": "불안정 보행", "in_bed": False,
             "tof_risk": "warning", "speed": 0.55, "sway": 0.09, "stride": 0.33, "cv": 0.38, "freeze": 0.34,
             "mmw_risk": "warning", "reason": "보행 변동성 증가 — 낙상 전조 패턴", "activity": "walking", "pos": (2.8, 4.2)},
    "fall": {"name": "낙상 감지됨", "dur": 20, "posture": None, "posture_ko": "낙상 감지", "in_bed": False,
             "tof_risk": "critical", "speed": 0.0, "sway": 0.15, "stride": 0.0, "cv": 0.55, "freeze": 0.98,
             "mmw_risk": "critical", "reason": "바닥 근접 감지 — 낙상 의심, 즉시 확인 필요",
             "activity": "fallen", "pos": (2.85, 4.05)},
}

# posture_key(simulation.js의 PHASES[key].posture 대응) — synthetic_tof_latest()가 몸 모양 격자를
# 고를 때 씀. exit/checking*/walk/risk/fall처럼 침대를 비운 phase는 posture_key를 안 둬서
# 빈 침대(baseline) 격자를 쓰게 한다.
POSTURE_KEY = {"sleep": "supine", "toss": "tossing", "edge": "edge"}

SCENARIOS: dict[str, list[str]] = {
    "bed_sim_calm": ["sleep", "toss"],
    "bed_sim_bedexit": ["sleep", "edge", "exit", "checkingNormal", "walk"],
    "bed_sim_gaitcaution": ["sleep", "toss", "toss"],
    "bed_sim_risk": ["sleep", "toss", "edge", "risk", "toss"],
    "bed_sim_fall": ["sleep", "toss", "exit", "checkingAbnormal", "fall"],
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
            return {"key": key, "progress": (t - acc) / dur, **PHASES[key]}
        acc += dur
    first = keys[0]
    return {"key": first, "progress": 0.0, **PHASES[first]}


def _room_position(phase: dict, now_ts: float, bed_id: str, progress: float = 0.0) -> tuple[float, float] | None:
    """simulation.js의 roomPosition()과 동일한 흔들림 공식 — 같은 순간엔 같은 좌표가 나오도록
    seed/계수를 그대로 맞춤. pos가 없는 phase(침대 안)는 프런트와 동일하게 None(마커 없음).
    posFrom/posTo가 있는 phase(예: walk)는 progress로 선형보간 — simulation.js의 roomPosition()이
    posFrom→posTo로 이동시키는 것과 정확히 같은 좌표를 내야 프런트 평면도와 raw mmWave 뷰어가
    서로 다른 위치를 보여주지 않는다."""
    base = phase.get("pos")
    if not base:
        pos_from, pos_to = phase.get("pos_from"), phase.get("pos_to")
        if pos_from and pos_to:
            base = (pos_from[0] + (pos_to[0] - pos_from[0]) * progress,
                    pos_from[1] + (pos_to[1] - pos_from[1]) * progress)
    if not base:
        return None
    activity = phase.get("activity")
    amp = 0.05 if activity == "walking" else (0.01 if activity in ("fallen", "checking") else 0.08)
    seed = _seed_for(bed_id)
    x = base[0] + math.sin(now_ts * 0.9 + seed) * amp
    y = base[1] + math.cos(now_ts * 0.8 + seed) * amp * 0.6
    return round(x, 2), round(y, 2)


# ── 8×8 ToF 존 격자(mm) 합성 — /tof/3d 실제 재구성 코드는 그대로 두고, 값만 채워 넣는다 ──
_TOF_BASELINE_MM = 700  # 빈 매트리스 표면(대략치) — 몸이 있으면 이보다 가까워짐
_TOF_GRID = 8


def _body_grid(center_r: float, center_c: float, radius: float, depth_mm: float,
               jitter_mm: float, now_ts: float, seed: float) -> list[int]:
    """중심(center_r,center_c) 주변을 baseline보다 depth_mm만큼 가깝게(몸이 떠 있으니) 파인
    가우시안형 함몰로 채운 8x8(행 우선) 리스트. 실제 WebGL 재구성(tof_3d.html)이 이 값을
    그대로 catmull 보간해서 곡면으로 그려주므로, 대략적인 타원형 함몰만으로도 몸 형태처럼 보임."""
    out = []
    for r in range(_TOF_GRID):
        for c in range(_TOF_GRID):
            dr = (r - center_r) / max(radius, 0.001)
            dc = (c - center_c) / max(radius * 1.6, 0.001)
            fall = math.exp(-(dr * dr + dc * dc))
            jitter = math.sin(now_ts * 1.7 + seed + r * 0.7 + c * 1.3) * jitter_mm
            d = _TOF_BASELINE_MM - depth_mm * fall + jitter
            out.append(max(80, round(d)))
    return out


def _posture_grid(posture_key: str | None, now_ts: float, seed: float) -> list[int]:
    if posture_key == "supine":
        return _body_grid(3.5, 3.5, 3.2, 220, 4, now_ts, seed)
    if posture_key == "tossing":
        # 중심에서 좌우로 치우친 비대칭 함몰 + 지터를 더 크게 줘서 "불안정" 느낌
        side = 1 if math.sin(now_ts * 0.3 + seed) > 0 else -1
        return _body_grid(3.5, 3.5 + side * 1.6, 2.6, 200, 10, now_ts, seed)
    if posture_key == "edge":
        # 한쪽 긴 변(마지막 열 쪽)에 좁고 깊은 함몰 — 걸터앉은 좁은 접촉면
        return _body_grid(3.5, 6.2, 1.6, 260, 5, now_ts, seed)
    # exit/checking*/walk/risk/fall — 침대는 비어 있음
    return [_TOF_BASELINE_MM] * (_TOF_GRID * _TOF_GRID)


def synthetic_tof_latest(bed_id: str) -> dict:
    """실제 /tof/latest와 동일한 모양 — tof_3d.html의 렌더링/재구성 코드는 그대로 두고 값만
    시뮬레이션에서 채워 넣는다. received_at은 매 호출마다 새로 찍어야 8초 stale 판정을 피한다."""
    phase = current_phase(bed_id)
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts).isoformat(timespec="milliseconds")
    if not phase:
        return {"tof1": None, "tof2": None, "posture": None}
    seed = _seed_for(bed_id)
    posture_key = POSTURE_KEY.get(phase["key"])
    grid1 = _posture_grid(posture_key, now_ts, seed)
    grid2 = _posture_grid(posture_key, now_ts, seed + 1.0)
    frame = {"resolution": "8x8", "targets": None, "received_at": now_iso}
    color = _POSTURE_COLOR.get(phase["posture"] or "", "#9aa0b4")
    return {
        "sim": True,  # tof_3d.html이 스파이크필터/배경적응 등 실제 센서용 지연 로직을 건너뛰게 하는 표시
        "tof1": {**frame, "distances_mm": grid1},
        "tof2": {**frame, "distances_mm": grid2},
        "posture": phase["posture"], "posture_raw": phase["posture"],
        "posture_ko": phase["posture_ko"], "posture_color": color, "posture_conf": 0.9,
        "risk": {"status": "anomaly" if phase["tof_risk"] in ("warning", "critical") else "normal",
                 "reason": phase["reason"], "y": None, "ratio": 0.0,
                 "posture": phase["posture"], "at": now_iso},
    }


def synthetic_tof_presence(bed_id: str) -> dict:
    phase = current_phase(bed_id)
    now_iso = datetime.fromtimestamp(time.time()).isoformat(timespec="milliseconds")
    in_bed = bool(phase and phase["in_bed"])
    occ = {"occupied": 40 if in_bed else 0, "detected": in_bed, "at": now_iso}
    return {"in_bed": in_bed, "since": now_iso, "tof1": occ, "tof2": occ,
            "baseline_set": {"tof1": True, "tof2": True}}


def synthetic_mmw_live(bed_id: str) -> dict:
    """실제 /mmw/live(및 /mmw/viz)와 동일한 모양 — 값만 시나리오 기준으로 채움. checking/fallen
    구간은 roomPosition()과 동일하게 진폭이 거의 0이라 x,y가 폴링마다 거의 안 바뀜(=포인트가
    안 움직임 → 낙상 의심 신호)."""
    phase = current_phase(bed_id)
    now_ts = time.time()
    if not phase:
        return {"targets": [], "ts": int(now_ts * 1000)}
    pos = _room_position(phase, now_ts, bed_id, phase["progress"])
    if pos is None:
        return {"targets": [], "ts": int(now_ts * 1000)}
    # 아주 짧은 시간차 위치를 다시 계산해 속도 벡터를 근사(진짜 물리 시뮬레이션이 아니라
    # roomPosition()의 흔들림+posFrom/posTo 이동 공식 자체를 미분한 것 — "그럴듯한 값"이면 충분).
    # walk처럼 progress로 실제 이동하는 phase도 있어 current_phase를 다시 불러 progress를 새로 구한다.
    phase_prev = current_phase(bed_id, now_ts - 0.2) or phase
    pos_prev = _room_position(phase_prev, now_ts - 0.2, bed_id, phase_prev["progress"])
    vx = round((pos[0] - pos_prev[0]) / 0.2, 3) if pos_prev else 0.0
    vy = round((pos[1] - pos_prev[1]) / 0.2, 3) if pos_prev else 0.0
    return {"targets": [{"tid": 1, "x": pos[0], "y": pos[1], "vx": vx, "vy": vy}],
            "ts": int(now_ts * 1000)}


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
