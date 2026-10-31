"""
ToF(VL53L5CX) 수신 서버
포트: 5001

POST /tof              — ToF 거리 데이터 수신 (raw 를 tof_readings 에 저장)
GET  /tof/latest       — 센서별 최신 프레임 (in-memory)
GET  /tof/log          — 최근 수신 로그 (in-memory)
GET  /tof/presence     — 침상 재실 판정
POST /tof/calibrate    — 빈 침대 베이스라인 저장
"""

from flask import Flask, request, jsonify, send_from_directory, Response, redirect
from flask_cors import CORS
from datetime import datetime
import logging
import os, json, sys
from collections import deque
import db
from copy import deepcopy
from diagnostic_observer import observer, tof_snapshot
import sim_state
from live_diagnostics import publisher as diagnostic_publisher, stream_publisher

# Windows 콘솔(cp949)이 —/⚠ 같은 유니코드를 못 그리면 print()가 UnicodeEncodeError로
# 죽어 서버 전체가 내려가므로, 그런 문자는 죽는 대신 대체 문자로 바꿔 출력만 깨지게 함.
sys.stdout.reconfigure(errors="replace")
sys.stderr.reconfigure(errors="replace")

# 12번 탭(시스템 확장) STEP 6-9 raw/clean 일관성 진단 — 옵션(없어도 서버는 정상 동작)
try:
    import data_integrity as _data_integrity
except Exception:
    _data_integrity = None

# 라벨별 기대 유효존 범위(센서 한 대의 64칸 중 측정값이 있는 칸 수).
# 2026-10-12: 새 설치(발 50°·머리 30°)의 TOF/dataset_v2(S01 1명, 18만 프레임)에서 라벨별 1~99백분위 ±3칸으로 다시 잡았다.
# 이전 값(empty 0~6 등)은 옛 설치 기준이라 지금 빈 침대(약 39~54칸)를 늘 불일치로 표시했다. 1명 데이터라 참고용 진단이다.
TOF_VALID_ZONE_EXPECTED = {
    "empty": (36, 57), "supine": (24, 46), "side_left": (24, 47), "side_right": (25, 49),
    "sitting": (37, 57), "edge_sit": (37, 59),
}

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

# LSTM-AE 점수 계산은 요청 처리와 분리한 스레드에서 한다(아래 _anomaly_worker).
import threading as _threading
_anom_lock = _threading.Lock()
_anom_state = {"feat": None, "at": None, "scored_at": None}
ANOMALY_SCORE_EVERY_SEC = 0.5


def _anomaly_worker():
    import time as _t
    try:
        import torch as _torch
        _torch.set_num_threads(1)          # 센서 수신 스레드와 CPU를 다투지 않게
    except Exception:
        pass
    while True:
        _t.sleep(ANOMALY_SCORE_EVERY_SEC)
        with _anom_lock:
            at, feat = _anom_state["at"], _anom_state["feat"]
            if at is None or at == _anom_state["scored_at"]:
                continue
            _anom_state["scored_at"] = at
            filled = len(_ANOM.buf)
            window = list(_ANOM.buf)
        try:
            r = _ANOM.score_window(window) if filled >= _ANOM.seq else None
            if r:
                r["at"] = at
                anomaly_latest.update(r)
            observer.record('tof', 'lstm', {'features': list(feat), 'result': deepcopy(r),
                            'filled': filled, 'seq': _ANOM.seq,
                            'normalization_mu': _ANOM.mu.tolist(), 'normalization_sd': _ANOM.sd.tolist()})
        except Exception as e:
            print(f"[anomaly] 계산 실패: {e}")


if _ANOM is not None:
    _threading.Thread(target=_anomaly_worker, daemon=True).start()

# 최신 이상탐지 결과 (대시보드가 /tof/anomaly 로 폴링)
anomaly_latest = {"status": "init", "err": None, "threshold": None,
                  "ratio": None, "filled": 0, "seq": None, "at": None}

app = Flask(__name__)
# 센서 POST마다 남는 접속 기록이 /tmp 로그를 하루 수백 MB씩 키웠다(2026-10-11) → 경고 이상만 남긴다.
logging.getLogger("werkzeug").setLevel(logging.WARNING)
CORS(app)

# ── 침상 재실(있다/없다) 감지 설정 ────────────────────────────────────
# A 방식: 양쪽 레일에서 침대 위를 내려다봄.
# 빈 침대 베이스라인 대비, 그보다 PRESENCE_DELTA_MM 이상 "가까운" 존 =
# 매트리스 위에 몸/이불이 올라온 것 → 그런 존이 일정 개수 이상이면 사람 있음.
PRESENCE_DELTA_MM  = 150    # 베이스라인보다 이만큼(mm) 가까우면 점유 존
PRESENCE_MIN_ZONES = 8      # 들어올 때: 점유 존이 이 개수 이상이면 그 센서는 "감지"
                            # (8x8 전환: 존 개수가 4x4 대비 4배라 2→8로 비례 조정 — 실측 미검증치)
STAY_MIN_ZONES     = 4      # 이미 재실 중엔 이 개수 이상이면 유지 (8x8: 1→4로 비례 조정 — 실측 미검증치)
STRONG_ZONES       = 16     # 10-29: 두 센서 사람 칸 합이 이 이상이면 레이더 보정(서 있음·아무도 없음)으로 재실을 끄지 않는다(누운 사람 22~26칸)
CONFIRM_ENTER      = 3      # 이탈→재실 확정에 필요한 연속 프레임 (빠르게 진입)
CONFIRM_EXIT       = 16     # 재실→이탈 확정에 필요한 연속 프레임(두 센서 합쳐 초당 약 20 → 약 0.8초; 10-22 시연 24→16)
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


# 원시 프레임 저장 조건(2026-10-11): 침상에 사람이 있을 때 + 이탈 확정 후 TOF_STORE_AFTER_EXIT_SEC초.
# 빈 침대 프레임은 같은 값만 반복돼 하루 수백만 행이 쌓였다(DB 1.1GB). 재실 판정·대시보드·로우데이터 화면은
# 저장 여부와 관계없이 그대로 동작한다.
TOF_STORE_AFTER_EXIT_SEC = 10.0
_raw_store = {"until": 0.0}


def _should_store_raw(in_bed: bool, now_ts: float) -> bool:
    if in_bed:
        _raw_store["until"] = now_ts + TOF_STORE_AFTER_EXIT_SEC
        return True
    return now_ts < _raw_store["until"]


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
        observer.record('tof', 'baseline_save', {'status': 'written', 'path': BASELINE_FILE,
                                                'note': '수동 보정 거리 기준 저장; 누적 평균 아님'}, event=True)
    except Exception as e:
        observer.record('tof', 'baseline_save', {'status': 'failed', 'path': BASELINE_FILE,
                                                'error': str(e)}, event=True)
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


BED_POSTURES = ("edge_sit", "sitting", "supine", "side_left", "side_right")
_posture_hint = {"label": None, "conf": None, "at": 0.0}
_disp_state = {"shown": None, "cand": None, "since": 0.0}
DISP_STABLE_SEC = 0.5   # 10-22 시연: 1초 → 0.5초(조명 가린 뒤)   # _posture_fields가 갱신(재실 판단 보조)


def now_ts_s():
    return _time.time()


_recent_frames = {"tof1": deque(maxlen=3), "tof2": deque(maxlen=3)}


def _median3(sensor_id, distances):
    """2026-10-22: 조명(형광등·LED 깜빡임·햇빛) 때문에 칸 하나가 한두 프레임만 튀어 빈 침대에 '사람'이 잠깐 잡혔다 →
    칸마다 최근 3프레임 중앙값(측정 실패 -1은 빼고, 유효값이 2개 미만이면 -1)으로 재실을 판단한다."""
    buf = _recent_frames[sensor_id]
    buf.append(list(distances or []))
    out = []
    for z in range(max(len(f) for f in buf)):
        vals = sorted(f[z] for f in buf if z < len(f) and f[z] is not None and f[z] > 0)
        out.append(vals[len(vals) // 2] if len(vals) >= 2 else -1)
    return out


def update_presence(sensor_id, distances, now):
    occ = occupied_zones(sensor_id, _median3(sensor_id, distances))
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
    # 2026-10-22: 침대 끝에 걸터앉으면 몸 대부분이 매트리스 밖이라 사람 칸이 0~1개 → '이탈'로 깜빡였다.
    # 자세 모델이 최근 3초 안에 사람 자세(걸터앉음·앉음·누움)를 확신도 0.5 이상으로 냈고 사람 칸이 하나라도 있으면 침대 위로 본다.
    h = _posture_hint
    # 단 레이더에서 침대 근처 사람이 모두 서 있는 높이면 쓰지 않는다(침대 옆에 선 사람, 10-22 저녁)
    if (not overall and h["label"] in BED_POSTURES and (h["conf"] or 0) >= 0.5 and now_ts_s() - h["at"] <= 3.0
            and (t1o + t2o) >= 1 and not _standing_by_bed() and not _nobody_by_bed()):
        overall = True
    # 10-23 오후: 침대 옆에 서 있으면 머리 쪽 ToF가 그 사람 몸을 8~9칸 잡아 칸 수만으로 재실(걸터앉음·주의)이 됐다.
    # 레이더에서 침대 근처 사람이 모두 서 있는 높이면 칸 수와 상관없이 침대 위가 아니다(앉거나 누우면 레이더 높이가 낮아짐)
    # 10-23 오후: 서 있음이 0.4초(레이더 두 번) 이어지면 이탈을 기다리지 않고 바로 확정한다 — 예전엔 자세는 바로 '빈 침대'가 되는데
    # 재실은 16프레임(약 0.8초)을 더 기다려 그 사이 알림이 '정상'으로 떴다가 '경고'로 바뀌었다
    # 10-29: 누운 환자를 레이더가 놓치고(누우면 흔히 놓침) 침대 옆에 유령 추적(높이 1.6m)이 있으면 '모두 서 있음'으로
    # 머리 쪽 24칸이 무시돼 누워 있는데 이탈이 떴다 → 사람 칸이 STRONG_ZONES 이상이면 레이더 보정(서 있음·아무도 없음)을 안 쓴다.
    # 서 있는 사람(머리 쪽 8~9칸)·가장자리 물건·조명 잡음(2~3칸)은 이보다 적다
    strong = (t1o + t2o) >= STRONG_ZONES
    stand = _standing_by_bed() and not strong
    nowt = now_ts_s()
    _stand["since"] = (_stand["since"] or nowt) if stand else None
    _stand["stable"] = stand and nowt - _stand["since"] >= 0.4
    if overall and stand:
        overall = False
    # 10-23 오후: 침대 옆에 물건(가방·의자)을 두면 두 센서 가장자리 열이 잡혀 아무도 없는데 걸터앉음이 됐다.
    # 레이더가 살아 있는데 침대 1.5m 안에 5초 넘게 아무도 없으면 칸 수와 상관없이 침대 위가 아니다
    nobody = _nobody_by_bed() and not strong
    _empty["since"] = (_empty["since"] or nowt) if nobody else None
    if overall and nobody and nowt - _empty["since"] >= 5.0:
        overall = False

    if overall != presence["in_bed"]:
        presence["pending"] += 1
        need = CONFIRM_ENTER if overall else (1 if _stand["stable"] else CONFIRM_EXIT)
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

    # Original storage/analysis continues below. This extra copy is latest-only RAM transport.
    diagnostic_publisher.publish(sensor_id, {
        'sensor': sensor_id, 'resolution': resolution, 'distances_mm': distances,
        'raw_distances_mm': data.get('raw_distances_mm'),
        'target_status': data.get('target_status'), 'targets': targets, 'received_at': now,
        # 펌웨어가 보내는 설치 각도는 20°로 고정돼 있어(업로드 전) 실제 설치값으로 표시한다(2026-10-08 설치).
        'mount_height_m': TOF_MOUNT.get(sensor_id, {}).get('height_m', data.get('mount_height_m')),
        'mount_down_tilt_deg': TOF_MOUNT.get(sensor_id, {}).get('down_tilt_deg', data.get('mount_down_tilt_deg')),
    })
    # 노트북 스트림(:5012/api/stream, 2026-10-31): 펌웨어가 보낸 프레임 전부를 그대로(센서 ID만 맞바꾼 뒤) 전달. Pi에는 저장 안 함.
    stream_publisher.publish(sensor_id, {'received_at': now, 'firmware_sensor': data.get('sensor'), 'payload': data,
                                         'mount': TOF_MOUNT.get(sensor_id)})

    log.append({"sensor": sensor_id, "resolution": resolution,
                 "distances_mm": distances, "received_at": now})
    if len(log) > 100:
        log.pop(0)

    # 재실(있다/없다) 판정 업데이트
    previous_presence = deepcopy(presence)
    update_presence(sensor_id, distances, now)
    observer.record('tof', 'ingest', {'sensor': sensor_id, 'received_at': now,
                    'zones_received': len(distances), 'occupied': presence[sensor_id]['occupied'],
                    'presence_before': previous_presence, 'presence_after': deepcopy(presence),
                    'in_bed': presence['in_bed'],
                    'reason': '재실 전환' if previous_presence['in_bed'] != presence['in_bed'] else '기존 재실 유지'},
                    event=previous_presence['in_bed'] != presence['in_bed'])

    # LSTM-AE 이상탐지: 이번 프레임 특징을 버퍼에 넣고 재구성오차 점수 갱신
    # 2026-10-12 재학습 모델(TOF/ml/train_lstm_ae_v2.py)은 두 센서를 짝지은 시점(약 9~10Hz)으로 학습했다.
    # 같은 속도로 맞추려고 tof2(머리 쪽) 프레임이 들어올 때만 특징을 넣는다(두 센서 최신값을 함께 씀).
    # 특징 계산·버퍼 추가만 여기서 하고, 모델 점수는 _anomaly_worker 스레드가 0.5초마다 매긴다.
    # (요청 처리 중에 모델을 돌리면 tof2 응답이 늦어져 ESP가 프레임을 버렸다: 12Hz → 5Hz, 2026-10-12)
    if _ANOM is not None and _frame_feature is not None and sensor_id == "tof2":
        try:
            feat = _frame_feature(latest, baseline, presence)
            with _anom_lock:
                _ANOM.push(feat)
                _anom_state["feat"], _anom_state["at"] = feat, now
        except Exception as e:
            print(f"[anomaly] 특징 계산 실패: {e}")

    # raw SQLite 저장 (AI 학습용 원천 데이터 누적) — distances/targets 는 존별 컬럼
    label, _conf = None, None
    raw_id = None
    store_raw = _should_store_raw(presence["in_bed"], _time.time())
    if store_raw:
        try:
            raw_id = observer.write('tof', 'tof_readings', db.insert_tof, sensor_id, now, resolution, distances, targets,
                                    occupied=presence[sensor_id]["occupied"],
                                    in_bed=presence["in_bed"],
                                    labels={'sensor': sensor_id, 'in_bed': presence['in_bed'],
                                            'occupied': presence[sensor_id]['occupied'], 'posture': None,
                                            'note': 'tof_readings에는 자세 라벨 컬럼 없음'})
        except Exception as e:
            print(f"[DB] ToF insert failed: {e}")

    # 12번 탭 STEP 6-9: raw/clean 일관성 진단(라벨이 있을 때만 — 지금은 실시간 라벨이
    # latest.posture에 들어있을 수 있어 그걸 사용, 없으면 None으로 판단 보류)
    if _data_integrity is not None and store_raw:
        try:
            label = _recent_posture_label(now)
            diag = _data_integrity.check_tof_consistency(distances, label, TOF_VALID_ZONE_EXPECTED)
            observer.record('tof', 'integrity', diag)
            if diag.get("consistent") is False:
                observer.write('tof', 'sensor_clean_data', db.insert_clean_data,
                    patient_id="bed_01", sensor_type="tof", timestamp=now,
                    cleaned_value={"diagnosis": diag}, preprocessing_method="tof_valid_zone_check",
                    confidence=None, removed_outlier=False, raw_table="tof_readings", raw_id=raw_id,
                    labels={'preprocessing_method': 'tof_valid_zone_check', 'patient_id': 'bed_01',
                            'sensor_type': 'tof', 'raw_id': raw_id},
                )
                observer.write('tof', 'activity_log', db.insert_sensor_event,
                                        "bed_01", "data_integrity_flag", ts=now, level="caution",
                                        title=f"ToF({sensor_id}) 일관성 불일치", note=diag.get("note"),
                                        source_table="tof_readings", source_id=raw_id,
                                        labels={'event_type': 'data_integrity_flag', 'level': 'caution', 'source_id': raw_id})
        except Exception as e:
            print(f"[data_integrity] ToF 진단 실패: {e}")

    # 콘솔 로그는 재실이 바뀔 때만 남긴다(프레임마다 찍으면 /tmp 로그가 하루 수백 MB씩 커진다).
    if previous_presence["in_bed"] != presence["in_bed"]:
        print(f"[{now}] {sensor_id} | occ={presence[sensor_id]['occupied']} | "
              f"침상={'있음' if presence['in_bed'] else '없음'} | 원시 저장={'계속' if store_raw else '중지'}")
    return jsonify({"ok": True}), 200


@app.route("/tof/calibrate", methods=["POST", "GET"])
def calibrate():
    """빈 침대 상태에서 호출 → 5초 동안의 프레임 칸별 중앙값을 베이스라인으로 저장.
    2026-10-22: 예전엔 프레임 1장만 저장해 그 순간의 잡음·측정 실패(-1)가 기준에 들어갔다(기준 없는 칸·빈 침대에서 '걸터앉음' 깜빡임)."""
    # 10-22 저녁: 20:23 누군가 침대 근처에 사람이 있을 때 수동 저장 → 그 사람이 '빈 침대'로 들어가 빈 침대가 계속 걸터앉음·재실.
    # 자동 보정처럼 지금 레이더에 침대 1.5m 안 사람이 있으면 거절한다(?force=1이면 강제, 레이더 꺼짐이면 그대로 저장)
    if request.args.get("force") != "1":
        tg = _radar_targets()
        clr = _autocal.bed_clear([(_time.time(), tg)] * 20, _time.time()) if tg else {"clear": True}
        if not clr["clear"]:
            return jsonify({"ok": False, "error": f"빈 침대 기준 저장 거절: {clr['reason']} — 침대 주변을 비우고 다시"}), 409
    before = deepcopy(baseline)
    frames, seen = {"tof1": [], "tof2": []}, {}
    end = _time.time() + _autocal.COLLECT_SEC
    while _time.time() < end:
        for sid in ("tof1", "tof2"):
            at = latest[sid].get("received_at")
            if at and at != seen.get(sid) and latest[sid].get("distances_mm"):
                seen[sid] = at
                frames[sid].append(list(latest[sid]["distances_mm"]))
        _time.sleep(0.03)
    captured = {}
    for sid in ("tof1", "tof2"):
        if len(frames[sid]) >= 10:
            new, stats = _autocal.build_baseline(frames[sid], None)
            baseline[sid] = new
            captured[sid] = {"valid_zones": sum(1 for x in new if x and x > 0), "frames": len(frames[sid]),
                             "unstable_zones": stats["unstable_zones"]}
    if not captured:
        return jsonify({"ok": False,
                        "error": "센서 데이터가 아직 없음 - 잠시 후 다시"}), 400
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tof_baseline_history.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "manual": True, "old": before,
                                 "new": {k: baseline[k] for k in ("tof1", "tof2")}, "results": captured}, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[calibrate] 이력 기록 실패: {exc}")
    save_baseline()
    observer.record('tof', 'calibration', {'before': before, 'after': deepcopy(baseline),
                                        'captured_valid_zones': captured})
    print(f"[calibrate] 베이스라인 저장: {captured}")
    return jsonify({"ok": True, "captured_valid_zones": captured}), 200


@app.route("/tof/baseline", methods=["GET"])
def get_baseline():
    """빈 침대 기준값(칸별 mm) 읽기 전용 — 개발 대시보드(/dev)가 '사람 칸'을 칠하는 데 쓴다."""
    return jsonify({"tof1": baseline["tof1"], "tof2": baseline["tof2"], "auto": dict(baseline_auto)})


@app.route("/tof/presence", methods=["GET"])
def get_presence():
    bed_id = request.args.get("bed_id")
    if bed_id and sim_state.is_simulated(bed_id):
        return jsonify(sim_state.synthetic_tof_presence(bed_id))
    return jsonify({
        "in_bed": presence["in_bed"],
        "since": presence["since"],
        "tof1": presence["tof1"],
        "tof2": presence["tof2"],
        "baseline_set": {"tof1": baseline["tof1"] is not None,
                         "tof2": baseline["tof2"] is not None},
        "baseline_auto": dict(baseline_auto),   # 빈 침대 기준 자동 재캘리브레이션 최근 결과
        # 대시보드가 Pi 시계와 보는 기기 시계 차이를 보정하는 데 쓴다(data.js clockOffset)
        "server_now": datetime.now().isoformat(timespec="milliseconds"),
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

# 뒤척임(2026-10-14 재설계, 사용자 정의): 자세가 아니라 '누운 채 자세를 바꿀 때의 몇 초짜리 움직임'.
# ToF 모양이 멈춰 있던 모양에서 크게 달라졌다가(움직임) 다시 멈췄을 때, 전·후 자세가 다른 누운 자세면 그 구간이 뒤척임.
# 움직임 없이 자세 이름만 바뀐 것(모델이 왼쪽↔오른쪽을 헷갈림)은 세지 않는다. 계산은 server/tof_toss.py.
LYING_POSTURES = ("supine", "side_left", "side_right")
import tof_toss as _tof_toss
_tossdet = _tof_toss.TossDetector()


def _toss_update(raw):
    frames = {sid: (latest[sid].get("distances_mm") or []) for sid in ("tof1", "tof2")}
    return _tossdet.update(_time.time(), frames, raw, baseline)


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

# 실험(2026-10-13): 자세 판정에서 지우는 침대 폭 밖 칸으로 몸 일부(다리·팔)가 튀어나왔는지 본다.
# 판정·알림에는 아직 쓰지 않음 — /dev에서 실측 후 연결. 가장자리 ①=좌우 좌표 음수 쪽, ②=양수 쪽(실제 좌우는 측정으로 확인).
OFFBED_DELTA_MM = 150      # 빈 침대 기준보다 이만큼 가까우면 무언가 있음
OFFBED_MIN_Z_M = 0.10      # 닿은 지점이 바닥보다 이만큼 높아야(바닥 잡음 제외)


def _offbed_parts():
    sides = {"neg": [], "pos": []}
    if _roi is None:
        return None
    for sid in ("tof1", "tof2"):
        d = (latest.get(sid) or {}).get("distances_mm")
        base = baseline.get(sid)
        if not d or not base:
            continue
        for z, dist in enumerate(d):
            b = base[z] if z < len(base) else None
            if not dist or dist <= 0 or _roi.on_bed(sid, z, dist):
                continue
            if b and b > 0 and dist >= b - OFFBED_DELTA_MM:
                continue
            p = _roi.hit_point(sid, z, dist)
            if p is None or p[2] < OFFBED_MIN_Z_M:
                continue
            sides["neg" if p[1] < 0 else "pos"].append({"sensor": sid, "zone": z, "y": round(p[1], 2), "z": round(p[2], 2)})
    return {"neg": len(sides["neg"]), "pos": len(sides["pos"]),
            "max_z": max([q["z"] for v in sides.values() for q in v], default=None), "zones": sides}


# 실험(2026-10-13): 가장자리 움직임 — 걸터앉은 사람이 바깥으로 나가는지(이탈 임박), 다리를 흔드는지, 안으로 돌아오는지.
# 침대 안 사람 칸 + 튀어나온 몸 일부를 합친 좌우 위치를 최근 4초 동안 보고, 한쪽으로 꾸준히 옮겨 가면 '바깥쪽으로 이동',
# 오갔다 돌아오기를 반복하며 제자리면 '다리 흔듦'으로 본다. 단독 판정이 아니라 분류를 돕는 부가 정보다.
EDGE_WINDOW_SEC = 4.0
EDGE_MOVE_M = 0.12        # 4초 동안 이만큼 한쪽으로 옮겨 가면 이동
EDGE_SWING_M = 0.03       # 이만큼 넘게 움직였다 방향을 바꾸면 한 번 오간 것
_edge_hist = deque()


def _inbed_person_ys():
    ys = []
    for sid in ("tof1", "tof2"):
        d = (latest.get(sid) or {}).get("distances_mm")
        base = baseline.get(sid)
        if not d or _roi is None:
            continue
        for z, dist in enumerate(d):
            b = base[z] if (base and z < len(base)) else None
            if not dist or dist <= 0 or not b or b <= 0 or b > ROI_MAX_MM or dist >= b - PRESENCE_DELTA_MM:
                continue
            y = _roi.hit_y(sid, z, dist)
            if y is not None and abs(y) <= 0.9 and _roi.on_bed(sid, z, dist):
                ys.append(y)
    return ys


def _edge_motion(offbed):
    now = _time.time()
    if not presence["in_bed"] or offbed is None:
        _edge_hist.clear()
        return None
    ys = _inbed_person_ys() + [q["y"] for k in ("neg", "pos") for q in offbed["zones"][k]]
    if ys:
        _edge_hist.append((now, sum(ys) / len(ys), offbed["neg"], offbed["pos"]))
    while _edge_hist and now - _edge_hist[0][0] > EDGE_WINDOW_SEC:
        _edge_hist.popleft()
    if len(_edge_hist) < 8:
        return {"state": "판단 중", "parts": {"①": offbed["neg"], "②": offbed["pos"]}}
    yv = [h[1] for h in _edge_hist]
    sm = [sorted(yv[max(0, i - 1):i + 2])[len(yv[max(0, i - 1):i + 2]) // 2] for i in range(len(yv))]   # 3점 중앙값
    net, amp = sm[-1] - sm[0], max(sm) - min(sm)
    reversals, direction, anchor = 0, 0, sm[0]
    for v in sm[1:]:
        if direction >= 0 and v < anchor - EDGE_SWING_M:
            reversals += direction > 0
            direction, anchor = -1, v
        elif direction <= 0 and v > anchor + EDGE_SWING_M:
            reversals += direction < 0
            direction, anchor = 1, v
        elif (direction > 0 and v > anchor) or (direction < 0 and v < anchor):
            anchor = v
    out_neg, out_pos = _edge_hist[-1][2], _edge_hist[-1][3]
    side = -1 if out_neg > out_pos else 1 if out_pos > out_neg else (-1 if sm[-1] < 0 else 1)
    outward = net * side
    if reversals >= 2 and abs(net) < EDGE_MOVE_M:
        state = "다리 흔듦(제자리)"
    elif outward >= EDGE_MOVE_M and reversals <= 1:
        state = "바깥쪽으로 이동(이탈 임박)"
    elif outward <= -EDGE_MOVE_M:
        state = "안쪽으로 돌아옴"
    else:
        state = "가만히"
    return {"state": state, "side": "①" if side < 0 else "②", "net_m": round(net, 3), "amplitude_m": round(amp, 3),
            "reversals": reversals, "parts": {"①": out_neg, "②": out_pos}}


def _fall_risk(disp, now_iso):
    """낙상위험 자세만 위험 판정. 지금은 '가장자리에 쏠려 누움'만 감지."""
    lying = disp in LYING_POSTURES
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
    observer.record('tof', 'body_risk', {'n_points': npts, 'mean_y_m': ymean,
                    'sum_y_m': ymean * npts if ymean is not None else None,
                    'lying': lying, 'danger_candidate': danger, 'pending': deepcopy(_risk_pending),
                    'status': risk_latest['status'], 'reason': reason}, event=bool(danger))
    return risk_latest


# 현장 실측 설치값(2026-10-08): tof1=발 쪽, tof2=머리 쪽. TOF/ml/roi.py 의 TILT 값과 같은 설치.
TOF_MOUNT = {"tof1": {"height_m": 1.2, "down_tilt_deg": 50}, "tof2": {"height_m": 1.2, "down_tilt_deg": 30}}

_posture_cache = {"key": None, "fields": {}}


def _recent_posture_label(now_iso: str, max_age_sec: float = 2.0):
    """일관성 진단용 자세 라벨. 자세는 /tof/latest 조회 때 계산돼 _posture_cache에만 남는다
    (latest[sensor]에는 저장되지 않아 예전 코드는 라벨을 늘 None으로 읽었다). 캐시가 이 프레임보다
    max_age_sec 넘게 오래됐으면 None(판단 보류)을 돌려준다."""
    key, fields = _posture_cache.get("key"), _posture_cache.get("fields") or {}
    if not key or not fields.get("posture_raw"):
        return None
    try:
        cached_at = max(datetime.fromisoformat(t) for t in key if t)
        age = (datetime.fromisoformat(now_iso) - cached_at).total_seconds()
    except (TypeError, ValueError):
        return None
    return fields["posture_raw"] if 0 <= age <= max_age_sec else None


_posture_lock = _threading.Lock()


def _posture_fields() -> dict:
    """새 센서 프레임이 들어왔을 때만 자세를 판정하고(프레임 캐시), 자세 관련 필드를 돌려준다.
    /tof/latest 조회와 _bed_alert_worker 가 함께 쓴다."""
    if _POSTURE is None:
        return {}
    frame_key = (latest["tof1"].get("received_at"), latest["tof2"].get("received_at"))
    with _posture_lock:
        if _posture_cache.get("key") == frame_key:
            return dict(_posture_cache["fields"])
        try:
            label, conf = _predict_posture(_POSTURE, latest)
            # 빈침대 게이트: 점유존(baseline 대비 가까운 존)이 하나도 없으면 RF 무시하고 empty 확정.
            # RF가 발쪽 걸터앉기↔빈침대를 헷갈려 빈침대를 edge_sit 등으로 오탐하는 것 방지.
            # (사람이 있으면 occ>0 → RF 자세 그대로 사용, 걸터앉기 감지도 유지)
            occ = (presence["tof1"]["occupied"] or 0) + (presence["tof2"]["occupied"] or 0)
            # 10-22 저녁: 침대 옆에 서면 다리가 가장자리 1~2칸에 걸려 RF가 '걸터앉음'을 내고 화면에 이탈·걸터앉음·주의·경고가
            # 번갈아 떴다 → 재실이 아니고 레이더에서 침대 근처 사람이 모두 서 있는 높이면 빈 침대로 본다
            # 서 있음은 재실 확정과 같은 기준(_stand["stable"])으로 — 둘이 어긋나면 그 사이 알림이 '정상'으로 떴다
            if occ == 0 or _stand["stable"] or (not presence["in_bed"] and (_standing_by_bed() or _nobody_by_bed())):
                label, conf = "empty", 1.0
            _posture_hint.update(label=label, conf=conf, at=_time.time())
            # 화면 표시는 새 자세가 1초 이상 이어질 때만 바꾼다(조명 잡음으로 한두 번 튀는 '걸터앉음' 깜빡임 제거, 10-22)
            nowt = _time.time()
            if label != _disp_state["cand"]:
                _disp_state.update(cand=label, since=nowt)
            if _disp_state["shown"] is None or nowt - _disp_state["since"] >= DISP_STABLE_SEC:
                _disp_state["shown"] = label
            disp = _disp_state["shown"]
            toss = _toss_update(label)
            ko, color = POSTURE_KO.get(disp, (disp or "--", "#9aa0b4"))
            if toss["active"]:
                ko += " · 뒤척임"
            now_iso = latest["tof1"].get("received_at") or latest["tof2"].get("received_at")
            fields = {
                "posture": disp,                          # empty/supine/side_*/edge_sit/sitting
                "tossing": toss,                          # 누운 채 자세를 바꿨나(최근 5초)·10분간 횟수
                "posture_raw": label,                     # 뒤척임 판정 전 원시 라벨
                "posture_ko": ko,                         # 한글 표시 (3D·카드 공통)
                "posture_color": color,
                "posture_conf": round(conf, 3) if conf is not None else None,
                "risk": _fall_risk(disp, now_iso) if disp else dict(risk_latest),
            }
            observer.record('tof', 'posture', {'raw_label': label, 'display_label': disp,
                            'confidence': fields['posture_conf'], 'sensor_at': now_iso,
                            'risk': deepcopy(fields['risk']),
                            'body_mean': observer.latest_stage('tof', 'body_risk') if disp else None,
                            'gate_occupied': occ, 'status': 'executed_on_tof_latest'})
            _posture_cache["key"] = frame_key
            _posture_cache["fields"] = fields
            return dict(fields)
        except Exception as exc:
            observer.record('tof', 'posture', {'status': 'failed', 'error': str(exc)})
            return {"posture": None}


# ── 환자별 위험도 × 거동 범위 침상 알림(server/fall_risk.py, 2026-10-12) ──────────────
# 실제 센서는 연결 환자 bed_01 한 명에게만 연결된다. 1초마다 자세·재실로 상황을 정하고,
# 그 환자의 위험군·거동 범위로 알림 단계를 정한다. 단계가 주의 이상으로 오르면 activity_log에 남긴다.
import fall_risk
LIVE_BED_ID = "bed_01"
bed_alert_latest = {"level": "normal", "situation": None, "reason": None, "at": None}


def _bed_alert_worker():
    import time as _t
    profile, profile_at, last_level = None, 0.0, "normal"
    while True:
        _t.sleep(0.25)   # 10-23: 1초 → 0.25초(이탈 직후 알림이 늦게 바뀌던 것)
        try:
            if _t.time() - profile_at > 5:
                with db.get_conn() as conn:
                    profile = fall_risk.get_profile(conn, LIVE_BED_ID, events=0)
                profile_at = _t.time()
            fresh = any(latest[s].get("received_at") and
                        (datetime.now() - datetime.fromisoformat(latest[s]["received_at"])).total_seconds() < 10
                        for s in ("tof1", "tof2"))
            fields = _posture_fields() if fresh else {}
            situation = fall_risk.situation_from_tof(fields.get("posture"), presence["in_bed"] if fresh else None,
                                                     presence.get("since"))
            alert = fall_risk.bed_alert(profile, situation)
            alert["at"] = datetime.now().isoformat(timespec="milliseconds")
            alert["profile"] = ({k: profile[k] for k in ("score", "group", "group_ko", "mobility", "mobility_ko")}
                                if profile else None)
            bed_alert_latest.clear(); bed_alert_latest.update(alert)
            level = alert["level"]
            if fall_risk.LEVELS.index(level) > fall_risk.LEVELS.index(last_level) and level != "normal":
                db.insert_sensor_event(LIVE_BED_ID, "bed_alert", ts=alert["at"], level=level,
                                       title=f"침상 알림: {alert.get('situation_ko')}", note=alert.get("reason"),
                                       source_table="fall_risk_profile")
            last_level = level
        except Exception as e:
            print(f"[bed_alert] 계산 실패: {e}")


_threading.Thread(target=_bed_alert_worker, daemon=True).start()


# ── 빈 침대 기준값 자동 재캘리브레이션(server/tof_autocal.py, 2026-10-13) ──────────────
# 사용자 결정: ToF 침상 이탈 확정 10초 뒤, 레이더가 사람을 추적 중이고 그 사람이 0.3m 이상 움직였으면
# 5초 동안의 칸별 중앙값으로 기준값을 다시 잡는다(베개·이불 위치 변화 반영). 바뀐 칸이 너무 많으면 적용하지 않고 기록만.
import tof_autocal as _autocal
import urllib.request as _urlreq
baseline_auto = {"status": "idle", "at": None, "reason": None}
BASELINE_HISTORY_FILE = os.path.join(HERE, "tof_baseline_history.jsonl")


def _radar_targets():
    """mmw_server 실시간 위치 [(tid, x, y)]. 2초 넘게 갱신이 없거나 못 읽으면 None(레이더 꺼짐).
    높이까지 담은 최근 값은 _radar_last에 남긴다(침대 옆에 선 사람 구분용, 10-22 저녁)."""
    try:
        with _urlreq.urlopen("http://127.0.0.1:5002/mmw/live", timeout=0.5) as r:
            d = json.load(r)
        if _time.time() * 1000 - (d.get("ts") or 0) > 2000:
            return None
        ok = [t for t in d.get("targets") or []
              if isinstance(t, dict) and isinstance(t.get("x"), (int, float)) and isinstance(t.get("y"), (int, float))
              and t.get("role") not in ("shadow", "dup")]
        _radar_last.update(at=_time.time(), targets=[(float(t["x"]), float(t["y"]), t.get("z")) for t in ok])
        return [(t["tid"], float(t["x"]), float(t["y"])) for t in ok]
    except Exception:
        return None


def _autocal_log(status, reason, level, detail=None):
    now_iso = datetime.now().isoformat(timespec="milliseconds")
    baseline_auto.clear()
    baseline_auto.update(status=status, at=now_iso, reason=reason, **(detail or {}))
    observer.record('tof', 'baseline_auto', dict(baseline_auto), event=True)
    if status in ("applied", "refused"):
        try:
            db.insert_sensor_event(LIVE_BED_ID, "tof_baseline_auto", ts=now_iso, level=level,
                                   title="빈 침대 기준 자동 갱신" if status == "applied" else "빈 침대 기준 재확인 필요",
                                   note=reason, source_table="tof_baseline")
        except Exception as e:
            print(f"[autocal] 이벤트 기록 실패: {e}")
    print(f"[autocal] {status}: {reason}")


_radar_last = {"at": 0.0, "targets": None}
_stand = {"since": None, "stable": False}
_empty = {"since": None}   # 레이더에서 침대 근처에 아무도 없음이 이어진 시작 시각   # 침대 근처 사람이 모두 서 있음이 이어진 시작 시각·0.4초 넘었나(update_presence가 갱신)
STANDING_Z_M = 1.2    # 레이더 몸 중심이 이 이상이면 서 있는 사람(부스: 서기 1.4~1.8m, 걸터앉음은 그보다 낮음)


def _nobody_by_bed():
    """10-23 아침: 빈 침대에서 햇빛·조명 잡음으로 발치 센서 2~3칸이 잡히고 자세 모델이 '걸터앉음'을 내 재실이 깜빡였다.
    레이더가 살아 있는데(2초 안 갱신) 침대 1.5m 안에 추적이 하나도 없으면 True. 레이더 정보가 없으면 False."""
    r = _radar_last
    if r["targets"] is None or _time.time() - r["at"] > 2.0:
        return False
    bc = _autocal.bed_center()
    if bc is None:   # 2026-10-31: 침대 위치를 안 쟀으면 레이더로 침대 근처를 판단하지 않는다(틀린 좌표면 누운 환자가 '이탈'이 됨)
        return False
    bx, by = bc
    return not any(((x - bx) ** 2 + (y - by) ** 2) ** 0.5 <= _autocal.BED_CLEAR_M for x, y, _ in r["targets"])


def _standing_by_bed():
    """10-22 저녁: 침대 옆에 선 다리가 발치 ToF 가장자리 1~2칸에 걸리고 자세 모델이 '걸터앉음'으로 착각해 재실로 떴다.
    레이더에서 침대 1.5m 안 사람이 모두 서 있는 높이면 True(자세 보조 규칙을 쓰지 않음). 레이더 정보가 없으면 False."""
    r = _radar_last
    if r["targets"] is None or _time.time() - r["at"] > 2.0:
        return False
    bc = _autocal.bed_center()
    if bc is None:   # 2026-10-31: 침대 위치를 안 쟀으면 레이더로 침대 근처를 판단하지 않는다(틀린 좌표면 누운 환자가 '이탈'이 됨)
        return False
    bx, by = bc
    near = [z for x, y, z in r["targets"] if ((x - bx) ** 2 + (y - by) ** 2) ** 0.5 <= _autocal.BED_CLEAR_M]
    return bool(near) and all(isinstance(z, (int, float)) and z >= STANDING_Z_M for z in near)


def _autocal_worker():
    import time as _t
    from collections import deque as _dq
    radar_hist = _dq(maxlen=1500)     # 0.5초 간격 약 12분
    done_since = None                 # 이미 처리(적용·거절·포기)한 이탈 시각
    while True:
        _t.sleep(0.5)
        try:
            now = _t.time()
            targets = _radar_targets()
            if targets is not None:
                radar_hist.append((now, targets))
            since_iso = presence.get("since")
            if presence["in_bed"] or not since_iso or since_iso == done_since:
                continue
            since_ts = datetime.fromisoformat(since_iso).timestamp()
            age = now - since_ts
            if age < _autocal.WAIT_AFTER_EXIT_SEC:
                continue
            if age > _autocal.GIVE_UP_SEC:
                done_since = since_iso
                _autocal_log("skipped", "이탈 후 10분 동안 레이더 조건(추적 중·0.3m 이상 이동)이 맞지 않아 이번 이탈은 건너뜀", "normal")
                continue
            if targets is None or not targets:
                continue                                  # 레이더 꺼짐·방에 추적 없음 → 조건 대기
            mv = _autocal.radar_moved(list(radar_hist), since_ts)
            if not mv["moved"]:
                continue
            clr = _autocal.bed_clear(list(radar_hist), now)
            if not clr["clear"]:
                continue                                  # 침대 근처에 아직 사람이 있음 → 비워질 때까지 대기(10분 넘으면 포기)
            frames, seen, aborted = {"tof1": [], "tof2": []}, {}, False
            end = _t.time() + _autocal.COLLECT_SEC
            while _t.time() < end:
                if presence["in_bed"] or presence.get("since") != since_iso:
                    aborted = True
                    break
                tg = _radar_targets()
                if tg and not _autocal.bed_clear([(_t.time(), tg)] * 20, _t.time())["clear"]:
                    aborted = True                        # 수집 중 침대 근처에 사람이 들어옴
                    break
                for sid in ("tof1", "tof2"):
                    at = latest[sid].get("received_at")
                    if at and at != seen.get(sid) and latest[sid].get("distances_mm"):
                        seen[sid] = at
                        frames[sid].append(list(latest[sid]["distances_mm"]))
                _t.sleep(0.03)
            done_since = since_iso
            if aborted:
                _autocal_log("aborted", "수집 중 다시 침상 재실 또는 침대 근처에 사람 → 이번 갱신 취소", "normal")
                continue
            results, refuse = {}, []
            for sid in ("tof1", "tof2"):
                if len(frames[sid]) < 10:
                    refuse.append(f"{sid} 프레임 부족({len(frames[sid])})")
                    continue
                new, stats = _autocal.build_baseline(frames[sid], baseline[sid])
                cmp = _autocal.compare(baseline[sid], new)
                results[sid] = {"new": new, "stats": stats, "compare": cmp}
                if not cmp["apply"]:
                    refuse.append(f"{sid} 30cm 넘게 바뀐 칸 {cmp['big_changes']}개(허용 {_autocal.BIG_CHANGE_MAX})")
            summary = " · ".join(f"{sid} 바뀐 칸 {r['compare']['changed']}·평균 {r['compare']['mean_abs_mm']}mm·흔들린 칸 {r['stats']['unstable_zones']}"
                                 for sid, r in results.items())
            detail = {"radar_move_m": mv["max_move_m"], "bed_nearest_m": clr["nearest_m"], "frames": {k: len(v) for k, v in frames.items()}}
            if refuse:
                _autocal_log("refused", "자동 적용 안 함: " + ", ".join(refuse) + (f" ({summary})" if summary else "")
                             + " — 침대·물건 위치를 확인하고 빈 침대에서 수동으로 다시 잡으세요", "caution", detail)
                continue
            old = deepcopy(baseline)
            for sid, r in results.items():
                baseline[sid] = r["new"]
            save_baseline()
            try:
                with open(BASELINE_HISTORY_FILE, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "exit_since": since_iso,
                                         "radar_move_m": mv["max_move_m"], "old": old, "new": deepcopy(baseline),
                                         "results": {k: {"stats": v["stats"], "compare": v["compare"]} for k, v in results.items()}},
                                        ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"[autocal] 이력 기록 실패: {e}")
            _autocal_log("applied", f"이탈 후 레이더 {mv['max_move_m']}m 이동 확인, 5초 중앙값으로 갱신 ({summary})", "normal", detail)
        except Exception as e:
            print(f"[autocal] 오류: {e}")


_threading.Thread(target=_autocal_worker, daemon=True).start()

_edge_latest = {"offbed": None, "edge_motion": None}


def _edge_worker():
    import time as _t
    while True:
        _t.sleep(0.2)
        try:
            ob = _offbed_parts()
            _edge_latest.update(offbed=ob, edge_motion=_edge_motion(ob))
        except Exception as e:
            print(f"[edge] 오류: {e}")


_threading.Thread(target=_edge_worker, daemon=True).start()


@app.route("/tof/latest", methods=["GET"])
def get_latest():
    bed_id = request.args.get("bed_id")
    if bed_id and sim_state.is_simulated(bed_id):
        return jsonify(sim_state.synthetic_tof_latest(bed_id))
    resp = dict(latest)                      # 원본 latest 훼손 방지
    # 자세 판정은 새 센서 프레임이 들어왔을 때만 한다(_posture_fields 캐시). 대시보드 여러 개가 자주 조회하면
    # 조회마다 RF를 돌려 CPU가 포화되고 ESP 수신까지 밀렸다(2026-10-08).
    resp.update(_posture_fields())
    resp.update(_edge_latest)
    resp["bed_alert"] = dict(bed_alert_latest)
    return jsonify(resp)


@app.route("/tof/3d", methods=["GET"])
def tof_3d():
    """ToF 자세 3D 뷰어 (같은 출처에서 /tof/latest·/tof/presence 를 읽음)."""
    return send_from_directory(HERE, "tof_3d.html")


# ── 대시보드 서빙 (CSI 제거 → tof_server가 대시보드 호스팅) ──────────────
_SITE = os.path.join(HERE, "..", "site", "index.html")


@app.route("/emr", methods=["GET"])
def emr_view():
    return send_from_directory(os.path.join(HERE, '..', 'site'), 'emr-dashboard.html', max_age=0)


@app.route("/dashboard", methods=["GET"])
@app.route("/dashboard_design.html", methods=["GET"])
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


@app.route("/dev", methods=["GET"])
def dev_dashboard():
    """개발·센서 테스트 대시보드(2026-10-13). 간호사 화면(/dashboard)과 분리 — 원시값·서버 상태·진단 도구."""
    return send_from_directory(os.path.join(HERE, "..", "site"), "dev.html", max_age=0)


@app.route("/dashboard-assets/<path:filename>", methods=["GET"])
def dashboard_asset(filename):
    return send_from_directory(os.path.join(HERE, "..", "site", "dashboard-assets"), filename, max_age=0)


from dashboard_api import register_dashboard
register_dashboard(app)


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


@app.route("/mmw/patient", methods=["POST"])
def proxy_mmw_patient():
    """대시보드 평면도에서 점을 눌러 환자 지정(2026-10-16) → :5002/mmw/patient."""
    try:
        req = urllib.request.Request("http://127.0.0.1:5002/mmw/patient", data=request.get_data(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return Response(r.read(), mimetype="application/json")
    except Exception as e:
        return Response(f"mmw proxy error(patient): {e}", status=502)


@app.route("/tof/log", methods=["GET"])
def get_log():
    return jsonify(log[-20:])


@app.route("/beds", methods=["GET"])
def list_beds():
    return jsonify(db.list_beds())


# ── 12번 탭(시스템 확장) STEP 1-9: 환자 프로필 조회 ────────────────────
@app.route("/patients/<bed_id>", methods=["GET"])
def get_patient_route(bed_id):
    p = db.get_patient(bed_id)
    if p is None:
        return jsonify({"error": "환자를 찾을 수 없습니다"}), 404
    return jsonify(p)


@app.route("/patients/<bed_id>/medications", methods=["GET"])
def get_medications_route(bed_id):
    return jsonify(db.list_medications(bed_id))


@app.route("/patients/<bed_id>/risk_factors", methods=["GET"])
def get_risk_factors_route(bed_id):
    return jsonify(db.list_risk_factors(bed_id))


# ── 12번 탭 STEP 13-17: 논문 Vector DB 검색 ────────────────────────────
@app.route("/kb/search", methods=["POST"])
def kb_search_route():
    import paper_kb
    q = request.args.get("q", "")
    top_k = int(request.args.get("top_k", 5))
    return jsonify(paper_kb.search(q, top_k=top_k))


# ── 12번 탭 STEP 18-20: AI Agent 조사 ──────────────────────────────────
@app.route("/agent/investigate", methods=["POST"])
def agent_investigate_route():
    import ai_agent
    bed_id = request.args.get("bed_id", "bed_01")
    max_rounds = int(request.args.get("max_rounds", 2))
    top_k = int(request.args.get("top_k", 5))
    return jsonify(ai_agent.investigate(bed_id, max_rounds=max_rounds, top_k=top_k))


# ── 12번 탭 STEP 21-23: 통합분석 + 종합 리포트 ─────────────────────────
@app.route("/agent/analyze", methods=["POST"])
def agent_analyze_route():
    import integrated_analysis
    bed_id = request.args.get("bed_id", "bed_01")
    max_rounds = int(request.args.get("max_rounds", 2))
    top_k = int(request.args.get("top_k", 5))
    return jsonify(integrated_analysis.generate_integrated_report(bed_id, max_rounds=max_rounds, top_k=top_k))


# ── 12번 탭 STEP 26: 근거 추적(analysis_id → 논문 원문 역조회) ─────────
@app.route("/agent/evidence", methods=["GET"])
def agent_evidence_route():
    import paper_kb
    analysis_id = request.args.get("analysis_id", type=int)
    if analysis_id is None:
        return jsonify({"success": False, "error_type": "bad_request", "message": "analysis_id가 필요합니다"}), 400
    row = db.get_analysis_by_id(analysis_id)
    if row is None:
        return jsonify({"success": False, "error_type": "not_found", "message": "해당 analysis_id를 찾을 수 없습니다"}), 404
    try:
        evidence_ids = json.loads(row.get("evidence_ids_json") or "[]")
    except json.JSONDecodeError:
        evidence_ids = []
    return jsonify({"success": True, "analysis_id": analysis_id, "evidence": paper_kb.resolve_chunks(evidence_ids)})


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


@app.get('/raw-data')
def raw_data_dashboard():
    host = request.host.split(':')[0]
    return redirect(f'http://{host}:5012/')


@app.get('/system-architecture')
def system_architecture():
    return send_from_directory(os.path.join(HERE, '..', 'docs'), 'SYSTEM_ARCHITECTURE.html')



@app.route('/api/diagnostics/tof', methods=['GET'])
def diagnostic_tof():
    bed_id = request.args.get('bed_id', 'bed_01')
    response = jsonify(tof_snapshot(sys.modules[__name__], bed_id) if bed_id == 'bed_01' else
                       {'scope_error':'ToF 실측 스트림은 연결 환자 bed_01 전용입니다.', 'bed_id':bed_id})
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/diagnostics/reports', methods=['GET'])
def diagnostic_reports():
    try:
        from report_trace import snapshot
        bed_id = request.args.get('bed_id', 'bed_01')
        body = snapshot(bed_id) if bed_id == 'bed_01' else {'runs':[], 'scope_error':'실측 리포트 관측은 연결 환자 bed_01 전용입니다.'}
    except ImportError:
        body = {'runs': [], 'status': 'trace_module_unavailable'}
    response = jsonify(body)
    response.headers['Cache-Control'] = 'no-store'
    return response

if __name__ == "__main__":
    db.init_db()
    print("VL53L5CX ToF 서버 시작 → http://0.0.0.0:5001")
    # threaded=True: tof1·tof2가 동시에 POST해도 서로 안 막고 병렬 처리(싱글스레드면 8x8 동시 전송 시 실효 속도 반토막)
    app.run(host="0.0.0.0", port=5001, debug=True, threaded=True, use_reloader=False)  # 코드 변경 자동 재시작 시 torch 스레드가 abort("terminate called...")로 서버 전체가 죽어서 끔
