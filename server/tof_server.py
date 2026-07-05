"""
ToF(VL53L5CX) 수신 서버
포트: 5001

POST /tof              — ToF 거리 데이터 수신 (raw 를 tof_readings 에 저장)
GET  /tof/latest       — 센서별 최신 프레임 (in-memory)
GET  /tof/log          — 최근 수신 로그 (in-memory)
GET  /tof/presence     — 침상 재실 판정
POST /tof/calibrate    — 빈 침대 베이스라인 저장
※ CSI 는 csi_server.py(:5003) 전담 (여기서 분리).
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime
import os, json, sys
import db

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 자세 분류 모델 (옵션) ──────────────────────────────────────────────
# sklearn/joblib/모델이 없으면 조용히 비활성(posture=None). 서버는 정상 동작.
_POSTURE = None
try:
    sys.path.insert(0, os.path.join(HERE, "..", "TOF", "ml"))
    from predict_posture import load_model as _load_posture, predict as _predict_posture
    _POSTURE = _load_posture()
    print("[posture] 자세 분류 모델 로드됨")
except Exception as e:
    print(f"[posture] 모델 미로드({e}) — 자세 표시 비활성")

# ── LSTM-AE 이상탐지 (옵션) ────────────────────────────────────────────
# torch / lstm_ae.pt 없으면 조용히 비활성(anomaly=None). 서버는 정상 동작.
_ANOM = None
_frame_feature = None
try:
    from tof_anomaly import Detector as _AnomDetector, frame_feature as _frame_feature
    _ANOM = _AnomDetector()
    print(f"[anomaly] LSTM-AE 이상탐지 로드됨 (seq={_ANOM.seq}, thr={_ANOM.thr:.4f})")
except Exception as e:
    print(f"[anomaly] 미로드({e}) — 이상탐지 비활성")

# 최신 이상탐지 결과 (대시보드가 /tof/anomaly 로 폴링)
anomaly_latest = {"status": "init", "err": None, "threshold": None,
                  "ratio": None, "filled": 0, "seq": None, "at": None}

app = Flask(__name__)
CORS(app)

# ── 침상 재실(있다/없다) 감지 설정 ────────────────────────────────────
# A 방식: 양쪽 레일에서 침대 위를 내려다봄.
# 빈 침대 베이스라인 대비, 그보다 PRESENCE_DELTA_MM 이상 "가까운" 존 =
# 매트리스 위에 몸/이불이 올라온 것 → 그런 존이 일정 개수 이상이면 사람 있음.
PRESENCE_DELTA_MM  = 150    # 베이스라인보다 이만큼(mm) 가까우면 점유 존
PRESENCE_MIN_ZONES = 2      # 들어올 때: 점유 존이 이 개수 이상이면 그 센서는 "감지"
                            # (수평 배치라 유효존이 적어 2로 낮춤. 빈 침대는 0이라 오탐 낮음)
STAY_MIN_ZONES     = 1      # 이미 재실 중엔 이 개수 이상이면 유지 (빈 침대 0존은 이탈)
CONFIRM_ENTER      = 3      # 이탈→재실 확정에 필요한 연속 프레임 (빠르게 진입)
CONFIRM_EXIT       = 24     # 재실→이탈 확정에 필요한 연속 프레임 (약 2.4초, 깜빡임 억제)
CONFIRM_FRAMES     = 3      # (하위호환) 사용 안 함
ABS_FALLBACK_MM    = 1500   # 베이스라인 없을 때: 이보다 가까운 유효 존을 점유로 간주
ROI_MAX_MM         = 2300   # 침대 ROI: 빈 침대 기준거리가 이보다 먼 존은 벽·바닥으로 보고
                            # 감지에서 제외 (벽쪽 오탐 방지). 침대 near~far가 이 안에 들어오게 설정.

BASELINE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tof_baseline.json")

baseline = {"tof1": None, "tof2": None}   # 빈 침대 기준 존별 거리(mm)

presence = {
    "in_bed": False,        # 안정화된 최종 판정: 침상에 있음?
    "since": None,          # 현재 상태로 바뀐 시각
    "pending": 0,           # 상태 전환 카운터
    "tof1": {"occupied": 0, "detected": False, "at": None},
    "tof2": {"occupied": 0, "detected": False, "at": None},
}


def load_baseline():
    try:
        with open(BASELINE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for sid in ("tof1", "tof2"):
            if isinstance(data.get(sid), list):
                baseline[sid] = data[sid]
        print(f"[baseline] 로드됨: tof1={'O' if baseline['tof1'] else 'X'} "
              f"tof2={'O' if baseline['tof2'] else 'X'}")
    except FileNotFoundError:
        print("[baseline] 저장된 베이스라인 없음 (절대거리 fallback 사용). "
              "빈 침대 상태에서 /tof/calibrate 호출 권장")
    except Exception as e:
        print(f"[baseline] 로드 실패: {e}")


def save_baseline():
    try:
        with open(BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(baseline, f)
    except Exception as e:
        print(f"[baseline] 저장 실패: {e}")


def occupied_zones(sensor_id, distances):
    """매트리스 위에 물체가 올라온 존 개수 (침대 ROI 밖 존은 제외)."""
    base = baseline.get(sensor_id)
    cnt = 0
    for i, d in enumerate(distances):
        if d is None or d <= 0:
            continue
        if base:   # 이 센서는 보정됨 → 침대 ROI 안의 존만 사용
            b = base[i] if i < len(base) else None
            # 기준거리가 없거나(무효) 침대 ROI보다 먼 존(=벽·바닥)은 감지에서 제외
            if not b or b <= 0 or b > ROI_MAX_MM:
                continue
            if d < b - PRESENCE_DELTA_MM:
                cnt += 1
        elif d < ABS_FALLBACK_MM:   # 미보정 시: 절대거리 기준 (ROI도 이 안)
            cnt += 1
    return cnt


def update_presence(sensor_id, distances, now):
    occ = occupied_zones(sensor_id, distances)
    detected = occ >= PRESENCE_MIN_ZONES
    presence[sensor_id] = {"occupied": occ, "detected": detected, "at": now}

    t1o = presence["tof1"]["occupied"]
    t2o = presence["tof2"]["occupied"]
    # 히스테리시스(Schmitt): 들어올 땐 MIN_ZONES 이상, 이미 재실 중엔 STAY_MIN_ZONES만
    # 넘어도 유지. + 이탈 확정은 CONFIRM_EXIT 프레임(약 2.4초) 지속돼야 함 → 깜빡임 제거.
    if presence["in_bed"]:
        overall = (t1o >= STAY_MIN_ZONES) or (t2o >= STAY_MIN_ZONES)
    else:
        overall = (t1o >= PRESENCE_MIN_ZONES) or (t2o >= PRESENCE_MIN_ZONES)

    if overall != presence["in_bed"]:
        presence["pending"] += 1
        need = CONFIRM_ENTER if overall else CONFIRM_EXIT
        if presence["pending"] >= need:
            presence["in_bed"] = overall
            presence["since"] = now
            presence["pending"] = 0
    else:
        presence["pending"] = 0


load_baseline()

latest = {
    "tof1": {"resolution": None, "distances_mm": None, "targets": None, "received_at": None},
    "tof2": {"resolution": None, "distances_mm": None, "targets": None, "received_at": None},
}
log = []  # 최근 100건

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

    # 재실(있다/없다) 판정 업데이트
    update_presence(sensor_id, distances, now)

    # LSTM-AE 이상탐지: 이번 프레임 특징을 버퍼에 넣고 재구성오차 점수 갱신
    if _ANOM is not None and _frame_feature is not None:
        try:
            feat = _frame_feature(latest, baseline, presence)
            _ANOM.push(feat)
            r = _ANOM.score()
            if r:
                r["at"] = now
                anomaly_latest.update(r)
        except Exception as e:
            print(f"[anomaly] 계산 실패: {e}")

    # raw SQLite 저장 (AI 학습용 원천 데이터 누적) — distances/targets 는 존별 컬럼
    try:
        db.insert_tof(sensor_id, now, resolution, distances, targets,
                      occupied=presence[sensor_id]["occupied"],
                      in_bed=presence["in_bed"])
    except Exception as e:
        print(f"[DB] ToF insert failed: {e}")

    valid = [d for d in distances if d and d > 0]
    min_d = min(valid) if valid else -1
    print(f"[{now}] {sensor_id} ({resolution}) | zones={len(distances)} | "
          f"min={min_d} mm | occ={presence[sensor_id]['occupied']} | "
          f"침상={'있음' if presence['in_bed'] else '없음'}")
    return jsonify({"ok": True}), 200


@app.route("/tof/calibrate", methods=["POST", "GET"])
def calibrate():
    """빈 침대 상태에서 호출 → 현재 거리 격자를 베이스라인으로 저장."""
    captured = {}
    for sid in ("tof1", "tof2"):
        d = latest[sid]["distances_mm"]
        if d:
            baseline[sid] = list(d)
            captured[sid] = len([x for x in d if x and x > 0])
    if not captured:
        return jsonify({"ok": False,
                        "error": "센서 데이터가 아직 없음 - 잠시 후 다시"}), 400
    save_baseline()
    print(f"[calibrate] 베이스라인 저장: {captured}")
    return jsonify({"ok": True, "captured_valid_zones": captured}), 200


@app.route("/tof/presence", methods=["GET"])
def get_presence():
    return jsonify({
        "in_bed": presence["in_bed"],
        "since": presence["since"],
        "tof1": presence["tof1"],
        "tof2": presence["tof2"],
        "baseline_set": {"tof1": baseline["tof1"] is not None,
                         "tof2": baseline["tof2"] is not None},
    })


@app.route("/tof/anomaly", methods=["GET"])
def get_anomaly():
    """LSTM-AE 실시간 이상탐지 결과. 재구성오차 > 임계면 status=anomaly."""
    return jsonify(anomaly_latest)


@app.route("/tof/latest", methods=["GET"])
def get_latest():
    resp = dict(latest)                      # 원본 latest 훼손 방지
    if _POSTURE is not None:
        try:
            label, conf = _predict_posture(_POSTURE, latest)
            resp["posture"] = label
            resp["posture_conf"] = round(conf, 3) if conf is not None else None
        except Exception:
            resp["posture"] = None
    return jsonify(resp)


@app.route("/tof/3d", methods=["GET"])
def tof_3d():
    """ToF 자세 3D 뷰어 (같은 출처에서 /tof/latest·/tof/presence 를 읽음)."""
    return send_from_directory(HERE, "tof_3d.html")


@app.route("/tof/log", methods=["GET"])
def get_log():
    return jsonify(log[-20:])


@app.route("/beds", methods=["GET"])
def list_beds():
    return jsonify(db.list_beds())


@app.route("/", methods=["GET"])
def index():
    t1, t2 = latest["tof1"], latest["tof2"]

    def summary(t):
        if t["distances_mm"] is None:
            return "아직 없음"
        valid = [d for d in t["distances_mm"] if d and d > 0]
        return f"최솟값 {min(valid)} mm / 평균 {int(sum(valid)/len(valid))} mm" if valid else "유효값 없음"

    log_rows = "".join(
        f"<tr><td>{e['received_at']}</td><td>{e['sensor']}</td>"
        f"<td>{e['resolution']}</td>"
        f"<td>{min((d for d in e['distances_mm'] if d and d > 0), default='—')} mm</td></tr>"
        for e in reversed(log[-20:])
    )

    # 침상 재실 뱃지
    in_bed = presence["in_bed"]
    badge_color = "#0a7" if in_bed else "#c33"
    badge_text  = "🛏️ 침상에 있음" if in_bed else "🚪 침상 비어 있음"
    base_ok = baseline["tof1"] is not None or baseline["tof2"] is not None
    base_note = ("" if base_ok else
                 " &nbsp;·&nbsp; ⚠ 베이스라인 미설정 (빈 침대에서 '캘리브레이션' 누르세요)")
    occ1 = presence["tof1"]["occupied"]
    occ2 = presence["tof2"]["occupied"]

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
<h2>VL53L5CX 침상 모니터 (1초 자동 새로고침)</h2>

<div class=card style="text-align:center;background:{badge_color};color:#fff">
  <div style="font-size:2.2em;font-weight:bold">{badge_text}</div>
  <div style="font-size:.85em;opacity:.9;margin-top:6px">
    변경 시각: {presence['since'] or '—'} &nbsp;·&nbsp;
    점유 존: ToF1 {occ1} / ToF2 {occ2} (각 임계 {PRESENCE_MIN_ZONES}){base_note}
  </div>
  <button onclick="fetch('/tof/calibrate',{{method:'POST'}}).then(r=>r.json()).then(j=>alert(j.ok?('베이스라인 저장됨: '+JSON.stringify(j.captured_valid_zones)):('실패: '+j.error)))"
    style="margin-top:10px;padding:8px 16px;font-size:1em;border:0;border-radius:6px;cursor:pointer;background:#fff;color:#333;font-weight:bold">
    빈 침대 캘리브레이션
  </button>
</div>

<div style="display:flex;gap:16px;flex-wrap:wrap">
  <div class=card style="flex:1;min-width:280px">
    <div class=label>ToF 1 — {t1['resolution'] or '—'} | 점유 {occ1}존 | {t1['received_at'] or '수신 없음'}</div>
    <div class=summary>{summary(t1)}</div>
    {grid_html(t1['distances_mm'], t1['resolution'] or '4x4')}
  </div>
  <div class=card style="flex:1;min-width:280px">
    <div class=label>ToF 2 — {t2['resolution'] or '—'} | 점유 {occ2}존 | {t2['received_at'] or '수신 없음'}</div>
    <div class=summary>{summary(t2)}</div>
    {grid_html(t2['distances_mm'], t2['resolution'] or '4x4')}
  </div>
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
    db.init_db()
    print("VL53L5CX ToF 서버 시작 → http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, debug=True)
