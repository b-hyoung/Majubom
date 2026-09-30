(function () {
  'use strict';

  var THEME_KEY = 'majubom-theme';
  var CONN_BASE_KEY = 'majubom-conn-base';
  var CONN_TARGET_KEY = 'majubom-conn-target';

  var state = {
    patients: [],
    liveBedId: 'bed_01',
    selectedId: null,
    detail: null,
    detailCache: {},
    page: 'overview',
    ai: null,
    mmwTargetId: 'room_01',
    live: null,
  };
  var sensorClient = null;
  var simEngine = null;

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmt2(v) { return (typeof v === 'number' && Number.isFinite(v)) ? v.toFixed(2) : '—'; }

  function fmtTime(value) {
    if (value == null) return '—';
    var d = typeof value === 'number' ? new Date(value) : new Date(String(value));
    if (Number.isNaN(d.getTime())) return String(value);
    try {
      return d.toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
    } catch (e) { return d.toString(); }
  }

  function fmtAge(p) {
    var sexKo = p.sex === 'F' ? '여' : p.sex === 'M' ? '남' : null;
    if (p.age != null && sexKo) return p.age + '세 · ' + sexKo;
    if (p.age != null) return p.age + '세';
    if (sexKo) return sexKo;
    return '정보 없음';
  }

  function levelPill(level) {
    var map = {
      normal: ['정상', 'ok'], caution: ['주의', 'caution'], warning: ['경고', 'warning'],
      critical: ['위험', 'critical'], unknown: ['판정 대기', '']
    };
    var pair = map[level] || map.unknown;
    return '<span class="pill ' + pair[1] + '">' + pair[0] + '</span>';
  }

  function connPill(ok) {
    return ok ? '<span class="pill ok">연결됨</span>' : '<span class="pill">연결 안 됨</span>';
  }

  function riskCls(score) {
    if (score == null) return '';
    return score >= 0.85 ? 'critical' : score >= 0.6 ? 'warning' : score >= 0.3 ? 'caution' : 'ok';
  }

  async function api(path, opts) {
    var res = await fetch(path, Object.assign({ headers: { Accept: 'application/json' } }, opts || {}));
    var body = null;
    try { body = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok && !(body && typeof body === 'object' && 'success' in body)) {
      throw new Error('HTTP ' + res.status);
    }
    return body;
  }

  /* ---------------- Theme ---------------- */
  function applyThemeIcon() {
    var t = document.documentElement.getAttribute('data-theme');
    var knob = $('.theme-switch-knob', $('#themeToggle'));
    if (knob) knob.textContent = t === 'dark' ? '🌙' : '☀️';
    $('#themeToggle').setAttribute('aria-checked', String(t === 'dark'));
  }
  function wireThemeToggle() {
    applyThemeIcon();
    $('#themeToggle').addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-theme');
      var next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* ignore */ }
      applyThemeIcon();
    });
  }

  /* ---------------- Navigation ---------------- */
  function wireNav() {
    $all('nav button[data-page]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        state.page = btn.getAttribute('data-page');
        $all('nav button[data-page]').forEach(function (b) { b.setAttribute('aria-selected', String(b === btn)); });
        $all('.screen').forEach(function (s) { s.hidden = s.getAttribute('data-screen') !== state.page; });
      });
    });
  }

  /* ---------------- Patient list ---------------- */
  function patientRowStatus(p) {
    if (p.bed_id === state.liveBedId) {
      var live = state.live;
      if (!live) return null;
      return dominantOf(live.tof && live.tof.riskLevel, live.mmwave && live.mmwave.riskLevel,
        live.tof && live.tof.connected, live.mmwave && live.mmwave.connected);
    }
    if (window.MajuSim && window.MajuSim.isSimulated(p.bed_id)) {
      var snap = window.MajuSim.getSnapshot(p.bed_id, Date.now());
      return snap ? snap.level : null;
    }
    return null;
  }

  function dominantOf(tofLevel, mmwLevel, tofConnected, mmwConnected) {
    var a = tofConnected ? tofLevel : null;
    var b = mmwConnected ? mmwLevel : null;
    if (window.MajuSim) return window.MajuSim.dominantLevel(a, b);
    var order = { critical: 3, warning: 2, caution: 1, normal: 0 };
    var av = a && order[a] != null ? order[a] : -1;
    var bv = b && order[b] != null ? order[b] : -1;
    if (av < 0 && bv < 0) return null;
    return av >= bv ? a : b;
  }

  function patientRowHtml(p) {
    var active = p.bed_id === state.selectedId ? ' active' : '';
    var typeBadge = p.is_live ? '<span class="pill accent sm">LIVE</span>' :
      (p.is_simulated ? '<span class="pill info sm">SIM</span>' : '');
    var status = patientRowStatus(p);
    var statusBadge = status ? levelPill(status) : '';
    var meta = fmtAge(p) + (p.room ? ' · ' + p.room + '호' : '');
    return '<button class="patient-row' + active + '" data-patient-id="' + escapeHtml(p.bed_id) + '" type="button">' +
      '<span class="patient-row-name">' + escapeHtml(p.name || p.bed_id) + typeBadge + statusBadge + '</span>' +
      '<span class="patient-row-meta">' + escapeHtml(meta) + '</span>' +
      '</button>';
  }

  function renderPatientList(filterText) {
    var list = $('#patientList');
    var q = (filterText || '').trim();
    var items = state.patients;
    if (q) {
      var needle = q.toLowerCase();
      items = state.patients.filter(function (p) {
        return (p.name || '').toLowerCase().indexOf(needle) !== -1 ||
          (p.room || '').toLowerCase().indexOf(needle) !== -1 ||
          p.bed_id.toLowerCase().indexOf(needle) !== -1;
      });
    }
    if (items.length === 0) {
      list.innerHTML = '<div class="list-empty">검색 결과가 없습니다.</div>';
      return;
    }
    list.innerHTML = items.map(patientRowHtml).join('');
    $all('#patientList [data-patient-id]').forEach(function (el) {
      el.addEventListener('click', function () { selectPatient(el.getAttribute('data-patient-id')); });
    });
  }

  function wireSearch() {
    $('#patientSearch').addEventListener('input', function (ev) { renderPatientList(ev.target.value); });
  }

  async function loadPatients() {
    var data = await api('/api/dashboard/patients');
    if (!data || !data.success) return;
    state.patients = data.patients || [];
    state.liveBedId = data.live_bed_id || state.liveBedId;
    if (!state.selectedId) {
      state.selectedId = (state.patients[0] && state.patients[0].bed_id) || state.liveBedId;
    }
    renderPatientList($('#patientSearch').value);
  }

  async function selectPatient(bedId) {
    if (bedId === state.selectedId) return;
    state.selectedId = bedId;
    $('#mainContent').setAttribute('data-patient-id', bedId);
    renderPatientList($('#patientSearch').value);
    $('#reportModal').hidden = true;
    // raw 3D/포인트클라우드 뷰어가 이미 열려있는 채로 환자를 바꾸면(패널을 다시 안 열어도)
    // 새로 선택한 환자의 bed_id로 iframe을 다시 가리키게 한다 — 안 그러면 예전 환자 화면이
    // 계속 떠 있어서 "반응이 느리다"처럼 보인다.
    [$('#ovRawViewer'), $('#rawViewers')].forEach(function (box) {
      if (box && !box.hidden) refreshRawViewerSrc(box);
    });
    // A handful of fixed patients (live + demo) — boot() prefetches all of them, so switching
    // is normally instant with no "불러오는 중" flash. Only a patient we've truly never fetched
    // yet (e.g. one just added) falls back to showing a loading state while it fetches.
    var cached = state.detailCache[bedId];
    if (cached !== undefined) {
      state.detail = cached;
      renderAll();
      loadDetail(bedId).then(function () { if (state.selectedId === bedId) renderAll(); });
      return;
    }
    renderOverviewSensors();
    renderRiskBanner();
    renderDevices();
    $('#ovPatientHeader').innerHTML = '<div class="empty-state">불러오는 중…</div>';
    $('#ovEvents').innerHTML = '<div class="empty-state">불러오는 중…</div>';
    $('#ovRiskFactors').innerHTML = '<div class="empty-state">불러오는 중…</div>';
    $('#ovMedications').innerHTML = '<div class="empty-state">불러오는 중…</div>';
    $('#medicationsList').innerHTML = '<div class="empty-state">불러오는 중…</div>';
    await loadDetail(bedId);
    renderAll();
  }

  function prefetchAllDetails() {
    return Promise.all(state.patients.map(function (p) { return loadDetail(p.bed_id); }));
  }

  async function loadDetail(bedId) {
    var detail = null;
    try {
      var data = await api('/api/dashboard/patients/' + encodeURIComponent(bedId));
      detail = (data && data.success) ? data : null;
    } catch (e) { /* keep whatever was cached, if anything */ }
    if (detail) state.detailCache[bedId] = detail;
    if (bedId === state.selectedId) state.detail = detail || state.detailCache[bedId] || null;
    return detail;
  }

  /* ---------------- Overview ---------------- */
  function renderOverviewHeader() {
    var root = $('#ovPatientHeader');
    var d = state.detail;
    if (!d) { root.innerHTML = '<div class="empty-state">환자 정보를 불러오지 못했습니다.</div>'; return; }
    var p = d.patient;
    var mode = currentSensorMode();
    var initial = (p.name || p.bed_id || '?').trim().slice(0, 1) || '?';
    var badge = mode === 'live' ? '<span class="pill accent sm">LIVE 센서 연결</span>' :
      mode === 'sim' ? '<span class="pill info sm">시뮬레이션 진행 중</span>' :
      '<span class="pill sm">센서 미연결</span>';
    root.innerHTML =
      '<div class="patient-id">' +
      '<div class="avatar">' + escapeHtml(initial) + '</div>' +
      '<div>' +
      '<div class="patient-name">' + escapeHtml(p.name || p.bed_id) + badge + '</div>' +
      '<div class="patient-meta muted">' + escapeHtml(fmtAge(p) + (p.room ? ' · ' + p.room + '호' : '') + (p.diagnosis ? ' · ' + p.diagnosis : '')) + '</div>' +
      '</div></div>';
  }

  var SIM_TAG = '<span class="pill info sm sim-tag">시뮬레이션</span>';

  function renderTofCard(tof, mode) {
    if (mode === 'none') {
      return '<div class="card-h"><div class="card-title">ToF · 침상 자세</div></div>' +
        '<div class="sensor-empty">이 환자는 실시간 센서에 연결되어 있지 않습니다.</div>';
    }
    if (mode === 'live' && (!tof || !tof.connected)) {
      var age = (tof && tof.ageMs != null) ? Math.round(tof.ageMs / 1000) + '초 전' : '알 수 없음';
      return '<div class="card-h"><div class="card-title">ToF · 침상 자세</div><span class="pill">센서 연결 끊김</span></div>' +
        '<div class="sensor-empty">마지막 수신: ' + escapeHtml(age) + '</div>';
    }
    var inBedText = tof.inBed === true ? '침상 내' : tof.inBed === false ? '침상 이탈' : '판정 대기';
    var tag = mode === 'sim' ? SIM_TAG : '';
    return '<div class="card-h"><div class="card-title">ToF · 침상 자세</div>' + tag + levelPill(tof.riskLevel) + '</div>' +
      '<div class="sensor-metrics">' +
      '<div class="metric"><span class="metric-label">자세</span><span class="metric-value">' + escapeHtml(tof.postureLabel || '판정 중') + '</span></div>' +
      '<div class="metric"><span class="metric-label">재실</span><span class="metric-value">' + escapeHtml(inBedText) + '</span></div>' +
      '<div class="metric"><span class="metric-label">머리맡 센서</span><span class="metric-value">' + (tof.headConnected ? '연결됨' : '연결 안 됨') + '</span></div>' +
      '<div class="metric"><span class="metric-label">발치 센서</span><span class="metric-value">' + (tof.footConnected ? '연결됨' : '연결 안 됨') + '</span></div>' +
      '</div>' + (tof.reason ? '<div class="sensor-note muted">' + escapeHtml(tof.reason) + '</div>' : '');
  }

  function renderMmwCard(mmw, mode) {
    if (mode === 'none') {
      return '<div class="card-h"><div class="card-title">mmWave · 보행</div></div>' +
        '<div class="sensor-empty">이 환자는 실시간 센서에 연결되어 있지 않습니다.</div>';
    }
    if (mode === 'live' && (!mmw || !mmw.connected)) {
      var age = (mmw && mmw.ageMs != null) ? Math.round(mmw.ageMs / 1000) + '초 전' : '알 수 없음';
      return '<div class="card-h"><div class="card-title">mmWave · 보행</div><span class="pill">센서 연결 끊김</span></div>' +
        '<div class="sensor-empty">마지막 수신: ' + escapeHtml(age) + '</div>';
    }
    var tag = mode === 'sim' ? SIM_TAG : '';
    // 잠정: 개인 기준이 생기기 전 입원 기록·문헌 기대 범위와 비교한 판정(정식 알람 아님)
    var provisional = mmw.provisional ? '<span class="pill sm" title="개인 보행 기록 5건 전 — 문헌 기대 범위와 비교한 잠정 판정">잠정</span>' : '';
    return '<div class="card-h"><div class="card-title">mmWave · 보행</div>' + tag + provisional + levelPill(mmw.riskLevel) + '</div>' +
      '<div class="sensor-metrics">' +
      '<div class="metric"><span class="metric-label">속도</span><span class="num metric-value">' + fmt2(mmw.speed) + '<span class="unit">m/s</span></span></div>' +
      '<div class="metric"><span class="metric-label">흔들림</span><span class="num metric-value">' + fmt2(mmw.sway) + '</span></div>' +
      '<div class="metric"><span class="metric-label">보폭</span><span class="num metric-value">' + fmt2(mmw.stride) + '<span class="unit">m</span></span></div>' +
      '<div class="metric"><span class="metric-label">속도 변동성</span><span class="num metric-value">' + fmt2(mmw.cv) + '</span></div>' +
      '</div>' + (mmw.reason ? '<div class="sensor-note muted">' + escapeHtml(mmw.reason) + '</div>' : '');
  }

  /** live snapshot for bed_01, simulated snapshot for a bed_sim_* demo patient, or null. */
  function currentSensorMode() {
    if (state.selectedId === state.liveBedId) return 'live';
    if (window.MajuSim && window.MajuSim.isSimulated(state.selectedId)) return 'sim';
    return 'none';
  }

  function currentSnapshot(mode) {
    if (mode === 'live') return state.live;
    if (mode === 'sim') return window.MajuSim ? window.MajuSim.getSnapshot(state.selectedId, Date.now()) : null;
    return null;
  }

  function renderOverviewSensors() {
    var mode = currentSensorMode();
    var snap = currentSnapshot(mode);
    $('#ovTof').innerHTML = renderTofCard(snap && snap.tof, mode);
    $('#ovMmw').innerHTML = renderMmwCard(snap && snap.mmwave, mode);
    renderFloorplan(mode, snap);
  }

  var LEVEL_COLOR_VAR = { normal: '--ok', caution: '--caution', warning: '--warning', critical: '--critical' };
  var FLOORPLAN_PX = 44; // px per meter

  /** Static 1인실 layout — bed/bedside/table/door/bathroom/sensor markers and their coverage
   * cones — ported from the reference design's RoomViz.jsx geometry, minus the mmWave sweep
   * beam and CSI expanding-ring animations (that "device is scanning" motion is the one thing
   * that was asked to be left out; the room drawing itself, sensor icons and coverage shading
   * are the same). Built once per (mode, patient); the moving parts are separate <g> groups
   * filled in by renderPatientMarkerSvg()/renderTrailSvg() so this never gets rebuilt on tick. */
  function buildSimRoomSvg(room, PX) {
    var W = (room.w * PX).toFixed(1), H = (room.h * PX).toFixed(1);
    var bath = room.bath, bed = room.bed, bedside = room.bedside, table = room.table, door = room.door;
    var byKind = function (kind, subtype) {
      for (var i = 0; i < room.sensors.length; i++) {
        var s = room.sensors[i];
        if (s.kind === kind && (!subtype || s.subtype === subtype)) return s;
      }
      return null;
    };
    var tofCeil = byKind('ToF', 'ceiling'), tofBed = byKind('ToF', 'under-bed'), mmw = byKind('mmWave');

    function cone(s, fillId) {
      if (!s) return '';
      var half = (s.fov / 2) * Math.PI / 180, dir = s.angle * Math.PI / 180;
      var cx = s.x * PX, cy = s.y * PX, r = s.r * PX;
      var p1x = cx + Math.cos(dir - half) * r, p1y = cy + Math.sin(dir - half) * r;
      var p2x = cx + Math.cos(dir + half) * r, p2y = cy + Math.sin(dir + half) * r;
      var large = s.fov > 180 ? 1 : 0;
      return '<path d="M' + cx.toFixed(1) + ',' + cy.toFixed(1) + ' L' + p1x.toFixed(1) + ',' + p1y.toFixed(1) +
        ' A' + r.toFixed(1) + ',' + r.toFixed(1) + ' 0 ' + large + ' 1 ' + p2x.toFixed(1) + ',' + p2y.toFixed(1) +
        ' Z" fill="url(#' + fillId + ')"/>';
    }
    function marker(s) {
      if (!s) return '';
      var color = s.kind === 'ToF' ? 'var(--accent)' : 'var(--info)';
      var arrow = s.subtype === 'ceiling'
        ? '<g stroke="' + color + '" stroke-width="1" fill="none"><line x1="0" y1="-3.2" x2="0" y2="3.2"/><polyline points="-2,1.3 0,3.2 2,1.3"/></g>' : '';
      return '<g transform="translate(' + (s.x * PX).toFixed(1) + ',' + (s.y * PX).toFixed(1) + ')">' +
        '<circle r="6" fill="var(--bg-elev-1)" stroke="' + color + '" stroke-width="1.3"/>' +
        '<circle r="2.3" fill="' + color + '"/>' + arrow + '</g>';
    }

    var pad = 0.15;
    var fx = (bed.x - pad) * PX, fy = (bed.y - pad) * PX, fw = (bed.w + pad * 2) * PX, fh = (bed.h + pad * 2) * PX;

    return '<svg class="floorplan-svg floorplan-room-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="병실 평면도(시뮬레이션)">' +
      '<defs>' +
      '<pattern id="fpGrid" width="18" height="18" patternUnits="userSpaceOnUse"><path d="M18 0L0 0 0 18" fill="none" stroke="var(--line-faint)" stroke-width="0.5"/></pattern>' +
      '<radialGradient id="fpTofCone" cx="50%" cy="0%" r="100%"><stop offset="0%" stop-color="var(--accent)" stop-opacity="0.28"/><stop offset="90%" stop-color="var(--accent)" stop-opacity="0.02"/></radialGradient>' +
      '<radialGradient id="fpMmwCone" cx="50%" cy="0%" r="100%"><stop offset="0%" stop-color="var(--info)" stop-opacity="0.24"/><stop offset="90%" stop-color="var(--info)" stop-opacity="0.02"/></radialGradient>' +
      '</defs>' +
      '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="url(#fpGrid)"/>' +
      '<rect x="0.5" y="0.5" width="' + (W - 1) + '" height="' + (H - 1) + '" fill="none" stroke="var(--line-strong)" stroke-width="1.2"/>' +
      '<rect x="' + (bath.x * PX).toFixed(1) + '" y="' + (bath.y * PX).toFixed(1) + '" width="' + (bath.w * PX).toFixed(1) + '" height="' + (bath.h * PX).toFixed(1) + '" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="1" stroke-dasharray="3 3"/>' +
      '<text x="' + ((bath.x + bath.w / 2) * PX).toFixed(1) + '" y="' + ((bath.y + bath.h / 2) * PX).toFixed(1) + '" text-anchor="middle" dominant-baseline="middle" font-size="8" fill="var(--fg-faint)" font-family="var(--font-mono)">화장실</text>' +
      '<rect x="' + (door.x * PX).toFixed(1) + '" y="' + (door.y * PX).toFixed(1) + '" width="' + (door.w * PX).toFixed(1) + '" height="3" fill="var(--bg)" stroke="var(--line-strong)"/>' +
      '<path d="M' + (door.x * PX).toFixed(1) + ',' + (door.y * PX).toFixed(1) + ' a' + (door.w * PX).toFixed(1) + ',' + (door.w * PX).toFixed(1) + ' 0 0 0 ' + (door.w * PX).toFixed(1) + ',-' + (door.w * PX).toFixed(1) + '" fill="none" stroke="var(--line)" stroke-width="0.7" stroke-dasharray="2 2"/>' +
      '<text x="' + (door.x * PX - 10).toFixed(1) + '" y="' + (door.y * PX - 4).toFixed(1) + '" font-size="8" fill="var(--fg-faint)" font-family="var(--font-mono)">문</text>' +
      cone(tofBed, 'fpTofCone') + cone(mmw, 'fpMmwCone') +
      '<g>' +
      '<rect x="' + fx.toFixed(1) + '" y="' + fy.toFixed(1) + '" width="' + fw.toFixed(1) + '" height="' + fh.toFixed(1) + '" rx="6" fill="var(--accent)" opacity="0.05"/>' +
      '<rect x="' + fx.toFixed(1) + '" y="' + fy.toFixed(1) + '" width="' + fw.toFixed(1) + '" height="' + fh.toFixed(1) + '" rx="6" fill="none" stroke="var(--accent)" stroke-width="0.8" stroke-dasharray="4 4" opacity="0.4"/>' +
      '</g>' +
      '<rect x="' + (bed.x * PX).toFixed(1) + '" y="' + (bed.y * PX).toFixed(1) + '" width="' + (bed.w * PX).toFixed(1) + '" height="' + (bed.h * PX).toFixed(1) + '" rx="5" fill="var(--bg-elev-1)" stroke="var(--line-strong)" stroke-width="1.1"/>' +
      '<rect x="' + ((bed.x + 0.1) * PX).toFixed(1) + '" y="' + ((bed.y + 0.1) * PX).toFixed(1) + '" width="' + ((bed.w - 0.2) * PX).toFixed(1) + '" height="' + (0.28 * PX).toFixed(1) + '" rx="3" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="0.5"/>' +
      '<text x="' + ((bed.x + bed.w / 2) * PX).toFixed(1) + '" y="' + ((bed.y + bed.h) * PX - 4).toFixed(1) + '" text-anchor="middle" font-size="7.5" fill="var(--fg-faint)" font-family="var(--font-mono)">BED</text>' +
      '<rect x="' + (bedside.x * PX).toFixed(1) + '" y="' + (bedside.y * PX).toFixed(1) + '" width="' + (bedside.w * PX).toFixed(1) + '" height="' + (bedside.h * PX).toFixed(1) + '" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="0.7"/>' +
      '<rect x="' + (table.x * PX).toFixed(1) + '" y="' + (table.y * PX).toFixed(1) + '" width="' + (table.w * PX).toFixed(1) + '" height="' + (table.h * PX).toFixed(1) + '" rx="3" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="0.7"/>' +
      marker(tofCeil) + marker(tofBed) + marker(mmw) +
      '<g id="ovFloorplanTrail"></g>' +
      '<g id="ovFloorplanMarker" class="floorplan-marker"></g>' +
      '</svg>';
  }

  /** The person indicator: concentric circles while in/near bed, a flattened "fallen" shape with
   * a label once down. Its own gentle scale/position comes from the continuous roomPos math
   * (simulation.js) — this only decides which shape family to draw for the current activity. */
  function renderPatientMarkerSvg(snap) {
    var color = 'var(' + (LEVEL_COLOR_VAR[snap.level] || '--ok') + ')';
    if (snap.activity === 'fallen') {
      return '<ellipse rx="20" ry="9" fill="' + color + '" opacity="0.22"/>' +
        '<ellipse rx="15" ry="6.5" fill="' + color + '" opacity="0.5"/>' +
        '<ellipse rx="10" ry="4.5" fill="' + color + '"/>' +
        '<text y="-15" text-anchor="middle" font-size="8" font-weight="700" fill="' + color + '" font-family="var(--font-mono)">낙상</text>';
    }
    if (snap.activity === 'inBed') {
      return '<circle r="10" fill="' + color + '" opacity="0.16"/><circle r="7" fill="' + color + '" opacity="0.4"/><circle r="4.5" fill="' + color + '"/>';
    }
    if (snap.activity === 'checking') {
      return '<circle r="14" fill="none" stroke="' + color + '" stroke-width="2" opacity="0.55" class="marker-pulse-ring"/>' +
        '<circle r="8" fill="' + color + '" opacity="0.35"/><circle r="5" fill="' + color + '"/>' +
        '<text y="3" text-anchor="middle" font-size="9" font-weight="800" fill="#fff">?</text>';
    }
    return '<circle r="12" fill="' + color + '" opacity="0.16"/><circle r="8" fill="' + color + '" opacity="0.4"/><circle r="5" fill="' + color + '"/>';
  }

  /** Fading trail of the last ~2.5s of positions — shows where the person actually walked,
   * which is real information (unlike a sensor's own scanning animation). */
  function renderTrailSvg(trail, PX) {
    if (!trail || trail.length < 2) return '';
    var segs = [];
    for (var i = 1; i < trail.length; i++) {
      var a = trail[i - 1], b = trail[i];
      var op = Math.max(0, 0.5 - (b.ageMs / 3000) * 0.5);
      if (op < 0.02) continue;
      var color = b.activity === 'fallen' ? 'var(--critical)' : 'var(--warning)';
      segs.push('<line x1="' + (a.x * PX).toFixed(1) + '" y1="' + (a.y * PX).toFixed(1) + '" x2="' + (b.x * PX).toFixed(1) + '" y2="' + (b.y * PX).toFixed(1) +
        '" stroke="' + color + '" stroke-width="1.4" stroke-linecap="round" opacity="' + op.toFixed(2) + '"/>');
    }
    return segs.join('');
  }

  /** Builds the static room/grid once per (mode, patient) and afterwards only moves the dot's
   * cx/cy/fill on the SAME <circle> element, so the CSS transition on .floorplan-dot can
   * actually animate it — replacing an element every tick would just teleport it instead. */
  function renderFloorplan(mode, snap) {
    var sub = $('#ovFloorplanSub');
    var el = $('#ovFloorplan');
    var wantBase = mode + ':' + state.selectedId;
    if (mode === 'none') {
      sub.textContent = '';
      el.innerHTML = '<div class="empty-state">이 환자는 실시간 위치 추정을 사용할 수 없습니다.</div>';
      el.dataset.base = '';
      return;
    }
    if (mode === 'sim') {
      sub.textContent = '1인실 평면도 (실측 배치 기준) · 시뮬레이션 위치 — 실측 좌표 아님';
      var room = window.MajuSim ? window.MajuSim.ROOM : null;
      if (!room) { el.innerHTML = '<div class="empty-state">위치 데이터가 없습니다.</div>'; el.dataset.base = ''; return; }
      if (el.dataset.base !== wantBase) {
        el.dataset.base = wantBase;
        el.innerHTML = buildSimRoomSvg(room, FLOORPLAN_PX) +
          '<div class="floorplan-legend">' +
          '<span><i class="dot" style="color:var(--accent)"></i>ToF</span>' +
          '<span><i class="dot" style="color:var(--info)"></i>mmWave</span>' +
          '</div>';
      }
      if (snap && snap.roomPos) {
        var marker = document.getElementById('ovFloorplanMarker');
        if (marker) {
          marker.style.transform = 'translate(' + (snap.roomPos.x * FLOORPLAN_PX).toFixed(1) + 'px,' + (snap.roomPos.y * FLOORPLAN_PX).toFixed(1) + 'px)';
          marker.innerHTML = renderPatientMarkerSvg(snap);
        }
        var trailEl = document.getElementById('ovFloorplanTrail');
        if (trailEl && window.MajuSim) {
          trailEl.innerHTML = renderTrailSvg(window.MajuSim.getTrail(state.selectedId, Date.now()), FLOORPLAN_PX);
        }
      }
      return;
    }
    // live: raw mmWave meters on a plain grid — no room/bed drawn, since there's no verified
    // sensor-to-room calibration yet (see server/site/DASHBOARD_WORK.md) and drawing one would
    // imply a precision we don't have.
    sub.textContent = 'mmWave 원시 좌표 (m) · 방 배치 보정 전';
    var pos = snap && snap.mmwave && snap.mmwave.position;
    if (el.dataset.base !== wantBase) {
      el.dataset.base = wantBase;
      var vb = 160;
      el.innerHTML =
        '<svg class="floorplan-svg" viewBox="0 0 ' + vb + ' ' + vb + '" role="img" aria-label="mmWave 원시 좌표">' +
        '<line class="floorplan-grid-line" x1="' + (vb / 2) + '" y1="0" x2="' + (vb / 2) + '" y2="' + vb + '"/>' +
        '<line class="floorplan-grid-line" x1="0" y1="' + (vb / 2) + '" x2="' + vb + '" y2="' + (vb / 2) + '"/>' +
        '<text class="floorplan-axis-label" x="4" y="10">+y</text><text class="floorplan-axis-label" x="4" y="' + (vb - 4) + '">-y</text>' +
        '<text class="floorplan-axis-label" x="' + (vb - 16) + '" y="' + (vb / 2 - 4) + '">+x</text>' +
        '<circle class="floorplan-dot" id="ovFloorplanDot" cx="-99" cy="-99" r="6"><title id="ovFloorplanDotTitle"></title></circle>' +
        '</svg>' +
        '<div class="muted" id="ovFloorplanNote" style="font-size:10.5px;margin-top:6px;min-height:1.2em"></div>';
    }
    if (!pos || pos.x == null || pos.y == null) return;
    var span = 4; // +/- meters shown
    var vb2 = 160;
    var px = vb2 / 2 + Math.max(-span, Math.min(span, pos.x)) / span * (vb2 / 2 - 10);
    var py = vb2 / 2 + Math.max(-span, Math.min(span, pos.y)) / span * (vb2 / 2 - 10);
    var lvl = dominantOf(snap.tof && snap.tof.riskLevel, snap.mmwave && snap.mmwave.riskLevel, true, true);
    // A locked target sitting still still has a real (x,y) — that's not wrong — but plotting it
    // exactly like an actively-walking reading was misleading. Speed is the only walking signal
    // data.js exposes here, so use it to tell the two states apart visually.
    var walking = typeof snap.mmwave.speed === 'number' && snap.mmwave.speed > 0.05;
    var dot2 = document.getElementById('ovFloorplanDot');
    if (dot2) {
      dot2.setAttribute('cx', px.toFixed(1));
      dot2.setAttribute('cy', py.toFixed(1));
      dot2.setAttribute('fill', 'var(' + (LEVEL_COLOR_VAR[lvl] || '--accent') + ')');
      dot2.setAttribute('opacity', walking ? '1' : '0.45');
      var title2 = document.getElementById('ovFloorplanDotTitle');
      if (title2) title2.textContent = (walking ? '실시간 위치' : '정지 상태 추정 위치') + ' · x=' + pos.x + 'm, y=' + pos.y + 'm';
    }
    var noteEl = document.getElementById('ovFloorplanNote');
    if (noteEl) noteEl.textContent = walking ? '' : '현재 보행 중이 아니며, 마지막으로 잠긴 위치를 정지 상태로 표시하고 있습니다.';
  }

  function eventRowHtml(e) {
    var level = e.level || 'info';
    var note = e.note ? '<div class="event-note">' + escapeHtml(e.note) + '</div>' : '';
    return '<div class="event-row">' +
      '<span class="event-time mono">' + escapeHtml(fmtTime(e.ts || e.timestamp)) + '</span>' +
      '<span class="dot ' + escapeHtml(level) + '"></span>' +
      '<span class="event-title">' + escapeHtml(e.title || e.event_type || '이벤트') + '</span>' +
      note +
      '</div>';
  }

  function todayIsoDate() {
    var d = new Date();
    return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
  }

  // 오늘 이벤트는 전부, 이전 이벤트는 기본값으로 최근 7일치만 남긴다(그 이전은 날짜를
  // 직접 골라야 보임 — /patients/<id>/events?date= 로 서버에서 별도 조회).
  function splitEventsByDay(events) {
    var todayStr = new Date().toDateString();
    var cutoff = Date.now() - 7 * 24 * 3600 * 1000;
    var today = [], past = [];
    events.forEach(function (e) {
      var d = new Date(String(e.ts || e.timestamp));
      if (Number.isNaN(d.getTime())) return;
      if (d.toDateString() === todayStr) today.push(e);
      else if (d.getTime() >= cutoff) past.push(e);
    });
    return { today: today, past: past };
  }

  function eventTocHtml(pastEvents) {
    // 이전 이벤트를 날짜별로 묶어 AI 리포트 목록처럼 목차 형태로 보여준다.
    var groups = [], byDate = {};
    pastEvents.forEach(function (e) {
      var d = new Date(String(e.ts || e.timestamp));
      var key = Number.isNaN(d.getTime()) ? '날짜 미상' :
        d.toLocaleDateString('ko-KR', { month: '2-digit', day: '2-digit', weekday: 'short' });
      if (!byDate[key]) { byDate[key] = []; groups.push(key); }
      byDate[key].push(e);
    });
    return groups.map(function (key) {
      return '<div class="event-toc-date">' + escapeHtml(key) + '</div>' +
        byDate[key].map(eventRowHtml).join('');
    }).join('');
  }

  // 당일 이벤트는 고정 크기의 스크롤 로그 박스에 오래된 것이 위 · 새 이벤트가 아래에 쌓이고,
  // 이전 이벤트는 기본 최근 7일 목차 + 날짜 선택 input(고르면 그 날짜만 서버에서 다시 불러옴).
  function renderEvents(events) {
    events = events || [];
    var bedId = state.selectedId || '';
    var wrapOpen = '<div class="events-wrap" data-bed="' + escapeHtml(bedId) + '">';
    if (events.length === 0) {
      return wrapOpen + '<div class="empty-state">최근 기록된 이벤트가 없습니다.</div></div>';
    }
    var split = splitEventsByDay(events.slice(0, 60));
    var todayAsc = split.today.slice().reverse(); // 오래된 것 위 → 새 것 아래
    var html = wrapOpen;
    html += '<div class="event-log-label">오늘의 이벤트<span class="event-log-count">' +
      todayAsc.length + '건</span></div>' +
      '<div class="event-log-box">' + (
        todayAsc.length ? todayAsc.map(eventRowHtml).join('') :
          '<div class="event-log-empty">오늘 기록된 이벤트가 없습니다.</div>'
      ) + '</div>';
    html += '<div class="event-log-label"><span class="event-past-label">이전 이벤트 (최근 7일)</span>' +
      '<input type="date" class="event-date-pick" max="' + todayIsoDate() + '" title="날짜를 고르면 그 날짜의 이벤트만 봅니다"></div>' +
      '<div class="events-list">' + (
        split.past.length ? eventTocHtml(split.past) :
          '<div class="empty-state">최근 7일간 기록된 이벤트가 없습니다.</div>'
      ) + '</div>';
    html += '</div>';
    return html;
  }

  // "이전 이벤트" 날짜 선택 — 고른 날짜(또는 초기화 시 기본 7일)를 서버에서 다시 조회해
  // 그 events-wrap 안의 목록·라벨만 갱신한다(오늘 로그 박스는 건드리지 않음).
  document.addEventListener('change', function (ev) {
    var inp = ev.target.closest ? ev.target.closest('.event-date-pick') : null;
    if (!inp) return;
    var wrap = inp.closest('.events-wrap');
    var bedId = wrap && wrap.getAttribute('data-bed');
    var list = wrap && wrap.querySelector('.events-list');
    var label = wrap && wrap.querySelector('.event-past-label');
    if (!bedId || !list) return;
    var dateVal = inp.value;
    if (label) label.textContent = dateVal ? (dateVal + ' 이벤트') : '이전 이벤트 (최근 7일)';
    list.innerHTML = '<div class="empty-state">불러오는 중…</div>';
    api('/api/dashboard/patients/' + encodeURIComponent(bedId) + '/events' +
      (dateVal ? ('?date=' + encodeURIComponent(dateVal)) : ''))
      .then(function (res) {
        var evs = (res && res.success && res.events) || [];
        list.innerHTML = evs.length ? eventTocHtml(evs) :
          '<div class="empty-state">' + (dateVal ? '이 날짜엔' : '최근 7일간') + ' 기록된 이벤트가 없습니다.</div>';
      })
      .catch(function () { list.innerHTML = '<div class="empty-state">불러오지 못했습니다.</div>'; });
  });

  // 위 렌더링 직후 호출 — 오늘 로그 박스를 항상 최신(맨 아래)으로 스크롤한다.
  function scrollEventLogToBottom(containerEl) {
    var box = containerEl && containerEl.querySelector('.event-log-box');
    if (box) box.scrollTop = box.scrollHeight;
  }

  var LEVEL_LABEL = { caution: '주의', warning: '경고', critical: '위험' };

  function renderRiskBanner() {
    var el = $('#ovRiskBanner');
    var mode = currentSensorMode();
    var snap = currentSnapshot(mode);
    var level = snap ? dominantOf(snap.tof && snap.tof.riskLevel, snap.mmwave && snap.mmwave.riskLevel, true, true) : null;
    if (mode === 'none' || !level || level === 'normal') { el.hidden = true; el.innerHTML = ''; return; }
    var reason = (snap.mmwave && snap.mmwave.reason) || (snap.tof && snap.tof.reason) || null;
    if (reason && snap.activity === 'checking' && snap.phaseDurSec) {
      reason += ' (' + Math.min(snap.phaseElapsedSec, snap.phaseDurSec) + '/' + snap.phaseDurSec + '초)';
    }
    var simNote = mode === 'sim' ? ' · 시뮬레이션 데이터' : '';
    el.hidden = false;
    el.innerHTML = '<div class="risk-banner risk-banner-' + escapeHtml(level) + '">' +
      '<strong>' + escapeHtml(LEVEL_LABEL[level] || level) + ' 신호 감지</strong>' +
      '<span>' + escapeHtml((reason || (snap.phaseName ? snap.phaseName + ' 상태' : '위험 신호가 감지되었습니다')) + simNote) + '</span>' +
      '<button class="btn sm" id="ovRiskBannerAction" type="button">AI 리포트 확인</button>' +
      '</div>';
    var btn = $('#ovRiskBannerAction');
    if (btn) btn.addEventListener('click', function () { $('nav button[data-page="reports"]').click(); });
  }

  function fmtHour(sec) { var h = Math.floor(sec / 3600); return (h < 10 ? '0' : '') + h + ':00'; }

  function renderTimeline24h() {
    var el = $('#ovTimeline');
    var sub = $('#ovTimelineSub');
    var mode = currentSensorMode();
    if (mode === 'sim' && window.MajuSim) {
      var segs = window.MajuSim.getTimeline24h(state.selectedId);
      var daySec = 24 * 3600;
      sub.textContent = '시뮬레이션 시나리오를 24시간에 반복 배치한 예시입니다.';
      var bars = segs.map(function (s) {
        var widthPct = (s.endSec - s.startSec) / daySec * 100;
        return '<div class="timeline-seg timeline-' + escapeHtml(s.level) + '" style="width:' + widthPct.toFixed(3) + '%" title="' + escapeHtml(fmtHour(s.startSec) + ' ' + s.name) + '"></div>';
      }).join('');
      el.innerHTML = '<div class="timeline-bar">' + bars + '</div>' +
        '<div class="timeline-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>' +
        '<div class="timeline-legend">' +
        '<span><i class="dot normal"></i>정상</span><span><i class="dot caution"></i>주의</span>' +
        '<span><i class="dot warning"></i>경고</span><span><i class="dot critical"></i>위험</span></div>';
      return;
    }
    var events = (state.detail && state.detail.events) || [];
    sub.textContent = mode === 'live' ? '실제 센서 이벤트 기록 기반' : '';
    if (!events.length) { el.innerHTML = '<div class="empty-state">최근 24시간 동안 기록된 이벤트가 없습니다.</div>'; return; }
    el.innerHTML = renderEvents(events);
    scrollEventLogToBottom(el);
  }

  var SEVERITY_LABEL = { high: '높음', medium: '중간', low: '낮음' };
  var SEVERITY_CLASS = { high: 'warning', medium: 'caution', low: '' };

  function renderRiskFactors(list) {
    if (!list || !list.length) return '<div class="empty-state">등록된 위험 인자가 없습니다.</div>';
    return '<div class="factor-list">' + list.map(function (f) {
      var sev = f.severity || 'low';
      var pill = '<span class="pill ' + (SEVERITY_CLASS[sev] || '') + ' sm">' + escapeHtml(SEVERITY_LABEL[sev] || sev) + '</span>';
      return '<div class="factor-row">' + pill +
        '<div><div class="factor-label">' + escapeHtml(f.factor || f.label || '') + '</div>' +
        (f.notes ? '<div class="factor-note muted">' + escapeHtml(f.notes) + '</div>' : '') + '</div></div>';
    }).join('') + '</div>';
  }

  function renderMedications(list) {
    if (!list || !list.length) return '<div class="empty-state">등록된 투약 정보가 없습니다.</div>';
    return '<div class="med-list">' + list.map(function (m) {
      return '<div class="med-row">' +
        '<div class="med-main"><span class="med-name">' + escapeHtml(m.name || '') + '</span>' +
        '<span class="muted mono" style="font-size:11px">' + escapeHtml(m.frequency || '') + '</span></div>' +
        (m.notes ? '<div class="med-note muted">' + escapeHtml(m.notes) + '</div>' : '') + '</div>';
    }).join('') + '</div>';
  }

  var SEVERITY_KO = { none: '치매 기록 없음', cognitive_impairment: '인지저하', mild: '경도 치매', moderate: '중등도 치매',
    severe: '중증 치매', unknown: '치매(중증도 기록 없음)' };
  var AMBULATION_KO = { independent: '독립 보행', assisted: '보조 필요', walker: '보행기 사용',
    wheelchair_user: '휠체어 사용(걷는지 기록 없음)', non_ambulatory: '보행 불가(와상 등)', unknown: '보행 상태 기록 없음' };

  // gait_prior.py 요약(/patients/<id>의 gait_prior). 숫자는 서버가 계산한 값만 표시한다.
  function renderGaitPrior(summary) {
    var button = '<button class="btn sm" id="gaitPriorBuild" type="button">입원 기록으로 계산</button>';
    if (!summary) {
      return '<div class="empty-state">아직 계산하지 않았습니다. 나이·키·진단·과거 기록을 입력한 뒤 계산하세요.</div>' + button;
    }
    var rows = [];
    if (summary.not_applicable) {
      rows.push('<div class="factor-row"><span class="pill sm">계산 안 함</span><div class="factor-label">' +
        escapeHtml(summary.not_applicable) + '</div></div>');
    } else if (summary.speed_expected_mps) {
      var s = summary.speed_expected_mps;
      rows.push('<div class="factor-row"><span class="pill sm">보행속도</span><div><div class="factor-label">' +
        '평균 <b class="num">' + fmt2(s.mean) + '</b> m/s · 기대 범위 <span class="num">' + fmt2(s.p10) + '–' + fmt2(s.p90) +
        '</span> m/s</div><div class="factor-note muted">하위 10%–상위 10% 범위 · 개인 보행 기록이 쌓이면 개인 기준이 우선</div></div></div>');
    }
    rows.push('<div class="factor-row"><span class="pill sm">기록</span><div class="factor-label">' +
      escapeHtml(SEVERITY_KO[summary.dementia_severity] || '—') + ' · ' +
      escapeHtml(AMBULATION_KO[summary.ambulation] || '—') +
      (summary.ai_used ? ' · <span class="muted">AI 기록 확인 사용</span>' : ' · <span class="muted">규칙 추출</span>') +
      '</div></div>');
    var oos = summary.out_of_scope_factors || [];
    rows.push('<div class="factor-row"><span class="pill ' + (oos.length ? 'caution' : '') + ' sm">센서 밖</span><div>' +
      '<div class="factor-label">' + (oos.length ? escapeHtml(oos.join(' · ')) : '해당 기록 없음') + '</div>' +
      '<div class="factor-note muted">걸려 넘어짐·갑자기 힘이 빠지는 주저앉음 같은 낙상은 센서가 미리 알 수 없습니다</div></div></div>');
    return '<div class="factor-list">' + rows.join('') + '</div>' + button.replace('입원 기록으로 계산', '다시 계산');
  }

  function wireGaitPriorButton() {
    var btn = $('#gaitPriorBuild');
    if (!btn) return;
    btn.addEventListener('click', async function () {
      btn.disabled = true;
      btn.textContent = '계산 중…';
      var bedId = state.selectedId;
      try {
        var useAi = !!(state.ai && state.ai.configured);
        var res = await api('/api/dashboard/patients/' + encodeURIComponent(bedId) + '/gait-prior', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ use_ai: useAi })
        });
        if (!res || !res.success) throw new Error((res && res.message) || '계산하지 못했습니다.');
        await loadDetail(bedId);
        if (bedId === state.selectedId) renderOverview();
      } catch (e) {
        btn.disabled = false;
        btn.textContent = '다시 시도';
        $('#ovGaitPrior').insertAdjacentHTML('beforeend', '<div class="empty-state">' + escapeHtml(e.message) + '</div>');
      }
    });
  }

  var medicationEditingId = null;

  function medicationRowEditableHtml(m) {
    return '<div class="med-row med-row-editable">' +
      '<div class="med-main"><span class="med-name">' + escapeHtml(m.name || '') +
      (m.dose ? ' <span class="muted">· ' + escapeHtml(m.dose) + '</span>' : '') + '</span>' +
      '<span class="muted mono" style="font-size:11px">' + escapeHtml(m.frequency || '') + '</span></div>' +
      (m.notes ? '<div class="med-note muted">' + escapeHtml(m.notes) + '</div>' : '') +
      '<div class="med-actions">' +
      '<button class="btn sm ghost" data-med-edit="' + m.id + '" type="button">수정</button>' +
      '<button class="btn sm ghost" data-med-delete="' + m.id + '" type="button">삭제</button>' +
      '</div></div>';
  }

  function renderMedicationsPage() {
    var list = (state.detail && state.detail.medications) || [];
    $('#medicationsList').innerHTML = list.length ? list.map(medicationRowEditableHtml).join('') :
      '<div class="empty-state">등록된 투약 정보가 없습니다. "+ 투약 추가"로 등록하세요.</div>';
  }

  function openMedicationModal(existing) {
    medicationEditingId = existing ? existing.id : null;
    $('#medicationModalTitle').textContent = existing ? '투약 수정' : '투약 추가';
    $('#medicationSubmitBtn').textContent = existing ? '저장' : '추가';
    $('#medicationError').hidden = true;
    var form = $('#medicationForm');
    form.reset();
    if (existing) {
      form.elements.name.value = existing.name || '';
      form.elements.dose.value = existing.dose || '';
      form.elements.frequency.value = existing.frequency || '';
      form.elements.notes.value = existing.notes || '';
    }
    $('#medicationModal').hidden = false;
  }

  function closeMedicationModal() { $('#medicationModal').hidden = true; }

  async function refreshDetailAndRerender() {
    await loadDetail(state.selectedId);
    renderOverview();
    renderMedicationsPage();
  }

  function wireMedicationsPage() {
    $('#addMedicationBtn').addEventListener('click', function () { openMedicationModal(null); });
    $('#closeMedicationModal').addEventListener('click', closeMedicationModal);
    $('#medicationModal').addEventListener('click', function (ev) { if (ev.target.id === 'medicationModal') closeMedicationModal(); });

    $('#medicationsList').addEventListener('click', function (ev) {
      var editBtn = ev.target.closest ? ev.target.closest('[data-med-edit]') : null;
      var delBtn = ev.target.closest ? ev.target.closest('[data-med-delete]') : null;
      var list = (state.detail && state.detail.medications) || [];
      if (editBtn) {
        var id = Number(editBtn.getAttribute('data-med-edit'));
        var med = null;
        for (var i = 0; i < list.length; i++) { if (list[i].id === id) { med = list[i]; break; } }
        if (med) openMedicationModal(med);
      } else if (delBtn) {
        var did = Number(delBtn.getAttribute('data-med-delete'));
        if (!window.confirm('이 투약 기록을 삭제할까요?')) return;
        api('/api/dashboard/patients/' + encodeURIComponent(state.selectedId) + '/medications/' + did, { method: 'DELETE' })
          .then(function (res) { if (res && res.success) refreshDetailAndRerender(); })
          .catch(function () { /* best-effort */ });
      }
    });

    $('#medicationForm').addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var errorEl = $('#medicationError');
      errorEl.hidden = true;
      var fd = new FormData(ev.target);
      var name = String(fd.get('name') || '').trim();
      if (!name) return;
      var payload = {
        name: name, dose: String(fd.get('dose') || '').trim(),
        frequency: String(fd.get('frequency') || '').trim(), notes: String(fd.get('notes') || '').trim()
      };
      var submitBtn = $('#medicationSubmitBtn');
      submitBtn.disabled = true;
      try {
        var base = '/api/dashboard/patients/' + encodeURIComponent(state.selectedId) + '/medications';
        var url = medicationEditingId ? (base + '/' + medicationEditingId) : base;
        var method = medicationEditingId ? 'PUT' : 'POST';
        var res = await api(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        if (res && res.success) {
          closeMedicationModal();
          await refreshDetailAndRerender();
        } else {
          errorEl.textContent = (res && res.message) || '저장하지 못했습니다.';
          errorEl.hidden = false;
        }
      } catch (e) {
        errorEl.textContent = '저장하지 못했습니다. 서버 연결을 확인하세요.';
        errorEl.hidden = false;
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  function renderOverview() {
    renderRiskBanner();
    renderOverviewHeader();
    renderOverviewSensors();
    renderTimeline24h();
    $('#ovRiskFactors').innerHTML = renderRiskFactors(state.detail && state.detail.risk_factors);
    $('#ovMedications').innerHTML = renderMedications(state.detail && state.detail.medications);
    $('#ovGaitPrior').innerHTML = renderGaitPrior(state.detail && state.detail.gait_prior);
    wireGaitPriorButton();
    var ovEventsEl = $('#ovEvents');
    ovEventsEl.innerHTML = renderEvents(state.detail && state.detail.events);
    scrollEventLogToBottom(ovEventsEl);
  }

  /* ---------------- Sparklines ---------------- */
  function sparklineSvg(values, opts) {
    opts = opts || {};
    var width = opts.width || 220, height = opts.height || 46;
    var color = opts.color || 'var(--fg-faint)';
    var dotColor = opts.dotColor || color;
    var fmt = opts.fmt || function (v) { return String(v); };
    var known = values.map(function (v, i) { return { v: v, i: i }; }).filter(function (o) { return typeof o.v === 'number' && Number.isFinite(o.v); });
    if (known.length < 2) return '<div class="sparkline-empty">추이를 그리기에 데이터가 부족합니다.</div>';
    var nums = known.map(function (o) { return o.v; });
    var min = Math.min.apply(null, nums), max = Math.max.apply(null, nums);
    var span = (max - min) || 1;
    var stepX = width / (values.length - 1 || 1);
    var pts = known.map(function (o) {
      var x = o.i * stepX;
      var y = height - 6 - ((o.v - min) / span) * (height - 12);
      return [x, y];
    });
    var path = pts.map(function (p, i) { return (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ');
    var last = pts[pts.length - 1];
    var lastVal = nums[nums.length - 1];
    return '<svg class="sparkline" viewBox="0 0 ' + width + ' ' + height + '" width="' + width + '" height="' + height + '" role="img" aria-label="추이 그래프">' +
      '<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="3.5" fill="' + dotColor + '"><title>' + escapeHtml(fmt(lastVal)) + '</title></circle>' +
      '</svg>';
  }

  /* ---------------- Trends ---------------- */
  function renderRiskTrend(analysesChrono) {
    if (!analysesChrono.length) return '<div class="empty-state">아직 AI 분석 기록이 없습니다 — 매주 자동으로 생성됩니다.</div>';
    var values = analysesChrono.map(function (a) { return a.risk_score; });
    var known = values.filter(function (v) { return typeof v === 'number'; });
    if (!known.length) return '<div class="empty-state">저장된 위험점수가 없습니다.</div>';
    var last = known[known.length - 1];
    var avg = known.reduce(function (s, v) { return s + v; }, 0) / known.length;
    var cls = riskCls(last) || 'ok';
    var spark = sparklineSvg(values, { dotColor: 'var(--' + cls + ')', fmt: function (v) { return Math.round(v * 100) + '%'; } });
    return '<div class="trend-row">' + spark +
      '<div class="trend-readout"><span class="pill ' + cls + '">현재 ' + Math.round(last * 100) + '%</span>' +
      '<span class="muted" style="font-size:11px">최근 ' + known.length + '회 분석</span></div>' +
      '<div class="trend-readout trend-readout-avg"><span class="num" style="font-size:20px;font-weight:700">' + Math.round(avg * 100) + '<span class="unit">%</span></span>' +
      '<span class="muted" style="font-size:11px">평균 위험점수</span></div></div>';
  }

  function renderGaitTrend(rows) {
    if (!rows.length) return '<div class="empty-state">누적된 mmWave 기록이 없습니다.</div>';
    var values = rows.map(function (r) { return r.speed; });
    var known = values.filter(function (v) { return typeof v === 'number'; });
    if (!known.length) return '<div class="empty-state">속도 데이터가 없습니다.</div>';
    var spark = sparklineSvg(values, { dotColor: 'var(--accent)', fmt: function (v) { return v.toFixed(2) + ' m/s'; } });
    return '<div class="trend-row">' + spark + '<div class="trend-readout"><span class="num" style="font-size:16px;font-weight:600">' + known[known.length - 1].toFixed(2) + '<span class="unit">m/s</span></span>' +
      '<span class="muted" style="font-size:11px">최근 ' + known.length + '건</span></div></div>';
  }

  function renderTofTrend(rows) {
    if (!rows.length) return '<div class="empty-state">누적된 ToF 기록이 없습니다.</div>';
    var values = rows.map(function (r) { return r.valid_zones; });
    var known = values.filter(function (v) { return typeof v === 'number'; });
    if (!known.length) return '<div class="empty-state">데이터가 없습니다.</div>';
    var spark = sparklineSvg(values, { dotColor: 'var(--info)', fmt: function (v) { return v + '존'; } });
    return '<div class="trend-row">' + spark + '<div class="trend-readout"><span class="num" style="font-size:16px;font-weight:600">' + known[known.length - 1] + '<span class="unit">존</span></span>' +
      '<span class="muted" style="font-size:11px">최근 ' + known.length + '건</span></div></div>';
  }

  var RISK_LEVEL_NAMES = ['정상', '주의', '경고', '위험'];
  function renderRiskLevelTrend(rows) {
    if (!rows.length) return '<div class="empty-state">시뮬레이션 데이터가 없습니다.</div>';
    var values = rows.map(function (r) { return r.riskNum; });
    var lastLevel = rows[rows.length - 1].level;
    var cls = lastLevel === 'critical' ? 'critical' : lastLevel === 'warning' ? 'warning' : lastLevel === 'caution' ? 'caution' : 'ok';
    var spark = sparklineSvg(values, { dotColor: 'var(--' + cls + ')', fmt: function (v) { return RISK_LEVEL_NAMES[v] || v; } });
    return '<div class="trend-row">' + spark + '<div class="trend-readout"><span class="pill ' + cls + '">' + escapeHtml(RISK_LEVEL_NAMES[values[values.length - 1]] || '') + '</span>' +
      '<span class="muted" style="font-size:11px">최근 ' + rows.length + '건 · 시뮬레이션</span></div></div>';
  }

  function renderTrends() {
    var d = state.detail;
    var mode = currentSensorMode();
    var analyses = (d && d.analyses) || [];
    var chrono = analyses.slice().reverse();
    $('#trendRisk').innerHTML = renderRiskTrend(chrono);
    if (mode === 'sim' && window.MajuSim) {
      var series = window.MajuSim.getRecentSeries(state.selectedId);
      $('#trendGait').innerHTML = renderGaitTrend(series);
      $('#trendTofTitle').textContent = '위험 수준 추이 (시뮬레이션)';
      $('#trendTof').innerHTML = renderRiskLevelTrend(series);
    } else {
      $('#trendTofTitle').textContent = 'ToF 점유 존 추이';
      var mmwHist = (d && d.history && d.history.mmwave) || [];
      $('#trendGait').innerHTML = renderGaitTrend(mmwHist);
      var tofHist = (d && d.history && d.history.tof) || [];
      $('#trendTof').innerHTML = renderTofTrend(tofHist);
    }
    var trendEventsEl = $('#trendEvents');
    trendEventsEl.innerHTML = renderEvents(d && d.events);
    scrollEventLogToBottom(trendEventsEl);
  }

  /* ---------------- AI report ---------------- */
  var FIELD_LABELS = {
    현재_상태: '현재 상태', 최근_변화: '최근 변화', ToF_변화: 'ToF 변화', mmWave_변화: 'mmWave 변화',
    보행_변화: '보행 변화', 활동_변화: '활동 변화', 침상_관련_행동: '침상 관련 행동',
    낙상_위험도_설명: '낙상 위험도', 주요_위험_요인: '주요 위험 요인', 과거_데이터와의_비교: '과거 데이터와의 비교',
    향후_위험_가능성: '향후 위험 가능성 (현재 기록에 따른 설명)', 관련_연구_근거: '관련 연구 근거',
    투약_조정_제안: '투약 검토 사항', 데이터_신뢰도_설명: '데이터 신뢰도', 추가_관찰이_필요한_항목: '추가 관찰이 필요한 항목'
  };
  var REPORT_SECTIONS = [
    { title: '관측된 변화', keys: ['최근_변화', 'ToF_변화', 'mmWave_변화', '보행_변화', '활동_변화', '침상_관련_행동'] },
    { title: '위험 평가 및 향후 가능성', keys: ['낙상_위험도_설명', '주요_위험_요인', '과거_데이터와의_비교', '향후_위험_가능성'], highlight: '향후_위험_가능성' },
    { title: '근거 및 권고', keys: ['관련_연구_근거', '투약_조정_제안', '데이터_신뢰도_설명', '추가_관찰이_필요한_항목'] }
  ];

  var COMPONENT_LABELS = { tof_posture: 'ToF 자세', tof_exit: 'ToF 이탈', mmwave_gait: 'mmWave 보행' };

  function renderCrossValidation(risk, confidence) {
    if (!risk || !risk.component_levels) return '';
    var levels = risk.component_levels;
    var keys = Object.keys(COMPONENT_LABELS).filter(function (k) { return levels[k] != null; });
    if (!keys.length) return '';
    var pills = keys.map(function (k) {
      var isDominant = risk.dominant_signal === k;
      return '<div class="xval-row' + (isDominant ? ' xval-dominant' : '') + '">' +
        '<span class="xval-label">' + escapeHtml(COMPONENT_LABELS[k]) + '</span>' + levelPill(levels[k]) +
        (isDominant ? '<span class="faint" style="font-size:10.5px">최종 판정 근거</span>' : '') + '</div>';
    }).join('');
    var confText = confidence != null ?
      '<div class="muted" style="font-size:11.5px;margin-top:8px">신호 ' + keys.length + '/3종 확보 · 확보 비율 ' + Math.round(confidence * 100) + '%' +
      ' — 신호의 확보 정도이며 AI 정확도나 센서 간 일치율이 아닙니다.</div>' : '';
    return '<div class="report-section"><div class="uppercase-label">센서별 판정 근거</div>' + pills + confText + '</div>';
  }

  function renderEvidenceList(evidence, targetSel) {
    var el = $(targetSel || '#reportModalEvidence');
    if (!el) return;
    if (!evidence || !evidence.length) { el.innerHTML = ''; return; }
    el.innerHTML = '<div class="report-section"><div class="uppercase-label">참고 문헌 (연구 근거)</div>' +
      '<div class="evidence-list">' + evidence.map(function (e) {
        var link = e.source_url ? '<a href="' + escapeHtml(e.source_url) + '" target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>' : '';
        return '<div class="evidence-row">' +
          '<div class="evidence-title">' + escapeHtml(e.title || e.paper_id || '') + (e.section ? '<span class="faint"> · ' + escapeHtml(e.section) + '</span>' : '') + '</div>' +
          '<div class="evidence-text muted">' + escapeHtml((e.original_text || '').slice(0, 220)) + ((e.original_text || '').length > 220 ? '…' : '') + '</div>' +
          '<div class="evidence-meta">' + link + (e.doi ? '<span class="faint mono" style="font-size:10px">DOI ' + escapeHtml(e.doi) + '</span>' : '') + '</div>' +
          '</div>';
      }).join('') + '</div></div>';
  }

  function renderReportResult(report, meta, targetSel) {
    var el = $(targetSel || '#reportModalBody');
    if (!el) return;
    if (!report || typeof report !== 'object') { el.innerHTML = '<div class="empty-state">리포트 내용이 없습니다.</div>'; return; }
    var metaRow = '';
    var source = meta && meta.report_source;
    if (!source && meta && meta.model_version === 'demo-history-seed') source = 'demo_seed';
    var sourceLabel = source === 'demo_seed' ? '미리 작성한 시뮬레이션 예시 · AI 생성 아님' :
      source === 'simulation_ai' ? '시뮬레이션 기반 AI 리포트' : '';
    if (sourceLabel) metaRow += '<span class="pill info report-source">' + escapeHtml(sourceLabel) + '</span>';
    if (meta && meta.risk_score != null) {
      metaRow += '<span class="pill ' + (riskCls(meta.risk_score) || 'ok') + '">위험점수 ' + Math.round(meta.risk_score * 100) + '%</span>';
    }
    if (meta && meta.confidence != null) {
      metaRow += '<span class="muted" style="font-size:11px;margin-left:8px">신호 확보 비율 ' + Math.round(meta.confidence * 100) + '%</span>';
    }
    var html = metaRow ? '<div class="report-meta row">' + metaRow + '</div>' : '';
    html += '<div class="muted" style="font-size:11.5px;margin:8px 0">위험점수는 위험 단계를 수치로 표시한 값이며, 낙상 발생 확률이 아닙니다.</div>';
    if (meta && meta.input_snapshot_available === false) {
      html += '<div class="muted" style="font-size:11.5px;margin:8px 0">이 과거 리포트는 작성 당시의 전체 입력 기록이 보존되어 있지 않습니다.</div>';
    }
    if (report['현재_상태']) {
      html += '<div class="report-field-highlight" style="margin-bottom:12px;background:var(--bg-inset);border-color:var(--line)">' +
        '<div class="report-field-label" style="color:var(--fg-muted)">현재 상태</div>' +
        '<div class="report-field-body">' + escapeHtml(report['현재_상태']) + '</div></div>';
    }
    REPORT_SECTIONS.forEach(function (sec) {
      var parts = sec.keys.map(function (k) {
        var val = report[k];
        if (!val) return '';
        var label = FIELD_LABELS[k] || k;
        var cls = sec.highlight === k ? ' report-field-highlight' : '';
        return '<div class="report-field' + cls + '"><div class="report-field-label">' + escapeHtml(label) + '</div>' +
          '<div class="report-field-body">' + escapeHtml(val) + '</div></div>';
      }).join('');
      if (!parts) return;
      html += '<div class="report-section"><div class="uppercase-label">' + escapeHtml(sec.title) + '</div>' + parts + '</div>';
    });
    html += renderCrossValidation(meta && meta.risk, meta && (meta.data_confidence != null ? meta.data_confidence : meta.confidence));
    el.innerHTML = html || '<div class="empty-state">리포트 내용이 없습니다.</div>';
  }

  function analysisRowHtml(a) {
    var score = a.risk_score != null ? Math.round(a.risk_score * 100) + '%' : '—';
    return '<button class="analysis-row" data-analysis-id="' + a.id + '" type="button">' +
      '<span class="mono" style="font-size:11px">' + escapeHtml(fmtTime(a.created_at)) + '</span>' +
      '<span class="num">' + score + '</span>' +
      '<span class="analysis-summary">' + window.MajubomReportReview.badge(a.review_summary) +
      '<span class="muted"> ' + escapeHtml((a.summary || '').slice(0, 80)) + '</span></span>' +
      '</button>';
  }

  function renderReportsData() {
    var analyses = (state.detail && state.detail.analyses) || [];
    $('#reportRiskTrend').innerHTML = renderRiskTrend(analyses.slice().reverse());
    $('#reportHistory').innerHTML = analyses.length ?
      analyses.map(analysisRowHtml).join('') :
      '<div class="empty-state">아직 생성된 리포트가 없습니다 — 매주 자동으로 생성됩니다.</div>';
  }

  function closeReportModal() { $('#reportModal').hidden = true; }

  function openReportModal(analysis) {
    var patientId = state.selectedId;
    var reviewBox = $('#reportModalReview');
    $('#reportModal').dataset.analysisId = String(analysis.id);
    function isCurrent() {
      return !$('#reportModal').hidden && $('#reportModal').dataset.analysisId === String(analysis.id) && state.selectedId === patientId;
    }
    var when = fmtTime(analysis.created_at);
    var score = analysis.risk_score != null ? ' · 위험점수 ' + Math.round(analysis.risk_score * 100) + '%' : '';
    $('#reportModalSub').textContent = when + score;
    renderReportResult(analysis.report, analysis, '#reportModalBody');
    $('#reportModalEvidence').innerHTML = '';
    $('#reportModal').hidden = false;
    reviewBox.innerHTML = '<div class="empty-state">작성 근거와 검토 결과를 불러오는 중…</div>';
    api('/api/dashboard/analyses/' + encodeURIComponent(analysis.id) + '/review?bed_id=' + encodeURIComponent(patientId))
      .then(function (res) {
        if (!isCurrent()) return;
        reviewBox.innerHTML = res && res.success ? window.MajubomReportReview.render(res) :
          '<div class="empty-state">검토 결과를 불러오지 못했습니다. 서버 업데이트 여부를 확인하세요.</div>';
      }).catch(function () {
        if (isCurrent()) reviewBox.innerHTML = '<div class="empty-state">검토 결과를 불러오지 못했습니다. 다시 열어 주세요.</div>';
      });
    api('/agent/evidence?analysis_id=' + encodeURIComponent(analysis.id))
      .then(function (res) {
        if (isCurrent() && res && res.success) renderEvidenceList(Array.isArray(res.evidence) ? res.evidence : (res.evidence && res.evidence.found), '#reportModalEvidence');
      })
      .catch(function () { /* best-effort */ });
  }

  function wireReports() {
    $('#reportHistory').addEventListener('click', function (ev) {
      var row = ev.target.closest ? ev.target.closest('[data-analysis-id]') : null;
      if (!row) return;
      var id = Number(row.getAttribute('data-analysis-id'));
      var analyses = (state.detail && state.detail.analyses) || [];
      var found = null;
      for (var i = 0; i < analyses.length; i++) { if (analyses[i].id === id) { found = analyses[i]; break; } }
      if (found) openReportModal(found);
    });
    $('#reportModalClose').addEventListener('click', closeReportModal);
    $('#reportModal').addEventListener('click', function (ev) { if (ev.target.id === 'reportModal') closeReportModal(); });
  }

  /* ---------------- Devices ---------------- */
  function renderDevices() {
    var mode = currentSensorMode();
    var snap = currentSnapshot(mode);
    var tof = snap && snap.tof;
    var mmw = snap && snap.mmwave;
    if (mode === 'sim') {
      $('#devTof').innerHTML = '<div class="card-h"><div class="card-title">ToF (침상 센서)</div>' + SIM_TAG + '</div>' +
        '<div class="empty-state">실제 하드웨어 없이 시나리오(' + escapeHtml(snap.scenarioLabel || '') + ')를 재생 중입니다. 현재 단계: ' +
        escapeHtml(snap.phaseIcon + ' ' + snap.phaseName) + '</div>';
      $('#devMmw').innerHTML = '<div class="card-h"><div class="card-title">mmWave (보행 센서)</div>' + SIM_TAG + '</div>' +
        '<div class="empty-state">실제 하드웨어 없이 시나리오를 재생 중입니다 — 값은 참고용입니다.</div>';
      return;
    }
    if (mode === 'none') {
      $('#devTof').innerHTML = '<div class="card-h"><div class="card-title">ToF (침상 센서)</div></div><div class="empty-state">이 환자는 실시간 센서에 연결되어 있지 않습니다.</div>';
      $('#devMmw').innerHTML = '<div class="card-h"><div class="card-title">mmWave (보행 센서)</div></div><div class="empty-state">이 환자는 실시간 센서에 연결되어 있지 않습니다.</div>';
      return;
    }
    $('#devTof').innerHTML = '<div class="card-h"><div class="card-title">ToF (침상 센서)</div></div>' +
      '<div class="device-row"><span>머리맡</span>' + connPill(tof && tof.headConnected) + '</div>' +
      '<div class="device-row"><span>발치</span>' + connPill(tof && tof.footConnected) + '</div>' +
      '<div class="muted" style="font-size:11px;margin-top:6px">마지막 수신: ' + escapeHtml(tof && tof.lastSeen ? fmtTime(tof.lastSeen) : '—') + '</div>';
    $('#devMmw').innerHTML = '<div class="card-h"><div class="card-title">mmWave (보행 센서)</div></div>' +
      '<div class="device-row"><span>연결 상태</span>' + connPill(mmw && mmw.connected) + '</div>' +
      '<div class="muted" style="font-size:11px;margin-top:6px">타겟 ID: <span class="mono">' + escapeHtml((mmw && mmw.targetId) || state.mmwTargetId || '—') +
      '</span> · 마지막 수신: ' + escapeHtml(mmw && mmw.lastSeen ? fmtTime(mmw.lastSeen) : '—') + '</div>';
  }

  // 시뮬레이션 환자면 실제 /tof/3d·/mmw/viz 뷰어(렌더링 코드는 그대로)에 ?bed_id=를 달아서
  // 하드웨어 없이도 시나리오 값으로 채워진 3D/포인트클라우드가 뜨게 한다. 라이브(bed_01)는
  // 파라미터 없이 지금과 완전히 동일하게 동작.
  function refreshRawViewerSrc(box) {
    var mode = currentSensorMode();
    var suffix = mode === 'sim' ? ('?bed_id=' + encodeURIComponent(state.selectedId)) : '';
    var key = mode + ':' + (state.selectedId || '');
    if (box.dataset.loaded === key) return;
    box.dataset.loaded = key;
    box.innerHTML =
      '<div class="raw-viewer-wrap raw-viewer"><div class="uppercase-label">ToF 3D 자세</div><iframe src="/tof/3d' + suffix + '" loading="lazy"></iframe></div>' +
      '<div class="raw-viewer-wrap raw-viewer"><div class="uppercase-label">mmWave 포인트클라우드</div><iframe src="/mmw/viz' + suffix + '" loading="lazy"></iframe></div>';
  }

  function wireRawViewerToggle(buttonId, boxId) {
    var btn = $(buttonId), box = $(boxId);
    if (!btn || !box) return;
    btn.addEventListener('click', function () {
      box.hidden = !box.hidden;
      if (!box.hidden) refreshRawViewerSrc(box);
      btn.textContent = box.hidden ? '3D/포인트클라우드 뷰어 열기' : '뷰어 닫기';
    });
  }

  function wireConnectionForm() {
    $('#connApply').addEventListener('click', function () {
      var base = $('#connBaseUrl').value.trim();
      var target = $('#connTargetId').value.trim() || 'room_01';
      try { localStorage.setItem(CONN_BASE_KEY, base); localStorage.setItem(CONN_TARGET_KEY, target); } catch (e) { /* ignore */ }
      if (sensorClient) { sensorClient.setBaseUrl(base); sensorClient.setTargetId(target); }
    });
    wireRawViewerToggle('#toggleRawViewers', '#rawViewers');
    wireRawViewerToggle('#ovToggleViewer', '#ovRawViewer');
  }

  /* ---------------- Add patient ---------------- */
  function closeAddPatientModal() {
    $('#addPatientModal').hidden = true;
    $('#addPatientError').hidden = true;
  }
  function wireAddPatient() {
    $('#addPatientBtn').addEventListener('click', function () { $('#addPatientModal').hidden = false; });
    $('#closeAddPatient').addEventListener('click', closeAddPatientModal);
    $('#addPatientModal').addEventListener('click', function (ev) { if (ev.target.id === 'addPatientModal') closeAddPatientModal(); });
    $('#addPatientForm').addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var errorEl = $('#addPatientError');
      errorEl.hidden = true;
      var fd = new FormData(ev.target);
      var name = String(fd.get('name') || '').trim();
      if (!name) return;
      var payload = { name: name };
      var age = Number(fd.get('age'));
      if (fd.get('age') && Number.isFinite(age)) payload.age = age;
      var sex = fd.get('sex'); if (sex) payload.sex = sex;
      var room = String(fd.get('room') || '').trim(); if (room) payload.room = room;
      var diagnosis = String(fd.get('diagnosis') || '').trim(); if (diagnosis) payload.diagnosis = diagnosis;
      var submitBtn = ev.target.querySelector('button[type=submit]');
      submitBtn.disabled = true;
      try {
        var res = await api('/api/dashboard/patients', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        if (res && res.success) {
          closeAddPatientModal();
          ev.target.reset();
          await loadPatients();
          await selectPatient(res.patient.bed_id);
        } else {
          errorEl.textContent = (res && res.message) || '환자를 추가하지 못했습니다.';
          errorEl.hidden = false;
        }
      } catch (e) {
        errorEl.textContent = '환자를 추가하지 못했습니다. 서버 연결을 확인하세요.';
        errorEl.hidden = false;
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  /* ---------------- Live sensor stream ---------------- */
  function onLiveUpdate(snapshot) {
    state.live = snapshot;
    if (state.selectedId === state.liveBedId) { renderRiskBanner(); renderDevices(); }
    renderPatientList($('#patientSearch').value);
  }

  /* ---------------- Simulation engine ---------------- */
  function logSimEvent(bedId, snap) {
    var note = (snap.mmwave && snap.mmwave.reason) || (snap.tof && snap.tof.reason) || null;
    api('/api/dashboard/sim-events', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bed_id: bedId, event_type: 'sim_' + snap.phaseKey, level: snap.level,
        title: (snap.phaseIcon ? snap.phaseIcon + ' ' : '') + snap.phaseName, note: note
      })
    }).catch(function () { /* logging is best-effort — never blocks the demo */ });
  }

  var AUTO_REPORT_COOLDOWN_MS = 30 * 60 * 1000; // don't re-trigger for the same repeating demo fall
  var lastAutoReportAt = {};                    // within this session more than once per 30 min

  function triggerAutoReport(bedId) {
    var last = lastAutoReportAt[bedId];
    if (last && Date.now() - last < AUTO_REPORT_COOLDOWN_MS) return;
    lastAutoReportAt[bedId] = Date.now();
    api('/api/dashboard/analyses', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bed_id: bedId })
    }).then(function (res) {
      if (!res || !res.job_id) return;
      var poll = function () {
        api('/api/dashboard/analyses/' + encodeURIComponent(res.job_id)).then(function (job) {
          if (!job) return;
          if (job.status === 'completed' || job.status === 'failed') {
            if (bedId === state.selectedId) { loadDetail(bedId).then(function () { if (state.page === 'reports') renderReportsData(); }); }
            return;
          }
          setTimeout(poll, 2000);
        }).catch(function () { /* best-effort */ });
      };
      setTimeout(poll, 2000);
    }).catch(function () { /* fall was still logged/alerted even if the report couldn't start */ });
  }

  function showFallAlert(bedId, snap) {
    var patient = null;
    for (var i = 0; i < state.patients.length; i++) { if (state.patients[i].bed_id === bedId) { patient = state.patients[i]; break; } }
    var name = (patient && patient.name) || bedId;
    var room = patient && patient.room;
    $('#fallAlertBody').innerHTML =
      '<div class="fall-alert-line">' + (room ? escapeHtml(room) + '호 · ' : '') + escapeHtml(name) + ' 환자</div>' +
      '<div class="muted">낙상이 감지되었습니다 — 즉시 확인이 필요합니다. (시뮬레이션 데이터)</div>' +
      '<div class="faint mono" style="font-size:11px;margin-top:6px">' + escapeHtml(fmtTime(Date.now())) + '</div>';
    $('#fallAlertModal').dataset.bedId = bedId;
    $('#fallAlertModal').hidden = false;
    triggerAutoReport(bedId);
  }

  function wireFallAlert() {
    $('#fallAlertClose').addEventListener('click', function () { $('#fallAlertModal').hidden = true; });
    $('#fallAlertModal').addEventListener('click', function (ev) { if (ev.target.id === 'fallAlertModal') $('#fallAlertModal').hidden = true; });
    $('#fallAlertGoto').addEventListener('click', function () {
      var bedId = $('#fallAlertModal').dataset.bedId;
      $('#fallAlertModal').hidden = true;
      if (bedId) selectPatient(bedId);
    });
  }

  function onSimTick() {
    renderPatientList($('#patientSearch').value);
    if (window.MajuSim && window.MajuSim.isSimulated(state.selectedId)) {
      renderRiskBanner();
      renderDevices();
    }
  }

  /* ---------------- Boot ---------------- */
  function renderAll() {
    renderOverview();
    renderTrends();
    renderMedicationsPage();
    renderReportsData();
    renderDevices();
  }

  async function boot() {
    wireThemeToggle();
    wireNav();
    wireSearch();
    wireAddPatient();
    wireMedicationsPage();
    wireConnectionForm();
    wireReports();
    wireFallAlert();

    try {
      var status = await api('/api/dashboard/status');
      if (status && status.success) {
        state.liveBedId = status.live_bed_id || state.liveBedId;
        state.mmwTargetId = status.mmw_target_id || state.mmwTargetId;
        state.ai = status.ai || null;
      }
    } catch (e) { /* server not reachable yet — surfaced via banner/empty states */ }

    await loadPatients();
    state.selectedId = state.selectedId || state.liveBedId;
    $('#mainContent').setAttribute('data-patient-id', state.selectedId);
    await loadDetail(state.selectedId);
    renderAll();
    // Warm the cache for every other patient in the background — there are only a handful
    // (live + the fixed demo set), so after this, switching between them shows no loading flash.
    prefetchAllDetails();

    var storedBase = '';
    var storedTarget = state.mmwTargetId || 'room_01';
    try {
      storedBase = localStorage.getItem(CONN_BASE_KEY) || '';
      storedTarget = localStorage.getItem(CONN_TARGET_KEY) || storedTarget;
    } catch (e) { /* ignore */ }
    $('#connBaseUrl').value = storedBase;
    $('#connTargetId').value = storedTarget;

    if (window.MajuData) {
      sensorClient = window.MajuData.createClient({ baseUrl: storedBase, targetId: storedTarget, onUpdate: onLiveUpdate });
      sensorClient.start();
    }
    if (window.MajuSim) {
      simEngine = window.MajuSim.createEngine(onSimTick, showFallAlert, logSimEvent);
      simEngine.start();
    }
    // Smooth position/reading motion: tof/mmwave/floor-plan must never disagree (they're one
    // renderOverviewSensors() call sharing one snapshot), so instead of animating the floor
    // plan dot on its own timer, everything redraws together, fast enough (~180ms) to read as
    // continuous motion rather than the 2s sim-tick jump this replaces.
    setInterval(renderOverviewSensors, 180);
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
