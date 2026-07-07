#!/usr/bin/env python3
"""
ToF 8x8 실험용 서버 (프로덕션과 완전 분리)
==========================================
목적: main.ino를 8x8로 바꾼 테스트 보드 하나를 이 서버에 붙여서
  ① I2C 버스가 8x8에서도 안 멈추는지 (센서→ESP32)
  ② WiFi POST가 64존짜리 payload도 문제없이 오는지 (ESP32→서버)
를 확인한다. server/majubom.db, server/tof_server.py는 전혀 건드리지 않음.

- DB 없음 (SQLite 미사용, 순수 메모리) — 테스트용이라 영구 저장 불필요
- 해상도 무관(4x4=16존/8x8=64존 모두) — 프로덕션 db.py처럼 16존으로 자르지 않음
- 포트 5011 — 프로덕션 ToF 서버(5001)와 절대 겹치지 않음

사용:
  python test_tof_server.py
  → http://<이 PC의 IP>:5011/dashboard  에서 실시간 그리드 확인
  → ESP32 테스트 보드의 SERVER_URL 을 http://<이 PC의 IP>:5011/tof 로 설정
"""
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
PORT = 5011

# 센서별 최신 프레임 (메모리만, DB 없음)
latest = {}          # {"tof1": {"resolution","distances_mm","targets","received_at","n_zones"}}
frame_count = {}      # {"tof1": 누적 수신 프레임 수} — 끊김 없이 잘 들어오는지 확인용
log = []              # 최근 30건 (수신 시각·존개수·최솟값만 요약)


@app.route("/tof", methods=["POST"])
def receive_tof():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    sensor_id  = data.get("sensor", "unknown")
    resolution = data.get("resolution", "?")
    distances  = data.get("distances_mm") or []
    targets    = data.get("targets") or []

    now = datetime.now().isoformat(timespec="milliseconds")
    latest[sensor_id] = {
        "resolution": resolution,
        "distances_mm": distances,
        "targets": targets,
        "received_at": now,
        "n_zones": len(distances),
    }
    frame_count[sensor_id] = frame_count.get(sensor_id, 0) + 1

    valid = [d for d in distances if d and d > 0]
    min_d = min(valid) if valid else -1
    log.append({
        "at": now, "sensor": sensor_id, "resolution": resolution,
        "n_zones": len(distances), "min_mm": min_d, "seq": frame_count[sensor_id],
    })
    if len(log) > 30:
        log.pop(0)

    print(f"[{now}] {sensor_id} ({resolution}, {len(distances)}존) "
          f"min={min_d}mm  누적프레임={frame_count[sensor_id]}", flush=True)
    return jsonify({"ok": True, "received_zones": len(distances)}), 200


@app.route("/tof/latest", methods=["GET"])
def get_latest():
    return jsonify(latest)


@app.route("/tof/log", methods=["GET"])
def get_log():
    return jsonify(list(reversed(log)))


@app.route("/dashboard", methods=["GET"])
def dashboard():
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>ToF 8x8 테스트 대시보드</title>
<style>
  body{font-family:ui-monospace,Consolas,monospace;background:#0f151a;color:#e7ece9;padding:24px}
  h1{font-size:18px;color:#5fd2e6;margin:0 0 4px}
  .hint{color:#93a19d;font-size:12.5px;margin-bottom:20px}
  .boards{display:flex;gap:20px;flex-wrap:wrap}
  .board{background:#171e23;border:1px solid #2b353a;border-radius:10px;padding:14px 16px;min-width:280px}
  .board h2{font-size:14px;margin:0 0 8px;color:#e7ece9}
  .meta{font-size:12px;color:#93a19d;margin-bottom:10px}
  .grid{display:grid;gap:3px}
  .zone{aspect-ratio:1;display:flex;align-items:center;justify-content:center;
        font-size:10px;border-radius:3px;background:#1c242a}
  .warn{color:#e3a55c;font-size:12px;margin-top:8px}
  .empty{color:#5b6570}
</style></head>
<body>
  <h1>ToF 8x8 실험용 대시보드 (포트 5011, DB 없음)</h1>
  <p class="hint">프로덕션 서버(5001)와 무관 — main.ino를 8x8로 바꾼 테스트 보드 확인용. 1초마다 폴링.</p>
  <div class="boards" id="boards">데이터 대기 중...</div>

<script>
function colorOf(d){
  if(!d || d<=0) return '#1c242a';
  if(d<500) return '#123226';
  if(d<1500) return '#332512';
  return '#20282d';
}
function textColor(d){
  if(!d || d<=0) return '#5b6570';
  if(d<500) return '#59d19a';
  if(d<1500) return '#e3a55c';
  return '#93a19d';
}
async function poll(){
  try{
    const res = await fetch('/tof/latest');
    const data = await res.json();
    const el = document.getElementById('boards');
    const sensors = Object.keys(data);
    if(sensors.length===0){ el.innerHTML = '<span class="empty">아직 수신된 프레임이 없습니다. ESP32 테스트 보드의 SERVER_URL을 확인하세요.</span>'; return; }
    el.innerHTML = sensors.map(sid=>{
      const f = data[sid];
      const n = f.n_zones;
      const side = Math.round(Math.sqrt(n)) || 4;
      const cells = f.distances_mm.map(d=>
        `<div class="zone" style="background:${colorOf(d)};color:${textColor(d)}">${(d&&d>0)?d:'—'}</div>`
      ).join('');
      return `<div class="board">
        <h2>${sid}</h2>
        <div class="meta">해상도 ${f.resolution} · 존 ${n}개 · 마지막 수신 ${f.received_at}</div>
        <div class="grid" style="grid-template-columns:repeat(${side},1fr);max-width:${side*34}px">${cells}</div>
        ${n>16 ? '<div class="warn">64존 수신 중 → WiFi 전송 자체는 정상</div>' : ''}
      </div>`;
    }).join('');
  }catch(e){ console.error(e); }
}
setInterval(poll, 1000);
poll();
</script>
</body></html>"""


@app.route("/", methods=["GET"])
def index():
    return (f"ToF 8x8 테스트 서버 실행 중 (포트 {PORT}, DB 없음)<br>"
            f"대시보드: <a href='/dashboard'>/dashboard</a><br>"
            f"최신값 JSON: <a href='/tof/latest'>/tof/latest</a>")


if __name__ == "__main__":
    print(f"[test-tof] 8x8 실험용 서버 시작 → http://0.0.0.0:{PORT}  (DB 없음, 프로덕션과 무관)")
    print(f"[test-tof] 대시보드: http://<이 PC IP>:{PORT}/dashboard")
    app.run(host="0.0.0.0", port=PORT, debug=False)
