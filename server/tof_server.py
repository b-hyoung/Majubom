"""
다중 센서 수신 서버 (ToF + CSI)
포트: 5001

POST /tof  — VL53L5CX ToF 거리 데이터
POST /csi  — WiFi CSI 진폭 통계 (ESP32 csi_http 프로젝트)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

latest = {
    "tof1": {"resolution": None, "distances_mm": None, "targets": None, "received_at": None},
    "tof2": {"resolution": None, "distances_mm": None, "targets": None, "received_at": None},
}
log = []  # 최근 100건

# ── CSI ──────────────────────────────────────────────────────────────
latest_csi = {
    "seq": None, "rssi": None, "channel": None,
    "noise_floor": None, "amp_mean": None, "amp_std": None,
    "received_at": None,
}
csi_log = []  # 최근 100건

def grid_html(distances, resolution):
    if not distances:
        return "<td>—</td>"
    cols = 4 if resolution == "4x4" else 8
    rows_count = 4 if resolution == "4x4" else 8
    cells = ""
    for i, d in enumerate(distances):
        if i % cols == 0:
            cells += "<tr>"
        color = "#0a7" if d and d < 500 else ("#f80" if d and d < 1500 else "#888")
        cells += f'<td style="background:{color}22;color:{color};padding:4px 8px;text-align:center;border:1px solid #ddd">{d if d and d > 0 else "—"}</td>'
        if (i + 1) % cols == 0:
            cells += "</tr>"
    return f'<table style="border-collapse:collapse;font-family:monospace;font-size:12px">{cells}</table>'

@app.route("/tof", methods=["POST"])
def receive_tof():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    sensor_id  = data.get("sensor")        # "tof1" or "tof2"
    resolution = data.get("resolution", "4x4")  # "4x4" or "8x8"
    distances  = data.get("distances_mm")  # list[int]
    targets    = data.get("targets")       # list[int], optional

    if sensor_id not in ("tof1", "tof2"):
        return jsonify({"error": "sensor must be tof1 or tof2"}), 400
    if not isinstance(distances, list) or len(distances) == 0:
        return jsonify({"error": "distances_mm must be a non-empty list"}), 400

    now = datetime.now().isoformat(timespec="milliseconds")
    latest[sensor_id] = {
        "resolution": resolution,
        "distances_mm": distances,
        "targets": targets,
        "received_at": now,
    }

    log.append({"sensor": sensor_id, "resolution": resolution,
                 "distances_mm": distances, "received_at": now})
    if len(log) > 100:
        log.pop(0)

    # 4x4 기준 최솟값만 터미널 출력
    valid = [d for d in distances if d and d > 0]
    min_d = min(valid) if valid else -1
    print(f"[{now}] {sensor_id} ({resolution}) | zones={len(distances)} | min={min_d} mm")
    return jsonify({"ok": True}), 200


@app.route("/tof/latest", methods=["GET"])
def get_latest():
    return jsonify(latest)


@app.route("/tof/log", methods=["GET"])
def get_log():
    return jsonify(log[-20:])


# ── CSI 엔드포인트 ────────────────────────────────────────────────────
@app.route("/csi", methods=["POST"])
def receive_csi():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    now = datetime.now().isoformat(timespec="milliseconds")
    latest_csi.update({
        "seq":         data.get("seq"),
        "rssi":        data.get("rssi"),
        "channel":     data.get("channel"),
        "noise_floor": data.get("noise_floor"),
        "amp_mean":    data.get("amp_mean"),
        "amp_std":     data.get("amp_std"),
        "received_at": now,
    })
    csi_log.append(dict(latest_csi))
    if len(csi_log) > 100:
        csi_log.pop(0)

    amp_mean = data.get("amp_mean") or 0
    amp_std  = data.get("amp_std")  or 0
    print(f"[{now}] CSI seq={data.get('seq')} | "
          f"amp={amp_mean:.1f}±{amp_std:.1f} | "
          f"rssi={data.get('rssi')} ch={data.get('channel')}")
    return jsonify({"ok": True}), 200


@app.route("/csi/latest", methods=["GET"])
def get_csi_latest():
    return jsonify(latest_csi)


@app.route("/csi/log", methods=["GET"])
def get_csi_log():
    return jsonify(csi_log[-20:])


@app.route("/", methods=["GET"])
def index():
    t1, t2 = latest["tof1"], latest["tof2"]

    def summary(t):
        if t["distances_mm"] is None:
            return "아직 없음"
        valid = [d for d in t["distances_mm"] if d and d > 0]
        return f"최솟값 {min(valid)} mm / 평균 {int(sum(valid)/len(valid))} mm" if valid else "유효값 없음"

    # CSI 최신값
    c = latest_csi
    csi_summary = (
        f"amp={c['amp_mean']:.1f}±{c['amp_std']:.1f} | rssi={c['rssi']} ch={c['channel']}"
        if c["amp_mean"] is not None else "아직 없음"
    )
    csi_rows = "".join(
        f"<tr><td>{e['received_at']}</td><td>{e['seq']}</td>"
        f"<td>{e['amp_mean']:.1f}</td><td>{e['amp_std']:.1f}</td>"
        f"<td>{e['rssi']}</td><td>{e['channel']}</td></tr>"
        for e in reversed(csi_log[-10:])
        if e["amp_mean"] is not None
    )

    log_rows = "".join(
        f"<tr><td>{e['received_at']}</td><td>{e['sensor']}</td>"
        f"<td>{e['resolution']}</td>"
        f"<td>{min((d for d in e['distances_mm'] if d and d > 0), default='—')} mm</td></tr>"
        for e in reversed(log[-20:])
    )

    return f"""<!doctype html>
<html><head><meta charset=utf-8><title>VL53L5CX 모니터</title>
<meta http-equiv="refresh" content="1">
<style>
  body{{font-family:monospace;padding:20px;background:#f8f8f8}}
  h2{{color:#333}}
  .card{{background:#fff;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 4px #0002}}
  .label{{font-size:.75em;color:#999;margin-bottom:6px}}
  .summary{{font-size:1.2em;font-weight:bold;color:#0a7;margin-bottom:8px}}
  table.log{{border-collapse:collapse;width:100%;margin-top:8px}}
  table.log td,table.log th{{border:1px solid #ddd;padding:5px 10px;text-align:left;font-size:12px}}
</style></head>
<body>
<h2>VL53L5CX 실시간 모니터 (1초 자동 새로고침)</h2>
<div style="display:flex;gap:16px;flex-wrap:wrap">
  <div class=card style="flex:1;min-width:280px">
    <div class=label>ToF 1 — {t1['resolution'] or '—'} | {t1['received_at'] or '수신 없음'}</div>
    <div class=summary>{summary(t1)}</div>
    {grid_html(t1['distances_mm'], t1['resolution'] or '4x4')}
  </div>
  <div class=card style="flex:1;min-width:280px">
    <div class=label>ToF 2 — {t2['resolution'] or '—'} | {t2['received_at'] or '수신 없음'}</div>
    <div class=summary>{summary(t2)}</div>
    {grid_html(t2['distances_mm'], t2['resolution'] or '4x4')}
  </div>
</div>
<div class=card>
  <div class=label>CSI (WiFi) — {c['received_at'] or '수신 없음'}</div>
  <div class=summary>{csi_summary}</div>
  <table class=log style="margin-top:8px">
    <tr><th>시각</th><th>seq</th><th>amp_mean</th><th>amp_std</th><th>rssi</th><th>ch</th></tr>
    {csi_rows if csi_rows else '<tr><td colspan=6 style="color:#aaa">아직 없음</td></tr>'}
  </table>
</div>
<div class=card>
  <div class=label>ToF 최근 수신 로그 (최대 20건)</div>
  <table class=log>
    <tr><th>시각</th><th>센서</th><th>해상도</th><th>최솟값</th></tr>
    {log_rows if log_rows else '<tr><td colspan=4 style="color:#aaa">아직 없음</td></tr>'}
  </table>
</div>
</body></html>"""


if __name__ == "__main__":
    print("VL53L5CX ToF 서버 시작 → http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, debug=True)
