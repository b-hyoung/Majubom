/* 개발·센서 테스트 대시보드(site/dev.html, 2026-10-13). 간호사 화면(index.html)과 분리했다.
   읽기 전용 조회 + 수동 빈 침대 캘리브레이션 버튼 + 간호사 화면 연결 설정(localStorage) 만 있다. */
(function () {
  'use strict';
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var CONN_BASE_KEY = 'majubom-conn-base', CONN_TARGET_KEY = 'majubom-conn-target', THEME_KEY = 'majubom-theme';
  var clockOffset = 0;   // Pi 시각 − 이 기기 시각(ms)
  var piNow = function () { return Date.now() + clockOffset; };

  function getJSON(path) {
    return fetch(path, { cache: 'no-store', headers: { Accept: 'application/json' } }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }
  function ageSec(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    return isFinite(t) ? (piNow() - t) / 1000 : null;
  }
  function ageText(sec) { return sec == null ? '—' : sec < 1 ? '방금' : sec < 120 ? Math.round(sec) + '초 전' : Math.round(sec / 60) + '분 전'; }
  function pill(ok, text) { return '<span class="pill sm ' + (ok === true ? 'ok' : ok === false ? 'critical' : '') + '">' + esc(text) + '</span>'; }
  function kv(rows) { return rows.map(function (r) { return '<span>' + esc(r[0]) + '</span><span>' + r[1] + '</span>'; }).join(''); }
  function num(v, d) { return typeof v === 'number' && isFinite(v) ? v.toFixed(d == null ? 2 : d) : '—'; }

  function zoneGrid(title, dist, base) {
    if (!Array.isArray(dist)) return '<div><div class="zone-title">' + esc(title) + '</div><div class="empty-state">수신 없음</div></div>';
    var cells = dist.map(function (d, i) {
      var b = base && base[i];
      var person = d > 0 && b > 0 && b <= 2300 && d < b - 150;   // 서버 occupied_zones와 같은 식
      var shade = d > 0 ? Math.max(0.08, Math.min(0.85, 1 - d / 2500)) : 0;
      var bg = d > 0 ? (person ? 'rgba(234,88,12,' + shade + ')' : 'rgba(15,118,110,' + shade + ')') : 'transparent';
      return '<div' + (d > 0 ? ' style="background:' + bg + '"' : ' class="fail"') + ' title="칸 ' + i + (b ? ' · 기준 ' + b + 'mm' : '') + '">' + (d > 0 ? d : '-1') + '</div>';
    }).join('');
    var nPerson = dist.filter(function (d, i) { var b = base && base[i]; return d > 0 && b > 0 && b <= 2300 && d < b - 150; }).length;
    return '<div><div class="zone-title">' + esc(title) + ' <span class="muted" style="font-weight:500">· 사람 칸 ' + nPerson + '</span></div><div class="zone-grid">' + cells + '</div></div>';
  }

  function render(tof, pres, live, mmw, falls) {
    // 서비스·센서 상태
    var t1 = ageSec(tof && tof.tof1 && tof.tof1.received_at), t2 = ageSec(tof && tof.tof2 && tof.tof2.received_at);
    var liveAge = live && live.ts ? (piNow() - live.ts) / 1000 : null;
    var mmwAge = mmw && mmw.received_at ? ageSec(mmw.received_at) : null;
    var items = [
      ['대시보드 서버 :5001', pres ? pill(true, '응답') : pill(false, '응답 없음'), 'ToF 수신·자세·API'],
      ['ToF 발치(tof1)', pill(t1 != null && t1 < 8, t1 != null && t1 < 8 ? '수신 중' : '끊김'), '마지막 ' + ageText(t1)],
      ['ToF 머리맡(tof2)', pill(t2 != null && t2 < 8, t2 != null && t2 < 8 ? '수신 중' : '끊김'), '마지막 ' + ageText(t2)],
      ['mmWave 서버 :5002', live ? pill(true, '응답') : pill(false, '응답 없음'), '보행 판정·낙상 기록'],
      ['레이더 리더(send_mmw)', pill(liveAge != null && liveAge < 2, liveAge != null && liveAge < 2 ? '프레임 수신 중' : '멈춤'), '위치 갱신 ' + (liveAge == null ? '—' : liveAge.toFixed(1) + '초 전') + ' · systemd majubom-mmw-reader'],
      ['6초 보행 판정', pill(mmwAge != null && mmwAge < 15, mmwAge != null && mmwAge < 15 ? '갱신 중' : '대기'), '마지막 ' + ageText(mmwAge)],
    ];
    $('#devStatus').innerHTML = items.map(function (it) {
      var cls = it[1].indexOf('pill sm ok') >= 0 ? 'kpi-good' : it[1].indexOf('pill sm critical') >= 0 ? 'kpi-bad' : 'kpi-wait';
      var text = it[1].replace(/<[^>]+>/g, '');
      return '<div class="kpi ' + cls + '"><div class="kpi-label">' + esc(it[0]) + '</div><div class="kpi-value">' + esc(text) +
        '</div><div class="kpi-foot" title="' + esc(it[2]) + '">' + esc(it[2]) + '</div></div>';
    }).join('');
    $('#devUpdated').textContent = '마지막 갱신 ' + new Date().toLocaleTimeString('ko-KR');

    // ToF
    var base = (pres && pres.__baseline) || null;
    $('#devTofGrids').innerHTML = zoneGrid('tof1 · 발치', tof && tof.tof1 && tof.tof1.distances_mm, base && base.tof1) +
      zoneGrid('tof2 · 머리맡', tof && tof.tof2 && tof.tof2.distances_mm, base && base.tof2);
    var risk = (tof && tof.risk) || {}, ba = (tof && tof.bed_alert) || {};
    $('#devTofInfo').innerHTML = kv([
      ['재실', pres ? (pres.in_bed ? pill(true, '침상 재실') : pill(null, '빈 침대')) + ' <span class="muted">(' + esc(pres.since ? '바뀐 시각 ' + pres.since.slice(11, 19) : '서버 시작 후 변화 없음') + ')</span>' : '—'],
      ['사람 칸 수', pres ? 'tof1 ' + esc(pres.tof1 && pres.tof1.occupied) + ' · tof2 ' + esc(pres.tof2 && pres.tof2.occupied) + ' <span class="muted">(들어옴 ≥8, 유지 ≥4)</span>' : '—'],
      ['자세(RF)', tof ? esc((tof.posture_ko || '—') + ' · 원시 ' + (tof.posture_raw || '—') + ' · 확신 ' + num(tof.posture_conf)) : '—'],
      ['가장자리 쏠림', esc((risk.status || '—') + (risk.y != null ? ' · 좌우 평균 ' + num(risk.y) + 'm' : '') + (risk.reason ? ' · ' + risk.reason : ''))],
      ['침상 알림', esc((ba.level_ko || ba.level || '—') + (ba.reason ? ' · ' + ba.reason : ''))],
      ['몸 일부 튀어나옴(실험)', (function (o) {
        if (!o) return '—';
        var list = function (k) { return (o.zones[k] || []).map(function (q) { return (q.sensor === 'tof1' ? '발' : '머리') + q.zone + '번(' + q.z + 'm)'; }).join(' '); };
        return pill(o.neg + o.pos >= 2 ? false : true, '가장자리① ' + o.neg + '칸 · 가장자리② ' + o.pos + '칸') +
          (o.max_z != null ? ' <span class="muted">가장 높은 지점 ' + o.max_z + 'm · ① ' + esc(list('neg') || '없음') + ' · ② ' + esc(list('pos') || '없음') + '</span>' : '');
      })(tof && tof.offbed)],
      ['가장자리 움직임(실험)', (function (e) {
        if (!e) return '<span class="muted">침상 재실일 때만 계산</span>';
        if (!e.side) return esc(e.state);
        return pill(e.state.indexOf('이탈') >= 0 ? false : null, e.state) + ' <span class="muted">쪽 ' + esc(e.side) + ' · 4초 이동 ' + num(e.net_m) +
          'm · 흔들림 폭 ' + num(e.amplitude_m) + 'm · 오간 횟수 ' + esc(e.reversals) + '</span>';
      })(tof && tof.edge_motion)],
      ['뒤척임', (function (t) {
        if (!t) return '—';
        var KO = { supine: '바로 누움', side_left: '옆으로 누움', side_right: '옆으로 누움' };
        var turn = /^side_/.test(t.from) && /^side_/.test(t.to) ? '반대쪽으로 돌아누움' : (KO[t.from] || t.from) + ' → ' + (KO[t.to] || t.to);
        return (t.active ? pill(null, '뒤척임 ' + (t.sec != null ? t.sec + '초' : '') + ': ' + turn) : (t.moving ? '움직이는 중' : '없음')) +
          ' <span class="muted">최근 10분 ' + esc(t.count_10min) + '회</span>';
      })(tof && tof.tossing)],
    ]);
    var au = (pres && pres.baseline_auto) || {};
    var AU_KO = { idle: '대기(아직 이탈 없음)', applied: '적용됨', refused: '적용 안 함(재확인 필요)', aborted: '취소(다시 누움)', skipped: '건너뜀' };
    $('#devAutocal').innerHTML = kv([
      ['기준값 저장', pres && pres.baseline_set ? 'tof1 ' + (pres.baseline_set.tof1 ? '있음' : '없음') + ' · tof2 ' + (pres.baseline_set.tof2 ? '있음' : '없음') : '—'],
      ['자동 갱신 최근 결과', pill(au.status === 'applied' ? true : au.status === 'refused' ? false : null, AU_KO[au.status] || au.status || '—')],
      ['시각', esc(au.at ? au.at.replace('T', ' ').slice(0, 19) : '—')],
      ['내용', esc(au.reason || '—')],
      ['레이더 이동', esc(au.radar_move_m != null ? au.radar_move_m + ' m' : '—')],
    ]);

    // 레이더
    var targets = (live && live.targets) || [];
    $('#devTargets').innerHTML = targets.length ? '<table class="dev-table"><thead><tr><th>추적</th><th>역할</th><th class="num">좌우 x</th><th class="num">바닥 거리 y</th><th class="num">높이 z</th><th class="num">속도 m/s</th><th class="num">반사점</th></tr></thead><tbody>' +
      targets.map(function (t) {
        var ROLE = { patient: pill(true, '환자'), shadow: pill(null, '그림자(무시)'), other: pill(null, '다른 사람') };
        return '<tr><td>' + esc(t.tid) + '</td><td>' + (ROLE[t.role] || '—') + '</td><td class="num">' + num(t.x) + '</td><td class="num">' + num(t.y) + '</td><td class="num">' + num(t.z) + '</td><td class="num">' +
          num(Math.hypot(t.vx || 0, t.vy || 0)) + '</td><td class="num">' + (t.npts == null ? '—' : esc(t.npts)) + '</td></tr>';
      }).join('') + '</tbody></table>' : '<div class="empty-state">추적된 사람 없음</div>';
    var fl = (live && live.falls) || [];
    $('#devFallState').innerHTML = kv([['낙상 판정 상태', fl.length ? fl.map(function (f) {
      return pill(f.suppressed ? null : false, '추적 ' + f.tid + ' ' + f.stage + (f.suppressed ? '(ToF 침대 → 제외)' : '')) + ' <span class="muted">z ' + num(f.z) + ' / 평소 ' + num(f.ref) + '</span>';
    }).join(' ') : pill(true, '정상')]]);
    var raw = (mmw && mmw.raw) || {}, q = (mmw && mmw.quality) || {}, p = (mmw && mmw.presence) || {};
    $('#devGait').innerHTML = mmw ? kv([
      ['판정', esc((mmw.alert_level_ko || mmw.alert_level || '판정 없음') + (mmw.provisional ? ' (잠정)' : ''))],
      ['속도 · 변동', num(raw.speed) + ' m/s · ' + num(raw.speed_cv)],
      ['보폭 · 변동', num(raw.stride_length) + ' m · ' + num(raw.stride_cv)],
      ['흔들림 · 멈춤 비율', num(raw.sway, 3) + ' m · ' + num(raw.freeze_ratio)],
      ['보행 구간', esc(q.walking === true ? '예' : q.walking === false ? '아니오' : '—') + ' · 직진성 ' + num(q.straightness)],
      ['사람 수 · 환자 확정', esc((p.n_targets != null ? p.n_targets + '명' : '—') + ' · ' + (p.patient_locked ? '확정(추적 ' + p.main_tid + ')' : '보류'))],
      ['이유', esc((mmw.reasons || []).join(' · ') || '—')],
    ]) : '<div class="empty-state">판정 기록 없음</div>';

    // 낙상 사건
    falls = Array.isArray(falls) ? falls.slice(-15).reverse() : [];
    $('#devFalls').innerHTML = falls.length ? '<table class="dev-table"><thead><tr><th>시각</th><th>단계</th><th>종류</th><th>추적</th><th>몸 중심 z</th><th>평소 높이</th><th>하강·머묾</th><th>알림</th></tr></thead><tbody>' +
      falls.map(function (e) {
        var STAGE = { fallen: '낙상', recovered: '일어남', caution: '주의(내려갔다 일어남)', fast_drop: '급격한 하강(예전)' }, KIND = { fast: '급격한 하강', slow: '못 일어남', dip: '쪼그림 등' };
        return '<tr><td>' + esc((e.received_at || '').slice(11, 19)) + '</td><td>' + esc(STAGE[e.stage] || e.stage) + '</td><td>' + esc(KIND[e.kind] || e.kind || '') + '</td><td>' + esc(e.tid) +
          '</td><td>' + num(e.z) + '</td><td>' + num(e.ref) + '</td><td>' + esc((e.drop_sec != null ? '하강 ' + e.drop_sec + '초 ' : '') + (e.low_sec ? '머묾 ' + e.low_sec + '초' : '')) +
          '</td><td>' + (e.stage === 'caution' ? (e.suppressed ? pill(null, 'ToF 침대 → 제외') : pill(null, '주의')) : e.stage !== 'fallen' ? '' : e.suppressed ? pill(null, 'ToF 침대 → 제외') : pill(false, '알림')) + '</td></tr>';
      }).join('') + '</tbody></table>' : '<div class="empty-state">서버 시작 후 낙상 사건 없음</div>';

    $('#devRawTof').textContent = JSON.stringify({ presence: pres && Object.assign({}, pres, { __baseline: undefined }), latest_posture: tof && { posture: tof.posture, posture_raw: tof.posture_raw, posture_conf: tof.posture_conf, risk: tof.risk, bed_alert: tof.bed_alert } }, null, 1);
    $('#devRawMmw').textContent = JSON.stringify({ live: live, latest: mmw }, null, 1);
  }

  function poll() {
    Promise.allSettled([getJSON('/tof/latest'), getJSON('/tof/presence'), getJSON('/mmw/live'),
      getJSON('/mmw/latest?target=room_01'), getJSON('/mmw/fall?target=room_01'), getJSON('/tof/baseline')]).then(function (rs) {
      var v = rs.map(function (r) { return r.status === 'fulfilled' ? r.value : null; });
      if (v[1] && v[5]) v[1].__baseline = v[5];
      if (v[1] && v[1].server_now) { var t = Date.parse(v[1].server_now); if (isFinite(t)) clockOffset = t - Date.now(); }
      var mmw = v[3];
      if (mmw && !Object.prototype.hasOwnProperty.call(mmw, 'raw')) mmw = mmw.room_01 || null;
      render(v[0], v[1], v[2], mmw, v[4]);
    }).finally(function () { setTimeout(poll, 1000); });
  }


  $('#devViewerFold').addEventListener('toggle', function () {
    var box = $('#devViewers');
    if (this.open) {
      box.innerHTML = '<div><div class="zone-title">ToF 3D 자세</div><iframe src="/tof/3d" loading="lazy"></iframe></div>' +
        '<div><div class="zone-title">mmWave 포인트클라우드</div><iframe src="/mmw/viz" loading="lazy"></iframe></div>';
    } else {
      box.innerHTML = '';   // 닫으면 뷰어를 내려 CPU를 아낀다
    }
  });
  $('#devCalibrate').addEventListener('click', function () {
    if (!window.confirm('침대가 비어 있고 침구를 정리한 상태인가요? 지금 프레임을 빈 침대 기준으로 저장합니다.')) return;
    fetch('/tof/calibrate', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (d) {
      $('#devCalibrateMsg').textContent = d.ok ? '저장했습니다 · 유효 칸 ' + JSON.stringify(d.captured_valid_zones) : '실패: ' + (d.error || '');
    }).catch(function (e) { $('#devCalibrateMsg').textContent = '실패: ' + e.message; });
  });
  $('#devTheme').addEventListener('click', function () {
    var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* 저장 못 해도 화면은 바뀐다 */ }
  });
  try {
    $('#connBaseUrl').value = localStorage.getItem(CONN_BASE_KEY) || '';
    $('#connTargetId').value = localStorage.getItem(CONN_TARGET_KEY) || 'room_01';
  } catch (e) { /* 저장소 없음 */ }
  $('#connApply').addEventListener('click', function () {
    try {
      localStorage.setItem(CONN_BASE_KEY, $('#connBaseUrl').value.trim());
      localStorage.setItem(CONN_TARGET_KEY, $('#connTargetId').value.trim() || 'room_01');
      $('#connMsg').textContent = '저장했습니다. 간호사 대시보드를 새로고침하면 적용됩니다.';
    } catch (e) { $('#connMsg').textContent = '이 브라우저에서는 저장할 수 없습니다.'; }
  });
  poll();
})();
