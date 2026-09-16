'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../dashboard-assets/data.js'), 'utf8');
const NOW = Date.parse('2026-09-16T03:00:00.000Z');
const iso = timestamp => new Date(timestamp).toISOString();
const plain = value => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let index = 0; index < 8; index += 1) await Promise.resolve(); };

function harness(fetch = async () => ({ ok: true, json: async () => ({}) })) {
  const clock = { now: NOW };
  let nextTimer = 0;
  const timers = new Map();
  class TestDate extends Date { static now() { return clock.now; } }
  const context = vm.createContext({
    Date: TestDate, URL, AbortController, fetch,
    setTimeout(callback, milliseconds) {
      const id = ++nextTimer;
      timers.set(id, { callback, due: clock.now + milliseconds });
      return id;
    },
    clearTimeout(id) { timers.delete(id); }
  });
  vm.runInContext(source, context);
  return {
    api: context.MajuData, clock, timers,
    async advance(milliseconds) {
      clock.now += milliseconds;
      const due = [...timers.entries()].filter(([, timer]) => timer.due <= clock.now);
      for (const [id, timer] of due) { if (timers.delete(id)) timer.callback(); }
      await flush();
    }
  };
}

function tofFixture(timestamp = NOW - 1000) {
  return {
    tof1: { received_at: iso(timestamp), distances_mm: [1200, 1250] },
    tof2: { received_at: iso(timestamp), distances_mm: [1500, 1550] },
    posture: 'edge_sit', posture_ko: '걸터앉음', posture_conf: 0.94,
    risk: { status: 'anomaly', at: iso(timestamp), reason: '침상 가장자리 접근' }
  };
}

function mmwFixture(timestamp = NOW - 1000) {
  return {
    target_id: 'room_01', received_at: iso(timestamp),
    raw: { speed: 0.6, sway: 0.08, stride_length: 0.42, speed_cv: 0.18, freeze_ratio: 0.1 },
    quality: { reliable: true, walking: true },
    presence: { patient_locked: true, main_tid: 7 },
    alert_level: 'warning', measuring_held: false, reasons: ['보행 변동 증가']
  };
}

function endpointFetch(values, calls = []) {
  return async (url, options) => {
    calls.push({ url, options });
    const endpoint = url.replace(/^https?:\/\/[^/]+/, '');
    const body = values[endpoint];
    if (body instanceof Error) throw body;
    return { ok: body !== undefined, status: body === undefined ? 404 : 200, json: async () => body };
  };
}

test('timestamp parsing accepts server formats and rejects invalid or future frames', () => {
  const { api } = harness();
  assert.equal(api.parseTimestamp(iso(NOW), NOW), NOW);
  assert.equal(api.parseTimestamp(NOW / 1000, NOW), NOW);
  assert.equal(api.parseTimestamp(String(NOW), NOW), NOW);
  assert.equal(api.parseTimestamp('2026-09-16T12:00:00+09:00', NOW), NOW);
  for (const invalid of [null, false, '', 'yesterday', 0, -1, NaN, Infinity, NOW + 1, iso(NOW + 1)]) {
    assert.equal(api.parseTimestamp(invalid, NOW), null, String(invalid));
  }
});

test('ToF normalizes nested fall risk and preserves real confidence', () => {
  const { api } = harness();
  const data = api.normalizeTof(tofFixture(), null, NOW);
  assert.equal(data.connected, true);
  assert.equal(data.headConnected, true);
  assert.equal(data.footConnected, true);
  assert.equal(data.lastSeen, NOW - 1000);
  assert.equal(data.ageMs, 1000);
  assert.equal(data.postureLabel, '걸터앉음');
  assert.equal(data.confidence, 0.94);
  assert.equal(data.riskLevel, 'critical');
});

test('ToF tracks head and foot independently and refuses stale risk timestamps', () => {
  const { api } = harness();
  const latest = tofFixture();
  latest.tof1.received_at = iso(NOW - 9000);
  latest.risk.at = latest.tof1.received_at;
  const data = api.normalizeTof(latest, null, NOW);
  assert.equal(data.connected, true);
  assert.equal(data.headConnected, false);
  assert.equal(data.footConnected, true);
  assert.equal(data.posture, 'edge_sit');
  assert.equal(data.riskLevel, 'unknown');
});

test('ToF exactly eight-second-old HTTP data is disconnected and has no current posture', () => {
  const { api } = harness();
  const data = api.normalizeTof(tofFixture(NOW - 8000), null, NOW);
  assert.equal(data.connected, false);
  assert.equal(data.ageMs, 8000);
  assert.equal(data.posture, null);
  assert.equal(data.postureLabel, null);
  assert.equal(data.confidence, null);
  assert.equal(data.riskLevel, 'unknown');
});

test('fresh presence heartbeat cannot revive an old model prediction', () => {
  const { api } = harness();
  const data = api.normalizeTof(tofFixture(NOW - 9000), {
    in_bed: true, tof1: { at: iso(NOW - 300), occupied: 9, detected: true }, tof2: { at: null }
  }, NOW);
  assert.equal(data.headConnected, true);
  assert.equal(data.footConnected, false);
  assert.equal(data.inBed, true);
  assert.equal(data.posture, null);
  assert.equal(data.riskLevel, 'unknown');
});

test('empty payloads and future ToF frames do not imply a sensor connection', () => {
  const { api } = harness();
  for (const latest of [{}, null, tofFixture(NOW + 5000)]) {
    const data = api.normalizeTof(latest, null, NOW);
    assert.equal(data.connected, false);
    assert.equal(data.lastSeen, null);
    assert.equal(data.posture, null);
    assert.equal(data.riskLevel, 'unknown');
  }
});

test('mmWave maps raw measurements and only the patient-locked track', () => {
  const { api } = harness();
  const data = api.normalizeMmw(mmwFixture(), {
    ts: NOW - 100, targets: [{ tid: 6, x: 9, y: 8, z: 7 }, { tid: 7, x: 1, y: 2, z: 3 }]
  }, NOW);
  assert.equal(data.connected, true);
  assert.equal(data.ageMs, 100);
  assert.equal(data.speed, 0.6);
  assert.equal(data.stride, 0.42);
  assert.equal(data.cv, 0.18);
  assert.equal(data.freeze, 0.1);
  assert.equal(data.riskLevel, 'warning');
  assert.deepEqual(plain(data.position), { x: 1, y: 2, z: 3 });
});

test('fresh live heartbeat never revives stale gait measurements or risk', () => {
  const { api } = harness();
  const data = api.normalizeMmw(mmwFixture(NOW - 9000), {
    ts: NOW - 100, targets: [{ tid: 7, x: 1, y: 2 }]
  }, NOW);
  assert.equal(data.connected, true);
  for (const key of ['speed', 'sway', 'stride', 'cv', 'freeze', 'position']) assert.equal(data[key], null, key);
  assert.equal(data.riskLevel, 'unknown');
  assert.equal(data.metricsFresh, false);
});

test('mmWave measurements are held when unreliable, unattributed, or held by the server', () => {
  const { api } = harness();
  for (const change of [
    data => { data.quality.reliable = false; },
    data => { data.presence.patient_locked = false; data.measuring_held = true; },
    data => { data.measuring_held = true; }
  ]) {
    const latest = mmwFixture();
    change(latest);
    const data = api.normalizeMmw(latest, null, NOW);
    assert.equal(data.connected, true);
    assert.equal(data.speed, null);
    assert.equal(data.riskLevel, 'unknown');
  }
});

test('mmWave cannot borrow another room result or an arbitrary live person', () => {
  const { api } = harness();
  const other = { ...mmwFixture(), target_id: 'room_02' };
  const data = api.normalizeMmw(other, null, NOW, 'room_01');
  assert.equal(data.connected, false);
  assert.equal(data.speed, null);
  const keyed = api.normalizeMmw({ room_02: other }, null, NOW, 'room_01');
  assert.equal(keyed.speed, null);
  const unmatched = api.normalizeMmw(mmwFixture(), { ts: NOW, targets: [{ tid: 2, x: 4, y: 5 }] }, NOW);
  assert.equal(unmatched.position, null);
});

test('actual zero values survive normalization while null and invalid numbers remain absent', () => {
  const { api } = harness();
  const latest = mmwFixture();
  latest.raw = { speed: 0, sway: null, stride_length: -1, speed_cv: '0.2', freeze_ratio: NaN };
  const data = api.normalizeMmw(latest, null, NOW);
  assert.equal(data.speed, 0);
  for (const key of ['sway', 'stride', 'cv', 'freeze']) assert.equal(data[key], null, key);
  const tof = tofFixture();
  tof.posture_conf = 0;
  assert.equal(api.normalizeTof(tof, null, NOW).confidence, 0);
});

test('client uses read-only same-origin proxy requests and stable bed_01 event ownership', async () => {
  const calls = [];
  const values = {
    '/tof/latest': tofFixture(), '/tof/presence': {},
    '/mmw/latest?target=room_01': mmwFixture(), '/mmw/live': { ts: NOW - 100, targets: [] }
  };
  const { api } = harness(endpointFetch(values, calls));
  const updates = [];
  const client = api.createClient({ onUpdate: snapshot => updates.push(snapshot) });
  const first = await client.poll();
  assert.equal(calls.length, 4);
  assert.ok(calls.every(call => call.options.method === 'GET' && call.options.cache === 'no-store'));
  assert.ok(calls.some(call => call.url === '/mmw/latest?target=room_01'));
  assert.equal(first.receivedAt, NOW - 100);
  assert.ok(first.events.length >= 4);
  assert.ok(first.events.every(event => event.patientId === 'bed_01'));
  const count = first.events.length;
  first.events.length = 0;
  const repeated = await client.poll();
  assert.equal(repeated.events.length, count, 'same sensor frame does not create duplicate events');
  assert.equal(updates.length, 2);
  client.stop();
});

test('getSnapshot ages cached data even if HTTP requests fail', async () => {
  const values = {
    '/tof/latest': tofFixture(), '/tof/presence': {},
    '/mmw/latest?target=room_01': mmwFixture(), '/mmw/live': {}
  };
  const { api, clock } = harness(endpointFetch(values));
  const client = api.createClient();
  await client.poll();
  clock.now += 9000;
  assert.equal(client.getSnapshot().tof.connected, false);
  for (const endpoint of Object.keys(values)) values[endpoint] = new Error('offline');
  const stale = await client.poll();
  assert.equal(stale.tof.connected, false);
  assert.equal(stale.mmwave.connected, false);
  assert.equal(stale.mmwave.speed, null);
  assert.equal(stale.tof.riskLevel, 'unknown');
  client.stop();
});

test('overlapping polls reuse one request batch and stop ignores late responses', async () => {
  const pending = [];
  const updates = [];
  const { api } = harness((url, options) => new Promise(resolve => pending.push({ url, options, resolve })));
  const client = api.createClient({ onUpdate: snapshot => updates.push(snapshot) });
  const first = client.start();
  assert.equal(client.poll(), first);
  await flush();
  assert.equal(pending.length, 4);
  client.stop();
  assert.ok(pending.every(request => request.options.signal.aborted));
  pending.forEach(request => request.resolve({ ok: true, json: async () => tofFixture() }));
  await first;
  assert.equal(updates.length, 0);
  assert.equal(client.getSnapshot().tof.connected, false);
});

test('hung requests settle at the timeout even when a fetch implementation ignores abort', async () => {
  const { api, advance, timers } = harness(() => new Promise(() => {}));
  const client = api.createClient();
  const polling = client.poll();
  await flush();
  assert.equal(timers.size, 4);
  await advance(4500);
  const snapshot = await polling;
  assert.equal(snapshot.tof.connected, false);
  assert.equal(snapshot.mmwave.connected, false);
  assert.equal(timers.size, 0);
});

test('server and target changes clear old readings and route through the configured proxy', async () => {
  const calls = [];
  const values = {
    '/tof/latest': tofFixture(), '/tof/presence': {},
    '/mmw/latest?target=room_01': mmwFixture(), '/mmw/live': {}
  };
  const { api } = harness(endpointFetch(values, calls));
  const client = api.createClient();
  await client.poll();
  assert.equal(client.setBaseUrl('192.168.6.10:5001/'), 'http://192.168.6.10:5001');
  assert.equal(client.getSnapshot().tof.connected, false);
  assert.equal(client.getSnapshot().events.length, 0);
  client.setTargetId('room_02');
  await client.poll();
  assert.ok(calls.some(call => call.url === 'http://192.168.6.10:5001/mmw/latest?target=room_02'));
  assert.equal(client.getSnapshot().mmwave.speed, null);
  assert.equal(client.getSnapshot().mmwave.targetId, 'room_02');
  assert.throws(() => client.setBaseUrl('https://name:password@example.com'));
  client.stop();
});
