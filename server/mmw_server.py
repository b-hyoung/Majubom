"""
mmWave 보행 데이터 수신 + 분석 서버  (포트: 5002)
====================================================================
mmWave 파트(유현기/박형석) → 서버. 송신부(send_mmw.py)가 30초마다 1회 POST /mmw.

mmWave가 보내는 것 : raw(speed/speed_cv/sway/freeze_ratio/height_drop) + quality + presence
서버가 계산하는 것 : baseline(평소 μ·σ) · z-score · alert_level(4단계)  ← mmw_logic.py
구조 출처         : csi_server.py / csi_logic.py 와 동일 패턴

엔드포인트
  POST /mmw           송신부 → 보행 지표 수신 (분석·저장)
  GET  /mmw/latest    대상별 최신 보강 결과(JSON) — 사이트가 폴링
  GET  /mmw/log       최근 수신 로그(JSON)
  POST /mmw/reset     baseline 초기화 (평소 보행 다시 학습; tof/calibrate 대응)
  GET  /              간단 실시간 모니터(2초 자동 새로고침)
  GET  /health        헬스체크

주의: Windows 콘솔(cp949)에서 한글 print 시 깨질 수 있어 콘솔 출력은 ASCII만.
      한글은 JSON/HTML 응답(UTF-8)으로만 내보낸다.
"""
import os
import time
from datetime import datetime

from flask import Flask, request, jsonify

from flask_cors import CORS

import mmw_logic as L
import db

PORT = 5002

app = Flask(__name__)
CORS(app)  # 사이트(file:// 또는 타 포트)에서 fetch 허용

# 대상별 baseline 누적 통계 (영속: mmw_baseline.json) — 시작 시 로드
baseline_store = L.load_baseline()

# 대상별 최신 보강 결과
latest_by_target: dict[str, dict] = {}
# 최근 수신 로그 (최대 100건)
mmw_log: list[dict] = []
# 실시간 포인트 클라우드 snapshot (send_mmw가 0.3초마다 갱신)
live_snapshot: dict = {"targets": [], "ts": 0}


# ── 수신 ───────────────────────────────────────────────────────────────
@app.route("/mmw", methods=["POST"])
def receive_mmw():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    raw = data.get("raw")
    if not isinstance(raw, dict) or "speed" not in raw:
        return jsonify({"error": "raw.speed 등 보행 지표(raw)가 필요합니다"}), 400
    # 누락 필드 방어
    raw.setdefault("speed_cv", 0.0)
    raw.setdefault("sway", 0.0)
    raw.setdefault("freeze_ratio", 0.0)
    raw.setdefault("height_drop", 0.0)
    raw.setdefault("stride_length", 0.0)
    raw.setdefault("stride_cv", 0.0)
    data.setdefault("target_id", "room_01")

    # baseline 갱신 + z-score/alert 계산 (mmw_logic)
    result = L.evaluate(data, baseline_store, persist=True)

    latest_by_target[result["target_id"]] = result
    mmw_log.append(result)
    if len(mmw_log) > 100:
        mmw_log.pop(0)

    # raw + 계산값 SQLite 저장 (AI 학습용 원천 데이터 누적)
    z = result.get("zscore") or {}
    try:
        db.insert_mmw(
            result["target_id"],
            result.get("received_at") or datetime.now().isoformat(),
            raw, data.get("quality") or {}, data.get("presence") or {},
            total_abs=z.get("total_abs"),
            alert_level=result.get("alert_level"),
        )
    except Exception as e:
        print(f"[DB] MMW insert failed: {e}")

    # 콘솔 로그 (ASCII만)
    lvl = result.get("alert_level") or "ignored"
    z = result.get("zscore")
    ztot = z["total_abs"] if z else "-"
    print(f"[{result['received_at']}] MMW {result['target_id']} | "
          f"spd={raw.get('speed')} cv={raw.get('speed_cv')} "
          f"sway={raw.get('sway')} frz={raw.get('freeze_ratio')} "
          f"drop={raw.get('height_drop')} stride={raw.get('stride_length')} "
          f"s_cv={raw.get('stride_cv')} | z_total={ztot} | "
          f"level={lvl}{' ALARM' if result.get('alarm') else ''}")
    return jsonify({"ok": True, "alert_level": result.get("alert_level"),
                    "alarm": result.get("alarm")}), 200


# ── 조회 ───────────────────────────────────────────────────────────────
@app.route("/mmw/latest", methods=["GET"])
def get_latest():
    """대상별 최신 결과. ?target=room_01 이면 해당 대상만."""
    target = request.args.get("target")
    if target:
        return jsonify(latest_by_target.get(target, {}))
    return jsonify(latest_by_target)


@app.route("/mmw/log", methods=["GET"])
def get_log():
    n = request.args.get("n", default=20, type=int)
    return jsonify(mmw_log[-n:])


@app.route("/mmw/reset", methods=["POST"])
def reset_baseline():
    """baseline 초기화. ?target=room_01 이면 해당 대상만, 없으면 전체.
    평소 보행을 다시 학습시키고 싶을 때 사용 (tof/calibrate 대응)."""
    target = request.args.get("target")
    if target:
        baseline_store.pop(target, None)
    else:
        baseline_store.clear()
    L.save_baseline(baseline_store)
    print(f"[reset] baseline cleared: {target or 'ALL'}")
    return jsonify({"ok": True, "cleared": target or "all"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "targets": list(latest_by_target.keys()),
                    "log_count": len(mmw_log)})


# ── 실시간 포인트 클라우드 ─────────────────────────────────────────────
@app.route("/mmw/live", methods=["POST"])
def receive_live():
    data = request.get_json(silent=True)
    if data and "targets" in data:
        live_snapshot["targets"] = data["targets"]
        live_snapshot["ts"] = int(time.time() * 1000)
    return jsonify({"ok": True}), 200


@app.route("/mmw/live", methods=["GET"])
def get_live():
    return jsonify(live_snapshot)


@app.route("/mmw/viz", methods=["GET"])
def viz():
    return """<!doctype html><html><head><meta charset=utf-8>
<title>mmWave Point Cloud</title>
<style>
 *{margin:0;padding:0;box-sizing:border-box}
 body{background:#0d0b1a;color:#e8e6f5;font-family:system-ui,sans-serif;
      display:flex;flex-direction:column;align-items:center;padding:16px}
 h2{margin-bottom:8px;font-size:18px;font-weight:700}
 .info{font-size:13px;color:#9a93c4;margin-bottom:12px}
 canvas{border:1px solid #2a2545;border-radius:8px;background:#12101f}
 .legend{display:flex;gap:16px;margin-top:10px;font-size:12px;color:#b9b2dd}
 .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}
</style></head><body>
<h2>mmWave Point Cloud (Top View)</h2>
<div class=info>
  <span id=ntgt>-</span> targets |
  <span id=fps>-</span> fps |
  X: left-right | Y: distance from sensor
</div>
<canvas id=cv width=600 height=500></canvas>
<div class=legend>
  <span><span class=dot style="background:#22d3ee"></span>Target</span>
  <span><span class=dot style="background:#fbbf24"></span>Velocity</span>
  <span style="color:#555">Grid: 1m</span>
</div>
<script>
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
const W=cv.width,H=cv.height;
const SCALE=80;  // px per meter
const OX=W/2, OY=H-40;  // sensor at bottom center
const COLORS=['#22d3ee','#a78bfa','#34d399','#fb923c','#f472b6','#facc15','#60a5fa','#e879f9'];
let lastTs=0, frameCount=0, fpsVal=0, fpsT=Date.now();

function toScreen(x,y){return [OX+x*SCALE, OY-y*SCALE]}

function drawGrid(){
  ctx.strokeStyle='#1e1b3a';ctx.lineWidth=0.5;
  for(let m=-4;m<=4;m++){
    let[sx]=toScreen(m,0);
    ctx.beginPath();ctx.moveTo(sx,0);ctx.lineTo(sx,H);ctx.stroke();
  }
  for(let m=0;m<=6;m++){
    let[,sy]=toScreen(0,m);
    ctx.beginPath();ctx.moveTo(0,sy);ctx.lineTo(W,sy);ctx.stroke();
    if(m>0){ctx.fillStyle='#333';ctx.font='10px monospace';ctx.fillText(m+'m',4,sy+12)}
  }
}

function drawSensor(){
  ctx.fillStyle='#dc2626';
  ctx.beginPath();ctx.moveTo(OX,OY+10);ctx.lineTo(OX-8,OY+22);ctx.lineTo(OX+8,OY+22);
  ctx.closePath();ctx.fill();
  ctx.fillStyle='#666';ctx.font='10px monospace';ctx.fillText('SENSOR',OX-20,OY+34);
}

function drawTarget(t,i){
  let[sx,sy]=toScreen(t.x,t.y);
  let col=COLORS[i%COLORS.length];
  // body circle
  ctx.beginPath();ctx.arc(sx,sy,8,0,Math.PI*2);
  ctx.fillStyle=col+'44';ctx.fill();
  ctx.strokeStyle=col;ctx.lineWidth=2;ctx.stroke();
  // center dot
  ctx.beginPath();ctx.arc(sx,sy,3,0,Math.PI*2);ctx.fillStyle=col;ctx.fill();
  // velocity arrow
  if(t.vx!==undefined){
    let vlen=Math.sqrt(t.vx*t.vx+t.vy*t.vy);
    if(vlen>0.05){
      let ex=sx+t.vx*SCALE*0.5, ey=sy-t.vy*SCALE*0.5;
      ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(ex,ey);
      ctx.strokeStyle='#fbbf24';ctx.lineWidth=2;ctx.stroke();
      // arrowhead
      let a=Math.atan2(-(t.vy),t.vx);
      ctx.beginPath();ctx.moveTo(ex,ey);
      ctx.lineTo(ex-8*Math.cos(a-0.4),ey+8*Math.sin(a-0.4));
      ctx.lineTo(ex-8*Math.cos(a+0.4),ey+8*Math.sin(a+0.4));
      ctx.closePath();ctx.fillStyle='#fbbf24';ctx.fill();
    }
  }
  // label
  ctx.fillStyle='#fff';ctx.font='bold 11px monospace';
  ctx.fillText('T'+t.tid,sx+12,sy-4);
  ctx.fillStyle='#999';ctx.font='10px monospace';
  ctx.fillText('('+t.x.toFixed(1)+','+t.y.toFixed(1)+')',sx+12,sy+8);
}

function render(data){
  ctx.clearRect(0,0,W,H);
  drawGrid();
  drawSensor();
  let targets=data.targets||[];
  document.getElementById('ntgt').textContent=targets.length;
  targets.forEach((t,i)=>drawTarget(t,i));
  // fps
  frameCount++;
  let now=Date.now();
  if(now-fpsT>=1000){fpsVal=frameCount;frameCount=0;fpsT=now}
  document.getElementById('fps').textContent=fpsVal;
}

async function poll(){
  try{
    let r=await fetch('/mmw/live');
    let d=await r.json();
    if(d.ts!==lastTs){render(d);lastTs=d.ts}
  }catch(e){}
  requestAnimationFrame(poll);
}
poll();
</script></body></html>"""


# ── 간단 실시간 모니터 (디버그용) ───────────────────────────────────────
_COLOR = {"normal": "#16a34a", "caution": "#ca8a04",
          "warning": "#ea580c", "critical": "#dc2626", None: "#888"}


@app.route("/", methods=["GET"])
def index():
    cards = ""
    for tgt, r in sorted(latest_by_target.items()):
        raw = r.get("raw", {})
        z = r.get("zscore")
        b = r.get("baseline")
        lvl = r.get("alert_level")
        color = _COLOR.get(lvl, "#888")
        reliable = r.get("quality", {}).get("reliable", True)
        ztot = z["total_abs"] if z else "—"
        age = f"{b['age_days']:.1f}d / n={b['n']}" if b else "—"
        reason = "<br>".join(r.get("reasons", []))
        cards += f"""
        <div class=card>
          <div class=tgt>{tgt}
            <span class=badge style="background:{color}22;color:{color}">
              {r.get('alert_level_ko') or '측정보류'}</span></div>
          <div class=big>속도 <b>{raw.get('speed','—')}</b> m/s</div>
          <div class=row>변동 {raw.get('speed_cv','—')} · 흔들림 {raw.get('sway','—')}
              · 멈칫 {raw.get('freeze_ratio','—')} · 높이강하 {raw.get('height_drop','—')}
              · 보폭 {raw.get('stride_length','—')}m · 보폭변동 {raw.get('stride_cv','—')}
              · reliable {str(reliable).lower()}</div>
          <div class=row>z_total <b>{ztot}</b> · baseline {age}</div>
          <div class=reason>{reason}</div>
          <div class=ts>{r.get('received_at','')}</div>
        </div>"""
    if not cards:
        cards = "<div class=card style='color:#888'>아직 수신된 mmWave 데이터가 없습니다. send_mmw.py 로 POST 하세요.</div>"

    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>mmWave 서버 모니터 :{PORT}</title>
<meta http-equiv=refresh content=2>
<style>
 body{{font-family:system-ui,'Segoe UI',sans-serif;background:#0d0b1a;color:#e8e6f5;padding:24px}}
 h2{{font-weight:700}} a{{color:#22d3ee}}
 .card{{background:#17142a;border:1px solid #2a2545;border-radius:14px;padding:16px 20px;margin:12px 0;max-width:620px}}
 .tgt{{font-size:13px;color:#9a93c4;margin-bottom:6px}}
 .badge{{padding:2px 10px;border-radius:20px;font-size:12px;font-weight:700;margin-left:8px}}
 .big{{font-size:30px;font-weight:700}} .big b{{color:#fbbf24}}
 .row{{font-size:13px;color:#b9b2dd;margin-top:6px;font-family:monospace}}
 .reason{{font-size:12px;color:#8a83b4;margin-top:8px;line-height:1.6}}
 .ts{{font-size:11px;color:#5c5680;margin-top:8px;font-family:monospace}}
</style></head><body>
<h2>mmWave 수신 서버 (포트 {PORT}) · 2초 자동 새로고침</h2>
<p>최신 JSON → <a href="/mmw/latest">/mmw/latest</a> · 로그 → <a href="/mmw/log">/mmw/log</a> · <a href="/mmw/viz" style="font-size:1.1em;font-weight:bold">Point Cloud 시각화</a></p>
{cards}
</body></html>"""


if __name__ == "__main__":
    db.init_db()
    print(f"mmWave server start -> http://0.0.0.0:{PORT}  (POST /mmw)")
    print(f"baseline targets loaded: {list(baseline_store.keys()) or 'none'}")
    app.run(host="0.0.0.0", port=PORT, debug=True)
