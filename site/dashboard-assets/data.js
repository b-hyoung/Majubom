/* Read-only sensor adapter. Live readings always belong to bed_01. */
(function (root) {
  'use strict';

  const LIVE_PATIENT_ID = 'bed_01';
  const FRESH_MS = 8000;
  const POLL_MS = 2000;
  const TIMEOUT_MS = 4500;
  const POSTURES = {
    empty: '침상 비어 있음', supine: '바로 누움', lying: '누운 자세',
    side_left: '왼쪽으로 누움', side_right: '오른쪽으로 누움',
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
    return Number.isFinite(timestamp) && timestamp > 0 && timestamp <= now ? timestamp : null;
  }

  const latestTime = values => {
    const valid = values.filter(value => value !== null);
    return valid.length ? Math.max(...valid) : null;
  };
  const fresh = (timestamp, now) => timestamp !== null && now - timestamp >= 0 && now - timestamp < FRESH_MS;

  /** /tof/latest + /tof/presence. A fresh foot frame does not make the head connected. */
  function normalizeTof(latest, presence, now = Date.now()) {
    latest = obj(latest);
    presence = obj(presence);
    const headFrame = parseTimestamp(obj(latest.tof1).received_at, now);
    const footFrame = parseTimestamp(obj(latest.tof2).received_at, now);
    const headPresence = parseTimestamp(obj(presence.tof1).at, now);
    const footPresence = parseTimestamp(obj(presence.tof2).at, now);
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
    const presenceFresh = fresh(headPresence, now) || fresh(footPresence, now);
    return {
      connected, ageMs: lastSeen === null ? null : now - lastSeen, lastSeen,
      posture,
      postureLabel: posture ? (typeof latest.posture_ko === 'string' && latest.posture_ko.trim() ? latest.posture_ko : POSTURES[posture] || posture) : null,
      confidence: confidence !== null && confidence >= 0 && confidence <= 1 ? confidence : null,
      riskLevel, riskLabel: LEVELS[riskLevel] || null,
      headConnected, footConnected,
      inBed: presenceFresh && typeof presence.in_bed === 'boolean' ? presence.in_bed : null,
      reason: riskLevel !== 'unknown' && typeof risk.reason === 'string' ? risk.reason : null
    };
  }

  /** /mmw/latest?target=... + /mmw/live. Never choose an arbitrary tracked person. */
  function normalizeMmw(latest, live, now = Date.now(), targetId = 'room_01') {
    latest = obj(latest);
    live = obj(live);
    if (!Object.prototype.hasOwnProperty.call(latest, 'raw')) latest = obj(latest[targetId]);
    if (latest.target_id != null && String(latest.target_id) !== String(targetId)) latest = {};
    const readingTime = parseTimestamp(latest.received_at, now);
    const liveTime = parseTimestamp(live.ts, now);
    const readingFresh = fresh(readingTime, now);
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
    if (readingFresh && liveFresh && presence.patient_locked === true && presence.main_tid != null && Array.isArray(live.targets)) {
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

  function createClient({ baseUrl = '', targetId = 'room_01', onUpdate = () => {} } = {}) {
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
    const controllers = new Set();

    function getSnapshot() {
      const now = Date.now();
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
      const now = Date.now();
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

    function start() {
      if (running) return inFlight || Promise.resolve(getSnapshot());
      running = true;
      return poll();
    }

    function stop() {
      running = false;
      generation += 1;
      if (timer !== null) clearTimeout(timer);
      timer = null;
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
