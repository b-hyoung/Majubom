"""레이더 번호를 사람 ID, 표시 역할, 사람별 낙상 판정으로 바꾼다. 입출력 없이 프레임마다 update()를 호출한다.

처리 순서는 테스트 뷰어의 전처리 탭과 같다: 사람 ID → 문·남은 번호 제외 → 범위 필터 → 이동 이력 → EMA.
화면에서 누른 번호만 사람 ID를 받고, 마지막으로 누른 사람이 환자(보행 지표 대상)가 된다.
예외(2026-11-05): 방 안에 사람 ID가 붙은 사람이 없고 움직이는 사람 후보 번호가 정확히 하나면, 클릭 없이 그 번호를
환자로 정한다(locked="auto"). 클릭이 없으면 낙상 판정이 아예 돌지 않던 문제를 막는다. 2명 이상이면 클릭을 기다린다.

낙상 사건은 그대로 보내지 않는다.
- 침상 안: 침상에 눕는 것도 높이가 떨어지므로 보내지 않는다.
- 문 근처: 사람이 문으로 나간 뒤 남은 번호가 바닥으로 떨어지는 경우가 있어 낙상 대신 caution과 door_check로 보낸다
  (2026-11-01 S6 녹화 15:25:10). 문 앞에서 실제로 넘어진 사람도 같은 표시를 받는다.
"""
import math

import fall_detect
from person_ids import FRESH_MIN_FRAMES, FRESH_MIN_POINTS, PersonTracker, in_poly

EMA_ALPHA = 0.3          # 위치 평활화 계수
MOVE_SETTLE_SEC = 1.5    # 추적이 생긴 직후 칩이 위치를 잡는 동안은 이동으로 치지 않는다
MOVE_MIN_M = 0.3         # 생긴 자리에서 이만큼 움직인 적이 있는 추적만 사람 후보로 본다
AUTO_RECENT_MOVE_SEC = 5.0  # 자동 지정 후보는 최근 이 시간 안에 움직인 번호만(2026-11-07: 멈춘 유령 번호가 자동 지정됨)
TRACK_END_SEC = 2.0      # 이 시간 넘게 안 보인 추적 번호는 끝난 것으로 본다
FALL_CONFIRM_SEC = 1.0   # 기존 리더의 FALL_CONFIRM_SEC와 같다
DOOR_NEAR_M = 0.3        # 낙상 위치가 문 구역에서 이 안이면 문 근처로 본다
DOOR_RECENT_SEC = 5.0    # 이 시간 안에 문 구역에 있었으면 문 근처로 본다


def polygon_distance(x, y, poly):
    """점에서 다각형까지의 거리. 안쪽이면 0."""
    if in_poly(x, y, poly):
        return 0.0
    best = math.inf
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i - 1], poly[i]
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / max(dx * dx + dy * dy, 1e-9)))
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


class FallWatch:
    """사람 ID마다 기존 낙상 판정기를 하나씩 둔다. 방 안에 있는 사람의 번호만 넣는다."""

    def __init__(self, bed, door):
        self.bed, self.door = bed, door
        self.watches = {}

    def update(self, now, inputs, persons):
        """반환: (서버로 보낼 사건 목록, 화면에 표시할 낙상 상태 목록)"""
        by_tid = {t["tid"]: t for t in inputs}
        events, falls = [], []
        for person in persons:
            if person["state"] != "in" or person["tid"] not in by_tid:
                continue
            pid, tid = person["pid"], person["tid"]
            watch = self.watches.get(pid)
            if watch is None:
                watch = self.watches[pid] = {"det": fall_detect.FallDetector(manual_only=True, confirm_sec=FALL_CONFIRM_SEC),
                                             "seq": 0, "door_t": None, "alarmed": False}
            target = by_tid[tid]
            if in_poly(target["posX"], target["posY"], self.door):
                watch["door_t"] = now
            det = watch["det"]
            detected = det.update(now, [target])
            if det.patient_tid != tid:
                watch["seq"] += 1
                det.set_manual(now, tid, watch["seq"])
            for ev in detected:
                routed = self._route(now, watch, ev)
                if routed:
                    events.append({**routed, "pid": pid})
            if watch["alarmed"]:
                falls.extend({**fall, "pid": pid} for fall in det.state())
        return events, falls

    def _route(self, now, watch, ev):
        if ev["stage"] == "recovered":
            alarmed, watch["alarmed"] = watch["alarmed"], False
            return ev if alarmed else None
        x, y = ev.get("x", 0.0), ev.get("y", 0.0)
        if in_poly(x, y, self.bed):
            return None
        recent = watch["door_t"] is not None and now - watch["door_t"] <= DOOR_RECENT_SEC
        if recent or polygon_distance(x, y, self.door) <= DOOR_NEAR_M:
            return {**ev, "stage": "caution", "door_check": True, "detected_stage": ev["stage"]}
        if ev["stage"] == "fallen":
            watch["alarmed"] = True
        return ev


class RoomPipeline:
    def __init__(self, room, bed, door, max_people=3, room_filter=True, tilt=90.0, emit=None):
        self.room, self.bed, self.door = room, bed, door
        self.room_filter = room_filter
        self.people = PersonTracker(emit, max_people=max_people)   # emit: 사람 ID 사건(연결·끊김·나감) 기록 함수
        self.falls = FallWatch(bed, door)
        self.tracks = {}
        self.patient_pid = None
        self.patient_auto = False
        fall_detect.BED_CENTER = (sum(c[0] for c in bed) / len(bed), sum(c[1] for c in bed) / len(bed))
        if tilt >= 80:
            fall_detect.HEIGHT_FIT = (1.0, 0.0)   # 거리별 높이 보정은 벽 설치에서 잰 값이라 천장에서는 끈다

    def designate(self, now, tid):
        """화면에서 누른 번호를 사람으로 표시하고 환자로 정한다. 없는 번호면 None, 최대 인원이면 ValueError."""
        pid = self.people.label(now, tid, "person")
        if pid:
            self.patient_pid = pid
            self.patient_auto = False
        return pid

    def _auto_designate(self, now, snap, targets):
        """방 안에 사람 ID가 붙은 사람이 없고 사람 후보 번호가 정확히 하나면 그 번호를 환자로 정한다. 반환: 정했는지.
        후보: 사람 ID 없음, 숨김(문·나간 사람의 번호·반사점 없는 번호) 아님, 방 안, 생긴 자리에서 움직인 적 있음,
        최근 1초 반사점이 사람 잇기 기준(person_ids FRESH_MIN_*)을 넘음, 최근 AUTO_RECENT_MOVE_SEC 안에 움직임.
        멈춘 유령 번호는 움직인 적이 없어 빠지고, 사람을 따라 움직이다 멈춘 유령 번호는 멈춘 지 오래되어 빠진다."""
        if any(p["state"] == "in" for p in snap["persons"]):
            return False
        cands = []
        for t in targets:
            st = self.people.tracks.get(t["tid"])
            if st is None or st["pid"] or t["tid"] in snap["hidden"] or not st["moved"]:
                continue
            if now - st["anchor_t"] > AUTO_RECENT_MOVE_SEC:
                continue
            if not in_poly(t["posX"], t["posY"], self.room):
                continue
            counts = [n for _, n in st["frames"]]
            if sum(n > 0 for n in counts) < FRESH_MIN_FRAMES or sum(counts) / max(len(counts), 1) < FRESH_MIN_POINTS:
                continue
            cands.append(t["tid"])
        if len(cands) != 1:
            return False
        pid = self.people.label(now, cands[0], "person", reason="자동 지정(후보 1명)")
        if not pid:
            return False
        self.patient_pid = pid
        self.patient_auto = True
        return True

    def _moved(self, now, tid, x, y, z):
        state = self.tracks.get(tid)
        if state is None:
            state = self.tracks[tid] = {"born": now, "bx": x, "by": y, "moved": False, "pos": [x, y, z], "last": now}
        state["last"] = now
        state["pos"] = [EMA_ALPHA * v + (1 - EMA_ALPHA) * c for v, c in zip((x, y, z), state["pos"])]
        if now - state["born"] < MOVE_SETTLE_SEC:
            state["bx"], state["by"] = x, y
        elif math.hypot(x - state["bx"], y - state["by"]) > MOVE_MIN_M:
            state["moved"] = True
        return state

    def _role(self, tid, pid, patient_tid, snap, state, x, y):
        """대시보드는 still, dup을 그리지 않고, ToF 자동 보정은 dup을 사람으로 세지 않는다."""
        if pid:
            return "patient" if tid == patient_tid else "other"
        if self.room_filter and not in_poly(x, y, self.room):
            return None
        if snap["full"] or tid in snap["hidden"]:
            return "dup"
        if not state["moved"] and self.patient_pid is not None:
            return "still"   # 환자를 누르기 전에는 누구든 누를 수 있게 숨기지 않는다
        return "other"

    def update(self, now, targets):
        """targets: 바닥 기준 좌표 추적 목록(posX, posY, posZ, velX, velY, velZ, npts, linked_npts, top_z)."""
        tracks = [{"tid": t["tid"], "world": [t["posX"], t["posY"], t["posZ"]], "npts": t["npts"]} for t in targets]
        snap = self.people.update(now, tracks, self.room, self.door, self.bed)
        if self._auto_designate(now, snap, targets):
            snap = self.people.snapshot(now, self.room, self.door, self.bed)
        patient = next((p for p in snap["persons"] if p["pid"] == self.patient_pid), None)
        patient_tid = patient["tid"] if patient else None

        inputs = [{"tid": t["tid"], "posX": t["posX"], "posY": t["posY"], "posZ": t["posZ"], "velZ": t["velZ"],
                   "npts": t["linked_npts"], "top_z": t["top_z"]} for t in targets]
        events, falls = self.falls.update(now, inputs, snap["persons"])

        shown, patient_target = [], None
        for t in targets:
            tid, x, y = t["tid"], t["posX"], t["posY"]
            state = self._moved(now, tid, x, y, t["posZ"])
            pid = snap["tid_to_pid"].get(tid)
            role = self._role(tid, pid, patient_tid, snap, state, x, y)
            if tid == patient_tid:
                patient_target = t
            if role:
                shown.append({"tid": tid, "pid": pid, "role": role, "npts": t["npts"],
                              "x": round(state["pos"][0], 3), "y": round(state["pos"][1], 3), "z": round(state["pos"][2], 3),
                              "vx": round(t["velX"], 3), "vy": round(t["velY"], 3)})
        for tid in [k for k, v in self.tracks.items() if now - v["last"] > TRACK_END_SEC]:
            del self.tracks[tid]

        info = {"tid": patient_tid, "pid": self.patient_pid, "locked": ("auto" if self.patient_auto else "manual") if patient_tid is not None else None,
                "since": now - patient["since"] if patient else None,
                "bed_in": bool(patient_target and in_poly(patient_target["posX"], patient_target["posY"], self.bed)),
                "bed_park": False}
        return {"targets": shown, "persons": snap["persons"], "full": snap["full"], "patient_tid": patient_tid, "patient": info,
                "patient_target": patient_target, "events": events, "falls": falls,
                "n_people": sum(s["role"] in ("patient", "other") for s in shown)}
