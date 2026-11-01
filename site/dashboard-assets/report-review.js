(function (root) {
  'use strict';
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function url(value) { return /^https?:\/\//i.test(value || '') ? value : ''; }
  function badge(summary) {
    if (!summary) return '<span class="pill review-pending">검토 대기</span>';
    var cls = summary.status === 'needs_review' ? 'warning' : 'info';
    return '<span class="pill ' + cls + '">' + esc(summary.label) + '</span>';
  }
  function findings(items) {
    return (items || []).map(function (f) {
      return '<div class="review-finding"><strong>' + esc(f.field || f.title || '확인 사항') + '</strong>' +
        (f.quote ? '<blockquote>' + esc(f.quote) + '</blockquote>' : '') +
        '<p>' + esc(f.explanation) + '</p>' +
        (f.correction ? '<p class="review-correction"><b>검토 의견:</b> ' + esc(f.correction) + '</p>' : '') + '</div>';
    }).join('');
  }
  function jsonBlock(value) { return '<pre class="review-json">' + esc(JSON.stringify(value, null, 2)) + '</pre>'; }
  function render(response) {
    var r = response.review, p = response.input_snapshot, direct = r.direct_review;
    var html = '<section class="report-review"><div class="row"><strong>리포트 검토 결과</strong>' + badge(r) + '</div>';
    html += '<p class="muted">' + esc(r.scope) + '</p>';
    if (direct) {
      html += '<div class="review-direct"><strong>직접 대조한 결과 · ' + esc(direct.reviewer) + '</strong><p>' + esc(direct.conclusion) + '</p>';
      html += '<ul>' + (direct.checked || []).map(function (text) { return '<li>' + esc(text) + '</li>'; }).join('') + '</ul>';
      html += findings(direct.findings);
      html += (direct.sources || []).map(function (s) { return url(s.url) ? '<a href="' + esc(s.url) + '" target="_blank" rel="noopener noreferrer">' + esc(s.title) + ' ↗</a> ' : ''; }).join('');
      html += '<p class="muted">' + esc(direct.scope) + '</p></div>';
    } else {
      html += '<p class="muted">이 리포트에는 별도의 직접 대조 의견이 아직 없습니다.</p>';
    }
    html += '<details class="review-details"' + (r.findings.length ? ' open' : '') + '><summary>자동 점검 결과 · 확인 사항 ' + r.findings.length + '건</summary>';
    html += '<ul class="review-checks">' + (r.checks || []).map(function (c) {
      var status = { pass: '일치', fail: '불일치', limited: '확인 제한' }[c.status] || '미확인';
      return '<li><strong>' + esc(c.name) + ' · ' + status + '</strong><div>' + esc(c.detail) + '</div></li>';
    }).join('') + '</ul>' + findings(r.findings) + '</details>';
    if (p) {
      var risk = p['계산된_위험점수'] || {};
      var profile = p['환자_프로필'] || {}, mmw = p['mmWave_현재_판정'] || {}, tof = p['ToF_현재_판정'] || {};
      var items = [
        ['환자', (profile.name || profile.bed_id || '정보 없음') + (profile.age != null ? ' · ' + profile.age + '세' : '')],
        ['등록된 진단', profile.diagnosis || profile.diagnosis_primary_ko || profile.diagnosis_json || '정보 없음'],
        ['등록된 약', (p['투약'] || []).map(function (m) { return m.name; }).join(', ') || '등록 정보 없음'],
        ['위험 요인', (p['위험인자'] || []).map(function (f) { return f.factor || f.label; }).join(', ') || '등록 정보 없음'],
        ['침상 센서', tof.posture_ko || '현재 자세 정보 없음'],
        ['보행 센서', (mmw.quality || {}).walking === true ? '현재 보행 신호 있음' : (mmw.quality || {}).walking === false ? '현재 보행 신호 없음 · 보행 능력 판단과 다름' : '보행 신호 정보 없음'],
        ['최근 24시간', ((p['최근_24시간_활동_요약'] || {}).n_events == null ? '집계 정보 없음' : '저장된 이벤트 ' + p['최근_24시간_활동_요약'].n_events + '건')],
        ['원문 기록', '입원 ' + (p['입원_기록_원문'] || []).length + '건 · 간호 ' + (p['간호_기록_원문'] || []).length + '건']
      ];
      html += '<details class="review-details"><summary>도출 근거 보기 · 작성 당시 환자·센서 입력</summary>';
      html += '<p>분석 시점: ' + esc(p['분석_시점']) + ' · 출처: ' + esc(p['데이터_출처'] === 'simulation' ? '시뮬레이션' : p['데이터_출처']) + '</p>';
      if (p['검증_조건']) html += '<p><b>검증 시나리오:</b> ' + esc(p['검증_조건'].phase_name) + ' · ' + esc(p['검증_조건'].note) + '</p>';
      html += '<dl class="review-facts">' + items.map(function (item) { return '<dt>' + esc(item[0]) + '</dt><dd>' + esc(item[1]) + '</dd>'; }).join('') + '</dl>';
      html += '<p><b>계산 결과:</b> 위험점수 ' + esc(risk.combined_risk_score == null ? '판정 자료 없음' : risk.combined_risk_score) +
        ' · 대표 신호 ' + esc(risk.dominant_signal || '없음') + ' · 신호 확보 비율 ' + esc(p['데이터_신뢰도_비율'] == null ? '없음' : p['데이터_신뢰도_비율']) + '</p>';
      html += '<p>원자료는 작성 당시 저장한 값입니다.</p><details><summary>AI에 전달한 전체 자료 펼치기</summary>' + jsonBlock(p) + '</details></details>';
    } else {
      html += '<p class="review-missing">작성 당시 전체 입력이 없습니다. 현재 환자 정보를 당시 자료로 대신 표시하지 않습니다.</p>';
    }
    html += '<details class="review-details"><summary>연결된 문헌 근거 ' + (r.evidence || []).length + '개</summary><p class="muted">' + esc(r.evidence_note) + '</p>';
    html += (r.evidence || []).map(function (e) {
      return '<div class="review-paper"><strong>' + esc(e.title) + '</strong><div class="faint">' + esc(e.chunk_id) + '</div><p>' + esc(e.original_text) + '</p>' +
        (url(e.source_url) ? '<a href="' + esc(e.source_url) + '" target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>' : '') + '</div>';
    }).join('');
    html += '</details><div class="faint">자동 점검 시각: ' + esc(r.reviewed_at) + '</div></section>';
    return html;
  }
  var api = { render: render, badge: badge };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MajubomReportReview = api;
})(typeof window !== 'undefined' ? window : globalThis);
