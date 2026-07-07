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

from flask import Flask, request, jsonify, send_from_directory, Response
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

# roi 지오메트리(침대 폭·존별 3D 좌표) — 위 predict_posture import가 TOF/ml 를 path에 넣어줌.
# 낙상위험(침대 가장자리에 쏠려 누움) 계산에 존별 좌우 Y 좌표가 필요.
try:
    import roi as _roi
except Exception as e:
    _roi = None
    print(f"[risk] roi 로드 실패({e}) — 가장자리 낙상위험 비활성")

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
PRESENCE_MIN_ZONES = 8      # 들어올 때: 점유 존이 이 개수 이상이면 그 센서는 "감지"
                            # (8x8 전환: 존 개수가 4x4 대비 4배라 2→8로 비례 조정 — 실측 미검증치)
STAY_MIN_ZONES     = 4      # 이미 재실 중엔 이 개수 이상이면 유지 (8x8: 1→4로 비례 조정 — 실측 미검증치)
CONFIRM_ENTER      = 3      # 이탈→재실 확정에 필요한 연속 프레임 (빠르게 진입)
CONFIRM_EXIT       = 24     # 재실→이탈 확정에 필요한 연속 프레임 (약 2.4초, 깜빡임 억제)
CONFIRM_FRAMES     = 3      # (하위호환) 사용 안 함
ABS_FALLBACK_MM    = 1500   # 베이스라인 없을 때: 이보다 가까운 유효 존을 점유로 간주
ROI_MAX_MM         = 2300   # 침대 ROI: 빈 침대 기준거리가 이보다 먼 존은 벽·바닥으로 보고
                            # 감지에서 제외 (벽쪽 오탐 방지). 침대 near~far가 이 안에 들어오게 설정.

# 센서 물리 위치가 코드 가정과 반대(tof1↔tof2 뒤바뀜)면 True → 들어오는 데이터를 맞바꿈
# (주의: True로 하면 baseline·모델도 그 배치로 맞춰져 있어야 함. 3D 화면 방향만 문제면 뷰어 토글 사용)
SWAP_TOF = False

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

    # 센서 물리 위치가 바뀌어 tof1↔tof2가 뒤바뀐 경우: 들어오는 순간 맞바꿔
    # 이후 파이프라인(모델·재실·DB·3D)이 전부 일관된 배치로 처리되게 함.
    if SWAP_TOF and sensor_id in ("tof1", "tof2"):
        sensor_id = "tof2" if sensor_id == "tof1" else "tof1"

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
    """낙상위험 판정 (자세 기반). 예전 LSTM-AE는 빈침대 baseline에 묶여 사람만 있으면
    무조건 이상으로 떠서(오탐), '낙상위험 자세(가장자리에 쏠려 누움)'만 위험으로 바꿈.
    대시보드 카드가 기대하는 필드(status/err/ratio)에 맞춰 반환."""
    return jsonify({
        "status": risk_latest["status"],                       # anomaly(위험) / normal(정상)
        "err":    0.0 if risk_latest["posture"] else None,     # None이면 카드가 '대기'로 표시
        "ratio":  risk_latest["ratio"],                        # |Y|/침대반폭 (가장자리 근접도)
        "reason": risk_latest["reason"],
        "y":      risk_latest["y"],
        "posture": risk_latest["posture"],
        "at":     risk_latest["at"],
        "lstm":   anomaly_latest,                              # 참고용(예전 LSTM 값) — UI 미사용
    })


# ── 자세 표시(한글/색) · 뒤척임(temporal) · 낙상위험(가장자리) ──────────────
import time as _time
from collections import deque

# RF 원시 라벨 → (한글, 색). side_left/right 는 표시상 "옆으로 누움"으로 통일.
POSTURE_KO = {
    "empty":      ("이탈",        "#9aa0b4"),
    "supine":     ("누움",        "#22c55e"),
    "side_left":  ("옆으로 누움",  "#38bdf8"),
    "side_right": ("옆으로 누움",  "#38bdf8"),
    "tossing":    ("뒤척임",      "#fb923c"),
    "edge_sit":   ("걸터앉음",     "#f59e0b"),
    "sitting":    ("앉음",        "#4aa8ff"),
}

# 뒤척임: 좌우(side_left↔side_right)를 창(window) 안에서 여러 번 번갈으면 "뒤척임".
# 한쪽으로 누워 가만히 있으면 스위치가 안 쌓여(그리고 오래된 건 만료) → 그대로 옆으로 누움.
TOSS_WINDOW_SEC   = 6.0     # 이 시간(초) 안의 좌우 전환만 셈
TOSS_MIN_SWITCHES = 2       # 창 안에서 좌우 전환이 이 횟수 이상이면 뒤척임
_side_hist = deque()        # [(t, 'L'|'R'), ...] — 좌우가 '바뀔 때'만 기록

def _temporal_label(raw):
    now = _time.time()
    if raw in ("side_left", "side_right"):
        side = "L" if raw == "side_left" else "R"
        if not _side_hist or _side_hist[-1][1] != side:   # 바뀔 때만 push
            _side_hist.append((now, side))
    while _side_hist and now - _side_hist[0][0] > TOSS_WINDOW_SEC:
        _side_hist.popleft()
    switches = sum(1 for i in range(1, len(_side_hist))
                   if _side_hist[i][1] != _side_hist[i - 1][1])
    if switches >= TOSS_MIN_SWITCHES and raw in ("side_left", "side_right", "supine"):
        return "tossing"
    return raw

# 낙상위험: "침대 가장자리에 쏠려 누움". 점유존들의 좌우중심 Y(m)가 한쪽으로 크게 치우침.
BED_HALF_W    = 0.475   # 침대 폭 0.95m 의 절반 (roi.BED_WID/2)
EDGE_Y_M      = 0.25    # |좌우중심 Y| 가 이보다 크면 가장자리 쏠림 → 위험 (실측 튜닝 필요)
RISK_MIN_PTS  = 6       # 판단에 필요한 최소 점유 존
RISK_CONFIRM  = 2       # 연속 이 횟수 이상이어야 상태 전환(깜빡임 억제)
_risk_pending = {"anomaly": 0, "normal": 0}
risk_latest   = {"status": "normal", "reason": None, "y": None,
                 "ratio": 0.0, "posture": None, "at": None}

def _body_mean_y():
    """점유존(몸/이불이 올라온 존)들이 닿는 지점의 좌우 Y(m) 평균과 점 개수."""
    if _roi is None:
        return None, 0
    ys = []
    for sid in ("tof1", "tof2"):
        d = (latest.get(sid) or {}).get("distances_mm")
        base = baseline.get(sid)
        if not d:
            continue
        for z, dist in enumerate(d):
            if dist is None or dist <= 0:
                continue
            b = base[z] if (base and z < len(base)) else None
            if not b or b <= 0 or b > ROI_MAX_MM:      # 침대 ROI 밖(벽·바닥) 제외
                continue
            if dist < b - PRESENCE_DELTA_MM:           # baseline 대비 가까움 = 몸
                y = _roi.hit_y(sid, z, dist)
                if y is not None and abs(y) <= 0.9:
                    ys.append(y)
    if not ys:
        return None, 0
    return sum(ys) / len(ys), len(ys)

def _fall_risk(disp, now_iso):
    """낙상위험 자세만 위험 판정. 지금은 '가장자리에 쏠려 누움'만 감지."""
    lying = disp in ("supine", "side_left", "side_right", "tossing")
    ymean, npts = _body_mean_y()
    danger = bool(lying and npts >= RISK_MIN_PTS
                  and ymean is not None and abs(ymean) > EDGE_Y_M)
    want = "anomaly" if danger else "normal"
    _risk_pending[want] += 1
    _risk_pending["normal" if danger else "anomaly"] = 0
    if _risk_pending[want] >= RISK_CONFIRM:
        risk_latest["status"] = want
    ratio = min(abs(ymean) / BED_HALF_W, 1.5) if ymean is not None else 0.0
    reason = None
    if risk_latest["status"] == "anomaly":
        side = "왼쪽" if (ymean or 0) < 0 else "오른쪽"
        reason = f"침대 {side} 가장자리에 쏠려 누움 — 낙상 위험"
    risk_latest.update({"reason": reason,
                        "y": round(ymean, 3) if ymean is not None else None,
                        "ratio": round(ratio, 3), "posture": disp, "at": now_iso})
    return risk_latest


@app.route("/tof/latest", methods=["GET"])
def get_latest():
    resp = dict(latest)                      # 원본 latest 훼손 방지
    if _POSTURE is not None:
        try:
            label, conf = _predict_posture(_POSTURE, latest)
            # 빈침대 게이트: 점유존(baseline 대비 가까운 존)이 하나도 없으면 RF 무시하고 empty 확정.
            # RF가 발쪽 걸터앉기↔빈침대를 헷갈려 빈침대를 edge_sit 등으로 오탐하는 것 방지.
            # (사람이 있으면 occ>0 → RF 자세 그대로 사용, 걸터앉기 감지도 유지)
            occ = (presence["tof1"]["occupied"] or 0) + (presence["tof2"]["occupied"] or 0)
            if occ == 0:
                label, conf = "empty", 1.0
            disp = _temporal_label(label) if label else label   # 좌우 번갈음 → 뒤척임
            ko, color = POSTURE_KO.get(disp, (disp or "--", "#9aa0b4"))
            now_iso = latest["tof1"].get("received_at") or latest["tof2"].get("received_at")
            resp["posture"]       = disp                         # empty/supine/side_*/tossing/edge_sit/sitting
            resp["posture_raw"]   = label                        # 뒤척임 판정 전 원시 라벨
            resp["posture_ko"]    = ko                           # 한글 표시 (3D·카드 공통)
            resp["posture_color"] = color
            resp["posture_conf"]  = round(conf, 3) if conf is not None else None
            resp["risk"]          = _fall_risk(disp, now_iso) if disp else dict(risk_latest)
        except Exception:
            resp["posture"] = None
    return jsonify(resp)


@app.route("/tof/3d", methods=["GET"])
def tof_3d():
    """ToF 자세 3D 뷰어 (같은 출처에서 /tof/latest·/tof/presence 를 읽음)."""
    return send_from_directory(HERE, "tof_3d.html")


# ── 대시보드 서빙 (CSI 제거 → tof_server가 대시보드 호스팅) ──────────────
_SITE = os.path.join(HERE, "..", "site", "index.html")


@app.route("/dashboard", methods=["GET"])
def dashboard():
    try:
        with open(_SITE, "r", encoding="utf-8") as f:
            resp = Response(f.read(), mimetype="text/html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    except FileNotFoundError:
        return Response("site/index.html not found", status=404)


# ── mmWave(:5002) 프록시 ──────────────────────────────────────────────
# 대시보드가 :5001(여기)에서 서빙되므로, mmWave 점군 iframe·값도 :5001을 통해
# same-origin으로 받게 중계 → 브라우저의 타 포트(:5002) iframe/fetch 차단 회피.
import urllib.request


@app.route("/mmw/<path:p>", methods=["GET"])
def proxy_mmw(p):
    q = ("?" + request.query_string.decode()) if request.query_string else ""
    try:
        with urllib.request.urlopen("http://127.0.0.1:5002/mmw/" + p + q, timeout=6) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type", "application/octet-stream")
        resp = Response(body, mimetype=ctype)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        return Response(f"mmw proxy error({p}): {e}", status=502)


@app.route("/tof/log", methods=["GET"])
def get_log():
    return jsonify(log[-20:])


@app.route("/beds", methods=["GET"])
def list_beds():
    return jsonify(db.list_beds())


@app.route("/", methods=["GET"])
def index():
    return """<!doctype html>
<html><head><meta charset=utf-8><title>VL53L5CX 모니터 (실시간)</title>
<style>
  body{font-family:monospace;padding:20px;background:#f8f8f8}
  h2{color:#333;margin:0 0 4px}
  .hint{color:#888;font-size:12px;margin-bottom:14px}
  .card{background:#fff;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 4px #0002}
  .label{font-size:.75em;color:#999;margin-bottom:6px}
  .summary{font-size:1.2em;font-weight:bold;color:#0a7;margin-bottom:8px}
  .boards{display:flex;gap:16px;flex-wrap:wrap}
  .board{flex:1;min-width:300px}
  .grid{display:grid;gap:3px;margin-top:8px}
  .zone{aspect-ratio:1;display:flex;align-items:center;justify-content:center;
        font-size:10.5px;border-radius:3px;background:#eee;border:1px solid #ddd}
</style></head>
<body>
<h2>VL53L5CX 침상 모니터</h2>
<div class="hint">들어오는 프레임마다 즉시 갱신 (폴링 80ms) — 새로고침 불필요</div>

<div class="card" style="text-align:center" id="badge">연결 대기 중…</div>

<div class="boards" id="boards">데이터 대기 중...</div>

<div class="card">
  <button onclick="fetch('/tof/calibrate',{method:'POST'}).then(r=>r.json()).then(j=>alert(j.ok?('베이스라인 저장됨: '+JSON.stringify(j.captured_valid_zones)):('실패: '+j.error)))"
    style="padding:8px 16px;font-size:1em;border:0;border-radius:6px;cursor:pointer;background:#333;color:#fff;font-weight:bold">
    빈 침대 캘리브레이션
  </button>
</div>

<script>
function colorOf(d){
  if(!d || d<=0) return '#eee';
  if(d<500) return '#0a72';
  if(d<1500) return '#f802';
  return '#8882';
}
function textColor(d){
  if(!d || d<=0) return '#bbb';
  if(d<500) return '#0a7';
  if(d<1500) return '#f80';
  return '#888';
}
async function poll(){
  try{
    const [latest, presence] = await Promise.all([
      fetch('/tof/latest').then(r=>r.json()),
      fetch('/tof/presence').then(r=>r.json())
    ]);
    const badge = document.getElementById('badge');
    const inBed = presence.in_bed;
    badge.style.background = inBed ? '#0a7' : '#c33';
    badge.style.color = '#fff';
    const baseOk = presence.baseline_set && (presence.baseline_set.tof1 || presence.baseline_set.tof2);
    badge.innerHTML = `<div style="font-size:1.8em;font-weight:bold">${inBed?'🛏️ 침상에 있음':'🚪 침상 비어 있음'}</div>
      <div style="font-size:.85em;opacity:.9;margin-top:4px">
        변경 시각: ${presence.since||'—'} · 점유 존: tof1 ${presence.tof1?.occupied??'–'} / tof2 ${presence.tof2?.occupied??'–'}
        ${baseOk?'':' · ⚠ 베이스라인 미설정'}
        ${latest.posture? ' · 자세: '+latest.posture+' ('+Math.round((latest.posture_conf||0)*100)+'%)' : ''}
      </div>`;

    const boards = document.getElementById('boards');
    const sids = ['tof1','tof2'].filter(sid=>latest[sid] && latest[sid].distances_mm);
    if(sids.length===0){ boards.innerHTML='<span style="color:#999">아직 수신된 프레임이 없습니다.</span>'; return; }
    boards.innerHTML = sids.map(sid=>{
      const f = latest[sid];
      const n = f.distances_mm.length;
      const side = Math.round(Math.sqrt(n)) || 4;
      const valid = f.distances_mm.filter(d=>d>0);
      const summary = valid.length ? `최솟값 ${Math.min(...valid)} mm / 평균 ${Math.round(valid.reduce((a,b)=>a+b,0)/valid.length)} mm` : '유효값 없음';
      const cells = f.distances_mm.map(d=>
        `<div class="zone" style="background:${colorOf(d)};color:${textColor(d)}">${(d&&d>0)?d:'—'}</div>`
      ).join('');
      return `<div class="card board">
        <div class="label">${sid} — ${f.resolution||'—'} | ${f.received_at||'수신 없음'}</div>
        <div class="summary">${summary}</div>
        <div class="grid" style="grid-template-columns:repeat(${side},1fr);max-width:${side*40}px">${cells}</div>
      </div>`;
    }).join('');
  }catch(e){ console.error(e); }
}
setInterval(poll, 80);
poll();
</script>
</body></html>"""


if __name__ == "__main__":
    db.init_db()
    print("VL53L5CX ToF 서버 시작 → http://0.0.0.0:5001")
    # threaded=True: tof1·tof2가 동시에 POST해도 서로 안 막고 병렬 처리(싱글스레드면 8x8 동시 전송 시 실효 속도 반토막)
    app.run(host="0.0.0.0", port=5001, debug=True, threaded=True)
