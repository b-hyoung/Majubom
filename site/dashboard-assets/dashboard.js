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

  function lostPill(ageMs) {
    var age = ageMs != null ? ' · ' + Math.round(ageMs / 1000) + '초 전 수신' : '';
    return '<span class="pill">연결 끊김' + escapeHtml(age) + '</span>';
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
        if (state.page === 'reports') { btn.classList.remove('has-new'); renderReportsData(); }
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
    if ($('#ovEvents')) $('#ovEvents').innerHTML = '<div class="empty-state">불러오는 중…</div>';
    closeDrawer();
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
    // 연결이 끊겨도 카드 구성은 그대로 두고 값만 '—'로 바꾼다(2026-10-12: 끊김↔연결 반복 시 칸이 사라졌다 나타나 보기 불편)
    tof = tof || {};
    var tofLost = mode === 'live' && !tof.connected;
    var inBedText = tofLost ? '—' : tof.inBed === true ? '침상 내' : tof.inBed === false ? '침상 이탈' : '판정 대기';
    var tag = mode === 'sim' ? SIM_TAG : '';
    return '<div class="card-h"><div class="card-title">ToF · 침상 자세</div>' + tag + (tofLost ? lostPill(tof.ageMs) : levelPill(tof.riskLevel)) + '</div>' +
      '<div class="sensor-metrics">' +
      '<div class="metric"><span class="metric-label">자세</span><span class="metric-value">' + escapeHtml(tofLost ? '—' : tof.postureLabel || '판정 중') + '</span></div>' +
      '<div class="metric"><span class="metric-label">재실</span><span class="metric-value">' + escapeHtml(inBedText) + '</span></div>' +
      '<div class="metric"><span class="metric-label">머리맡 센서</span><span class="metric-value">' + (tof.headConnected ? '연결됨' : '연결 안 됨') + '</span></div>' +
      '<div class="metric"><span class="metric-label">발치 센서</span><span class="metric-value">' + (tof.footConnected ? '연결됨' : '연결 안 됨') + '</span></div>' +
      '</div>' + (!tofLost && tof.reason ? '<div class="sensor-note muted">' + escapeHtml(tof.reason) + '</div>' : '');
  }

  function renderMmwCard(mmw, mode) {
    if (mode === 'none') {
      return '<div class="card-h"><div class="card-title">mmWave · 보행</div></div>' +
        '<div class="sensor-empty">이 환자는 실시간 센서에 연결되어 있지 않습니다.</div>';
    }
    mmw = mmw || {};
    var mmwLost = mode === 'live' && !mmw.connected;
    var tag = mode === 'sim' ? SIM_TAG : '';
    // 잠정: 개인 기준이 생기기 전 입원 기록·문헌 기대 범위와 비교한 판정(정식 알람 아님)
    var provisional = mmw.provisional ? '<span class="pill sm" title="개인 보행 기록 5건 전 — 문헌 기대 범위와 비교한 잠정 판정">잠정</span>' : '';
    // 판정이 없을 때는 '판정 대기' 대신 이유(사람 없음 · 환자 구분 보류 · 보행 구간 아님)를 보여준다
    // 10-22 저녁: 실시간 환자는 6초 보행 판정의 대기 이유(보행 구간 아님 등) 대신 환자 지정 여부만 보인다
    var levelBadge = mmwLost ? lostPill(mmw.ageMs)
      : mode === 'live' ? (mmw.mainTid == null ? '<span class="pill">환자 지정 필요</span>' : '<span class="pill" title="0.1초마다 갱신">실시간</span>')
      : (mmw.riskLevel === 'unknown' && mmw.waitReason)
      ? '<span class="pill" title="보행 판정은 사람이 곧게 걷는 6초 구간에서만 계산됩니다">' + escapeHtml(mmw.waitReason) + '</span>'
      : levelPill(mmw.riskLevel);
    return '<div class="card-h"><div class="card-title">mmWave · 보행</div>' + tag + provisional + levelBadge + '</div>' +
      '<div class="sensor-metrics">' +
      // 10-22 저녁 사용자 결정: 실시간(라이브) 환자는 6초 보행 판정 대신 0.1초마다 들어오는 추적으로 바로 계산한 값만 보인다
      (mode === 'live' ? (function (g) {
        g = g || {};
        var m = function (label, v, unit, tip) {
          return '<div class="metric" title="' + tip + '"><span class="metric-label">' + label + '</span><span class="num metric-value">' + fmt2(v) +
            (unit ? '<span class="unit">' + unit + '</span>' : '') + '</span></div>';
        };
        return m('속도', g.speed, 'm/s', '최근 1.5초 레이더 추적 속도 평균(실시간)') +
          m('흔들림', g.sway, 'm', '최근 4초 이동 경로가 직선에서 옆으로 벗어난 정도(실시간)') +
          m('보폭', g.stride, 'm', '최근 4초 이동 거리 ÷ 걸음 수 × 2 — 2걸음 이상 걸을 때만(실시간)') +
          m('속도 변동성', g.cv, '', '최근 4초 속도의 표준편차 ÷ 평균 — 움직일 때만(실시간)');
      })(mmw.live) :
      '<div class="metric"><span class="metric-label">속도</span><span class="num metric-value">' + fmt2(mmw.speed) + '<span class="unit">m/s</span></span></div>' +
      '<div class="metric"><span class="metric-label">흔들림</span><span class="num metric-value">' + fmt2(mmw.sway) + '</span></div>' +
      '<div class="metric"><span class="metric-label">보폭</span><span class="num metric-value">' + fmt2(mmw.stride) + '<span class="unit">m</span></span></div>' +
      '<div class="metric"><span class="metric-label">속도 변동성</span><span class="num metric-value">' + fmt2(mmw.cv) + '</span></div>') +
      '</div>' + (!mmwLost && mode !== 'live' && mmw.reason ? '<div class="sensor-note muted">' + escapeHtml(mmw.reason) + '</div>' : '');
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
    renderKpis(mode, snap);
  }

  /* ---------------- 상단 상태 요약 타일(2026-10-13 디자인 개편) ---------------- */
  var lastKpiHtml = '';
  function renderKpis(mode, snap) {
    var el = $('#ovKpis');
    if (!el) return;
    var tof = (snap && snap.tof) || {}, mmw = (snap && snap.mmwave) || {};
    var fr = state.detail && state.detail.fall_risk;
    var level = mode === 'none' ? null : dominantOf(tof.riskLevel, mmw.riskLevel, true, true);
    var tofOk = mode === 'sim' || (mode === 'live' && tof.connected);
    var mmwOk = mode === 'sim' || (mode === 'live' && mmw.connected);
    var inBed = !tofOk ? '—' : tof.inBed === true ? '침상 내' : tof.inBed === false ? '침상 이탈' : '판정 대기';
    var gait = !mmwOk ? '—' : mode === 'live' ? (mmw.live && typeof mmw.live.speed === 'number' ? mmw.live.speed.toFixed(2) + ' m/s'
        : mmw.mainTid == null ? '환자 지정 필요' : '대기')
      : (typeof mmw.speed === 'number' && mmw.speed > 0) ? mmw.speed.toFixed(2) + ' m/s' : (mmw.waitReason || '대기');
    var tile = function (label, value, cls, extra) {
      return '<div class="kpi ' + (cls || '') + '"' + (extra || '') + '><div class="kpi-label">' + escapeHtml(label) + '</div><div class="kpi-value">' + value + '</div></div>';
    };
    var html =
      tile('종합 상태', mode === 'none' ? '센서 없음' : escapeHtml(({ normal: '정상', caution: '주의', warning: '경고', critical: '위험' })[level] || '판정 대기'),
        'kpi-level kpi-' + escapeHtml(level || 'none')) +
      tile('자세', escapeHtml(tofOk ? (tof.postureLabel || '판정 중') : '—')) +
      tile('재실', escapeHtml(inBed)) +
      tile('보행', escapeHtml(gait)) +
      tile('낙상 위험군', fr ? escapeHtml(fr.group_ko) + ' <span class="kpi-sub">' + fr.score + '점</span>' : '—',
        'kpi-click kpi-risk-' + escapeHtml(fr ? fr.group : 'none'), ' data-drawer="risk" role="button" tabindex="0" title="낙상 위험도 자세히"') +
      tile('거동 범위', fr ? escapeHtml(fr.mobility_ko) : '—', 'kpi-click', ' data-drawer="risk" role="button" tabindex="0" title="거동 범위 바꾸기"');
    if (html !== lastKpiHtml) { el.innerHTML = html; lastKpiHtml = html; }
  }

  /* ---------------- 오른쪽 상세 패널(낙상 위험도·보행 추세·보행 기대 범위) ---------------- */
  var DRAWER_TITLE = { risk: '낙상 위험도 · 거동 범위', gait: '보행 추세 예측', prior: '보행 기대 범위' };
  function openDrawer(kind) {
    state.drawer = kind;
    $('#drawerTitle').textContent = DRAWER_TITLE[kind] || '';
    renderDrawerBody();
    $('#sideDrawer').hidden = false;
  }
  function closeDrawer() {
    state.drawer = null;
    if ($('#sideDrawer')) $('#sideDrawer').hidden = true;
  }
  function renderDrawerBody() {
    var body = $('#drawerBody');
    var d = state.detail || {};
    if (state.drawer === 'risk') {
      body.innerHTML = renderFallRisk(d.fall_risk);
      wireFallRisk(state.selectedId);
    } else if (state.drawer === 'gait') {
      body.innerHTML = renderGaitForecast(d.gait_trend);
    } else if (state.drawer === 'prior') {
      body.innerHTML = '<div class="card-sub" style="margin-bottom:10px">입원 기록과 문헌 기준값으로 계산한 참고 범위 · 낙상 확률 아님</div><div id="ovGaitPrior">' + renderGaitPrior(d.gait_prior) + '</div>';
      wireGaitPriorButton();
    }
  }
  function wireDrawer() {
    $('#drawerClose').addEventListener('click', closeDrawer);
    $('#sideDrawer').addEventListener('click', function (ev) { if (ev.target.id === 'sideDrawer') closeDrawer(); });
    document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && state.drawer) closeDrawer(); });
    document.addEventListener('click', function (ev) {
      var t = ev.target.closest && ev.target.closest('[data-drawer]');
      if (t) openDrawer(t.getAttribute('data-drawer'));
    });
    document.addEventListener('keydown', function (ev) {
      var t = ev.target.closest && ev.target.closest('[data-drawer]');
      if (t && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); openDrawer(t.getAttribute('data-drawer')); }
    });
  }

  function renderFallRiskSummary(fr) {
    if (!fr) return '<div class="card-h"><div class="card-title">낙상 위험도</div></div><div class="empty-state">위험도 정보가 없습니다.</div>';
    var last = (fr.recent_events || [])[0];
    return '<div class="card-h"><div class="card-title">낙상 위험도</div><button class="btn sm" data-drawer="risk" type="button">자세히 · 재평가</button></div>' +
      '<div class="side-big"><span class="pill ' + (FR_GROUP_PILL[fr.group] || '') + '">' + escapeHtml(fr.group_ko) + '</span>' +
      '<span class="side-score num">' + fr.score + '<span class="unit">점</span></span></div>' +
      '<div class="side-rows"><div><span>Morse 기본</span><b class="num">' + fr.morse_score + '</b></div>' +
      '<div><span>변동 점수</span><b class="num">' + fr.dynamic_points +
      (fr.dynamic_points > 0 ? ' <button class="btn sm" id="frQuickReset" type="button" title="시험·오탐으로 쌓인 변동 점수만 0으로 (Morse·거동 범위 유지)">0으로</button>' : '') +
      '</b></div>' +
      '<div><span>거동 범위</span><b>' + escapeHtml(fr.mobility_ko) + '</b></div></div>' +
      (last ? '<div class="side-note">최근: ' + escapeHtml(String(last.ts || '').replace('T', ' ').slice(5, 16)) + ' ' +
        escapeHtml(FR_KIND_KO[last.kind] || last.kind) + '</div>' : '');
  }

  function renderGaitForecastSummary(gt) {
    var head = '<div class="card-h"><div class="card-title">보행 추세 예측</div><button class="btn sm" data-drawer="gait" type="button">자세히</button></div>';
    if (!gt) return head + '<div class="empty-state">예측 정보가 없습니다.</div>';
    if (gt.status !== 'ok') {
      var msg = gt.message || ('보행 기록 ' + (gt.days_with_data || 0) + '일 / 필요 ' + (gt.min_days || 14) + '일');
      return head + '<div class="side-big"><span class="pill">예측 대기</span></div><div class="side-note">' + escapeHtml(msg) + '</div>';
    }
    var sp = gt.metrics.speed || {};
    return head + '<div class="side-big"><span class="pill ' + (GT_LEVEL_PILL[gt.level] || '') + '">' + escapeHtml(gt.level_ko || '') + '</span></div>' +
      (sp.status === 'ok' ? '<div class="side-rows"><div><span>보행 속도(추정)</span><b class="num">' + (sp.current * 100).toFixed(0) + ' cm/s</b></div>' +
        '<div><span>7일 변화</span><b class="num">' + (sp.change7_cm > 0 ? '+' : '') + sp.change7_cm.toFixed(1) + ' cm/s</b></div>' +
        '<div><span>7일 뒤 예측</span><b class="num">' + (sp.forecast7.mean * 100).toFixed(0) + ' cm/s</b></div></div>' : '');
  }

  function renderGaitPriorSummary(summary) {
    var head = '<div class="card-h"><div class="card-title">보행 기대 범위</div><button class="btn sm" data-drawer="prior" type="button">자세히</button></div>';
    if (!summary) return head + '<div class="side-note">아직 계산하지 않았습니다.</div>';
    if (summary.not_applicable) return head + '<div class="side-note">' + escapeHtml(summary.not_applicable) + '</div>';
    var s = summary.speed_expected_mps;
    return head + (s ? '<div class="side-rows"><div><span>기대 보행 속도</span><b class="num">' + fmt2(s.p10) + '–' + fmt2(s.p90) + ' m/s</b></div></div>' : '') +
      '<div class="side-note">입원 기록·문헌 기준 참고 범위</div>';
  }

  var LEVEL_COLOR_VAR = { normal: '--ok', caution: '--caution', warning: '--warning', critical: '--critical' };
  var FLOORPLAN_PX = 44; // px per meter

  /** Static 1인실 layout — bed/bedside/table/door/bathroom/sensor markers and their coverage
   * cones — ported from the reference design's RoomViz.jsx geometry, minus the mmWave sweep
   * beam and CSI expanding-ring animations (that "device is scanning" motion is the one thing
   * that was asked to be left out; the room drawing itself, sensor icons and coverage shading
   * are the same). Built once per (mode, patient); the moving parts are separate <g> groups
   * filled in by renderPatientMarkerSvg()/renderTrailSvg() so this never gets rebuilt on tick. */
  // ── 부스 평면도(10-22 저녁 사용자 요청: 선택한 부스 영역이 평면도 전체) ──
  // 레이더 좌표의 부스 영역 = send_mmw.py ROI_POLY(사용자가 부스 가장자리를 걸은 경로를 0.3m 넓힘). 네 모서리가 정확한 직사각형이 아니라
  // 레이더 구석에서 두 변 방향으로 나눠 비율을 구해 직사각형에 펼친다(레이더 거리 오차도 그대로 들어 있음).
  // 사용자 지정 배치(10-22 저녁): 레이더는 왼쪽 아래 구석, 사람 위치는 위아래 반전, 침대는 가로 — 머리가 레이더 쪽(왼쪽), 발치는 오른쪽.
  // 침대 세로 위치는 send_mmw BED_CENTER(누운 자리 실측). ROI·침대를 바꾸면 여기도 같게.
  // 10-29 간호학과 방(사용자 확인 전 잠정): 6각형 ROI_POLY를 침대 방향(걸은 경로 아래 경계, 레이더 좌표 36°)으로 돌린 외접 직사각형.
  // 사용자 말: 'mmWave 옆이 발치' → 침대 가로, 발치 = 왼쪽(레이더 쪽), 머리(베개) = 오른쪽. 부스 값 origin [0.05,-0.49] back [2.96,2.55] side [-1.83,0.72] bed [-0.15,0.5]
  var BOOTH = { origin: [-0.34, -1.48], back: [3.81, 1.55], side: [-2.42, 1.37], bed: [1.0, 1.4], bedW: 0.9, bedL: 2.0, headRight: true };
  (function () {
    var o = BOOTH.origin;
    BOOTH.e1 = [BOOTH.back[0] - o[0], BOOTH.back[1] - o[1]];
    BOOTH.e2 = [BOOTH.side[0] - o[0], BOOTH.side[1] - o[1]];
    BOOTH.w = Math.hypot(BOOTH.e1[0], BOOTH.e1[1]);   // 그림 가로 m
    BOOTH.h = Math.hypot(BOOTH.e2[0], BOOTH.e2[1]);   // 그림 세로 m
  })();
  function radarToBooth(x, y) {
    if (typeof x !== 'number' || typeof y !== 'number') return null;
    var dx = x - BOOTH.origin[0], dy = y - BOOTH.origin[1], e1 = BOOTH.e1, e2 = BOOTH.e2;
    var det = e1[0] * e2[1] - e1[1] * e2[0];
    var a = (dx * e2[1] - dy * e2[0]) / det, b = (e1[0] * dy - e1[1] * dx) / det;
    return { x: a * BOOTH.w, y: (1 - b) * BOOTH.h };   // 위아래 반전 → 레이더(0, 0)가 왼쪽 아래
  }
  function boothBed() {
    // 가로 침대: 세로 가운데는 누운 자리. 부스는 머리(왼쪽)를 레이더 쪽 벽에서 0.1m, 10-29 방은 가로 가운데도 누운 자리
    var bc = radarToBooth(BOOTH.bed[0], BOOTH.bed[1]);
    var y = Math.max(0.05, Math.min(BOOTH.h - BOOTH.bedW - 0.05, bc.y - BOOTH.bedW / 2));
    var x = BOOTH.headRight ? Math.max(0.05, Math.min(BOOTH.w - BOOTH.bedL - 0.05, bc.x - BOOTH.bedL / 2)) : 0.1;
    return { x: x, y: y, w: BOOTH.bedL, h: BOOTH.bedW };
  }
  function buildBoothSvg(PX) {
    var W = BOOTH.w * PX, H = BOOTH.h * PX, bed = boothBed(), rd = radarToBooth(0, 0);
    var bx = bed.x * PX, by = bed.y * PX, bw = bed.w * PX, bh = bed.h * PX;
    return '<svg class="floorplan-svg floorplan-room-svg" viewBox="-14 -16 ' + (W + 28).toFixed(1) + ' ' + (H + 34).toFixed(1) + '" role="img" aria-label="부스 평면도(실측)">' +
      '<defs><pattern id="fpGrid" width="18" height="18" patternUnits="userSpaceOnUse"><path d="M18 0L0 0 0 18" fill="none" stroke="var(--line-faint)" stroke-width="0.5"/></pattern></defs>' +
      '<rect x="0" y="0" width="' + W.toFixed(1) + '" height="' + H.toFixed(1) + '" fill="url(#fpGrid)"/>' +
      '<rect x="0.5" y="0.5" width="' + (W - 1).toFixed(1) + '" height="' + (H - 1).toFixed(1) + '" fill="none" stroke="var(--line-strong)" stroke-width="1.2"/>' +
      '<rect x="' + bx.toFixed(1) + '" y="' + by.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + bh.toFixed(1) + '" rx="5" fill="var(--bg-elev-1)" stroke="var(--line-strong)" stroke-width="1.1"/>' +
      // 베개(머리): 부스 = 레이더 쪽 왼쪽 끝, 10-29 방 = 오른쪽 끝(발치가 레이더 옆)
      '<rect x="' + (BOOTH.headRight ? bx + bw - 4 - 0.28 * PX : bx + 4).toFixed(1) + '" y="' + (by + 4).toFixed(1) + '" width="' + (0.28 * PX).toFixed(1) + '" height="' + (bh - 8).toFixed(1) + '" rx="3" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="0.5"/>' +
      '<text x="' + (bx + bw / 2).toFixed(1) + '" y="' + (by + bh / 2 + 3).toFixed(1) + '" text-anchor="middle" font-size="7.5" fill="var(--fg-faint)" font-family="var(--font-mono)">BED</text>' +
      '<g transform="translate(' + (rd.x * PX).toFixed(1) + ',' + (rd.y * PX).toFixed(1) + ')"><circle r="6" fill="var(--bg-elev-1)" stroke="var(--info)" stroke-width="1.3"/><circle r="2.3" fill="var(--info)"/></g>' +
      '<text x="' + (rd.x * PX + 9).toFixed(1) + '" y="' + (rd.y * PX + 13).toFixed(1) + '" font-size="7.5" fill="var(--info)" font-family="var(--font-mono)">mmWave</text>' +
      '<g id="ovFloorplanTrail"></g>' +
      '<g id="ovFloorplanOthers"></g>' +
      '<g id="ovFloorplanMarker" class="floorplan-marker"></g>' +
      '</svg>';
  }

  function buildSimRoomSvg(room, PX, ariaLabel) {
    var W = (room.w * PX).toFixed(1), H = (room.h * PX).toFixed(1);
    var bath = room.bath, bed = room.bed, bedside = room.bedside, table = room.table, door = room.door;
    var byKind = function (kind, subtype) {
      for (var i = 0; i < room.sensors.length; i++) {
        var s = room.sensors[i];
        if (s.kind === kind && (!subtype || s.subtype === subtype)) return s;
      }
      return null;
    };
    var tofHead = byKind('ToF', 'head'), tofFoot = byKind('ToF', 'foot'), mmw = byKind('mmWave');

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

    // 2026-10-12: 평면도를 반시계 방향으로 90도 돌려 보여준다(방 좌표는 그대로, 그림만 회전).
    // 같은 날 사용자 요청으로 거기서 180° 더 돌려(=시계 방향 90°) 현장에서 보는 장면과 맞췄다.
    // 회전 그룹 안의 글자·환자 표시는 rotate(-90)으로 되돌려 똑바로 읽히게 한다.
    var upright = function (x, y) { return ' transform="rotate(-90 ' + x + ' ' + y + ')"'; };
    return '<svg class="floorplan-svg floorplan-room-svg" viewBox="0 0 ' + H + ' ' + W + '" role="img" aria-label="' + (ariaLabel || '병실 평면도(시뮬레이션)') + '">' +
      '<defs>' +
      '<pattern id="fpGrid" width="18" height="18" patternUnits="userSpaceOnUse"><path d="M18 0L0 0 0 18" fill="none" stroke="var(--line-faint)" stroke-width="0.5"/></pattern>' +
      '<radialGradient id="fpTofCone" cx="50%" cy="0%" r="100%"><stop offset="0%" stop-color="var(--accent)" stop-opacity="0.28"/><stop offset="90%" stop-color="var(--accent)" stop-opacity="0.02"/></radialGradient>' +
      '<radialGradient id="fpMmwCone" cx="50%" cy="0%" r="100%"><stop offset="0%" stop-color="var(--info)" stop-opacity="0.24"/><stop offset="90%" stop-color="var(--info)" stop-opacity="0.02"/></radialGradient>' +
      '</defs>' +
      '<g transform="translate(' + H + ',0) rotate(90)">' +
      '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="url(#fpGrid)"/>' +
      '<rect x="0.5" y="0.5" width="' + (W - 1) + '" height="' + (H - 1) + '" fill="none" stroke="var(--line-strong)" stroke-width="1.2"/>' +
      '<rect x="' + (bath.x * PX).toFixed(1) + '" y="' + (bath.y * PX).toFixed(1) + '" width="' + (bath.w * PX).toFixed(1) + '" height="' + (bath.h * PX).toFixed(1) + '" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="1" stroke-dasharray="3 3"/>' +
      '<text x="' + ((bath.x + bath.w / 2) * PX).toFixed(1) + '" y="' + ((bath.y + bath.h / 2) * PX).toFixed(1) + '"' + upright(((bath.x + bath.w / 2) * PX).toFixed(1), ((bath.y + bath.h / 2) * PX).toFixed(1)) + ' text-anchor="middle" dominant-baseline="middle" font-size="8" fill="var(--fg-faint)" font-family="var(--font-mono)">화장실</text>' +
      '<rect x="' + (door.x * PX).toFixed(1) + '" y="' + (door.y * PX).toFixed(1) + '" width="' + (door.w * PX).toFixed(1) + '" height="3" fill="var(--bg)" stroke="var(--line-strong)"/>' +
      '<path d="M' + (door.x * PX).toFixed(1) + ',' + (door.y * PX).toFixed(1) + ' a' + (door.w * PX).toFixed(1) + ',' + (door.w * PX).toFixed(1) + ' 0 0 0 ' + (door.w * PX).toFixed(1) + ',-' + (door.w * PX).toFixed(1) + '" fill="none" stroke="var(--line)" stroke-width="0.7" stroke-dasharray="2 2"/>' +
      '<text x="' + (door.x * PX - 10).toFixed(1) + '" y="' + (door.y * PX - 4).toFixed(1) + '"' + upright((door.x * PX - 10).toFixed(1), (door.y * PX - 4).toFixed(1)) + ' font-size="8" fill="var(--fg-faint)" font-family="var(--font-mono)">문</text>' +
      cone(tofHead, 'fpTofCone') + cone(tofFoot, 'fpTofCone') + cone(mmw, 'fpMmwCone') +
      '<g>' +
      '<rect x="' + fx.toFixed(1) + '" y="' + fy.toFixed(1) + '" width="' + fw.toFixed(1) + '" height="' + fh.toFixed(1) + '" rx="6" fill="var(--accent)" opacity="0.05"/>' +
      '<rect x="' + fx.toFixed(1) + '" y="' + fy.toFixed(1) + '" width="' + fw.toFixed(1) + '" height="' + fh.toFixed(1) + '" rx="6" fill="none" stroke="var(--accent)" stroke-width="0.8" stroke-dasharray="4 4" opacity="0.4"/>' +
      '</g>' +
      '<rect x="' + (bed.x * PX).toFixed(1) + '" y="' + (bed.y * PX).toFixed(1) + '" width="' + (bed.w * PX).toFixed(1) + '" height="' + (bed.h * PX).toFixed(1) + '" rx="5" fill="var(--bg-elev-1)" stroke="var(--line-strong)" stroke-width="1.1"/>' +
      '<rect x="' + ((bed.x + 0.1) * PX).toFixed(1) + '" y="' + ((bed.headAtEnd ? bed.y + bed.h - 0.38 : bed.y + 0.1) * PX).toFixed(1) + '" width="' + ((bed.w - 0.2) * PX).toFixed(1) + '" height="' + (0.28 * PX).toFixed(1) + '" rx="3" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="0.5"/>' +
      '<text x="' + ((bed.x + bed.w / 2) * PX).toFixed(1) + '" y="' + ((bed.y + bed.h / 2) * PX).toFixed(1) + '"' + upright(((bed.x + bed.w / 2) * PX).toFixed(1), ((bed.y + bed.h / 2) * PX).toFixed(1)) + ' text-anchor="middle" font-size="7.5" fill="var(--fg-faint)" font-family="var(--font-mono)">BED</text>' +
      '<rect x="' + (bedside.x * PX).toFixed(1) + '" y="' + (bedside.y * PX).toFixed(1) + '" width="' + (bedside.w * PX).toFixed(1) + '" height="' + (bedside.h * PX).toFixed(1) + '" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="0.7"/>' +
      '<rect x="' + (table.x * PX).toFixed(1) + '" y="' + (table.y * PX).toFixed(1) + '" width="' + (table.w * PX).toFixed(1) + '" height="' + (table.h * PX).toFixed(1) + '" rx="3" fill="var(--bg-inset)" stroke="var(--line)" stroke-width="0.7"/>' +
      marker(tofHead) + marker(tofFoot) + marker(mmw) +
      '<g id="ovFloorplanTrail"></g>' +
      '<g id="ovFloorplanOthers"></g>' +
      '<g id="ovFloorplanMarker" class="floorplan-marker"></g>' +
      '</g></svg>';
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
  // ── 실측 평면도 부드럽게 움직이기 + 점 눌러 환자 지정(2026-10-16) ──
  var fp = { goal: null, mainTid: null, others: {}, disp: { main: null, others: {} }, last: 0, running: false };
  var FP_TAU = 0.1;        // 따라가는 시간 상수(초). 레이더가 0.07초마다 새 위치 → 0.15초면 끊김 없이 자연스럽게
  var FP_SNAP_M = 1.5;    // 이보다 멀리 튀면 따라가지 않고 바로 옮긴다(다른 사람으로 바뀜 등)

  function fpSetGoals(pos, mainTid, others) {
    if (pos && fp.disp.main && (fp.mainTid !== mainTid || Math.hypot(pos.x - fp.disp.main.x, pos.y - fp.disp.main.y) > FP_SNAP_M)) {
      fp.disp.main = { x: pos.x, y: pos.y };
    }
    fp.goal = pos ? { x: pos.x, y: pos.y } : null;
    fp.mainTid = mainTid;
    var next = {};
    others.forEach(function (o) { next[o.t.tid] = { x: o.p.x, y: o.p.y }; });
    fp.others = next;
    if (!fp.running) { fp.running = true; fp.last = performance.now(); requestAnimationFrame(fpAnimate); }
  }

  function fpAnimate(ts) {
    var markerEl = document.getElementById('ovFloorplanMarker');
    var othersEl = document.getElementById('ovFloorplanOthers');
    if (!markerEl || !othersEl) { fp.running = false; fp.disp = { main: null, others: {} }; return; }
    var dt = Math.min(0.1, Math.max(0, (ts - fp.last) / 1000)); fp.last = ts;
    var k = 1 - Math.exp(-dt / FP_TAU);
    var step = function (d, g) {
      if (!d || Math.hypot(g.x - d.x, g.y - d.y) > FP_SNAP_M) return { x: g.x, y: g.y };
      return { x: d.x + (g.x - d.x) * k, y: d.y + (g.y - d.y) * k };
    };
    if (fp.goal) {
      fp.disp.main = step(fp.disp.main, fp.goal);
      markerEl.style.transform = 'translate(' + (fp.disp.main.x * FLOORPLAN_PX).toFixed(1) + 'px,' + (fp.disp.main.y * FLOORPLAN_PX).toFixed(1) + 'px)';
    } else { fp.disp.main = null; }
    var seen = {};
    Object.keys(fp.others).forEach(function (tid) {
      var d = fp.disp.others[tid] = step(fp.disp.others[tid], fp.others[tid]);
      seen[tid] = true;
      var c = othersEl.querySelector('[data-tid="' + tid + '"]');
      if (!c) {
        c = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        c.setAttribute('data-tid', tid);
        c.setAttribute('class', 'fp-other');
        c.innerHTML = '<circle r="11" fill="transparent"></circle><circle r="4.5" fill="var(--fg-faint)" opacity="0.8"></circle>' +
          '<title>다른 사람(추적 ' + escapeHtml(String(tid)) + ') · 눌러서 환자로 지정</title>';
        othersEl.appendChild(c);
      }
      c.setAttribute('transform', 'translate(' + (d.x * FLOORPLAN_PX).toFixed(1) + ',' + (d.y * FLOORPLAN_PX).toFixed(1) + ')');
    });
    Array.prototype.slice.call(othersEl.querySelectorAll('[data-tid]')).forEach(function (c) {
      var tid = c.getAttribute('data-tid');
      if (!seen[tid]) { c.remove(); delete fp.disp.others[tid]; }
    });
    requestAnimationFrame(fpAnimate);
  }

  function wireFloorplanAssign() {
    document.addEventListener('click', function (ev) {
      var g = ev.target.closest ? ev.target.closest('.fp-other') : null;
      if (!g) return;
      var tid = Number(g.getAttribute('data-tid'));
      if (!window.confirm('이 사람(추적 ' + tid + ')을 환자로 지정할까요?\n다른 사람을 다시 누르기 전까지 이 사람만 따라갑니다(자동으로 바뀌지 않음).')) return;
      api('/mmw/patient', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tid: tid, target_id: state.mmwTargetId || 'room_01' }) })
        .catch(function () { window.alert('환자 지정을 보내지 못했습니다.'); });
    });
  }

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
          marker.innerHTML = '<g transform="rotate(-90)">' + renderPatientMarkerSvg(snap) + '</g>';
        }
        var trailEl = document.getElementById('ovFloorplanTrail');
        if (trailEl && window.MajuSim) {
          trailEl.innerHTML = renderTrailSvg(window.MajuSim.getTrail(state.selectedId, Date.now()), FLOORPLAN_PX);
        }
      }
      return;
    }
    // live(2026-10-12 → 10-22 저녁 부스): 실측 mmWave 추적 위치를 부스 평면도 위에 올린다.
    // 레이더 좌표 → 부스 평면도 좌표는 radarToBooth(10-22 저녁, 마스킹 영역을 직사각형으로 펼침).
    // 10-22 저녁: 랩실 1인실 그림 대신 부스 영역(레이더 마스킹 영역) 전체를 평면도로 쓴다(buildBoothSvg)
    sub.textContent = '부스 평면도 · 실측 위치(mmWave 추적, 마스킹 영역 = 평면도 전체)';
    var room2 = { w: BOOTH.w, h: BOOTH.h, bed: boothBed() };
    if (el.dataset.base !== wantBase) {
      el.dataset.base = wantBase;
      state.liveTrail = [];
      el.innerHTML = buildBoothSvg(FLOORPLAN_PX) +
        '<div class="floorplan-legend">' +
        '<span><i class="dot" style="color:var(--info)"></i>mmWave</span>' +
        '<span><i class="dot" style="color:var(--fg-faint)"></i>다른 추적 대상</span>' +
        '</div>' +
        '<div class="muted" id="ovFloorplanNote" style="font-size:10.5px;margin-top:6px;min-height:1.2em"></div>';
    }
    var mm = (snap && snap.mmwave) || {};
    var tofLive = (snap && snap.tof) || {};
    var now = Date.now();
    var toRoom = function (t) { return radarToBooth(t.x, t.y); };
    // 마스킹 영역 = 평면도라 안에 들어온 추적은 모두 그린다(네 모서리가 정확한 직사각형이 아니라 가장자리는 0.3m 여유)
    var inside = function (p) { return p && p.x >= -0.3 && p.y >= -0.3 && p.x <= room2.w + 0.3 && p.y <= room2.h + 0.3; };
    var targets = (mm.targets || []).map(function (t) { return { t: t, p: toRoom(t) }; }).filter(function (o) { return inside(o.p); });
    var main = null;
    if (mm.mainTid != null) main = targets.filter(function (o) { return String(o.t.tid) === String(mm.mainTid); })[0] || null;
    // 10-22 저녁: 환자는 레이더 판정기가 따라가는(클릭으로 지정한) 번호만 그린다. 예전엔 환자가 없어도 사람이 1명이면 그 사람,
    // 여럿이면 침대에 가까운 사람을 환자처럼 그려서 '잡힌 줄 알고' 진행했는데 실제로는 환자 없음(낙상 판정 안 함)이었다
    if (!main && !mm.connected && targets.length === 1) main = targets[0];
    if (!main && !mm.connected && targets.length > 1) {
      var ref = state.liveLastMain && now - state.liveLastMain.at < 10000 ? state.liveLastMain
        : (tofLive.inBed === true ? { x: room2.bed.x + room2.bed.w / 2, y: room2.bed.y + room2.bed.h / 2 } : null);
      if (ref) main = targets.slice().sort(function (a, b) {
        return Math.hypot(a.p.x - ref.x, a.p.y - ref.y) - Math.hypot(b.p.x - ref.x, b.p.y - ref.y);
      })[0];
    }
    if (main) state.liveLastMain = { x: main.p.x, y: main.p.y, z: main.t.z, at: now };
    var lvl2 = dominantOf(tofLive.riskLevel, mm.riskLevel, true, true);
    var markerEl = document.getElementById('ovFloorplanMarker');
    var othersEl = document.getElementById('ovFloorplanOthers');
    var note = '';
    var pos2 = null, activity = 'walking';
    var lastMain = state.liveLastMain;
    if (main) {
      pos2 = main.p;
      var walkingNow = typeof mm.speed === 'number' && mm.speed > 0.05;
      // ToF가 침상 재실로 보면 바닥 낙상 아님(서버 mmw_logic과 같은 조건): 앉으면 레이더 높이가 0.3m대로 잡힌다
      // 낙상 표시는 레이더 리더의 프레임 단위 판정(mmWave/fall_detect.py)을 그대로 따른다(2026-10-12):
      // fallen(급격한 하강 즉시 / 바닥 높이에서 3초 넘게) → 빨간 '낙상'. fast_drop은 예전 호환('?')
      var fallHere = mm.fall && String(mm.fall.tid) === String(main.t.tid) ? mm.fall : null;
      activity = tofLive.inBed === true ? 'inBed'
        : fallHere ? (fallHere.stage === 'fallen' ? 'fallen' : 'checking') : (walkingNow ? 'walking' : 'standing');
      if (activity === 'fallen') lvl2 = 'critical';
      else if (activity === 'checking' && lvl2 !== 'critical') lvl2 = 'warning';
      if (tofLive.inBed === true) note = '레이더 추적 위치 · ToF 침상 재실';
    } else if (mm.fall) {
      // 넘어진 뒤 추적 번호가 바뀌어 환자로 못 고른 경우에도 낙상 위치를 보여준다
      pos2 = toRoom(mm.fall);
      activity = mm.fall.stage === 'fallen' ? 'fallen' : 'checking';
      lvl2 = mm.fall.stage === 'fallen' ? 'critical' : 'warning';
    } else if (lastMain && now - lastMain.at < 10000) {
      // 가만히 있으면 레이더 추적이 잠깐 끊긴다 → 10초 동안은 마지막 레이더 위치를 그대로 보여준다
      pos2 = { x: lastMain.x, y: lastMain.y };
      activity = tofLive.inBed === true ? 'inBed' : 'standing';
      note = '마지막 레이더 위치(' + Math.round((now - lastMain.at) / 1000) + '초 전)';
    } else if (tofLive.inBed === true) {
      // 레이더가 침대 위 사람을 놓쳐도 ToF가 재실을 보면 침대 가운데에 표시한다
      pos2 = { x: room2.bed.x + room2.bed.w / 2, y: room2.bed.y + room2.bed.h / 2 };
      activity = 'inBed';
      note = 'ToF 재실 판정으로 침대 위에 표시 — 레이더 추적 위치 아님';
    }
    // 2026-10-16: 위치를 받을 때마다(0.18초) 순간이동시키지 않고 목표만 정해 두고, 화면 주사율로 부드럽게 따라가게 한다(fpAnimate)
    if (markerEl) {
      markerEl.style.transition = 'none';
      if (pos2) {
        var mk = '<g>' + renderPatientMarkerSvg({ level: lvl2 || 'normal', activity: activity }) + '</g>';   // 부스 그림은 회전하지 않음
        if (markerEl.dataset.svg !== mk) { markerEl.innerHTML = mk; markerEl.dataset.svg = mk; }
      } else { markerEl.innerHTML = ''; markerEl.dataset.svg = ''; }
    }
    fpSetGoals(pos2, main ? main.t.tid : null, targets.filter(function (o) { return o !== main; }));
    var lockNote = mm.patientLock === 'manual' ? '환자: 화면에서 지정 — 다른 점을 누르면 바뀝니다'
      : mm.patientLock === 'auto' ? '환자: 방에 혼자 있어 자동 지정 — 다른 점을 누르면 바뀝니다'
      : mm.bedPark ? '환자 침대 위(누우면 레이더가 놓침) — 내려오면 다시 따라갑니다'
      : (mm.connected && mm.mainTid == null ? '환자 없음 — 회색 점을 눌러 환자를 지정하세요' : '');
    if (lockNote && !note) note = lockNote;
    // 최근 2.5초 이동 경로(레이더로 잡힌 환자만)
    state.liveTrail = (state.liveTrail || []).filter(function (p) { return now - p.at < 2500; });
    if (main && activity !== 'inBed') state.liveTrail.push({ x: pos2.x, y: pos2.y, at: now, activity: activity });
    var trailEl2 = document.getElementById('ovFloorplanTrail');
    if (trailEl2) trailEl2.innerHTML = renderTrailSvg(state.liveTrail.map(function (p) {
      return { x: p.x, y: p.y, ageMs: now - p.at, activity: p.activity };
    }), FLOORPLAN_PX);
    if (!pos2) note = mm.connected ? '방 안에 추적된 사람이 없습니다.' : 'mmWave 수신 대기 중';
    else if (main && activity === 'standing' && !note) note = '정지 상태(보행 아님)';
    var noteEl2 = document.getElementById('ovFloorplanNote');
    if (noteEl2) noteEl2.textContent = note;
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

  /* ---------------- 낙상 위험도 · 거동 범위 (server/fall_risk.py, 2026-10-12) ---------------- */
  var FR_OPTION_KO = { no: '아니오', yes: '예', none: '없음·침상 안정·간호사 부축', cane_walker: '지팡이·목발·보행기',
    furniture: '가구를 짚고 이동', normal: '정상·침상 안정·휠체어', weak: '약함', impaired: '장애',
    oriented: '자기 능력을 앎', forgets_limitations: '한계를 과대평가·잊음' };
  var FR_MOBILITY = [['free', '자유 거동'], ['toilet_only', '화장실 정도 단독'], ['assisted', '보조 시에만 거동'], ['bed_rest', '절대 안정']];
  var FR_GROUP_PILL = { low: 'ok', moderate: 'caution', high: 'warning' };
  var FR_KIND_KO = { initial: '초기 평가', assessment: '재평가', auto_refresh: '자동 초안 갱신', gait_warning: '보행 이상',
    floor_critical: '낙상 감지', nurse_fall: '낙상 기록', decay: '안정 감점', reset: '변동 점수 초기화' };

  function renderFallRisk(fr) {
    if (!fr) return '<div class="card-h"><div class="card-title">낙상 위험도 · 거동 범위</div></div><div class="empty-state">위험도 정보가 없습니다.</div>';
    var rows = Object.keys(fr.morse).map(function (key) {
      var item = fr.morse[key];
      var opts = item.options.map(function (o) {
        return '<option value="' + o + '"' + (o === item.value ? ' selected' : '') + '>' + escapeHtml(FR_OPTION_KO[o] || o) + '</option>';
      }).join('');
      return '<tr><td>' + escapeHtml(item.label) + '</td><td><select data-morse="' + key + '">' + opts + '</select></td>' +
        '<td class="num">' + item.points + '</td></tr>';
    }).join('');
    var mob = FR_MOBILITY.map(function (m) {
      return '<option value="' + m[0] + '"' + (m[0] === fr.mobility ? ' selected' : '') + '>' + m[1] + '</option>';
    }).join('');
    var evs = fr.recent_events || [];
    var hist = evs.map(function (e) {
      var d = e.delta ? (e.delta > 0 ? '+' : '') + Math.round(e.delta * 10) / 10 : '';
      return '<div class="hist-row"><div class="hist-main"><span class="hist-time mono">' + escapeHtml(String(e.ts || '').replace('T', ' ').slice(5, 16)) + '</span>' +
        '<span class="hist-kind">' + escapeHtml(FR_KIND_KO[e.kind] || e.kind) + '</span>' +
        (d ? '<span class="hist-delta ' + (e.delta > 0 ? 'up' : 'down') + '">' + d + '</span>' : '') +
        '<span class="hist-score num">' + Math.round(e.score_after) + '점</span></div>' +
        (e.reason ? '<div class="hist-reason">' + escapeHtml(e.reason) + '</div>' : '') + '</div>';
    }).join('');
    var src = fr.source === 'auto' ? ' · 기록 기반 자동 초안(간호사 확인 필요)' : fr.source === 'live_default_low' ? ' · 실측 환자 기본값(저위험)' : '';
    return '<div class="card-h"><div><div class="card-title">낙상 위험도 · 거동 범위</div>' +
      '<div class="card-sub">Morse 낙상 척도 + 센서·간호 기록 변동 점수 · 낙상 확률 아님' + escapeHtml(src) + '</div></div>' +
      '<span class="pill ' + (FR_GROUP_PILL[fr.group] || '') + '">' + escapeHtml(fr.group_ko) + ' ' + fr.score + '점</span></div>' +
      '<div class="sensor-metrics">' +
      '<div class="metric"><span class="metric-label">Morse 기본</span><span class="num metric-value">' + fr.morse_score + '</span></div>' +
      '<div class="metric"><span class="metric-label">변동 점수</span><span class="num metric-value">' + fr.dynamic_points + '</span></div>' +
      '<div class="metric"><span class="metric-label">거동 범위</span><span class="metric-value">' + escapeHtml(fr.mobility_ko) + '</span></div>' +
      '</div>' +
      (hist ? '<details class="fold"><summary>점수 변동 기록 <span class="fold-count">' + evs.length + '건</span></summary><div class="hist-list">' + hist + '</div></details>' : '') +
      '<details class="fold fr-edit"><summary>Morse 재평가 · 거동 범위</summary>' +
      '<table class="fr-table"><thead><tr><th>항목</th><th>평가</th><th>점수</th></tr></thead><tbody>' + rows + '</tbody></table>' +
      '<label>거동 범위 <select id="frMobility">' + mob + '</select></label> ' +
      '<button class="btn sm" id="frSave" type="button">재평가 저장</button></details>' +
      '<details class="fold fr-edit"><summary>낙상 기록 (간호사)</summary>' +
      '<input id="frFallNote" type="text" maxlength="200" placeholder="상황 메모 (예: 화장실 앞에서 미끄러짐)" style="width:100%">' +
      '<label><input id="frWorsened" type="checkbox"> 상태 악화 — 거동 범위를 절대 안정으로 낮춤</label> ' +
      '<button class="btn sm" id="frFall" type="button">낙상 기록 저장</button></details>' +
      '<details class="fold fr-edit"><summary>변동 점수 초기화</summary>' +
      '<div class="muted" style="font-size:11px;margin-bottom:6px">센서·간호 기록으로 쌓인 변동 점수(지금 ' + fr.dynamic_points +
      '점)만 0으로 되돌립니다. Morse 평가와 거동 범위는 그대로입니다. 오탐 정리나 시험 후에 사용하세요.</div>' +
      '<button class="btn sm" id="frReset" type="button">변동 점수 0으로 초기화</button></details>' +
      '<div class="muted" id="frMsg" style="font-size:11px;min-height:1.2em"></div>';
  }

  function wireFallRisk(bedId) {
    var msg = function (t) { var el = $('#frMsg'); if (el) el.textContent = t; };
    var after = function (res) {
      if (!res || !res.success) { msg((res && res.message) || '저장하지 못했습니다.'); return; }
      loadDetail(bedId).then(function () {
        if (bedId !== state.selectedId) return;
        renderOverview();
        if (state.drawer === 'risk') { renderDrawerBody(); msg('저장했습니다.'); }
      });
    };
    var save = $('#frSave');
    if (save) save.addEventListener('click', function () {
      var morse = {};
      document.querySelectorAll('select[data-morse]').forEach(function (el) { morse[el.getAttribute('data-morse')] = el.value; });
      api('/api/dashboard/patients/' + encodeURIComponent(bedId) + '/fall-risk', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ morse: morse, mobility: $('#frMobility').value })
      }).then(after).catch(function () { msg('저장하지 못했습니다.'); });
    });
    var reset = $('#frReset');
    if (reset) reset.addEventListener('click', function () {
      if (!window.confirm('변동 점수를 0으로 초기화할까요? (Morse 평가·거동 범위는 유지)')) return;
      api('/api/dashboard/patients/' + encodeURIComponent(bedId) + '/fall-risk/reset', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ by: 'nurse' })
      }).then(after).catch(function () { msg('초기화하지 못했습니다.'); });
    });
    var fall = $('#frFall');
    if (fall) fall.addEventListener('click', function () {
      api('/api/dashboard/patients/' + encodeURIComponent(bedId) + '/falls', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: $('#frFallNote').value, worsened: $('#frWorsened').checked })
      }).then(after).catch(function () { msg('저장하지 못했습니다.'); });
    });
  }

  /* ---------------- 보행 저하 추세 예측 (server/gait_trend.py, 2026-10-13) ---------------- */
  var GT_LEVEL_PILL = { normal: 'ok', caution: 'caution', warning: 'warning' };

  function gaitTrendChart(m, unitScale, unit) {
    // 하루 대표값(점)·칼만 추정 수준(선)·앞으로 14일 예측(띠: 80% 범위)
    var hist = m.history || [];
    if (!hist.length) return '';
    var W = 520, H = 150, P = 28;
    var t0 = new Date(hist[0].date).getTime(), day = 86400000;
    var lastX = (new Date(hist[hist.length - 1].date).getTime() - t0) / day;
    var maxX = lastX + 14;
    var ys = [];
    hist.forEach(function (h) { ys.push(h.obs, h.level); });
    [m.forecast7, m.forecast14].forEach(function (f) { if (f) ys.push(f.lo80, f.hi80); });
    var lo = Math.min.apply(null, ys), hi = Math.max.apply(null, ys), pad = (hi - lo) * 0.1 || 0.05;
    lo -= pad; hi += pad;
    var X = function (d) { return P + (W - 2 * P) * d / maxX; };
    var Y = function (v) { return H - P + 6 - (H - 2 * P) * (v - lo) / (hi - lo); };
    var dx = function (h) { return (new Date(h.date).getTime() - t0) / day; };
    var dots = hist.map(function (h) { return '<circle cx="' + X(dx(h)).toFixed(1) + '" cy="' + Y(h.obs).toFixed(1) + '" r="2.2" fill="var(--fg-faint)"/>'; }).join('');
    var line = hist.map(function (h, i) { return (i ? 'L' : 'M') + X(dx(h)).toFixed(1) + ',' + Y(h.level).toFixed(1); }).join(' ');
    var cur = hist[hist.length - 1];
    var band = 'M' + X(lastX).toFixed(1) + ',' + Y(m.current).toFixed(1) +
      ' L' + X(lastX + 7).toFixed(1) + ',' + Y(m.forecast7.hi80).toFixed(1) + ' L' + X(lastX + 14).toFixed(1) + ',' + Y(m.forecast14.hi80).toFixed(1) +
      ' L' + X(lastX + 14).toFixed(1) + ',' + Y(m.forecast14.lo80).toFixed(1) + ' L' + X(lastX + 7).toFixed(1) + ',' + Y(m.forecast7.lo80).toFixed(1) + ' Z';
    var fline = 'M' + X(lastX).toFixed(1) + ',' + Y(m.current).toFixed(1) + ' L' + X(lastX + 7).toFixed(1) + ',' + Y(m.forecast7.mean).toFixed(1) +
      ' L' + X(lastX + 14).toFixed(1) + ',' + Y(m.forecast14.mean).toFixed(1);
    var lab = function (v) { return (v * unitScale).toFixed(0) + unit; };
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;max-width:560px" role="img" aria-label="' + escapeHtml(m.ko) + ' 추세와 14일 예측">' +
      '<path d="' + band + '" fill="var(--info)" opacity="0.15"/>' +
      '<path d="' + fline + '" fill="none" stroke="var(--info)" stroke-width="1.6" stroke-dasharray="4 3"/>' +
      dots + '<path d="' + line + '" fill="none" stroke="var(--accent)" stroke-width="1.8"/>' +
      '<line x1="' + X(lastX) + '" y1="' + (P - 10) + '" x2="' + X(lastX) + '" y2="' + (H - P + 6) + '" stroke="var(--line)" stroke-dasharray="2 2"/>' +
      '<text x="' + X(lastX) + '" y="' + (P - 14) + '" font-size="9" text-anchor="middle" fill="var(--fg-faint)">오늘</text>' +
      '<text x="4" y="' + Y(hi - pad).toFixed(1) + '" font-size="9" fill="var(--fg-faint)">' + lab(hi - pad) + '</text>' +
      '<text x="4" y="' + Y(lo + pad).toFixed(1) + '" font-size="9" fill="var(--fg-faint)">' + lab(lo + pad) + '</text>' +
      '<text x="' + (W - P) + '" y="' + (H - 4) + '" font-size="9" text-anchor="end" fill="var(--fg-faint)">+14일</text></svg>';
  }

  function renderGaitForecast(gt) {
    var head = '<div class="card-h"><div><div class="card-title">보행 추세 예측</div>' +
      '<div class="card-sub">하루 보행 대표값 → 칼만 필터 추세 → 7·14일 예측 · 기준: Phillips 2017(7일 누적 감소) · 낙상 확률 아님</div></div>';
    if (!gt) return head + '</div><div class="empty-state">예측 정보가 없습니다.</div>';
    if (gt.status !== 'ok') {
      var msg = gt.message || ('보행 기록 ' + (gt.days_with_data || 0) + '일 / 필요 ' + (gt.min_days || 14) + '일 — 기록이 쌓이면 예측합니다.');
      return head + '<span class="pill sm">예측 대기</span></div><div class="empty-state">' + escapeHtml(msg) + '</div>';
    }
    var body = Object.keys(gt.metrics).map(function (k) {
      var m = gt.metrics[k];
      var cm = k === 'speed' ? ' cm/s' : ' cm';
      if (m.status !== 'ok') return '<div class="muted" style="font-size:12px">' + escapeHtml(m.message || '') + '</div>';
      var scale = 100, unit = k === 'speed' ? 'cm/s' : 'cm';
      return '<div style="margin:10px 0 4px"><b>' + escapeHtml(m.ko) + '</b> ' +
        '<span class="pill sm ' + (GT_LEVEL_PILL[m.level] || '') + '">' + ({ normal: '정상', caution: '주의', warning: '경고' }[m.level]) + '</span></div>' +
        '<div class="sensor-metrics">' +
        '<div class="metric"><span class="metric-label">지금 추정</span><span class="num metric-value">' + (m.current * scale).toFixed(1) + '<span class="unit">' + unit + '</span></span></div>' +
        '<div class="metric"><span class="metric-label">7일 변화</span><span class="num metric-value">' + (m.change7_cm > 0 ? '+' : '') + m.change7_cm.toFixed(1) + '<span class="unit">±' + m.change7_sd_cm.toFixed(1) + cm + '</span></span></div>' +
        '<div class="metric"><span class="metric-label">7일 뒤 예측</span><span class="num metric-value">' + (m.forecast7.mean * scale).toFixed(1) + '<span class="unit">(' + (m.forecast7.lo80 * scale).toFixed(0) + '~' + (m.forecast7.hi80 * scale).toFixed(0) + ')</span></span></div>' +
        '<div class="metric"><span class="metric-label">경고 기준(−' + m.warn_cm + cm + ') 넘을 확률</span><span class="num metric-value">' + Math.round(m.p_decline_warning * 100) + '%</span></div>' +
        '</div>' + gaitTrendChart(m, scale, unit);
    }).join('');
    return head + '<span class="pill ' + (GT_LEVEL_PILL[gt.level] || '') + '">' + escapeHtml(gt.level_ko || '') + '</span></div>' + body +
      '<div class="muted" style="font-size:11px;margin-top:6px">점: 하루 대표값 · 실선: 추정 수준 · 점선과 띠: 예측과 80% 범위. ' + escapeHtml(gt.caveat || '') + '</div>';
  }

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
        if (bedId === state.selectedId) { renderOverview(); if (state.drawer === 'prior') renderDrawerBody(); }
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
    // 2026-10-13 디자인 개편: 오른쪽에는 요약만, 전체 내용은 '자세히'를 누르면 오른쪽 패널에서 연다
    $('#ovFallRisk').innerHTML = renderFallRiskSummary(state.detail && state.detail.fall_risk);
    var quickReset = $('#frQuickReset');
    if (quickReset) quickReset.addEventListener('click', function () {
      var bedId = state.selectedId;
      if (!window.confirm('변동 점수를 0으로 초기화할까요? (시험·오탐 정리용 · Morse 평가·거동 범위는 유지)')) return;
      quickReset.disabled = true;
      api('/api/dashboard/patients/' + encodeURIComponent(bedId) + '/fall-risk/reset', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ by: 'nurse' })
      }).then(function () { return loadDetail(bedId); }).then(function () {
        if (bedId === state.selectedId) renderOverview();
      }).catch(function () { quickReset.disabled = false; window.alert('초기화하지 못했습니다.'); });
    });
    $('#ovGaitTrend').innerHTML = renderGaitForecastSummary(state.detail && state.detail.gait_trend);
    $('#ovGaitPriorCard').innerHTML = renderGaitPriorSummary(state.detail && state.detail.gait_prior);
    $('#ovRiskFactors').innerHTML = renderRiskFactors(state.detail && state.detail.risk_factors);
    $('#ovMedications').innerHTML = renderMedications(state.detail && state.detail.medications);
    lastKpiHtml = '';
    renderKpis(currentSensorMode(), currentSnapshot(currentSensorMode()));
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


  /* ---------------- 리포트 그래프(2026-10-13): 서버 report_charts.py가 준 숫자를 SVG로 그린다 ---------------- */
  var CHART_COLORS = ['#e11d48', '#f59e0b', '#0ea5e9', '#10b981', '#8b5cf6', '#64748b', '#ec4899', '#14b8a6', '#a3a3a3'];
  var PIE_COLOR = { '낙상': '#e11d48', '낙상 후 일어남': '#10b981', '쪼그림·주의': '#f59e0b', '침상 알림': '#0ea5e9', '침상 이탈': '#f97316',
    '침상 가장자리': '#eab308', '위험 자세': '#dc2626', '이탈 후 확인(정상)': '#22c55e', '이탈 후 확인(이상)': '#b91c1c',
    '뒤척임': '#a78bfa', '수면': '#6366f1', '보행': '#14b8a6', '보행 변화': '#0d9488', '기타': '#94a3b8' };

  function chartCard(title, body, note) {
    return '<div class="rchart"><div class="rchart-title">' + escapeHtml(title) + '</div>' + body +
      (note ? '<div class="rchart-note">' + escapeHtml(note) + '</div>' : '') + '</div>';
  }

  function donutChart(spec) {
    var items = (spec && spec.items || []).filter(function (it) { return it.value > 0; });
    if (!items.length) return chartCard(spec ? spec.title : '사건 종류', '<div class="empty-state">기록된 사건이 없습니다.</div>');
    var total = items.reduce(function (a, it) { return a + it.value; }, 0);
    var R = 52, r = 32, cx = 64, cy = 64, ang = -Math.PI / 2, paths = '';
    items.forEach(function (it, i) {
      var frac = it.value / total, a2 = ang + frac * Math.PI * 2;
      var col = PIE_COLOR[it.label] || CHART_COLORS[i % CHART_COLORS.length];
      if (frac >= 0.9999) {
        paths += '<circle cx="' + cx + '" cy="' + cy + '" r="' + ((R + r) / 2) + '" fill="none" stroke="' + col + '" stroke-width="' + (R - r) + '"/>';
      } else {
        var large = frac > 0.5 ? 1 : 0;
        var p = function (rad, a) { return (cx + rad * Math.cos(a)).toFixed(2) + ' ' + (cy + rad * Math.sin(a)).toFixed(2); };
        paths += '<path d="M' + p(R, ang) + ' A' + R + ' ' + R + ' 0 ' + large + ' 1 ' + p(R, a2) + ' L' + p(r, a2) + ' A' + r + ' ' + r + ' 0 ' + large + ' 0 ' + p(r, ang) + 'Z" fill="' + col + '"><title>' +
          escapeHtml(it.label + ' ' + it.value + spec.unit) + '</title></path>';
      }
      it._col = col; ang = a2;
    });
    var svg = '<svg viewBox="0 0 128 128" class="rchart-donut" role="img">' + paths +
      '<text x="64" y="62" text-anchor="middle" class="rchart-donut-n">' + total + '</text><text x="64" y="78" text-anchor="middle" class="rchart-donut-u">' + escapeHtml(spec.unit) + '</text></svg>';
    var legend = '<div class="rchart-legend">' + items.map(function (it) {
      return '<div><i style="background:' + it._col + '"></i><span>' + escapeHtml(it.label) + '</span><b class="num">' + Math.round(it.value / total * 100) + '%</b><span class="faint">' + it.value + '</span></div>';
    }).join('') + '</div>';
    return chartCard(spec.title, '<div class="rchart-pie">' + svg + legend + '</div>');
  }

  function lineChart(spec, opts) {
    opts = opts || {};
    var pts = (spec && spec.points || []).filter(function (p) { return typeof p.y === 'number'; });
    if (pts.length < (opts.minPoints || 2)) {
      return chartCard(spec ? spec.title : '', '<div class="empty-state">' + escapeHtml(opts.empty || '그래프를 그릴 만큼 기록이 쌓이지 않았습니다.') + '</div>');
    }
    var W = 320, H = 150, L = 36, Rm = 8, T = 10, B = 26;
    var numericX = pts.every(function (p) { return typeof p.x === 'number'; });
    var xs = numericX ? pts.map(function (p) { return p.x; }) : pts.map(function (p, i) { return i; });
    var ys = pts.map(function (p) { return p.y; }).concat((spec.marks || []).map(function (m) { return m.y; }));
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (opts.zeroBase) y0 = Math.min(0, y0);
    if (y1 - y0 < 1e-6) { y1 += 1; y0 -= 1; }
    var pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
    var X = function (v) { return L + (x1 === x0 ? 0.5 : (v - x0) / (x1 - x0)) * (W - L - Rm); };
    var Y = function (v) { return T + (1 - (v - y0) / (y1 - y0)) * (H - T - B); };
    var grid = '';
    for (var g = 0; g <= 3; g++) {
      var gv = y0 + (y1 - y0) * g / 3;
      grid += '<line x1="' + L + '" x2="' + (W - Rm) + '" y1="' + Y(gv).toFixed(1) + '" y2="' + Y(gv).toFixed(1) + '" class="rchart-grid"/>' +
        '<text x="' + (L - 4) + '" y="' + (Y(gv) + 3).toFixed(1) + '" text-anchor="end" class="rchart-ax">' + (Math.abs(gv) >= 10 ? Math.round(gv) : gv.toFixed(2)) + '</text>';
    }
    var marks = (spec.marks || []).map(function (m) {
      return '<line x1="' + L + '" x2="' + (W - Rm) + '" y1="' + Y(m.y).toFixed(1) + '" y2="' + Y(m.y).toFixed(1) + '" class="rchart-mark"/>' +
        '<text x="' + (W - Rm) + '" y="' + (Y(m.y) - 3).toFixed(1) + '" text-anchor="end" class="rchart-mark-t">' + escapeHtml(m.label) + '</text>';
    }).join('');
    var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + X(xs[i]).toFixed(1) + ' ' + Y(p.y).toFixed(1); }).join(' ');
    var area = d + ' L' + X(xs[xs.length - 1]).toFixed(1) + ' ' + (H - B) + ' L' + X(xs[0]).toFixed(1) + ' ' + (H - B) + 'Z';
    var dots = pts.length <= 40 ? pts.map(function (p, i) {
      return '<circle cx="' + X(xs[i]).toFixed(1) + '" cy="' + Y(p.y).toFixed(1) + '" r="2.6" class="rchart-dot"><title>' + escapeHtml(p.x + ' · ' + p.y + ' ' + spec.unit) + '</title></circle>';
    }).join('') : '';
    var zeroLine = numericX && x0 < 0 && x1 > 0 ? '<line x1="' + X(0).toFixed(1) + '" x2="' + X(0).toFixed(1) + '" y1="' + T + '" y2="' + (H - B) + '" class="rchart-now"/>' +
      '<text x="' + (X(0) + 3).toFixed(1) + '" y="' + (H - B - 4) + '" class="rchart-ax">낙상 판정</text>' : '';
    var xl = numericX ? [x0, (x0 + x1) / 2, x1].map(function (v) { return '<text x="' + X(v).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" class="rchart-ax">' + (Math.round(v * 10) / 10) + '</text>'; }).join('') :
      [0, Math.floor((pts.length - 1) / 2), pts.length - 1].filter(function (v, i, a) { return a.indexOf(v) === i; }).map(function (i) {
        return '<text x="' + X(xs[i]).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" class="rchart-ax">' + escapeHtml(String(pts[i].x)) + '</text>'; }).join('');
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="rchart-line" role="img">' + grid + marks + zeroLine +
      '<path d="' + area + '" class="rchart-area"/><path d="' + d + '" class="rchart-path"/>' + dots + xl + '</svg>';
    var last = pts[pts.length - 1].y;
    return chartCard(spec.title + ' (' + spec.unit + ')', svg, opts.note || (spec.x_label ? '가로축: ' + spec.x_label : '최근 값 ' + last + ' ' + spec.unit));
  }

  function renderReportCharts(charts, isFall) {
    if (!charts) return '';
    var parts = [];
    if (isFall && charts.fall_trace) parts.push(lineChart(charts.fall_trace, { note: '0초 = 낙상 판정 시각. 점선 = 서 있음·바닥 높이 기준' }));
    parts.push(donutChart(charts.activity_pie));
    parts.push(lineChart(charts.gait_line, { minPoints: 2, empty: '하루 보행 기록이 2일 이상 쌓이면 그려집니다.' }));
    parts.push(lineChart(charts.risk_line, { zeroBase: true, empty: '위험도 점수 변동 기록이 부족합니다.' }));
    return '<div class="report-section"><div class="uppercase-label">한눈에 보기</div><div class="rchart-grid-wrap">' + parts.join('') + '</div></div>';
  }

  var FALL_FIELDS = [['요약', '요약', true], ['낙상_양상', '낙상 양상'], ['추정_원인', '가능성 있는 원인'], ['직전_상황', '직전 30분 상황'],
    ['즉시_확인할_것', '지금 확인할 것', true], ['재발_방지_제안', '재발 방지 제안'], ['데이터_한계', '데이터 한계']];

  function renderFallIncident(report) {
    var ev = report['사건_수치'] || {};
    var chips = [['양상', ev['양상']], ['하강 시간', ev['하강_시간_초'] != null ? ev['하강_시간_초'] + '초' : null],
      ['세로 속도', ev['세로_속도_최저_m_s'] != null ? ev['세로_속도_최저_m_s'] + ' m/s' : null],
      ['바닥에 머묾', ev['바닥에_머문_초'] != null ? ev['바닥에_머문_초'] + '초' : null],
      ['몸 중심 / 평소', ev['몸_중심_높이_m'] != null ? ev['몸_중심_높이_m'] + ' / ' + ev['평소_높이_m'] + ' m' : null]]
      .filter(function (c) { return c[1] != null && c[1] !== ''; })
      .map(function (c) { return '<div class="fall-chip"><span>' + escapeHtml(c[0]) + '</span><b>' + escapeHtml(c[1]) + '</b></div>'; }).join('');
    var html = '<div class="fall-incident-head"><span class="pill critical">낙상 사건 리포트</span><span class="mono muted">' +
      escapeHtml(String(report['발생_시각'] || '').replace('T', ' ')) + '</span></div>' +
      (chips ? '<div class="fall-chips">' + chips + '</div>' : '');
    var causes = report['원인_후보'] || [];
    html += renderReportCharts(report.charts, true);
    FALL_FIELDS.forEach(function (f) {
      var v = report[f[0]];
      if (!v) return;
      v = String(v).replace(/\s+(?=\d{1,2}\.\s)/g, '\n');   // '1. … 2. …' 한 줄 목록을 줄마다
      html += '<div class="report-field' + (f[2] ? ' report-field-highlight' : '') + '"><div class="report-field-label">' + escapeHtml(f[1]) + '</div>' +
        '<div class="report-field-body" style="white-space:pre-line">' + escapeHtml(v) + '</div></div>';
    });
    if (causes.length) {
      html += '<details class="fold"><summary>원인 후보와 근거(규칙으로 뽑은 것) <span class="fold-count">' + causes.length + '개</span></summary>' +
        causes.map(function (c) { return '<div class="hist-row"><div class="hist-main"><span class="hist-kind">' + escapeHtml(c['원인']) + '</span></div><div class="hist-reason">' + escapeHtml(c['근거']) + '</div></div>'; }).join('') + '</details>';
    }
    html += '<div class="muted" style="font-size:11.5px;margin-top:10px">레이더 낙상 감지와 원인 후보는 기록에서 뽑은 가능성이며 진단이 아닙니다. 낙상 확률도 아닙니다.</div>';
    return html;
  }

  function renderReportResult(report, meta, targetSel) {
    var el = $(targetSel || '#reportModalBody');
    if (!el) return;
    if (!report || typeof report !== 'object') { el.innerHTML = '<div class="empty-state">리포트 내용이 없습니다.</div>'; return; }
    if (report['리포트_종류'] === '낙상 사건') { el.innerHTML = renderFallIncident(report); return; }
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
    html += renderReportCharts(report.charts, false);
    REPORT_SECTIONS.forEach(function (sec) {
      var parts = sec.keys.map(function (k) {
        var val = report[k];
        if (!val) return '';
        var label = FIELD_LABELS[k] || k;
        var cls = sec.highlight === k ? ' report-field-highlight' : '';
        return '<div class="report-field' + cls + '"><div class="report-field-label">' + escapeHtml(label) + '</div>' +
          '<div class="report-field-body" style="white-space:pre-line">' + escapeHtml(val) + '</div></div>';
      }).join('');
      if (!parts) return;
      html += '<div class="report-section"><div class="uppercase-label">' + escapeHtml(sec.title) + '</div>' + parts + '</div>';
    });
    html += renderCrossValidation(meta && meta.risk, meta && (meta.data_confidence != null ? meta.data_confidence : meta.confidence));
    el.innerHTML = html || '<div class="empty-state">리포트 내용이 없습니다.</div>';
  }

  function analysisRowHtml(a) {
    var isFall = a.analysis_type === 'fall_incident';
    var score = isFall ? '<span class="pill critical sm">낙상</span>' : a.risk_score != null ? Math.round(a.risk_score * 100) + '%' : '—';
    return '<button class="analysis-row' + (isFall ? ' analysis-row-fall' : '') + '" data-analysis-id="' + a.id + '" type="button">' +
      '<span class="mono" style="font-size:11px">' + escapeHtml(fmtTime(a.created_at)) + '</span>' +
      '<span class="num">' + score + '</span>' +
      '<span class="analysis-summary">' + (isFall ? '' : window.MajubomReportReview.badge(a.review_summary)) +
      '<span class="muted"> ' + escapeHtml((a.summary || '').slice(0, 80)) + '</span></span>' +
      '</button>';
  }

  function renderReportsData() {
    var analyses = (state.detail && state.detail.analyses) || [];
    $('#reportRiskTrend').innerHTML = renderRiskTrend(analyses.filter(function (a) { return a.analysis_type !== 'fall_incident'; }).slice().reverse());
    $('#reportHistory').innerHTML = analyses.length ?
      analyses.map(analysisRowHtml).join('') :
      '<div class="empty-state">아직 생성된 리포트가 없습니다 — 매주 자동으로 생성되고, 낙상이 감지되면 낙상 사건 리포트가 바로 만들어집니다. 위의 \'지금 리포트 만들기\'로 바로 만들 수도 있습니다.</div>';
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
    if ($('#reportModalTitle')) $('#reportModalTitle').textContent = analysis.analysis_type === 'fall_incident' ? '낙상 사건 리포트' : 'AI 종합 리포트';
    renderReportResult(analysis.report, analysis, '#reportModalBody');
    $('#reportModalEvidence').innerHTML = '';
    $('#reportModal').hidden = false;
    if (analysis.analysis_type === 'fall_incident') { reviewBox.innerHTML = ''; return; }
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

  function wireReportGenerate() {
    var btn = $('#reportGenerate'), msg = $('#reportGenerateMsg');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var bedId = state.selectedId;
      btn.disabled = true; msg.textContent = 'AI가 리포트를 쓰는 중입니다(보통 20~60초)…';
      api('/api/dashboard/analyses', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bed_id: bedId }) })
        .then(function (res) {
          if (!res || !res.job_id) { btn.disabled = false; msg.textContent = (res && res.message) || '리포트를 시작하지 못했습니다.'; return; }
          var poll = function () {
            api('/api/dashboard/analyses/' + encodeURIComponent(res.job_id)).then(function (job) {
              if (job && (job.status === 'completed' || job.status === 'failed')) {
                btn.disabled = false;
                msg.textContent = job.status === 'completed' ? '새 리포트를 저장했습니다.' : '실패: ' + ((job.error && job.error.message) || '');
                loadDetail(bedId).then(function () { if (bedId === state.selectedId && state.page === 'reports') renderReportsData(); });
                return;
              }
              setTimeout(poll, 2500);
            }).catch(function () { btn.disabled = false; msg.textContent = '상태를 확인하지 못했습니다.'; });
          };
          setTimeout(poll, 2500);
        }).catch(function () { btn.disabled = false; msg.textContent = '리포트를 시작하지 못했습니다.'; });
    });
  }

  // 10-22: 새 AI 리포트(낙상 사건 리포트 등)를 새로고침 없이 보여준다 — 4초마다 리포트 목록의 맨 위 번호만 비교해서,
  // 바뀌었으면 환자 정보를 다시 읽고 리포트 화면을 다시 그린다. 다른 탭에 있으면 'AI 리포트' 탭에 점을 찍는다.
  var reportWatch = { bed: null, top: null, busy: false };
  function watchReports() {
    var bedId = state.selectedId;
    if (!bedId || reportWatch.busy || document.hidden) return;
    reportWatch.busy = true;
    api('/api/dashboard/analyses?bed_id=' + encodeURIComponent(bedId)).then(function (res) {
      if (!res || !res.success || bedId !== state.selectedId) return;
      var top = res.analyses && res.analyses.length ? res.analyses[0].id : null;
      var known = state.detail && state.detail.analyses && state.detail.analyses.length ? state.detail.analyses[0].id : null;
      if (reportWatch.bed !== bedId) { reportWatch.bed = bedId; reportWatch.top = known; }
      if (top !== null && top !== known) {
        if (state.detail) state.detail.analyses = res.analyses;
        if (state.detailCache && state.detailCache[bedId]) state.detailCache[bedId].analyses = res.analyses;
        if (state.page === 'reports') renderReportsData();
        else { var tab = $('nav button[data-page="reports"]'); if (tab) tab.classList.add('has-new'); }
      }
      reportWatch.top = top;
    }).catch(function () { /* 다음 주기에 다시 */ }).finally(function () { reportWatch.busy = false; });
  }

  function wireReports() {
    setInterval(watchReports, 4000);
    wireReportGenerate();
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
    if (!$('#devTof') || !$('#devMmw')) return;   // 2026-10-13: 기기 탭은 개발 대시보드(/dev)로 옮겼다
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
    // 서버 연결 설정은 개발 대시보드(/dev)로 옮겼다. 같은 localStorage 키를 쓰므로 거기서 바꾸면 여기에도 적용된다.
    if ($('#connApply')) $('#connApply').addEventListener('click', function () {
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
    checkLiveFall(snapshot);
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

  // 10-13: 낙상이 뜨면 '낙상 사건 리포트'(원인 후보·그래프). 실측은 mmw_server가 판정 직후 서버에서 요청하고(브라우저가 꺼져 있어도),
  // 시뮬레이션은 여기서 요청한다. 어느 쪽이든 40초 뒤 리포트 목록을 새로 읽는다.
  function triggerAutoReport(bedId, snap, source) {
    var refresh = function () {
      if (bedId === state.selectedId) loadDetail(bedId).then(function () { if (state.page === 'reports') renderReportsData(); });
    };
    if (source === 'live') { setTimeout(refresh, 40000); return; }
    var last = lastAutoReportAt[bedId];   // 시뮬레이션 낙상은 몇 분마다 반복되므로 30분에 한 번만
    if (last && Date.now() - last < AUTO_REPORT_COOLDOWN_MS) return;
    lastAutoReportAt[bedId] = Date.now();
    var ev = { at: Date.now() / 1000, label: (snap && snap.phaseName) || '낙상(시뮬레이션)',
               note: (snap && ((snap.mmwave && snap.mmwave.reason) || (snap.tof && snap.tof.reason))) || null };
    api('/api/dashboard/patients/' + encodeURIComponent(bedId) + '/fall-report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source: 'sim', event: ev })
    }).then(function () { setTimeout(refresh, 40000); }).catch(function () { /* 알림은 이미 떴다 */ });
  }

  function showFallAlert(bedId, snap, source) {
    var patient = null;
    for (var i = 0; i < state.patients.length; i++) { if (state.patients[i].bed_id === bedId) { patient = state.patients[i]; break; } }
    var name = (patient && patient.name) || bedId;
    var room = patient && patient.room;
    $('#fallAlertBody').innerHTML =
      '<div class="fall-alert-line">' + (room ? escapeHtml(room) + '호 · ' : '') + escapeHtml(name) + ' 환자</div>' +
      '<div class="muted">낙상이 감지되었습니다 — 즉시 확인이 필요합니다. (' +
      (source === 'live' ? '실측 · mmWave 바닥 높이에 3초 넘게 머묾' : '시뮬레이션 데이터') + ' · AI 낙상 사건 리포트를 작성합니다' + ')</div>' +
      (source === 'live' && snap && snap.fall ? '<div class="faint" style="font-size:11px;margin-top:4px">' + escapeHtml((snap.fall.kind === 'fast' ? '급격한 하강' : '바닥에서 못 일어남') + ' · 몸 중심 ' +
        (snap.fall.z != null ? snap.fall.z.toFixed(2) + 'm' : '-') + ' (평소 ' + (snap.fall.ref != null ? snap.fall.ref.toFixed(2) + 'm' : '-') + ') · mmWave') + '</div>' : '') +
      '<div class="faint mono" style="font-size:11px;margin-top:6px">' + escapeHtml(fmtTime(Date.now())) + '</div>';
    $('#fallAlertModal').dataset.bedId = bedId;
    $('#fallAlertModal').hidden = false;
    triggerAutoReport(bedId, snap, source);
  }

  // 실측 낙상 의심(2026-10-12): mmWave 판정이 '바닥 근처'(몸 높이 0.4m 미만) critical로 바뀌는 순간 팝업. 1분에 한 번까지.
  var lastLiveFallAt = 0;
  function checkLiveFall(snapshot) {
    var mm = snapshot && snapshot.mmwave;
    // 실측 낙상(2026-10-12): 레이더 프레임 판정 fallen(1초 안에 바닥 높이로 / 바닥 높이에서 3초 넘게)이면 팝업. ToF 침상 재실이면 서버가 제외.
    var isFall = !!(mm && mm.fall && mm.fall.stage === 'fallen');
    if (isFall && !state.liveFallActive && Date.now() - lastLiveFallAt > 60000) {
      lastLiveFallAt = Date.now();
      showFallAlert(state.liveBedId, mm, 'live');
    }
    state.liveFallActive = isFall;
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
    wireFloorplanAssign();
    wireFallAlert();
    wireDrawer();

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
    if ($('#connBaseUrl')) $('#connBaseUrl').value = storedBase;
    if ($('#connTargetId')) $('#connTargetId').value = storedTarget;

    if (window.MajuData) {
      sensorClient = window.MajuData.createClient({ baseUrl: storedBase, targetId: storedTarget, onUpdate: onLiveUpdate,
        onLive: function (snapshot) { state.live = snapshot; checkLiveFall(snapshot); } });
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
    setInterval(renderOverviewSensors, 100);   // 10-22 시연: 0.18 → 0.1초
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
