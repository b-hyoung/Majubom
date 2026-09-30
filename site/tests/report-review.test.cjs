const { test } = require('node:test');
const assert = require('node:assert/strict');
const renderer = require('../dashboard-assets/report-review.js');
function response() {
  return { review: { status: 'limited', label: '확인 제한', scope: '점검 범위', reviewed_at: '2026-09-29',
    findings: [], checks: [], evidence: [] }, input_snapshot: null };
}
test('missing historic input is clearly distinguished from inspected input', () => {
  const html = renderer.render(response());
  assert.match(html, /작성 당시 전체 입력이 없습니다/);
  assert.doesNotMatch(html, /도출 근거 보기/);
});
test('stored input shows readable patient and sensor facts', () => {
  const r = response();
  r.input_snapshot = { 환자_프로필: { name: '가상 환자', age: 80 }, mmWave_현재_판정: { quality: { walking: false } }, 최근_24시간_활동_요약: { n_events: 0 } };
  const html = renderer.render(r);
  assert.match(html, /가상 환자 · 80세/);
  assert.match(html, /보행 능력 판단과 다름/);
  assert.match(html, /저장된 이벤트 0건/);
});
test('review findings and sources cannot inject markup or script URLs', () => {
  const r = response();
  r.review.findings = [{ field: '<img onerror=alert(1)>', quote: '<script>x</script>', explanation: '문제', correction: '수정' }];
  r.review.evidence = [{ title: '<b>paper</b>', source_url: 'javascript:alert(1)', original_text: '<iframe>' }];
  r.input_snapshot = { value: '</pre><script>bad</script>' };
  const html = renderer.render(r);
  assert.doesNotMatch(html, /<script>|<img|<iframe|javascript:/);
  assert.match(html, /&lt;script&gt;/);
});
test('direct review and automatic results are labeled separately', () => {
  const r = response();
  r.review.direct_review = { reviewer: 'Codex', conclusion: '두 신호 비교 완료', checked: ['원자료 확인'], findings: [], scope: '임상 승인 아님' };
  const html = renderer.render(r);
  assert.match(html, /직접 대조한 결과 · Codex/);
  assert.match(html, /자동 점검 결과/);
  assert.match(html, /임상 승인 아님/);
});
