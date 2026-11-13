"""mmWave 실시간 테스트 뷰어 서버 (이 PC에서 실행, 테스트 전용).

SSH로 Pi의 DATA 포트 바이트를 읽어 이 PC에서 해석하고, http://localhost:8765 로 보여 준다.
- Pi에 파일을 만들지 않고, 서버나 DB로 아무것도 보내지 않는다.
- cfg를 보내지 않는다. 레이더가 이미 켜져 있어야 한다.
- Pi의 리더(send_mmw.py --raw-tap 5003)가 켜져 있으면 리더가 내보내는 원본 바이트 복사본을 읽고,
  리더가 꺼져 있으면 DATA 포트를 직접 연다(이때는 리더를 함께 켤 수 없다).

실행: python viewer_server.py
종료: Ctrl+C
"""
import argparse
import base64
import importlib.util
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from person_ids import PersonTracker, in_poly  # noqa: E402
from gemini_pipeline import GeminiPipeline  # noqa: E402

REMOTE = r'''
import socket, sys
out = sys.stdout.buffer
try:   # 리더가 켜져 있으면 리더가 내보내는 원본 바이트 복사본(127.0.0.1:5003)을 읽는다
    s = socket.create_connection(("127.0.0.1", 5003), timeout=2)
    s.settimeout(None)
    tap = True
    sys.stderr.write("source: reader tap 5003\n")
    read = lambda: s.recv(8192)
except OSError:   # 리더 서비스가 꺼져 있을 때만 DATA 포트를 직접 연다
    import subprocess, time
    st = subprocess.run(["systemctl", "is-active", "majubom-mmw-reader"], capture_output=True, text=True).stdout.strip()
    if st != "inactive":   # 재시작 중인 리더보다 먼저 포트를 잡으면 환자 감시가 멈춘다. 기다렸다가 다시 접속한다
        sys.stderr.write(f"reader {st}, tap 5003 not ready; retry\n")
        time.sleep(2)
        sys.exit(1)
    import serial
    d = serial.Serial("/dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_011D1AD6-if01-port0",
                      921600, timeout=0.2, exclusive=True)
    tap = False
    sys.stderr.write("source: DATA port\n")
    read = lambda: d.read(8192)
while True:
    b = read()
    if not b:
        if tap:
            break   # 리더가 꺼지면 끝낸다. 뷰어가 3초 뒤 다시 접속한다
        continue
    out.write(b)
    out.flush()
'''

KNOWN_TLVS = {1010, 1011, 1012, 1020, 1021}
TRAIL_SEC = 5.0
HIST_SEC = 10.0
HIST_STEP = 0.25

SESSIONS = {
    "S0": {"name": "빈 방, 의자 평소 위치", "default": "object"},
    "S1": {"name": "빈 방, 의자 위치 변경", "default": "object"},
    "S2": {"name": "한 명씩 의자에 앉기", "default": "unknown"},
    "S3": {"name": "한 명씩 서 있기, 걷기", "default": "unknown"},
    "S4": {"name": "침대에 눕기", "default": "unknown"},
    "S5": {"name": "지나가며 의자 옮기기", "default": "unknown"},
    "S6": {"name": "4명 함께", "default": "unknown"},
    "S7": {"name": "다른 날 최종 시험", "default": "unknown"},
    "TEST": {"name": "기능 시험(비교에 쓰지 않음)", "default": "unknown"},
}
TRUTHS = {"person", "object", "unknown"}
TRACK_END_SEC = 2.0            # 이 시간 넘게 안 보인 추적 번호는 끝난 것으로 보고 라벨을 지운다
MAX_RECORD_SEC = 3600          # 끄는 것을 잊어도 1시간에 멈춘다
MIN_FREE_BYTES = 2 * 1024 ** 3 # 디스크 여유가 2GB 아래로 내려가면 멈춘다

NEW_NOTICE_SEC = 5.0           # 새 번호를 표에 "새" 표시로 남기는 시간
START_QUIET_SEC = 2.0          # 시작하거나 수신이 끊겼다 이어진 직후의 변화는 알리지 않는다
LINK_DIST_M = 1.0              # 새 번호와 사람 표시 번호가 이 거리 안이면 같은 사람일 수 있다
LINK_SEC = 3.0                 # 사라진 번호를 이 시간 안의 새 번호와 연결해 본다
JUMP_DIST_M = 1.0              # 한 번호가 이보다 멀리 튀면 번호 재사용을 의심한다
PERSON_NO_POINTS_SEC = 5.0     # 사람 표시 번호에 반사점이 이 시간 넘게 없으면 빈자리일 수 있다
FULL_POINT_RADIUS_M = 0.8      # 최대 인원이 다 잡히면 사람 주변의 반사점만 남긴다
SETTINGS_FILE = os.path.join(HERE, "people_settings.json")
FALL_STAGE = {"fallen": "낙상 감지", "caution": "주의(바닥까지 내려갔다 일어남)", "recovered": "바닥에서 일어남", "fast_drop": "급격한 하강"}
PERSON_NOTICE_REPEAT_SEC = 60.0  # 같은 번호의 "반사점 없음" 알림 간격
EVENT_KEEP = 200
# 전처리 탭: 2026-11-01 녹화 비교에서 고른 후보. 녹화 파일에는 원본만 저장한다.
NEAR_MIN_M = 0.5               # 레이더에서 이보다 가까운 반사점 제거
POINT_Z_RANGE = (-0.3, 2.3)    # 범위 안 반사점의 높이 범위
MOVE_SETTLE_SEC = 1.5          # 추적이 생긴 직후 칩이 위치를 잡는 동안은 이동으로 치지 않는다
MOVE_MIN_M = 0.3               # 생긴 자리에서 이만큼 움직인 적이 있는 추적만 남긴다
EMA_ALPHA = 0.3                # 위치 평활화 계수
HEAD_MIN_POINTS = 8            # 머리 높이를 계산할 최소 연결 반사점 수
HEAD_TOP_FRACTION = 0.9        # 연결 반사점 높이의 상위 10% 지점을 머리 쪽 높이로 본다
HEAD_WINDOW_SEC = 1.0          # 흔들림을 줄이려고 이 시간의 중앙값을 쓴다
HEAD_CEILING_MARGIN_M = 0.4    # 레이더 바로 앞 반사(z 약 2.6m)가 머리로 잡히지 않게 제외한다

state = {"error": None, "connected": False, "frames": 0, "corrupt": 0, "bytes": 0, "frame": None, "events": []}
lock = threading.Lock()


def load_reader(path):
    spec = importlib.util.spec_from_file_location("send_mmw", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def split_tlvs(hdr, frame):
    """TLV를 나눈다. 길이나 타입이 비정상이면 손상된 프레임으로 보고 None을 반환한다."""
    tlvs, off = {}, 40
    for _ in range(hdr["numTLVs"]):
        if off + 8 > len(frame):
            return None
        kind, size = struct.unpack_from("<2I", frame, off)
        if kind not in KNOWN_TLVS or off + 8 + size > len(frame):
            return None
        tlvs[kind] = frame[off + 8:off + 8 + size]
        off += 8 + size
    return tlvs


def parse_points(payload, to_world):
    """압축 점군(1020)을 바닥 기준 좌표로 바꾼다."""
    if len(payload) < 20:
        return []
    e_unit, a_unit, d_unit, r_unit, s_unit = struct.unpack_from("<5f", payload, 0)
    points = []
    for i in range((len(payload) - 20) // 8):
        el_i, az_i, dop, rng, snr = struct.unpack_from("<bbhHH", payload, 20 + i * 8)
        r, el, az = rng * r_unit, el_i * e_unit, az_i * a_unit
        w = to_world({
            "posX": r * math.cos(el) * math.sin(az), "posY": r * math.cos(el) * math.cos(az),
            "posZ": r * math.sin(el), "velX": 0.0, "velY": 0.0, "velZ": 0.0})
        points.append([round(w["posX"], 2), round(w["posY"], 2), round(w["posZ"], 2),
                       round(dop * d_unit, 2), round(snr * s_unit, 1)])
    return points


def classify(z, moved, seconds, room_height):
    """화면 표시용 추정. 사람인지 확정하지 않는다."""
    if z > room_height:
        return "above", "천장 위(불가능한 높이)"
    if z < 0.25:
        return "floor", "바닥 높이"
    if seconds >= 3.0 and moved < 0.15:
        return "still", "정지(사람 또는 물체)"
    return "person", "사람 후보"


def load_fall_module(args):
    """작업 브랜치의 mmWave/fall_detect.py(기존 브랜치 3644083에서 수정 없이 복사)를 읽는다."""
    spec = importlib.util.spec_from_file_location("fall_detect", os.path.join(os.path.dirname(args.reader), "fall_detect.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if args.tilt >= 80:
        module.HEIGHT_FIT = (1.0, 0.0)   # 거리별 높이 보정은 벽 설치에서 잰 값이라 천장에서는 끈다
    return module


class FallWatch:
    """사람 ID마다 기존 낙상 판정기를 하나씩 둔다. 방 안에 있는 사람의 번호만 넣는다.

    나간 사람의 번호는 넣지 않으므로 문으로 나갈 때 남은 번호로 거짓 낙상이 나지 않는다.
    Pi의 ToF를 쓰지 않으므로 침상 위 낙상은 확정 침상 안에서 났는지로 경보에서 제외한다.
    """
    CONFIRM_SEC = 1.0   # 기존 리더의 FALL_CONFIRM_SEC와 같다

    def __init__(self, module, emit):
        self.fd = module
        self.emit = emit
        self.detectors = {}

    def update(self, now, inputs, persons, bed):
        self.fd.BED_CENTER = (sum(c[0] for c in bed) / 4, sum(c[1] for c in bed) / 4)
        by_tid = {t["tid"]: t for t in inputs}
        status = {}
        for person in persons:
            if person["state"] != "in" or person["tid"] not in by_tid:
                continue
            pid, tid = person["pid"], person["tid"]
            watch = self.detectors.get(pid)
            if watch is None:
                watch = self.detectors[pid] = {"det": self.fd.FallDetector(manual_only=True, confirm_sec=self.CONFIRM_SEC), "seq": 0}
            det = watch["det"]
            events = det.update(now, [by_tid[tid]])
            if det.patient_tid != tid:
                watch["seq"] += 1
                det.set_manual(now, tid, watch["seq"])
            for ev in events:
                on_bed = in_poly(ev.get("x", 0.0), ev.get("y", 0.0), bed)
                note = " (침상 위, 경보 제외)" if on_bed else ""
                self.emit(now, "person_fall", pid, [ev.get("x", 0.0), ev.get("y", 0.0)],
                          f"{pid} {FALL_STAGE.get(ev['stage'], ev['stage'])}{note}: z={ev.get('z')} 기준 높이={ev.get('ref')}",
                          ev["stage"] == "fallen" and not on_bed)
            for fall in det.state():
                status[pid] = {"stage": fall["stage"], "z": fall.get("z"), "ref": fall.get("ref"),
                               "on_bed": in_poly(fall["x"], fall["y"], bed)}
        return status


class LegacyView:
    """기존 브랜치(claude/sensor-remount-posture-v2, 3644083)의 fall_detect.py를 그대로 실행한다.

    기존 리더와 같게 manual_only, auto_bed, 낙상 확정 1초를 쓰고 천장 설치에서는 높이 보정을 끈다.
    Pi 서버를 켜지 않아 ToF 재실은 없다(None). 빈 방 기준(ClutterMask)은 벽 설치 때 값뿐이라 쓰지 않는다.
    """
    CONFIRM_SEC = 1.0
    HIDDEN_ROLES = {"dup", "shadow", "still"}

    def __init__(self, module, emit):
        self.fd = module
        self.detector = self.fd.FallDetector(manual_only=True, confirm_sec=self.CONFIRM_SEC, auto_bed=True)
        self.emit = emit
        self.manual_tid, self.manual_seq = None, None
        self.error = None

    def choose_patient(self, tid):
        self.manual_tid, self.manual_seq = tid, (self.manual_seq or 0) + 1

    def update(self, now, targets, tracks, bed):
        """표시할 추적과 낙상 상태를 돌려준다. 원본 tracks는 바꾸지 않는다."""
        try:
            self.fd.BED_CENTER = (sum(c[0] for c in bed) / 4, sum(c[1] for c in bed) / 4)
            self.detector.set_bed(now, None)
            self.detector.set_manual(now, self.manual_tid, self.manual_seq)
            for ev in self.detector.update(now, targets):
                self.emit(now, "legacy_fall", ev["tid"], [ev.get("x", 0.0), ev.get("y", 0.0)],
                          f"기존 판정 {ev['stage']}: {ev['tid']}번 z={ev.get('z')} 기준 높이={ev.get('ref')}", ev["stage"] == "fallen")
            roles = self.detector.roles()
            shown = [{**t, "role": roles.get(t["tid"])} for t in tracks if roles.get(t["tid"]) not in self.HIDDEN_ROLES]
            counts = {}
            for role in roles.values():
                counts[role] = counts.get(role, 0) + 1
            self.error = None
            return {"tracks": shown, "roles": counts, "patient_tid": self.detector.patient_tid, "falls": self.detector.state(), "error": None}
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return {"tracks": tracks, "roles": {}, "patient_tid": None, "falls": [], "error": self.error}


class Recorder:
    """세션 하나를 sessions/<세션>_<시각>/ 에 저장한다.

    raw.bin       레이더 프레임 원본 바이트
    frames.jsonl  프레임별 수신 시각, raw.bin 위치, 추적 값, 그 시점의 정답(truth)
    labels.jsonl  라벨을 바꾼 기록과 추적이 끝난 기록
    session.json  세션 정보와 설치 값
    """

    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.active = None

    def start(self, session, participant, activity, note):
        if session not in SESSIONS:
            raise ValueError("알 수 없는 세션입니다.")
        with self.lock:
            if self.active:
                raise ValueError("이미 녹화 중입니다.")
            now = time.time()
            name = f"{session}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(now))}"
            path = os.path.join(HERE, "sessions", name)
            os.makedirs(path)
            info = {
                "session": session, "description": SESSIONS[session]["name"],
                "default_truth": SESSIONS[session]["default"],
                "participant": participant, "activity": activity, "note": note,
                "started_at": now, "stopped_at": None, "stop_reason": None,
                "height_m": self.args.height, "tilt_deg": self.args.tilt, "upside_down": not self.args.upright,
                "reader_commit": reader_commit(self.args.reader),
                "frames": 0, "corrupt": 0, "label_changes": 0,
            }
            self.active = {
                "name": name, "path": path, "info": info, "offset": 0, "truth": {}, "seen": {},
                "raw": open(os.path.join(path, "raw.bin"), "wb"),
                "frames": open(os.path.join(path, "frames.jsonl"), "w", encoding="utf-8"),
                "labels": open(os.path.join(path, "labels.jsonl"), "w", encoding="utf-8"),
            }
            self._write_info()
            return name

    def stop(self, reason="user"):
        with self.lock:
            if not self.active:
                return None
            return self._stop(reason)

    def _stop(self, reason):
        a = self.active
        a["info"]["stopped_at"] = time.time()
        a["info"]["stop_reason"] = reason
        for key in ("raw", "frames", "labels"):
            a[key].close()
        self._write_info()
        self.active = None
        return a["name"]

    def _write_info(self):
        a = self.active
        with open(os.path.join(a["path"], "session.json"), "w", encoding="utf-8") as fh:
            json.dump(a["info"], fh, ensure_ascii=False, indent=1)

    def _log_label(self, a, record):
        a["labels"].write(json.dumps(record, ensure_ascii=False) + "\n")
        a["labels"].flush()

    def log_event(self, record):
        with self.lock:
            if self.active:
                self._log_label(self.active, record)

    def set_label(self, tid, truth):
        if truth not in TRUTHS:
            raise ValueError("라벨은 person, object, unknown 중 하나여야 합니다.")
        now = time.time()
        with self.lock:
            a = self.active
            if not a:
                raise ValueError("녹화 중일 때만 라벨을 정할 수 있습니다.")
            if tid not in a["seen"] or now - a["seen"][tid] > TRACK_END_SEC:
                raise ValueError(f"현재 {tid}번 추적이 없습니다.")
            a["truth"][tid] = truth
            a["info"]["label_changes"] += 1
            self._log_label(a, {"t": round(now, 3), "tid": tid, "event": "label", "truth": truth})

    def write_frame(self, now, hdr, frame, tracks, presence):
        """프레임을 저장하고 추적마다 정답을 붙인다. 손상된 프레임은 tracks=None으로 표시한다."""
        with self.lock:
            a = self.active
            if not a:
                for t in tracks or []:
                    t["truth"] = None
                return
            info = a["info"]
            a["raw"].write(frame)
            line = {"t": round(now, 3), "frame": hdr["frameNum"], "offset": a["offset"], "length": len(frame)}
            a["offset"] += len(frame)
            info["frames"] += 1
            if tracks is None:
                info["corrupt"] += 1
                line["corrupt"] = True
            else:
                line["presence"] = presence
                line["tracks"] = []
                for t in tracks:
                    a["seen"][t["tid"]] = now
                    t["truth"] = a["truth"].get(t["tid"], info["default_truth"])
                    line["tracks"].append({k: t[k] for k in (
                        "tid", "raw", "world", "speed", "vz", "npts", "fw_height", "moved_5s", "head_z", "truth", "pid")})
                for tid in [k for k, v in a["seen"].items() if now - v > TRACK_END_SEC]:
                    del a["seen"][tid]
                    ended = a["truth"].pop(tid, None)
                    self._log_label(a, {"t": round(now, 3), "tid": tid, "event": "track_end", "truth": ended})
            a["frames"].write(json.dumps(line, ensure_ascii=False) + "\n")
            if info["frames"] % 50 == 0:
                a["raw"].flush()
                a["frames"].flush()
                if now - info["started_at"] > MAX_RECORD_SEC:
                    self._stop("max_duration")
                elif shutil.disk_usage(a["path"]).free < MIN_FREE_BYTES:
                    self._stop("low_disk")

    def status(self):
        with self.lock:
            a = self.active
            if not a:
                return None
            info = a["info"]
            return {"name": a["name"], "session": info["session"], "elapsed": time.time() - info["started_at"],
                    "frames": info["frames"], "corrupt": info["corrupt"], "label_changes": info["label_changes"],
                    "bytes": a["offset"], "default_truth": info["default_truth"]}


def reader_commit(path):
    try:
        return subprocess.check_output(["git", "-C", os.path.dirname(path), "rev-parse", "--short", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def load_max_people():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            return int(json.load(fh)["max_people"])
    except (FileNotFoundError, ValueError, KeyError):
        return 3


class Reader:
    def __init__(self, args, module, recorder):
        self.args = args
        self.module = module
        self.recorder = recorder
        self.trails = defaultdict(deque)
        self.hist = deque()
        self.fps_times = deque()
        self.alive = {}
        self.recent_ended = deque()
        self.last_frame = None
        self.quiet_until = 0.0
        self.prev_point_z = None
        self.prev_points = []
        self.head_samples = defaultdict(deque)
        self.proc_hist = deque()
        self.proc_tracks = {}
        self.shape_cache = (0.0, None)
        self.people = PersonTracker(self._emit, max_people=load_max_people())
        fall_module = load_fall_module(args)
        self.legacy = LegacyView(fall_module, self._emit)
        self.falls = FallWatch(fall_module, self._emit)
        self.gemini = GeminiPipeline()

    def to_world(self, track):
        return self.module.track_to_world(track, self.args.height, self.args.tilt)

    def run_gemini(self, now, tracks, points, polys, linked):
        """제미나이 경로에 오류가 나도 다른 탭은 계속 동작하게 한다."""
        try:
            return self.gemini.process(now, tracks, points, polys, linked)
        except Exception as exc:
            return {"tracks": [], "points": [], "z_hist": None, "note": f"제미나이 처리 오류: {type(exc).__name__}: {exc}"}

    def shape_polygons(self, now):
        """확정한 범위, 문 구역, 침상을 1초마다 다시 읽는다."""
        checked, shapes = self.shape_cache
        if shapes is None or now - checked > 1.0:
            with shape_lock:
                shapes = {name: [tuple(c) for c in load_shape(name)["corners"]] for name in SHAPES}
            self.shape_cache = (now, shapes)
        return shapes

    def preprocess(self, now, tracks, points, people):
        """근거리 제거 → 범위 필터 → 문·남은 번호 제외 → 이동 이력 → EMA 0.3. 원본 tracks, points는 바꾸지 않는다.

        최대 인원이 다 잡히면 사람 번호와 그 주변 반사점만 남긴다."""
        hidden, full = people["hidden"], people["full"]
        polygon = self.shape_polygons(now)["room"]
        height = self.args.height
        removed = {"point_near": 0, "point_room": 0, "track_room": 0, "track_not_moved": 0, "track_hidden": 0, "track_full": 0, "point_full": 0}
        kept_points = []
        for p in points:
            if math.hypot(p[0], p[1], height - p[2]) < NEAR_MIN_M:
                removed["point_near"] += 1
            elif self.module.in_room(p[0], p[1], polygon) and POINT_Z_RANGE[0] <= p[2] <= POINT_Z_RANGE[1]:
                kept_points.append(p)
            else:
                removed["point_room"] += 1

        kept_tracks = []
        for t in tracks:
            x, y, z = t["world"]
            state = self.proc_tracks.get(t["tid"])
            if state is None:
                state = self.proc_tracks[t["tid"]] = {"born": now, "bx": x, "by": y, "moved": False, "pos": [x, y, z], "trail": deque(maxlen=40), "last": now}
            state["last"] = now
            state["pos"] = [EMA_ALPHA * v + (1 - EMA_ALPHA) * c for v, c in zip((x, y, z), state["pos"])]
            state["trail"].append([round(state["pos"][0], 2), round(state["pos"][1], 2)])
            if now - state["born"] < MOVE_SETTLE_SEC:
                state["bx"], state["by"] = x, y
            elif math.hypot(x - state["bx"], y - state["by"]) > MOVE_MIN_M:
                state["moved"] = True
            if t.get("pid"):
                kept_tracks.append({**t, "world": [round(v, 3) for v in state["pos"]], "trail": list(state["trail"])})
            elif full:
                removed["track_full"] += 1
            elif t["tid"] in hidden:
                removed["track_hidden"] += 1
            elif not self.module.in_room(x, y, polygon):
                removed["track_room"] += 1
            elif not state["moved"]:
                removed["track_not_moved"] += 1
            else:
                kept_tracks.append({**t, "world": [round(v, 3) for v in state["pos"]], "trail": list(state["trail"])})
        for tid in [k for k, v in self.proc_tracks.items() if now - v["last"] > TRACK_END_SEC]:
            del self.proc_tracks[tid]
        if full:
            centers = [t["world"][:2] for t in tracks if t.get("pid")]
            before = len(kept_points)
            kept_points = [p for p in kept_points if any(math.hypot(p[0] - c[0], p[1] - c[1]) <= FULL_POINT_RADIUS_M for c in centers)]
            removed["point_full"] = before - len(kept_points)

        self.proc_hist.append((now, [p[2] for p in kept_points]))
        while self.proc_hist and now - self.proc_hist[0][0] > HIST_SEC:
            self.proc_hist.popleft()
        counts = defaultdict(int)
        for _, zs in self.proc_hist:
            for z in zs:
                counts[math.floor(z / HIST_STEP)] += 1
        return {"tracks": sorted(kept_tracks, key=lambda t: t["tid"]), "points": kept_points, "removed": removed,
                "z_hist": [[round(k * HIST_STEP, 2), counts[k]] for k in sorted(counts)]}

    def head_height(self, now, tid, index):
        """연결 반사점으로 머리 높이를 추정한다. 1011은 직전 프레임의 점을 가리킨다."""
        samples = self.head_samples[tid]
        if self.prev_point_z is not None and len(index) == len(self.prev_point_z):
            limit = self.args.height - HEAD_CEILING_MARGIN_M
            zs = sorted(z for owner, z in zip(index, self.prev_point_z) if owner == tid and z <= limit)
            if len(zs) >= HEAD_MIN_POINTS:
                samples.append((now, zs[int(HEAD_TOP_FRACTION * (len(zs) - 1))]))
        while samples and now - samples[0][0] > HEAD_WINDOW_SEC:
            samples.popleft()
        if not samples:
            return None
        values = sorted(s[1] for s in samples)
        return round(values[len(values) // 2], 2)

    def _emit(self, now, event, tid, pos, text, check):
        record = {"t": round(now, 3), "event": event, "tid": tid, "pos": [round(pos[0], 2), round(pos[1], 2)],
                  "text": text, "check": check}
        with lock:
            state["events"] = (state["events"] + [record])[-EVENT_KEEP:]
        self.recorder.log_event(record)

    def update_track_changes(self, now, tracks):
        """번호가 생기고, 사라지고, 튀는 순간을 알린다. 번호를 잇거나 라벨을 바꾸지는 않는다."""
        if self.last_frame is None or now - self.last_frame > TRACK_END_SEC:
            self.alive, self.quiet_until = {}, now + START_QUIET_SEC
            self.recent_ended.clear()
        self.last_frame = now
        quiet = now < self.quiet_until
        current = {t["tid"]: t for t in tracks}

        for tid, a in list(self.alive.items()):
            if tid not in current and now - a["last"] > TRACK_END_SEC:
                del self.alive[tid]
                self.recent_ended.append((now, tid, a["pos"], a["truth"]))
                if not quiet:
                    person = a["truth"] == "person"
                    self._emit(now, "track_gone", tid, a["pos"],
                               f"사람으로 표시한 {tid}번이 사라졌습니다." if person else f"{tid}번이 사라졌습니다.", person)
        while self.recent_ended and now - self.recent_ended[0][0] > LINK_SEC:
            self.recent_ended.popleft()

        for tid, t in current.items():
            pos = t["world"][:2]
            a = self.alive.get(tid)
            if a is None:
                a = self.alive[tid] = {"first": now, "last": now, "pos": pos, "truth": t.get("truth"),
                                       "no_points_since": None, "warned": 0.0}
                if not quiet:
                    near = self._near_person(tid, pos)
                    if near is None:
                        self._emit(now, "track_new", tid, pos, f"새 번호 {tid}번이 생겼습니다.", False)
                    else:
                        self._emit(now, "track_new", tid, pos,
                                   f"새 번호 {tid}번이 사람으로 표시한 {near}번 근처에서 생겼습니다. 같은 사람인지 확인하세요.", True)
            else:
                moved = math.dist(pos, a["pos"])
                if moved > JUMP_DIST_M and not quiet:
                    person = t.get("truth") == "person"
                    self._emit(now, "track_jump", tid, pos,
                               f"{tid}번이 {moved:.1f}m 튀었습니다. 다른 대상으로 바뀌었을 수 있습니다.", person)
            a.update(last=now, pos=pos, truth=t.get("truth"))
            if t.get("pid") and t["npts"] == 0:
                a["no_points_since"] = a["no_points_since"] or now
                if now - a["no_points_since"] >= PERSON_NO_POINTS_SEC and now - a["warned"] >= PERSON_NOTICE_REPEAT_SEC:
                    a["warned"] = now
                    self._emit(now, "person_no_points", tid, pos,
                               f"사람으로 표시한 {tid}번에 반사점이 없습니다. 사람이 떠난 자리일 수 있습니다.", True)
            else:
                a["no_points_since"] = None
            t["is_new"] = now - a["first"] < NEW_NOTICE_SEC and not quiet

    def _near_person(self, tid, pos):
        best, best_d = None, LINK_DIST_M
        people = [(other, a["pos"]) for other, a in self.alive.items() if other != tid and a["truth"] == "person"]
        people += [(other, p) for _, other, p, truth in self.recent_ended if other != tid and truth == "person"]
        for other, other_pos in people:
            d = math.dist(pos, other_pos)
            if d <= best_d:
                best, best_d = other, d
        return best

    def handle_frame(self, hdr, frame):
        now = time.time()
        tlvs = split_tlvs(hdr, frame)
        with lock:
            state["frames"] += 1
            if tlvs is None:
                state["corrupt"] += 1
        if tlvs is None:
            self.prev_point_z = None
            self.recorder.write_frame(now, hdr, frame, None, None)
            return
        self.fps_times.append(now)
        while self.fps_times and now - self.fps_times[0] > 3.0:
            self.fps_times.popleft()

        points = parse_points(tlvs.get(1020, b""), self.to_world)
        self.hist.append((now, [p[2] for p in points]))
        while self.hist and now - self.hist[0][0] > HIST_SEC:
            self.hist.popleft()

        index = tlvs.get(1011, b"")
        heights = {}
        raw_h = tlvs.get(1012, b"")
        for i in range(len(raw_h) // 12):
            tid, a, b = struct.unpack_from("<I2f", raw_h, i * 12)
            heights[tid] = [round(a, 3), round(b, 3)]

        tracks = []
        legacy_input = []
        linked_z = defaultdict(list)
        linked_points = defaultdict(list)
        if self.prev_point_z is not None and len(index) == len(self.prev_point_z):
            for owner, z, point in zip(index, self.prev_point_z, self.prev_points):
                if owner < 250:
                    linked_z[owner].append(z)
                    linked_points[owner].append(point)
        for raw in self.module.parse_target_list(tlvs.get(1010, b"")):
            w = self.to_world(raw)
            zs = sorted(linked_z.get(raw["tid"], []))
            legacy_input.append({"tid": raw["tid"], "posX": w["posX"], "posY": w["posY"], "posZ": w["posZ"], "velZ": w["velZ"],
                                 "npts": len(zs), "top_z": zs[min(len(zs) - 1, int(len(zs) * 0.9))] if zs else None})
            trail = self.trails[raw["tid"]]
            trail.append((now, w["posX"], w["posY"], w["posZ"]))
            while trail and now - trail[0][0] > TRAIL_SEC:
                trail.popleft()
            xs = [s[1] for s in trail]
            ys = [s[2] for s in trail]
            moved = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
            seconds = now - trail[0][0]
            kind, label = classify(w["posZ"], moved, seconds, self.args.height)
            tracks.append({
                "tid": raw["tid"],
                "raw": [round(raw[k], 3) for k in ("posX", "posY", "posZ")],
                "world": [round(w[k], 3) for k in ("posX", "posY", "posZ")],
                "speed": round(math.hypot(w["velX"], w["velY"]), 3),
                "vz": round(w["velZ"], 3),
                "npts": index.count(raw["tid"]),
                "fw_height": heights.get(raw["tid"]),
                "moved_5s": round(moved, 2),
                "head_z": self.head_height(now, raw["tid"], index),
                "kind": kind, "label": label,
                "trail": [[round(s[1], 2), round(s[2], 2)] for s in list(trail)[-40:]],
            })
        live = {t["tid"] for t in tracks}
        for tid in list(self.trails):
            if tid not in live and (not self.trails[tid] or now - self.trails[tid][-1][0] > TRAIL_SEC):
                del self.trails[tid]
        for tid in [k for k in self.head_samples if k not in live]:
            del self.head_samples[tid]
        self.prev_point_z = [p[2] for p in points]
        self.prev_points = points

        counts = defaultdict(int)
        for _, zs in self.hist:
            for z in zs:
                counts[math.floor(z / HIST_STEP)] += 1
        presence = struct.unpack("<I", tlvs[1021])[0] if len(tlvs.get(1021, b"")) == 4 else None
        fps = (len(self.fps_times) - 1) / max(now - self.fps_times[0], 1e-3) if len(self.fps_times) > 1 else 0.0
        polys = self.shape_polygons(now)
        people = self.people.update(now, tracks, polys["room"], polys["door"], polys["bed"])
        for t in tracks:
            t["pid"] = people["tid_to_pid"].get(t["tid"])
            t["hidden_reason"] = people["hidden"].get(t["tid"])
        self.recorder.write_frame(now, hdr, frame, tracks, presence)
        self.update_track_changes(now, tracks)
        processed = self.preprocess(now, tracks, points, people)
        gemini = self.run_gemini(now, tracks, points, polys, linked_points)
        fall_status = self.falls.update(now, legacy_input, people["persons"], polys["bed"])
        for person in people["persons"]:
            person["fall"] = fall_status.get(person["pid"])
        legacy = self.legacy.update(now, legacy_input, tracks, polys["bed"])

        with lock:
            state["frame"] = {
                "at": now, "number": hdr["frameNum"], "fps": round(fps, 1), "detected": hdr["numDetectedObj"],
                "presence": presence, "tracks": sorted(tracks, key=lambda t: t["tid"]), "points": points,
                "z_hist": [[round(k * HIST_STEP, 2), counts[k]] for k in sorted(counts)],
                "processed": processed, "persons": people["persons"], "legacy": legacy, "people_full": people["full"], "gemini": gemini,
            }

    def run_forever(self):
        code = base64.b64encode(REMOTE.encode()).decode()
        remote = (f"cd {self.args.repo} && .venv/bin/python -B -u -c "
                  f"\"import base64;exec(base64.b64decode('{code}'))\"")
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5", self.args.host, remote]
        while True:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            threading.Thread(target=self._stderr, args=(proc,), daemon=True).start()
            with lock:
                state["connected"] = True
            buf = b""
            while True:
                chunk = proc.stdout.read1(8192)
                if not chunk:
                    break
                with lock:
                    state["bytes"] += len(chunk)
                    state["error"] = None
                buf += chunk
                frames, buf = self.module.iter_frames(buf)
                for hdr, frame in frames:
                    self.handle_frame(hdr, frame)
            proc.wait()
            with lock:
                state["connected"] = False
            time.sleep(3)

    def _stderr(self, proc):
        lines = proc.stderr.read().decode(errors="replace").strip().splitlines()
        if lines:
            with lock:
                state["error"] = lines[-1][:300]


# 확정 전 초안. room: 실측 범위를 감싸는 사각형, bed: 머리·다리 쪽 실측에 폭 1.4m를 더한 사각형(1·2번 머리 쪽, 3·4번 다리 쪽)
SHAPES = {
    "room": {"file": os.path.join(HERE, "room_corners.json"), "default": [[-1.7, -1.3], [1.3, -3.0], [2.2, 1.3], [-1.3, 2.1]]},
    "bed": {"file": os.path.join(HERE, "bed_corners.json"), "default": [[-1.53, -1.07], [-0.13, -1.19], [0.01, 0.42], [-1.39, 0.54]]},
    "door": {"file": os.path.join(HERE, "door_corners.json"), "default": [[-1.8, 1.3], [-0.1, 1.3], [-0.1, 2.2], [-1.8, 2.2]]},
}
shape_lock = threading.Lock()


def load_shape(name):
    try:
        with open(SHAPES[name]["file"], encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"corners": SHAPES[name]["default"], "confirmed_at": None}


def segments_cross(a, b, c, d):
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def save_shape(name, corners):
    """꼭짓점 4개를 검사해 저장한다. 선이 서로 교차하는 사각형은 받지 않는다."""
    if not isinstance(corners, list) or len(corners) != 4:
        raise ValueError("꼭짓점은 4개여야 합니다.")
    pts = []
    for c in corners:
        if not isinstance(c, list) or len(c) != 2:
            raise ValueError("꼭짓점은 x, y 두 값이어야 합니다.")
        x, y = float(c[0]), float(c[1])
        if not (math.isfinite(x) and math.isfinite(y) and abs(x) <= 8 and abs(y) <= 8):
            raise ValueError("꼭짓점 좌표가 측정 가능한 범위를 벗어났습니다.")
        pts.append([round(x, 2), round(y, 2)])
    if segments_cross(pts[0], pts[1], pts[2], pts[3]) or segments_cross(pts[1], pts[2], pts[3], pts[0]):
        raise ValueError("사각형의 변이 서로 교차합니다. 꼭짓점을 둘레 순서대로 정해 주세요.")
    shape = {"corners": pts, "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with shape_lock:
        tmp = SHAPES[name]["file"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(shape, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, SHAPES[name]["file"])
    return shape


def make_handler(args, recorder, reader):
    meta = {"height": args.height, "tilt": args.tilt, "upside_down": not args.upright, "bed_top": 0.7,
            "person_height": args.person_height,
            "sessions": {k: v["name"] for k, v in SESSIONS.items()}}

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            # JSON 요청만 받아, 다른 웹사이트가 이 PC의 녹화를 조작하지 못하게 한다.
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._json(415, {"error": "JSON 요청만 받습니다."})
                return
            try:
                size = min(int(self.headers.get("Content-Length", 0)), 4096)
                data = json.loads(self.rfile.read(size) or b"{}")
                path = self.path.split("?")[0]
                if path == "/api/record/start":
                    name = recorder.start(str(data.get("session", "")), str(data.get("participant", ""))[:40],
                                          str(data.get("activity", ""))[:80], str(data.get("note", ""))[:200])
                    self._json(200, {"ok": True, "name": name})
                elif path == "/api/record/stop":
                    self._json(200, {"ok": True, "name": recorder.stop()})
                elif path in ("/api/room", "/api/bed", "/api/door"):
                    self._json(200, {"ok": True, "shape": save_shape(path.rsplit("/", 1)[1], data.get("corners"))})
                elif path == "/api/label":
                    tid, truth = int(data.get("tid")), str(data.get("truth"))
                    if truth not in TRUTHS:
                        raise ValueError("라벨은 person, object, unknown 중 하나여야 합니다.")
                    pid = reader.people.label(time.time(), tid, truth)
                    if truth == "person":
                        reader.legacy.choose_patient(tid)
                    if recorder.status():
                        recorder.set_label(tid, truth)
                    self._json(200, {"ok": True, "pid": pid})
                elif path == "/api/people/reset":
                    reader.people.reset()
                    reader.falls.detectors.clear()
                    self._json(200, {"ok": True})
                elif path == "/api/people/max":
                    value = int(data.get("max"))
                    if not 1 <= value <= 8:
                        raise ValueError("최대 인원은 1~8명이어야 합니다.")
                    reader.people.max_people = value
                    with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                        json.dump({"max_people": value}, fh)
                    self._json(200, {"ok": True, "max": value})
                else:
                    self._json(404, {"error": "없는 주소입니다."})
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})

        def do_GET(self):
            if self.path.split("?")[0] == "/api/state":
                with lock:
                    snapshot = dict(state)
                with shape_lock:
                    shapes = {name: load_shape(name) for name in SHAPES}
                snapshot.update(meta=meta, now=time.time(), recording=recorder.status(), shapes=shapes, max_people=reader.people.max_people)
                body = json.dumps(snapshot, ensure_ascii=False).encode()
                ctype = "application/json; charset=utf-8"
            elif self.path.split("?")[0] == "/":
                with open(os.path.join(HERE, "viewer.html"), "rb") as fh:
                    body = fh.read()
                ctype = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="pi@192.168.0.4")
    ap.add_argument("--repo", default="~/Documents/Github/Majubom")
    ap.add_argument("--reader", default=r"D:\Majubom-mmw\mmWave\send_mmw.py")
    ap.add_argument("--height", type=float, default=2.7)
    ap.add_argument("--tilt", type=float, default=90.0)
    ap.add_argument("--upright", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--person-height", type=float, default=1.73, help="화면 기준선으로 쓸 측정자 키(m)")
    args = ap.parse_args()

    module = load_reader(args.reader)
    module.MOUNT_UPSIDE_DOWN = not args.upright
    recorder = Recorder(args)
    reader = Reader(args, module, recorder)
    threading.Thread(target=reader.run_forever, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args, recorder, reader))
    print(f"http://localhost:{args.port}  (종료: Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        recorder.stop("server_exit")


if __name__ == "__main__":
    main()
