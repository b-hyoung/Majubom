"""
CSI (Wi-Fi Channel State Information) 수신 + 분석 서버  (포트: 5003)
====================================================================
CSI 파트(박형석) → 서버. 송신부(esp32/send_csi.py)가 60초마다 1회 POST /csi.

CSI가 보내는 것  : raw(hr_bpm/resp_rpm/autocorr_strength) + quality + presence
서버가 계산하는 것: baseline(평소 μ·σ) · z-score · alert_level(4단계)  ← csi_logic.py
명세 출처        : esp32/CSI_JSON_명세_상세.md , esp32/서버연동_변경사항.md

엔드포인트
  POST /csi          ESP32/Pi 분석기 → 측정값 수신 (분석·저장)
  GET  /csi/latest   침대별 최신 보강 결과(JSON) — 사이트가 폴링
  GET  /csi/log      최근 수신 로그(JSON)
  GET  /             간단 실시간 모니터(1초 자동 새로고침)
  GET  /dashboard    벤토 대시보드(../site/index.html) 동일 출처 서빙
  GET  /health       헬스체크

주의: Windows 콘솔(cp949)에서 한글 print 시 깨질 수 있어 콘솔 출력은 ASCII만.
      한글은 JSON/HTML 응답(UTF-8)으로만 내보낸다.
"""
import os
from datetime import datetime

from flask import Flask, request, jsonify, Response
from flask_cors import CORS

import csi_logic as L

PORT = 5003
_HERE = os.path.dirname(os.path.abspath(__file__))
SITE_PATH = os.path.join(_HERE, "..", "site", "index.html")

app = Flask(__name__)
CORS(app)  # 사이트(file:// 또는 타 포트)에서 fetch 허용

# 침대별 baseline 누적 통계 (영속: csi_baseline.json) — 시작 시 로드
baseline_store = L.load_baseline()

# 침대별 최신 보강 결과
latest_by_bed: dict[str, dict] = {}
# 최근 수신 로그 (최대 100건, 보강 결과)
csi_log: list[dict] = []


# ── 수신 ───────────────────────────────────────────────────────────────
@app.route("/csi", methods=["POST"])
def receive_csi():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    raw = data.get("raw")
    if not isinstance(raw, dict) or "hr_bpm" not in raw:
        return jsonify({"error": "raw.hr_bpm 등 측정값(raw)이 필요합니다"}), 400
    # 누락 필드 방어 (resp/strength 없으면 분석 불가)
    raw.setdefault("resp_rpm", 0)
    raw.setdefault("autocorr_strength", 0.0)
    data.setdefault("bed_id", "bed_01")

    # baseline 갱신 + z-score/alert 계산 (csi_logic)
    result = L.evaluate(data, baseline_store, persist=True)

    latest_by_bed[result["bed_id"]] = result
    csi_log.append(result)
    if len(csi_log) > 100:
        csi_log.pop(0)

    # 콘솔 로그 (ASCII만)
    lvl = result.get("alert_level") or "ignored"
    z = result.get("zscore")
    ztot = z["total_abs"] if z else "-"
    print(f"[{result['received_at']}] CSI {result['bed_id']} | "
          f"HR={raw.get('hr_bpm')} RR={raw.get('resp_rpm')} "
          f"str={raw.get('autocorr_strength')} | z_total={ztot} | "
          f"level={lvl}{' ALARM' if result.get('alarm') else ''}")
    return jsonify({"ok": True, "alert_level": result.get("alert_level"),
                    "alarm": result.get("alarm")}), 200


# ── 조회 ───────────────────────────────────────────────────────────────
@app.route("/csi/latest", methods=["GET"])
def get_latest():
    """침대별 최신 결과. ?bed=bed_01 이면 해당 침대만."""
    bed = request.args.get("bed")
    if bed:
        return jsonify(latest_by_bed.get(bed, {}))
    return jsonify(latest_by_bed)


@app.route("/csi/log", methods=["GET"])
def get_log():
    n = request.args.get("n", default=20, type=int)
    return jsonify(csi_log[-n:])


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "beds": list(latest_by_bed.keys()),
                    "log_count": len(csi_log)})


# ── 벤토 대시보드 서빙 (동일 출처 → CORS 무관) ─────────────────────────
@app.route("/dashboard", methods=["GET"])
def dashboard():
    try:
        with open(SITE_PATH, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="text/html")
    except FileNotFoundError:
        return Response("site/index.html not found", status=404)


# ── 간단 실시간 모니터 (디버그용) ───────────────────────────────────────
_COLOR = {"normal": "#16a34a", "caution": "#ca8a04",
          "warning": "#ea580c", "critical": "#dc2626", None: "#888"}


@app.route("/", methods=["GET"])
def index():
    cards = ""
    for bed, r in sorted(latest_by_bed.items()):
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
          <div class=bed>{bed}
            <span class=badge style="background:{color}22;color:{color}">
              {r.get('alert_level_ko') or '측정보류'}</span></div>
          <div class=big>HR <b>{raw.get('hr_bpm','—')}</b> bpm</div>
          <div class=row>호흡 {raw.get('resp_rpm','—')} rpm · 신호강도 {raw.get('autocorr_strength','—')}
              · reliable {str(reliable).lower()}</div>
          <div class=row>z_total <b>{ztot}</b> · baseline {age}</div>
          <div class=reason>{reason}</div>
          <div class=ts>{r.get('received_at','')}</div>
        </div>"""
    if not cards:
        cards = "<div class=card style='color:#888'>아직 수신된 CSI 데이터가 없습니다. send_csi.py 로 POST 하세요.</div>"

    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>CSI 서버 모니터 :{PORT}</title>
<meta http-equiv=refresh content=2>
<style>
 body{{font-family:system-ui,'Segoe UI',sans-serif;background:#0d0b1a;color:#e8e6f5;padding:24px}}
 h2{{font-weight:700}} a{{color:#22d3ee}}
 .card{{background:#17142a;border:1px solid #2a2545;border-radius:14px;padding:16px 20px;margin:12px 0;max-width:560px}}
 .bed{{font-size:13px;color:#9a93c4;margin-bottom:6px}}
 .badge{{padding:2px 10px;border-radius:20px;font-size:12px;font-weight:700;margin-left:8px}}
 .big{{font-size:30px;font-weight:700}} .big b{{color:#fbbf24}}
 .row{{font-size:13px;color:#b9b2dd;margin-top:6px;font-family:monospace}}
 .reason{{font-size:12px;color:#8a83b4;margin-top:8px;line-height:1.6}}
 .ts{{font-size:11px;color:#5c5680;margin-top:8px;font-family:monospace}}
</style></head><body>
<h2>CSI 수신 서버 (포트 {PORT}) · 2초 자동 새로고침</h2>
<p>벤토 대시보드 → <a href="/dashboard">/dashboard</a> · 최신 JSON → <a href="/csi/latest">/csi/latest</a></p>
{cards}
</body></html>"""


if __name__ == "__main__":
    print(f"CSI server start -> http://0.0.0.0:{PORT}  (POST /csi, GET /dashboard)")
    print(f"baseline beds loaded: {list(baseline_store.keys()) or 'none'}")
    app.run(host="0.0.0.0", port=PORT, debug=True)
