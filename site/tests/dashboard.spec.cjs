const { test: base, expect } = require('@playwright/test');
const path = require('node:path');

const LIVE_ID = 'bed_01';
const TEST_REPORT = '브라우저 검증: 관측된 보행 변화에 대한 추가 확인이 필요합니다.';
const PATIENTS = [
  { bed_id: LIVE_ID, name: '연결 환자', age: null, sex: null, room: null, diagnosis: null, mmw_target_id: 'room_01', is_live: true },
  { bed_id: 'bed_00', name: '미연결 환자', age: null, sex: null, room: null, diagnosis: null, mmw_target_id: null, is_live: false },
];

const report = {
  현재_상태: TEST_REPORT,
  최근_변화: '최근 관측 구간에서 움직임 변화를 확인했습니다.',
  ToF_변화: '침상 내 앉은 자세를 관측했습니다.',
  mmWave_변화: '보행 속도 0.73 m/s를 관측했습니다.',
  보행_변화: '관측값을 기준 구간과 비교해야 합니다.',
  활동_변화: '관측된 활동 기록을 확인하세요.',
  침상_관련_행동: '침상에서 자세가 변했습니다.',
  낙상_위험도_설명: '현재 센서 판정을 요약한 지표입니다.',
  주요_위험_요인: '앉은 자세와 보행 변화',
  과거_데이터와의_비교: '장기 비교 자료가 부족합니다.',
  향후_위험_가능성: '현재 신호의 변화로 추가 관찰이 필요합니다.',
  관련_연구_근거: '이번 검증에는 외부 논문을 포함하지 않았습니다.',
  투약_조정_제안: '투약 정보 부족으로 판단 불가',
  데이터_신뢰도_설명: '연결된 두 센서의 최근 관측을 사용했습니다.',
  추가_관찰이_필요한_항목: '침상 복귀 여부와 보행 안정성',
};

const analysis = {
  id: 301, created_at: '2026-09-07T09:15:00', risk_score: 0.7, confidence: 1,
  summary: TEST_REPORT.slice(0, 60),
  risk: { component_levels: { tof_posture: 'caution', tof_exit: 'normal', mmwave_gait: 'warning' }, component_scores: { tof_posture: 0.4, tof_exit: 0.1, mmwave_gait: 0.7 }, combined_risk_score: 0.7, dominant_signal: 'mmwave_gait', method: 'max_of_existing_alert_level_scores_v1' },
  report,
};

function detail(bedId, control) {
  const patient = PATIENTS.find(item => item.bed_id === bedId) || { bed_id: bedId, name: '미연결 환자', is_live: false };
  return {
    success: true,
    patient,
    profile: patient,
    medications: (control && control.medications[bedId]) || [],
    risk_factors: [],
    events: [],
    history: { tof: [], mmwave: [] },
    analyses: bedId === LIVE_ID ? [analysis] : [],
    summary: { window: '24h', n_events: 0, by_event_type: {} },
  };
}

async function installMockApi(page) {
  const control = {
    sensorAgeMs: 0,
    sensorUnavailable: false,
    jobFailure: false,
    jobPolls: 0,
    posts: [],
    unexpectedWrites: [],
    apiRequests: [],
    medications: {},
    nextMedId: 1,
  };

  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const isApi = /^\/(?:api|tof|mmw)(?:\/|$)/.test(pathname);
    if (!isApi) {
      if (request.method() !== 'GET' && request.method() !== 'HEAD') {
        control.unexpectedWrites.push(`${request.method()} ${pathname}`);
        return route.abort();
      }
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
      return route.continue();
    }

    control.apiRequests.push(`${request.method()} ${pathname}${url.search}`);
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (request.method() !== 'GET') {
      if (request.method() === 'POST' && pathname === '/api/dashboard/analyses') {
        control.posts.push(request.postDataJSON());
        control.jobPolls = 0;
        return json({ success: true, job_id: 'mock-job-01', status: 'pending', bed_id: LIVE_ID }, 202);
      }
      // The background demo-patient simulation (site/dashboard-assets/simulation.js) logs its own
      // phase transitions here in real usage — harmless, expected background traffic in every test.
      if (request.method() === 'POST' && pathname === '/api/dashboard/sim-events') {
        return json({ success: true, id: 1 }, 201);
      }
      control.unexpectedWrites.push(`${request.method()} ${pathname}`);
      return json({ success: false, message: 'Unexpected mutation blocked by browser test' }, 405);
    }

    if (pathname === '/api/dashboard/status') return json({
      success: true,
      live_bed_id: LIVE_ID,
      mmw_target_id: 'room_01',
      sensor_stale_seconds: { tof: 8, mmwave: 8 },
      jobs_persisted: false,
      ai: { configured: true, available: true, index_available: true, missing_dependencies: [], error_type: null, message: '분석 준비 완료', prediction_type: 'current_signals_and_recent_changes' },
    });
    if (pathname === '/api/dashboard/patients') return json({ success: true, patients: PATIENTS, live_bed_id: LIVE_ID });
    if (pathname.startsWith('/api/dashboard/patients/')) return json(detail(decodeURIComponent(pathname.split('/').pop())));
    if (pathname === '/api/dashboard/analyses/mock-job-01') {
      control.jobPolls += 1;
      if (control.jobPolls === 1) return json({ success: true, job_id: 'mock-job-01', bed_id: LIVE_ID, status: 'running', result: null, error: null });
      if (control.jobFailure) return json({ success: true, job_id: 'mock-job-01', bed_id: LIVE_ID, status: 'failed', result: null, error: { error_type: 'missing_key', message: '검증용 오류: AI 연결 설정을 확인하세요.' } });
      return json({
        success: true,
        job_id: 'mock-job-01',
        bed_id: LIVE_ID,
        status: 'completed',
        error: null,
        result: {
          success: true,
          bed_id: LIVE_ID,
          analysis_id: 301,
          created_at: new Date().toISOString(),
          risk: { component_levels: { tof_posture: 'caution', tof_exit: 'normal', mmwave_gait: 'warning' }, component_scores: { tof_posture: 0.4, tof_exit: 0.1, mmwave_gait: 0.7 }, combined_risk_score: 0.7, dominant_signal: 'mmwave_gait', method: 'max_of_existing_alert_level_scores_v1' },
          data_confidence: 1,
          report,
          evidence: [],
        },
      });
    }

    const ts = Date.now() - control.sensorAgeMs;
    const iso = new Date(ts).toISOString();
    if (/^\/(tof|mmw)\//.test(pathname) && control.sensorUnavailable) return json({ success: false, message: 'Sensor disconnected in browser fixture' }, 503);
    if (pathname === '/tof/latest') return json({
      tof1: { sensor: 'tof1', received_at: iso, timestamp: iso, posture: 'sitting', posture_ko: '앉음', posture_conf: 0.94, min_mm: 441, valid_zones: 52, occupied: true, in_bed: true },
      tof2: { sensor: 'tof2', received_at: iso, timestamp: iso, posture: 'sitting', posture_ko: '앉음', posture_conf: 0.92, min_mm: 589, valid_zones: 49, occupied: true, in_bed: true },
      posture: 'sitting',
      posture_ko: '앉은 자세',
      posture_conf: 0.94,
      risk: { status: 'anomaly', at: iso },
    });
    if (pathname === '/tof/presence') return json({ received_at: iso, timestamp: iso, state: 'IN_BED', in_bed: true, occupied: true, posture: 'sitting', tof1: { at: iso, occupied: 14, detected: true }, tof2: { at: iso, occupied: 11, detected: true } });
    if (pathname === '/mmw/latest') return json({
      received_at: iso,
      target_id: 'room_01',
      raw: { speed: 0.73, sway: 0.16, stride_length: 0.64, speed_cv: 0.27, freeze_ratio: 0.02 },
      quality: { reliable: true, walking: true },
      presence: { patient_locked: true, main_tid: 1 },
      alert_level: 'warning',
    });
    if (pathname === '/mmw/live') return json({ ts, targets: [{ tid: 1, x: 0.13, y: 1.74, z: 0.85 }] });
    if (pathname === '/agent/evidence') return json({ success: true, analysis_id: Number(url.searchParams.get('analysis_id')), evidence: [] });
    return json({ success: false, message: `No browser fixture for ${pathname}` }, 404);
  });
  return control;
}

const test = base.extend({
  dashboard: async ({ page }, use) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const control = await installMockApi(page);
    // The background bed_sim_fall demo (simulation.js) can legitimately pop its fall alert at
    // any point in a long-enough test run, regardless of what that test is about — auto-dismiss
    // it immediately, the way a nurse would, so it never blocks an unrelated click.
    await page.addInitScript(() => {
      setInterval(() => {
        const modal = document.getElementById('fallAlertModal');
        const close = document.getElementById('fallAlertClose');
        if (modal && close && !modal.hidden) close.click();
      }, 200);
    });
    await page.goto('/dashboard');
    await expect(page.locator('#patientList [data-patient-id]').first()).toHaveAttribute('data-patient-id', LIVE_ID);
    await use(control);
    expect(errors, 'Uncaught browser exceptions').toEqual([]);
    expect(control.unexpectedWrites, 'Only the mocked report job may use POST').toEqual([]);
  },
});

test('first patient owns live sensors and selection does not leak live measurements', async ({ page, dashboard }) => {
  await expect(page.locator('#mainContent')).toHaveAttribute('data-patient-id', LIVE_ID);
  await expect(page.locator('[data-sensor="mmwave"]')).toContainText('0.73');
  expect(dashboard.apiRequests.some(url => url.includes('/mmw/latest?target=room_01'))).toBeTruthy();
  const otherPatient = page.locator('#patientList [data-patient-id]').nth(1);
  await expect(otherPatient).toBeVisible();
  const otherId = await otherPatient.getAttribute('data-patient-id');
  await otherPatient.click();
  await expect(page.locator('#mainContent')).toHaveAttribute('data-patient-id', otherId);
  await expect(page.locator('#mainContent')).not.toContainText('0.73');
  await expect(page.locator('#patientList [data-patient-id]').first()).toHaveAttribute('data-patient-id', LIVE_ID);
  await page.locator(`#patientList [data-patient-id="${LIVE_ID}"]`).click();
  await expect(page.locator('[data-sensor="mmwave"]')).toContainText('0.73');
});

test('light and dark themes persist through a reload', async ({ page, dashboard }) => {
  const before = await page.locator('html').getAttribute('data-theme');
  expect(['light', 'dark']).toContain(before);
  await page.locator('#themeToggle').click();
  const after = before === 'light' ? 'dark' : 'light';
  await expect(page.locator('html')).toHaveAttribute('data-theme', after);
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', after);
  await page.screenshot({ path: path.join(__dirname, 'artifacts', `dashboard-${after}.png`), fullPage: true });
  await page.locator('#themeToggle').click();
  await page.screenshot({ path: path.join(__dirname, 'artifacts', `dashboard-${before}.png`), fullPage: true });
});

test('patient search handles an empty result and clearing it', async ({ page, dashboard }) => {
  const search = page.locator('#patientSearch');
  await search.fill('검색결과가없는환자987');
  await expect(page.locator('#patientList [data-patient-id]')).toHaveCount(0);
  await expect(page.locator('#patientList')).toContainText(/없|찾/);
  await search.clear();
  await expect(page.locator('#patientList [data-patient-id]').first()).toHaveAttribute('data-patient-id', LIVE_ID);
});

test('stale sensor frames become disconnected and fresh data reconnects', async ({ page, dashboard }) => {
  await expect(page.locator('[data-sensor="mmwave"]')).toContainText('0.73');
  dashboard.sensorAgeMs = 120000;
  await expect(page.locator('[data-sensor="mmwave"]')).toContainText(/대기|끊|오프라인|미연결|연결 안|연결 없음|수신 중단|신호 없음/, { timeout: 12000 });
  await expect(page.locator('[data-sensor="mmwave"]')).not.toContainText('0.73');
  dashboard.sensorAgeMs = 0;
  await expect(page.locator('[data-sensor="mmwave"]')).toContainText('0.73', { timeout: 12000 });
});

test('weekly report list opens the full report in a modal', async ({ page, dashboard }) => {
  await page.locator('nav button[data-page="reports"]').click();
  const row = page.locator('#reportHistory [data-analysis-id]').first();
  await expect(row).toBeVisible();
  await expect(row).toContainText('70%');
  await row.click();
  await expect(page.locator('#reportModal')).toBeVisible();
  await expect(page.locator('#reportModalBody')).toContainText(TEST_REPORT);
  await expect(page.locator('#reportModalBody')).toContainText('mmWave 보행');
  // There is no manual "generate" button anymore — reports are weekly-scheduled or
  // fall-triggered only (see simulation.js triggerAutoReport / server scheduler).
  await expect(page.locator('#generateReport')).toHaveCount(0);
  await page.locator('#reportModalClose').click();
  await expect(page.locator('#reportModal')).toBeHidden();
});

test('risk trend sparkline reflects a real stored risk score', async ({ page, dashboard }) => {
  await page.locator('nav button[data-page="reports"]').click();
  await expect(page.locator('#reportRiskTrend')).toContainText('70%');
});

test('all screens are accessible on a phone without horizontal overflow or removed sensor visuals', async ({ page, dashboard }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const screen of ['overview', 'trends', 'medications', 'reports', 'devices']) {
    await page.locator(`nav button[data-page="${screen}"]`).click();
    await expect(page.locator('#mainContent')).toBeVisible();
    const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: window.innerWidth }));
    expect(dimensions.scroll, `${screen} horizontal overflow`).toBeLessThanOrEqual(dimensions.viewport + 1);
    await expect(page.locator('body')).not.toContainText(/\bCSI\b/i);
    await expect(page.locator('#roomSvg, #roomFloorplan, [data-scan], .scan-cone, .radar-sweep')).toHaveCount(0);
  }
  await expect(page.locator('#connectionForm')).toBeVisible();
  await page.screenshot({ path: path.join(__dirname, 'artifacts', 'dashboard-mobile.png'), fullPage: true });
});
