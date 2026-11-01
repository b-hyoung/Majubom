/* Client-side demo simulation for the fixed bed_sim_* patients (see server/dashboard_api.py
   DEMO_PATIENTS). Nothing here touches bed_01 or writes to the database — every value produced
   is synthetic and only ever rendered behind a visible "시뮬레이션" tag (see dashboard.js). */
(function (root) {
  'use strict';

  // Real 1인실(single-occupancy room) footprint and furniture layout, ported from the
  // reference design (Capstone_Design-MajuBom_Project- / data.jsx ROOM) — same geometry, in
  // meters. Only the mmWave sweep-beam / CSI expanding-ring "scanning" animation was dropped
  // (and CSI itself, since this project has no CSI sensor); everything else (bed, bedside,
  // table, door, bathroom, sensor markers, static coverage cones) is the same drawing.
  // bed_01's real position instead uses raw mmWave meters on a plain grid — we have no real
  // bed-to-sensor calibration yet to justify drawing it against this same room.
  var ROOM = {
    w: 4.6, h: 5.8,
    // headAtEnd: 머리(베개)가 bed.y+bed.h 쪽. 2026-10-12 사용자 확인: 화면 기준 왼쪽 아래가 발, 오른쪽이 머리.
    // 같은 날: 화면(시계 90°) 오른쪽·위쪽 벽에 빈틈 없이 붙도록 침대·센서·협탁을 y −0.95m, x −0.55m 이동.
    bed: { x: 0.05, y: 0.25, w: 1.0, h: 2.2, headAtEnd: true },
    bedside: { x: 1.15, y: 0.25, w: 0.5, h: 0.8 },
    table: { x: 2.6, y: 1.2, w: 0.8, h: 0.6 },
    door: { x: 3.2, y: 5.4, w: 1.0, h: 0.2 },
    bath: { x: 0.2, y: 4.4, w: 1.6, h: 1.3 },
    sensors: [
      // 2026-10-12 실제 설치에 맞춤: ToF 두 대가 침대 머리맡(tof2)·발치(tof1) 바깥에서 서로 침대 쪽을 본다(높이 1.2m).
      // 침대는 bed.y(발) ~ bed.y+bed.h(머리, 베개 쪽) — 같은 날 사용자 확인으로 머리/발을 바꿨다(이전엔 bed.y가 머리).
      // angle은 SVG 기준(90=아래=머리 쪽, 270=위=발 쪽), 시야 45°.
      { id: 'tof1', kind: 'ToF', subtype: 'foot', x: 0.55, y: 0.12, r: 2.4, angle: 90, fov: 45 },
      { id: 'tof2', kind: 'ToF', subtype: 'head', x: 0.55, y: 2.60, r: 2.4, angle: 270, fov: 45 },
      // 2026-10-12 실제 설치(사용자 확인): 발치 ToF(tof1) 바로 왼쪽 10cm, 높이 2.5m·하향 50°, 발치 ToF처럼 침대(머리) 쪽을 본다.
      // 현장 사진(10-12): 레이더 스탠드는 발치 ToF보다 침대가 붙은 벽(책상) 쪽 구석에 있다 → 방 좌표 -x(벽 쪽)로 10cm.
      // 화면(반시계 90° 회전)에서는 왼쪽 끝, 발치 ToF 바로 아래.
      // 구석 설치라 침대와 일직선이 아니다: 화면 기준 반시계 45°(방 좌표 angle 90→45, 화면에서 오른쪽 위=방 안쪽을 향함).
      { id: 'mmw-1', kind: 'mmWave', x: 0.45, y: 0.12, r: 7.0, angle: 45, fov: 120 }   // 시뮬레이션 그림 전용(실측은 dashboard.js 부스 평면도)
    ]
  };

  // 실측 mmWave 추적 좌표(m, 레이더 기준: x=좌우, y=정면 거리) → 위 ROOM 좌표(m, SVG 기준 y가 아래).
  // 레이더는 ROOM.sensors 의 mmw-1 위치·방향(angle, 0=오른쪽 90=아래)에 있다고 보고 회전·이동만 한다.
  // 2026-10-12 기준 방 배치 실측 보정 전이라 위치는 추정이다. 좌우가 거울처럼 뒤집혀 보이면 MMW_X_SIGN 을 -1로.
  var MMW_X_SIGN = 1;
  function mmwToRoom(x, y) {
    var s = null;
    for (var i = 0; i < ROOM.sensors.length; i++) if (ROOM.sensors[i].kind === 'mmWave') s = ROOM.sensors[i];
    if (!s || typeof x !== 'number' || typeof y !== 'number') return null;
    var a = s.angle * Math.PI / 180, xr = x * MMW_X_SIGN;
    return { x: s.x + y * Math.cos(a) - xr * Math.sin(a), y: s.y + y * Math.sin(a) + xr * Math.cos(a) };
  }

  var PHASES = {
    sleep: { name: '정상 수면', icon: '😴', dur: 30, posture: 'supine', postureLabel: '바로 누움', inBed: true,
      tofRisk: 'normal', speed: 0, sway: 0, stride: 0, cv: 0.10, freeze: 1.00, mmRisk: 'normal', level: 'normal',
      activity: 'inBed', pos: { x: 0.55, y: 1.75 } },
    toss: { name: '수면 중 뒤척임', icon: '💤', dur: 20, posture: 'tossing', postureLabel: '뒤척임', inBed: true,
      tofRisk: 'caution', speed: 0.02, sway: 0.01, stride: 0, cv: 0.22, freeze: 0.90, mmRisk: 'caution', level: 'caution',
      activity: 'inBed', pos: { x: 0.5, y: 1.05 } },
    edge: { name: '기상 준비 · 걸터앉음', icon: '🪑', dur: 12, posture: null, postureLabel: '걸터앉음', inBed: true,
      tofRisk: 'caution', speed: 0.01, sway: 0.02, stride: 0, cv: 0.15, freeze: 0.75, mmRisk: 'caution', level: 'caution',
      reason: null, activity: 'inBed', pos: { x: 0.95, y: 0.65 } },
    exit: { name: '침상 이탈 감지', icon: '🛏️', dur: 25, posture: null, postureLabel: '침상 이탈 중', inBed: false,
      tofRisk: 'warning', speed: 0.12, sway: 0.03, stride: 0.15, cv: 0.18, freeze: 0.60, mmRisk: 'warning', level: 'warning',
      reason: '침상 가장자리 이탈 감지', activity: 'outOfBed', pos: { x: 1.2, y: 1.9 } },
    checkingNormal: { name: '낙상 의심 · 교차검증 중', icon: '🔎', dur: 10, posture: null, postureLabel: '움직임 없음 · 확인 중', inBed: false,
      tofRisk: 'warning', speed: 0, sway: 0, stride: 0, cv: 0, freeze: 1.00, mmRisk: 'warning', level: 'warning',
      reason: '이탈 전 자세: 걸터앉음(정상) — 이탈 후 움직임 없음, AI 교차검증 중', activity: 'checking', pos: { x: 1.2, y: 1.9 } },
    checkingAbnormal: { name: '낙상 의심 · 교차검증 중', icon: '🔎', dur: 10, posture: null, postureLabel: '움직임 없음 · 확인 중', inBed: false,
      tofRisk: 'warning', speed: 0, sway: 0, stride: 0, cv: 0, freeze: 1.00, mmRisk: 'warning', level: 'warning',
      reason: '이탈 전 자세: 뒤척임(비정상) — 이탈 후 움직임 없음, AI 교차검증 중', activity: 'checking', pos: { x: 1.2, y: 1.9 } },
    walk: { name: '이탈 후 이동', icon: '🚶', dur: 20, posture: null, postureLabel: '보행 중', inBed: false,
      tofRisk: 'normal', speed: 0.86, sway: 0.02, stride: 0.62, cv: 0.11, freeze: 0.05, mmRisk: 'normal', level: 'normal',
      reason: null, activity: 'walking', posFrom: { x: 1.9, y: 3.0 }, posTo: { x: 3.6, y: 5.25 } },
    risk: { name: '낙상 위험 임박', icon: '⚠️', dur: 20, posture: null, postureLabel: '불안정 보행', inBed: false,
      tofRisk: 'warning', speed: 0.55, sway: 0.09, stride: 0.33, cv: 0.38, freeze: 0.34, mmRisk: 'warning', level: 'warning',
      reason: '보행 변동성 증가 — 낙상 전조 패턴', activity: 'walking', pos: { x: 2.8, y: 4.2 } },
    fall: { name: '낙상 감지됨', icon: '🚨', dur: 20, posture: null, postureLabel: '낙상 감지', inBed: false,
      tofRisk: 'critical', speed: 0, sway: 0.15, stride: 0, cv: 0.55, freeze: 0.98, mmRisk: 'critical', level: 'critical',
      reason: '바닥 근접 감지 — 낙상 의심, 즉시 확인 필요', activity: 'fallen', pos: { x: 2.85, y: 4.05 } }
  };

  // Each patient cycles through a different phase key sequence — durations differ, and only
  // bed_sim_fall's sequence ever reaches 'fall'. Repeating a key (e.g. gaitcaution's 'toss','toss')
  // is a cheap way to bias how much of the cycle a patient spends at that severity.
  var SCENARIOS = {
    bed_sim_calm:        { label: '수면 위주 · 안정',      keys: ['sleep', 'toss'] },
    bed_sim_bedexit:     { label: '침상 이탈 반복',        keys: ['sleep', 'edge', 'exit', 'checkingNormal', 'walk'] },
    bed_sim_gaitcaution: { label: '뒤척임 반복 · 주의',    keys: ['sleep', 'toss', 'toss'] },
    bed_sim_risk:        { label: '낙상 위험 임박 · 회복', keys: ['sleep', 'toss', 'edge', 'risk', 'toss'] },
    // 2026-10-12: 낙상(fall) 장면 제거 — 실측 낙상 감지를 시험할 때 시뮬레이션 낙상 팝업·자동 리포트가 섞이지 않게.
    bed_sim_fall:        { label: '이탈 후 움직임 없음 · 확인', keys: ['sleep', 'toss', 'exit', 'checkingAbnormal'] }
  };

  var FALL_BED_ID = 'bed_sim_fall';
  var TICK_MS = 2000;

  function seedFor(bedId) {
    var h = 0;
    for (var i = 0; i < bedId.length; i++) h = (h * 31 + bedId.charCodeAt(i)) >>> 0;
    return (h % 1000) / 1000 * Math.PI * 2;
  }

  function scenarioLength(scenario) {
    var total = 0;
    for (var i = 0; i < scenario.keys.length; i++) total += PHASES[scenario.keys[i]].dur;
    return total;
  }

  /** Which phase a patient is in at time `now` (ms) — deterministic, wall-clock based, so every
   * tab/tick sees the same state without needing to persist anything. */
  function scenarioState(bedId, now) {
    var scenario = SCENARIOS[bedId];
    if (!scenario) return null;
    var total = scenarioLength(scenario);
    var t = (now / 1000 + seedFor(bedId) * 10) % total;
    var acc = 0;
    for (var k = 0; k < scenario.keys.length; k++) {
      var key = scenario.keys[k];
      var dur = PHASES[key].dur;
      if (t < acc + dur) return { key: key, phase: PHASES[key], progress: (t - acc) / dur, index: k, count: scenario.keys.length, remaining: acc + dur - t };
      acc += dur;
    }
    var first = scenario.keys[0];
    return { key: first, phase: PHASES[first], progress: 0, index: 0, count: scenario.keys.length, remaining: PHASES[first].dur };
  }

  function wobble(v, t, amp, seed) { return Math.max(0, v + Math.sin(t * 0.7 + seed) * amp); }
  function round2(v) { return Math.round(v * 100) / 100; }

  function snapshot(bedId, now) {
    var state = scenarioState(bedId, now);
    if (!state) return null;
    var ph = state.phase;
    var t = now / 1000;
    var seed = seedFor(bedId);
    return {
      tof: {
        connected: true, ageMs: 0, lastSeen: now,
        posture: ph.posture, postureLabel: ph.postureLabel, confidence: 0.9,
        riskLevel: ph.tofRisk, riskLabel: null,
        headConnected: true, footConnected: true,
        inBed: ph.inBed, reason: ph.reason || null
      },
      mmwave: {
        connected: true, ageMs: 0, lastSeen: now,
        speed: round2(wobble(ph.speed, t, ph.speed > 0 ? 0.05 : 0.01, seed)),
        sway: round2(wobble(ph.sway, t, 0.01, seed + 1)),
        stride: round2(wobble(ph.stride, t, ph.stride > 0 ? 0.03 : 0, seed + 2)),
        cv: round2(wobble(ph.cv, t, 0.02, seed + 3)),
        freeze: round2(Math.min(1, wobble(ph.freeze, t, 0.02, seed + 4))),
        riskLevel: ph.mmRisk, targetId: null, position: null, metricsFresh: true,
        reason: ph.reason || null
      },
      level: ph.level,
      activity: ph.activity,
      roomPos: roomPosition(ph, state.progress, t, seed),
      phaseKey: state.key, phaseName: ph.name, phaseIcon: ph.icon,
      phaseIndex: state.index, phaseCount: state.count,
      phaseElapsedSec: Math.round(ph.dur * state.progress),
      phaseDurSec: ph.dur,
      scenarioLabel: (SCENARIOS[bedId] || {}).label || null
    };
  }

  /** Position in room meters, with a small continuous wobble (breathing/shifting in bed,
   * natural sway while walking) — not a sensor-scanning effect, just the person moving. */
  function roomPosition(ph, progress, t, seed) {
    var base = ph.pos;
    if (!base && ph.posFrom && ph.posTo) {
      base = { x: ph.posFrom.x + (ph.posTo.x - ph.posFrom.x) * progress, y: ph.posFrom.y + (ph.posTo.y - ph.posFrom.y) * progress };
    }
    if (!base) return null;
    var amp = ph.activity === 'walking' ? 0.05 : (ph.activity === 'fallen' || ph.activity === 'checking') ? 0.01 : 0.08;
    return { x: round2(base.x + Math.sin(t * 0.9 + seed) * amp), y: round2(base.y + Math.cos(t * 0.8 + seed) * amp * 0.6) };
  }

  /** A believable full day for each patient, told in a handful of readable blocks — not the
   * short demo cycle mechanically repeated (that produced hundreds of slivers for the faster
   * scenarios). Purely illustrative, always labeled as simulated. Hours are wall-clock hours;
   * the block set itself is fixed per patient so the timeline reads the same across reloads. */
  var DAILY_NARRATIVE = {
    bed_sim_calm: [
      { fromH: 3.2, toH: 3.6, key: 'toss' }
    ],
    bed_sim_bedexit: [
      { fromH: 0.9, toH: 1.0, key: 'edge' }, { fromH: 1.0, toH: 1.3, key: 'exit' },
      { fromH: 1.3, toH: 1.45, key: 'checkingNormal' }, { fromH: 1.45, toH: 1.7, key: 'walk' },
      { fromH: 14.2, toH: 14.3, key: 'edge' }, { fromH: 14.3, toH: 14.5, key: 'exit' },
      { fromH: 14.5, toH: 14.65, key: 'checkingNormal' }, { fromH: 14.65, toH: 14.9, key: 'walk' }
    ],
    bed_sim_gaitcaution: [
      { fromH: 2.0, toH: 2.4, key: 'toss' }, { fromH: 11.1, toH: 11.4, key: 'toss' },
      { fromH: 20.2, toH: 20.6, key: 'toss' }
    ],
    bed_sim_risk: [
      { fromH: 3.0, toH: 3.25, key: 'toss' }, { fromH: 3.25, toH: 3.35, key: 'edge' },
      { fromH: 3.35, toH: 3.7, key: 'risk' }, { fromH: 3.7, toH: 3.95, key: 'toss' }
    ],
    bed_sim_fall: [
      { fromH: 21.0, toH: 21.3, key: 'toss' }, { fromH: 21.3, toH: 21.55, key: 'exit' },
      { fromH: 21.55, toH: 22.0, key: 'checkingAbnormal' }
    ]
  };

  function timeline24h(bedId) {
    var blocks = DAILY_NARRATIVE[bedId];
    if (!blocks) return [];
    var daySec = 24 * 3600;
    var segments = [];
    var cursor = 0;
    blocks.slice().sort(function (a, b) { return a.fromH - b.fromH; }).forEach(function (b) {
      var startSec = Math.max(0, Math.round(b.fromH * 3600));
      var endSec = Math.min(daySec, Math.round(b.toH * 3600));
      if (startSec > cursor) segments.push({ startSec: cursor, endSec: startSec, key: 'sleep', level: 'normal', name: '정상' });
      segments.push({ startSec: startSec, endSec: endSec, key: b.key, level: PHASES[b.key].level, name: PHASES[b.key].name });
      cursor = endSec;
    });
    if (cursor < daySec) segments.push({ startSec: cursor, endSec: daySec, key: 'sleep', level: 'normal', name: '정상' });
    return segments;
  }

  /** Short fading trail behind the current position — computed by re-sampling the same
   * deterministic position function a few seconds into the past, so no accumulated state is
   * needed (unlike the reference's window.simPushTrail buffer). Shows where the person just
   * walked, which is real information — distinct from a device's own scanning animation. */
  function getTrail(bedId, now, points, stepMs) {
    points = points || 8; stepMs = stepMs || 350;
    var out = [];
    for (var i = points - 1; i >= 0; i--) {
      var snap = snapshot(bedId, now - i * stepMs);
      if (snap && snap.roomPos) out.push({ x: snap.roomPos.x, y: snap.roomPos.y, ageMs: i * stepMs, activity: snap.activity });
    }
    return out;
  }

  var RISK_NUM = { normal: 0, caution: 1, warning: 2, critical: 3 };

  /** Samples the same deterministic phase function at regular past intervals so the Trends tab
   * has something to draw for these frontend-only patients — real readings would arrive every
   * ~2-30s in practice, so this mirrors that cadence rather than the (much longer) scenario
   * cycle, which is why it looks like natural noisy variation rather than a repeated shape. */
  function recentSeries(bedId, points, stepSec) {
    if (!isSimulated(bedId)) return [];
    points = points || 40; stepSec = stepSec || 45;
    var now = Date.now();
    var out = [];
    for (var i = points - 1; i >= 0; i--) {
      var snap = snapshot(bedId, now - i * stepSec * 1000);
      if (!snap) continue;
      out.push({
        speed: snap.mmwave.speed, sway: snap.mmwave.sway,
        riskNum: RISK_NUM[snap.level] != null ? RISK_NUM[snap.level] : 0,
        level: snap.level
      });
    }
    return out;
  }

  function isSimulated(bedId) { return Object.prototype.hasOwnProperty.call(SCENARIOS, bedId); }

  var LEVEL_ORDER = { critical: 3, warning: 2, caution: 1, normal: 0 };
  function dominantLevel(a, b) {
    var av = LEVEL_ORDER[a] != null ? LEVEL_ORDER[a] : -1;
    var bv = LEVEL_ORDER[b] != null ? LEVEL_ORDER[b] : -1;
    if (av < 0 && bv < 0) return null;
    return av >= bv ? a : b;
  }

  /** onPhaseChange(bedId, snap) fires once per phase entry for every simulated patient — the
   * caller (dashboard.js) logs it to activity_log so the AI report has real chronological
   * "what led up to this" context, not just the current instant. onFall(bedId, snap) fires only
   * for FALL_BED_ID entering 'fall', separately from onPhaseChange, so the popup+report trigger
   * stays a distinct, deliberate action rather than piggybacking on the generic log. */
  function createEngine(onTick, onFall, onPhaseChange) {
    var lastPhase = {};
    var timer = null;

    function tick() {
      var now = Date.now();
      Object.keys(SCENARIOS).forEach(function (bedId) {
        var snap = snapshot(bedId, now);
        if (!snap) return;
        var prev = lastPhase[bedId];
        if (prev !== snap.phaseKey) {
          if (prev !== undefined && onPhaseChange) onPhaseChange(bedId, snap);
          if (bedId === FALL_BED_ID && snap.phaseKey === 'fall' && onFall) onFall(bedId, snap);
        }
        lastPhase[bedId] = snap.phaseKey;
      });
      if (onTick) onTick();
    }

    return {
      start: function () { tick(); timer = setInterval(tick, TICK_MS); },
      stop: function () { if (timer) clearInterval(timer); timer = null; }
    };
  }

  root.MajuSim = Object.freeze({
    isSimulated: isSimulated,
    getSnapshot: snapshot,
    getTimeline24h: timeline24h,
    getRecentSeries: recentSeries,
    getTrail: getTrail,
    dominantLevel: dominantLevel,
    createEngine: createEngine,
    FALL_BED_ID: FALL_BED_ID,
    ROOM: ROOM,
    mmwToRoom: mmwToRoom,
    patientIds: Object.keys(SCENARIOS)
  });
})(typeof window !== 'undefined' ? window : globalThis);
