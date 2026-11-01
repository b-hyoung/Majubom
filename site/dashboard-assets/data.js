/* Read-only sensor adapter. Live readings always belong to bed_01. */
(function (root) {
  'use strict';

  const LIVE_PATIENT_ID = 'bed_01';
  const FRESH_MS = 8000;
  // mmWave 보행 판정은 6초 구간마다 한 번 온다. 여기에 2초 폴링 간격이 더해지면 8초 기준을 자주 넘어
  // 카드가 '사람 없음' ↔ '판정 대기'로 깜빡였다(2026-10-12). 보행 판정에만 넉넉한 기준을 쓴다.
  const MMW_WINDOW_FRESH_MS = 15000;
  // 판정 결과(자세·재실·보행·위험) 받아오기 주기. 2초에 특별한 근거가 없어 1초로 줄였다(2026-10-13, 요청 4개/초 수준).
  const POLL_MS = 300;   // 10-22 시연: 1초 → 0.3초(서버 응답 0.02초 이내라 부담 없음)
  // 평면도 위치(/mmw/live)만 따로 자주 받는다(2026-10-12: 2초마다 받아 점이 뚝뚝 끊겨 움직였다). 평면도는 0.18초마다 다시 그린다.
  const LIVE_POLL_MS = 100;   // 10-22 시연: 0.2 → 0.1초(리더가 0.1초마다 올림)
  const TIMEOUT_MS = 4500;
  const POSTURES = {
    empty: '침상 비어 있음', supine: '바로 누움', lying: '누운 자세',
    side_left: '옆으로 누움', side_right: '옆으로 누움',   // 10-14: 좌우는 센서값이 비슷해 구분하지 않는다
    tossing: '뒤척임', edge_sit: '침상 가장자리 앉음', sitting: '앉은 자세'
  };
  const LEVELS = { normal: '정상', caution: '주의', warning: '경고', critical: '위험' };
  const obj = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const nonnegative = value => number(value) !== null && value >= 0 ? value : null;
  const clone = value => JSON.parse(JSON.stringify(value));

  /** Accept server ISO dates, epoch seconds, or epoch milliseconds. Reject future frames. */
  function parseTimestamp(value, now = Date.now()) {
    let timestamp = null;
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
      timestamp = value < 1e11 ? value * 1000 : value;
    } else if (typeof value === 'string' && value.trim()) {
      const text = value.trim();
      if (/^\d+(\.\d+)?$/.test(text)) return parseTimestamp(Number(text), now);
      // The Python servers emit naive ISO timestamps in their local timezone.
      // An explicit offset, when present, is preserved by Date.parse.
      if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}/.test(text)) timestamp = Date.parse(text);
    }
    return Number.isFinite(timestamp) && timestamp > 0 && timestamp <= now + CLOCK_SKEW_MS ? timestamp : null;
  }

  const latestTime = values => {
    const valid = values.filter(value => value !== null);
    return valid.length ? Math.max(...valid) : null;
  };
  // 서버(Pi) 시각과 보는 기기 시각의 오차 허용. 예전에는 '미래 값'(now−t<0)을 끊김으로 봐서, 기기 시계가 Pi보다
  // 0.1초만 늦어도 0.1초마다 오는 ToF 값이 연결 끊김으로 깜빡였다(2026-10-12).
  // 그래도 parseTimestamp가 미래 시각을 버려 계속 깜빡여서, 클라이언트는 /tof/presence의 server_now로
  // Pi 시계 기준 now를 만든다(clockOffset). 남는 오차(네트워크 지연)만 이 허용치로 흡수한다.
  const CLOCK_SKEW_MS = 2000;
  const fresh = (timestamp, now) => timestamp !== null && now - timestamp >= -CLOCK_SKEW_MS && now - timestamp < FRESH_MS;

  /** /tof/latest + /tof/presence. A fresh foot frame does not make the head connected. */
  function normalizeTof(latest, presence, now = Date.now()) {
    latest = obj(latest);
    presence = obj(presence);
    // 2026-10-08 업로드 기준 tof1 = 발 쪽, tof2 = 머리 쪽(예전 코드는 반대로 읽었다)
    const headFrame = parseTimestamp(obj(latest.tof2).received_at, now);
    const footFrame = parseTimestamp(obj(latest.tof1).received_at, now);
    const headPresence = parseTimestamp(obj(presence.tof2).at, now);
    const footPresence = parseTimestamp(obj(presence.tof1).at, now);
    const headTime = latestTime([headFrame, headPresence]);
    const footTime = latestTime([footFrame, footPresence]);
    const lastSeen = latestTime([headTime, footTime]);
    const headConnected = fresh(headTime, now);
    const footConnected = fresh(footTime, now);
    const connected = headConnected || footConnected;
    const modelFresh = fresh(headFrame, now) || fresh(footFrame, now);
    const posture = modelFresh && typeof latest.posture === 'string' && latest.posture.trim() ? latest.posture : null;
    const confidence = posture ? number(latest.posture_conf) : null;
    const risk = obj(latest.risk);
    const riskTime = Object.prototype.hasOwnProperty.call(risk, 'at')
      ? parseTimestamp(risk.at, now) : latestTime([headFrame, footFrame]);
    let riskLevel = 'unknown';
    if (posture && fresh(riskTime, now)) {
      if (risk.status === 'anomaly') riskLevel = 'critical';
      else if (risk.status === 'normal') riskLevel = 'normal';
    }
    // 환자 위험군 × 거동 범위 침상 알림(server/fall_risk.py). 가장자리 쏠림 판정과 비교해 더 높은 단계를 쓴다.
    const bedAlert = obj(latest.bed_alert);
    const order = ['normal', 'caution', 'warning', 'critical'];
    let reasonText = riskLevel !== 'unknown' && typeof risk.reason === 'string' ? risk.reason : null;
    if (fresh(parseTimestamp(bedAlert.at, now), now) && order.includes(bedAlert.level)) {
      if (riskLevel === 'unknown' || order.indexOf(bedAlert.level) > order.indexOf(riskLevel)) {
        riskLevel = bedAlert.level;
        reasonText = typeof bedAlert.reason === 'string' ? bedAlert.reason : reasonText;
      }
    }
    const presenceFresh = fresh(headPresence, now) || fresh(footPresence, now);
    return {
      connected, ageMs: lastSeen === null ? null : now - lastSeen, lastSeen,
      posture,
      postureLabel: posture ? (typeof latest.posture_ko === 'string' && latest.posture_ko.trim() ? latest.posture_ko : POSTURES[posture] || posture) : null,
      confidence: confidence !== null && confidence >= 0 && confidence <= 1 ? confidence : null,
      riskLevel, riskLabel: LEVELS[riskLevel] || null,
      headConnected, footConnected,
      inBed: presenceFresh && typeof presence.in_bed === 'boolean' ? presence.in_bed : null,
      reason: reasonText,
      bedAlert: Object.keys(bedAlert).length ? bedAlert : null
    };
  }

  // 실시간 보행 지표(10-22 저녁 사용자 결정): 6초 보행 판정 대신 0.1초마다 들어오는 환자 추적으로 바로 계산한다.
  // 환자 번호가 바뀌면 처음부터 다시 쌓는다. 최근 4초(속도는 최근 1.5초)만 쓴다.
  const GAIT_WIN_MS = 4000, GAIT_SPEED_MS = 1500;
  const gaitBuf = { tid: null, s: [] };
  function liveGait(live, now) {
    const tid = live.patient_tid;
    const p = tid == null || !Array.isArray(live.targets) ? null : live.targets.find(t => t && String(t.tid) === String(tid));
    if (!p || number(p.x) === null || number(p.y) === null) { gaitBuf.tid = null; gaitBuf.s = []; return null; }
    if (String(gaitBuf.tid) !== String(tid)) { gaitBuf.tid = tid; gaitBuf.s = []; }
    const last = gaitBuf.s[gaitBuf.s.length - 1];
    if (!last || now - last.t >= 50) gaitBuf.s.push({ t: now, x: p.x, y: p.y, v: Math.hypot(number(p.vx) || 0, number(p.vy) || 0) });
    while (gaitBuf.s.length && now - gaitBuf.s[0].t > GAIT_WIN_MS) gaitBuf.s.shift();
    const s = gaitBuf.s;
    const recent = s.filter(q => now - q.t <= GAIT_SPEED_MS);
    const mean = a => a.reduce((x, y) => x + y, 0) / a.length;
    const speed = recent.length ? mean(recent.map(q => q.v)) : null;
    let cv = null, sway = null, stride = null;
    if (s.length >= 10) {
      const vs = s.map(q => q.v), m = mean(vs);
      if (m > 0.1) cv = Math.sqrt(mean(vs.map(v => (v - m) ** 2))) / m;
      // 흔들림: 4초 이동 경로를 직선(주 방향)에 맞췄을 때 옆으로 벗어난 정도(m, 제곱평균)
      const mx = mean(s.map(q => q.x)), my = mean(s.map(q => q.y));
      let sxx = 0, syy = 0, sxy = 0;
      s.forEach(q => { sxx += (q.x - mx) ** 2; syy += (q.y - my) ** 2; sxy += (q.x - mx) * (q.y - my); });
      const ang = 0.5 * Math.atan2(2 * sxy, sxx - syy), nx = -Math.sin(ang), ny = Math.cos(ang);
      sway = Math.sqrt(mean(s.map(q => ((q.x - mx) * nx + (q.y - my) * ny) ** 2)));
      // 보폭: 걸음마다 레이더 속도가 오르내린다 — 속도 봉우리(0.2m/s 이상) 수를 걸음 수로, 이동 거리 ÷ 걸음 수 × 2
      let path = 0, steps = 0;
      for (let i = 1; i < s.length; i++) path += Math.hypot(s[i].x - s[i - 1].x, s[i].y - s[i - 1].y);
      for (let i = 1; i < s.length - 1; i++) if (s[i].v >= 0.2 && s[i].v > s[i - 1].v && s[i].v >= s[i + 1].v) steps++;
      if (steps >= 2 && path >= 0.5) stride = 2 * path / steps;
    }
    return { speed, cv, sway, stride };
  }

  /** /mmw/latest?target=... + /mmw/live. Never choose an arbitrary tracked person. */
  function normalizeMmw(latest, live, now = Date.now(), targetId = 'room_01') {
    latest = obj(latest);
    live = obj(live);
    if (!Object.prototype.hasOwnProperty.call(latest, 'raw')) latest = obj(latest[targetId]);
    if (latest.target_id != null && String(latest.target_id) !== String(targetId)) latest = {};
    const readingTime = parseTimestamp(latest.received_at, now);
    const liveTime = parseTimestamp(live.ts, now);
    const readingFresh = readingTime !== null && now - readingTime >= -CLOCK_SKEW_MS && now - readingTime <= MMW_WINDOW_FRESH_MS;
    const liveFresh = fresh(liveTime, now);
    const lastSeen = latestTime([readingTime, liveTime]);
    const quality = obj(latest.quality);
    const presence = obj(latest.presence);
    const reliable = quality.reliable !== false;
    const locked = presence.patient_locked !== false;
    const metricsFresh = readingFresh && reliable && locked && latest.measuring_held !== true;
    const raw = metricsFresh ? obj(latest.raw) : {};
    const riskLevel = readingFresh && reliable && latest.measuring_held !== true && Object.prototype.hasOwnProperty.call(LEVELS, latest.alert_level)
      ? latest.alert_level : 'unknown';
    let position = null;
    // 10-22 저녁: 실시간 화면이 살아 있으면 판정기의 지금 환자 번호만 쓴다 — 몇 초 전 보행 기록의 번호를 쓰면 환자가 '없음'이
    // 된 뒤 레이더가 그 번호를 멀리 있는 다른 사람에게 다시 붙였을 때 점이 그쪽으로 튀었다
    if (readingFresh && liveFresh && presence.patient_locked === true && presence.main_tid != null && Array.isArray(live.targets)
        && String(presence.main_tid) === String(live.patient_tid)) {
      const target = live.targets.find(item => item && String(item.tid) === String(presence.main_tid));
      if (target && number(target.x) !== null && number(target.y) !== null) {
        position = { x: target.x, y: target.y, z: number(target.z) };
      }
    }
    return {
      connected: readingFresh || liveFresh, ageMs: lastSeen === null ? null : now - lastSeen, lastSeen,
      speed: nonnegative(raw.speed), sway: nonnegative(raw.sway), stride: nonnegative(raw.stride_length),
      cv: nonnegative(raw.speed_cv), freeze: nonnegative(raw.freeze_ratio),
      riskLevel, targetId: String(targetId), position, metricsFresh,
      // 개인 기준이 생기기 전 문헌 기대 범위와 비교한 '잠정' 판정인지 (정식 알람 아님)
      provisional: readingFresh && latest.provisional === true,
      // 판정이 없는 이유(2026-10-12): 보행 판정은 사람이 걷는 6초 구간에서만 나온다
      // 실측 평면도용: 최근 0.3초 간격 snapshot 의 모든 추적 대상과 환자로 잠긴 track 번호
      targets: liveFresh && Array.isArray(live.targets) ? live.targets
        .filter(t => t && number(t.x) !== null && number(t.y) !== null)
        .filter(t => t.role !== 'shadow' && t.role !== 'dup' && t.role !== 'still')   // 바닥 반사 그림자·빈 껍데기·10초 넘게 제자리인 비환자(10-22)는 평면도에 그리지 않는다
        .map(t => ({ tid: t.tid, x: number(t.x), y: number(t.y), z: number(t.z), vx: number(t.vx), vy: number(t.vy) })) : [],
      // 실시간 속도(10-22): 6초 보행 지표가 나오기 전에도 환자 추적의 레이더 속도를 0.2초마다 보여준다
      live: liveFresh ? liveGait(live, now) : null,
      liveSpeed: (() => {
        if (!liveFresh || live.patient_tid == null || !Array.isArray(live.targets)) return null;
        const p = live.targets.find(t => t && String(t.tid) === String(live.patient_tid));
        return p && number(p.vx) !== null && number(p.vy) !== null ? Math.hypot(p.vx, p.vy) : null;
      })(),
      // 환자 추적: 레이더 낙상 판정기가 고른 추적(한 명이면 그 사람, 여러 명이면 침대에 가장 가까운 사람이 3초 이어질 때 옮김, 2026-10-13)
      // 환자 고정 방식(2026-10-16): exit=침대에서 내려온 사람, manual=화면에서 지정, null=침대에 가까운 사람(자동)
      patientLock: liveFresh && live.patient ? (live.patient.locked || null) : null,
      // 10-22 저녁: 클릭한 환자가 침대에 누워 레이더가 놓친 상태 — 내려오거나 침대 위에서 다시 잡히면 자동으로 이어 간다
      bedPark: liveFresh && live.patient ? live.patient.bed_park === true : false,
      mainTid: liveFresh ? (live.patient_tid != null ? live.patient_tid : null)
        : readingFresh && presence.patient_locked === true && presence.main_tid != null ? presence.main_tid : null,
      // 프레임 단위 낙상 판정(mmWave/fall_detect.py, 2026-10-12). ToF 침상 재실이면 서버가 suppressed로 표시한다.
      // fallen(침대 밖 바닥에 5초 이상) > fast_drop(0.6초 안 급하강) 순으로 하나만 고른다.
      fall: liveFresh && Array.isArray(live.falls) ? (live.falls
        .filter(f => f && !f.suppressed && (f.stage === 'fallen' || f.stage === 'fast_drop') && number(f.x) !== null && number(f.y) !== null)
        .sort((a, b) => (b.stage === 'fallen') - (a.stage === 'fallen'))
        .map(f => ({ stage: f.stage, kind: f.kind || null, tid: f.tid, x: f.x, y: f.y, z: number(f.z), ref: number(f.ref) }))[0] || null) : null,
      waitReason: !readingFresh ? null : !reliable ? '사람 없음'
        : !locked ? (liveFresh && live.patient_tid == null ? '환자 없음 — 평면도에서 사람 점을 눌러 지정' : '환자 확인 중')
        : latest.measuring_held === true ? '보행 구간 아님' : null,
      reason: readingFresh && Array.isArray(latest.reasons) ? latest.reasons.filter(value => typeof value === 'string').join(' · ') || null : null
    };
  }

  function normalizeBaseUrl(value) {
    const text = String(value || '').trim().replace(/\/+$/, '');
    if (!text) return '';
    const normalized = /^https?:\/\//i.test(text) ? text : 'http://' + text;
    const url = new URL(normalized);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new TypeError('HTTP 또는 HTTPS 센서 서버 주소를 입력해주세요.');
    return url.origin + url.pathname.replace(/\/+$/, '');
  }

  function createClient({ baseUrl = '', targetId = 'room_01', onUpdate = () => {}, onLive = () => {} } = {}) {
    let base = normalizeBaseUrl(baseUrl);
    let target = String(targetId || 'room_01');
    let running = false;
    let timer = null;
    let inFlight = null;
    let generation = 0;
    let restartSoon = false;
    let cache = { tof: null, presence: null, mmwave: null, live: null };
    let events = [];
    let previous = null;
    let lastTofFrame = null;
    let lastMmwFrame = null;
    let clockOffset = 0;   // Pi 시각 − 이 기기 시각(ms). /tof/presence의 server_now로 매 폴링 갱신
    const controllers = new Set();
    const serverNow = () => Date.now() + clockOffset;

    function getSnapshot() {
      const now = serverNow();
      const tof = normalizeTof(cache.tof, cache.presence, now);
      const mmwave = normalizeMmw(cache.mmwave, cache.live, now, target);
      return clone({ tof, mmwave, events, receivedAt: latestTime([tof.lastSeen, mmwave.lastSeen]) });
    }

    function addEvent(source, type, level, title, at, note = null) {
      const id = [LIVE_PATIENT_ID, source, type, at].join(':');
      if (events.some(event => event.id === id)) return;
      events.unshift({ id, patientId: LIVE_PATIENT_ID, source, type, level, title, note, at, timestamp: at });
      events = events.slice(0, 100);
    }

    function recordChanges(next) {
      const now = serverNow();
      for (const [source, key, label] of [
        ['tof', 'headConnected', '머리맡 ToF'], ['tof', 'footConnected', '발치 ToF'], ['mmwave', 'connected', 'mmWave']
      ]) {
        const connected = next[source][key];
        const before = previous ? previous[source][key] : false;
        if (connected !== before) addEvent(source, key + (connected ? '_connected' : '_disconnected'), connected ? 'normal' : 'caution', label + (connected ? ' 연결됨' : ' 수신 대기'), now);
      }
      const tofFrame = latestTime([
        parseTimestamp(obj(cache.tof).tof1 && cache.tof.tof1.received_at, now),
        parseTimestamp(obj(cache.tof).tof2 && cache.tof.tof2.received_at, now)
      ]);
      if (next.tof.connected && tofFrame !== null && (lastTofFrame === null || tofFrame > lastTofFrame)) {
        if (next.tof.posture && (!previous || next.tof.posture !== previous.tof.posture)) {
          addEvent('tof', 'posture_' + next.tof.posture, next.tof.riskLevel, '자세 변화 · ' + next.tof.postureLabel, tofFrame);
        }
        if (next.tof.riskLevel !== 'unknown' && next.tof.riskLevel !== 'normal' && (!previous || next.tof.riskLevel !== previous.tof.riskLevel)) {
          addEvent('tof', 'risk_' + next.tof.riskLevel, next.tof.riskLevel, '침상 낙상 위험 감지', tofFrame, next.tof.reason);
        }
        lastTofFrame = tofFrame;
      }
      let mmwReading = obj(cache.mmwave);
      if (!Object.prototype.hasOwnProperty.call(mmwReading, 'raw')) mmwReading = obj(mmwReading[target]);
      const mmwFrame = parseTimestamp(mmwReading.received_at, now);
      if (next.mmwave.connected && mmwFrame !== null && (lastMmwFrame === null || mmwFrame > lastMmwFrame)) {
        if (next.mmwave.riskLevel !== 'unknown' && next.mmwave.riskLevel !== 'normal' && (!previous || next.mmwave.riskLevel !== previous.mmwave.riskLevel)) {
          addEvent('mmwave', 'risk_' + next.mmwave.riskLevel, next.mmwave.riskLevel, '보행 위험 변화 · ' + LEVELS[next.mmwave.riskLevel], mmwFrame, next.mmwave.reason);
        }
        lastMmwFrame = mmwFrame;
      }
    }

    function request(path, requestBase) {
      const controller = new AbortController();
      controllers.add(controller);
      let timeout;
      const aborted = new Promise((resolve, reject) => {
        controller.signal.addEventListener('abort', () => reject(new Error('Sensor request aborted')), { once: true });
        timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
      });
      const response = Promise.resolve().then(() => root.fetch(requestBase + path, {
        method: 'GET', cache: 'no-store', signal: controller.signal, headers: { Accept: 'application/json' }
      })).then(result => {
        if (!result.ok) throw new Error('Sensor HTTP ' + result.status);
        return result.json();
      });
      return Promise.race([response, aborted]).finally(() => {
        clearTimeout(timeout);
        controllers.delete(controller);
      });
    }

    function poll() {
      if (inFlight) return inFlight;
      if (timer !== null) { clearTimeout(timer); timer = null; }
      const version = generation;
      const requestBase = base;
      const requestTarget = target;
      const operation = Promise.allSettled([
        request('/tof/latest', requestBase), request('/tof/presence', requestBase),
        request('/mmw/latest?target=' + encodeURIComponent(requestTarget), requestBase), request('/mmw/live', requestBase)
      ]).then(results => {
        if (version !== generation) return getSnapshot();
        ['tof', 'presence', 'mmwave', 'live'].forEach((key, index) => {
          if (results[index].status === 'fulfilled') cache[key] = results[index].value;
        });
        if (results[1].status === 'fulfilled') {
          const piNow = Date.parse(obj(results[1].value).server_now);
          if (Number.isFinite(piNow)) clockOffset = piNow - Date.now();
        }
        const next = getSnapshot();
        recordChanges(next);
        previous = next;
        const snapshot = getSnapshot();
        onUpdate(snapshot);
        return snapshot;
      }).finally(() => {
        if (inFlight === operation) inFlight = null;
        if (running && timer === null) {
          timer = setTimeout(() => { timer = null; poll().catch(() => {}); }, restartSoon ? 0 : POLL_MS);
        }
        restartSoon = false;
      });
      inFlight = operation;
      return operation;
    }

    let liveTimer = null;
    function pollLive() {
      liveTimer = null;
      if (!running) return;
      const version = generation;
      request('/mmw/live', base).then(value => {
        if (version !== generation || !running) return;
        cache.live = value;
        onLive(getSnapshot());
      }).catch(() => {}).finally(() => {
        if (running && liveTimer === null) liveTimer = setTimeout(pollLive, LIVE_POLL_MS);
      });
    }

    function start() {
      if (running) return inFlight || Promise.resolve(getSnapshot());
      running = true;
      pollLive();
      return poll();
    }

    function stop() {
      running = false;
      generation += 1;
      if (timer !== null) clearTimeout(timer);
      timer = null;
      if (liveTimer !== null) clearTimeout(liveTimer);
      liveTimer = null;
      controllers.forEach(controller => controller.abort());
    }

    function resetConnection() {
      generation += 1;
      controllers.forEach(controller => controller.abort());
      cache = { tof: null, presence: null, mmwave: null, live: null };
      events = [];
      previous = null;
      lastTofFrame = null;
      lastMmwFrame = null;
      restartSoon = true;
      if (timer !== null) { clearTimeout(timer); timer = null; }
      onUpdate(getSnapshot());
      if (running && !inFlight) poll().catch(() => {});
    }

    function setBaseUrl(value) {
      const next = normalizeBaseUrl(value);
      if (next !== base) { base = next; resetConnection(); }
      return base;
    }

    function setTargetId(value) {
      const next = String(value || 'room_01').trim() || 'room_01';
      if (next !== target) { target = next; resetConnection(); }
      return target;
    }

    return { start, stop, poll, setBaseUrl, setTargetId, getSnapshot };
  }

  root.MajuData = Object.freeze({ LIVE_PATIENT_ID, FRESH_MS, createClient, normalizeTof, normalizeMmw, parseTimestamp });
})(typeof window !== 'undefined' ? window : globalThis);
