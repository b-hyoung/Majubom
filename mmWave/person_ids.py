"""레이더 번호와 별개로 사람 ID(P1, P2 ...)를 유지한다. 입출력 없이 프레임마다 update()를 호출한다.

레이더 번호는 사람이 아니다. 사람이 범위를 벗어나면 옛 번호가 경계에 남고, 돌아오면 새 번호가 생긴다.
- 사람 표시: 사용자가 사람으로 표시한 번호에 ID를 준다. 나간 사람이 있으면 그 ID를 다시 쓴다.
- 나감: 출입구(문, 범위 1·2번 꼭짓점 사이의 열린 변)에서 반사점이 끊기면 "나감"으로 바꾸고 옛 번호는 숨긴다. 벽으로는 나갈 수 없다.
- 들어옴: 나간 사람이 있을 때 출입구에서 움직이는 새 번호가 생기면 같은 ID로 잇는다.
- 방 안에서 반사점이 없어지면 지우지 않는다(누운 사람). 경계 근처라도 낮은 높이면 나감으로 보지 않는다.
- 문 구역 안에서만 움직인 번호는 문으로 보고 사람에서 제외한다.

레이더는 누구인지 구별하지 못한다. 나간 사람이 여러 명이면 잇는 것은 추정이며 guess=True로 표시한다.
"""
import math
from collections import deque

TRACK_END_SEC = 2.0     # 이 시간 넘게 안 보이면 번호가 끝난 것으로 본다
STALE_SEC = 3.0         # 이 시간 넘게 반사점이 없으면 추적이 멈춘 것으로 본다
EDGE_M = 0.6            # 범위 경계에서 이 안쪽이면 경계 근처로 본다
LOW_Z_M = 0.5           # 마지막 높이가 이보다 낮으면 쓰러졌을 수 있으므로 나감으로 보지 않는다
JUMP_M = 1.0            # 한 프레임에 이보다 멀리 튀면 다른 대상으로 본다
JUMP_GAP_SEC = 0.5
Z_MIN, Z_MAX = -0.6, 2.7
OUTSIDE_SEC = 1.0       # 범위 밖에 이 시간 이상 있으면 나감
HANDOVER_M = 1.2        # 사람이 끊긴 자리에서 이 안에 생긴 번호는 같은 사람일 수 있다
HANDOVER_SEC = 10.0
MOVE_MIN_M = 0.3
FRESH_WINDOW_SEC = 1.0
FRESH_MIN_FRAMES = 9    # 최근 1초 중 반사점이 있는 프레임이 이만큼은 있어야 살아 있는 추적으로 본다
FRESH_MIN_POINTS = 5.0  # 최근 1초의 프레임당 평균 반사점
RELINK_COOLDOWN_SEC = 3.0  # 나간 뒤 이 시간 동안은 같은 번호를 다시 잇지 않는다(경계에서 나감·들어옴 반복 방지)
OPENING_INSIDE_M = 0.3     # 다시 이으려면 열린 변에서 이만큼 안쪽에 있어야 한다
NO_POINTS_HIDE_SEC = 5.0  # 사람이 아닌 번호에 반사점이 이만큼 없으면 숨긴다(레이더가 번호만 오래 유지한다)
ALIVE_MIN_POINTS = 3.0    # 최근 1초의 프레임당 평균 반사점이 이 이상이어야 사람이 아닌 번호를 살아 있다고 본다
DOOR_FRAMES_OUT = 18      # 문 구역 밖에서 반사점과 함께 이만큼 프레임이 잡혀야 문을 벗어난 것으로 본다
STILL_M = 0.1             # 이 안에서만 움직이면 멈춘 것으로 본다
DOOR_STILL_SEC = 5.0      # 문 구역에서 이만큼 멈춰 있으면 사람이 아니라 문으로 본다


def in_poly(x, y, poly):
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def opening_distance(x, y, room):
    """열린 변(범위 1·2번 꼭짓점 사이)까지의 거리. 나머지 변은 벽이라 출입구가 아니다."""
    (x1, y1), (x2, y2) = room[0], room[1]
    dx, dy = x2 - x1, y2 - y1
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / max(dx * dx + dy * dy, 1e-9)))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def beyond_opening(x, y, room):
    """점이 열린 변의 바깥쪽에 있으면 True. 나머지 두 꼭짓점이 있는 쪽이 안쪽이다."""
    (x1, y1), (x2, y2) = room[0], room[1]
    side = lambda px, py: (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
    return side(x, y) * side(*room[2]) < 0


class PersonTracker:
    def __init__(self, emit=None, max_people=3):
        self.emit = emit or (lambda *a: None)
        self.max_people = max_people
        self.persons = {}
        self.tracks = {}
        self.next_id = 1

    def reset(self):
        self.persons.clear()
        for st in self.tracks.values():
            st["pid"] = None
            st["abandoned"] = False
        self.next_id = 1

    # ---------- 사용자 표시 ----------
    def label(self, now, tid, truth, reason="사용자 표시"):
        st = self.tracks.get(tid)
        if st is None:
            return None
        if truth != "person":
            if st["pid"]:
                self._lose(now, self.persons[st["pid"]], "사용자가 사람이 아니라고 표시", st)
            return None
        st["abandoned"] = False
        if st["pid"]:
            return st["pid"]
        waiting = [p for p in self.persons.values() if p["state"] != "in"]
        if waiting:
            person = max(waiting, key=lambda p: p["changed"])
        else:
            if len(self.persons) >= self.max_people:
                raise ValueError(f"최대 인원 {self.max_people}명이 모두 방 안에 있습니다. 최대 인원을 늘리거나 사람 ID를 초기화해 주세요.")
            pid = f"P{self.next_id}"
            self.next_id += 1
            person = self.persons[pid] = {"pid": pid, "state": "lost", "tid": None, "pos": st["pos"], "z": st["z"],
                                          "changed": now, "exit_kind": None, "guess": False}
        self._link(now, person, st, tid, guess=False, reason=reason)
        return person["pid"]

    # ---------- 내부 동작 ----------
    def _link(self, now, person, st, tid, guess, reason):
        was = person["state"]
        person.update(state="in", tid=tid, changed=now, guess=guess, exit_kind=None, pos=st["pos"], z=st["z"])
        st["pid"] = person["pid"]
        st["abandoned"] = False
        verb = "다시 들어옴" if was == "out" else "번호 연결"
        note = " (추정)" if guess else ""
        self.emit(now, "person_link", person["pid"], st["pos"], f"{person['pid']} {verb}: {tid}번 ({reason}){note}", guess)

    def _exit(self, now, person, st, kind):
        person.update(state="out", tid=None, changed=now, exit_kind=kind, pos=st["fresh_pos"], z=st["fresh_z"], guess=False)
        st["pid"] = None
        st["abandoned"] = True
        st["abandon_pos"] = st["pos"]
        st["abandoned_by"] = person["pid"]
        st["relink_after"] = now + RELINK_COOLDOWN_SEC
        self.emit(now, "person_exit", person["pid"], st["fresh_pos"], f"{person['pid']} 나감 ({kind})", False)

    def _lose(self, now, person, reason, st=None):
        person.update(state="lost", tid=None, changed=now, guess=False)
        if st is not None:
            person.update(pos=st["fresh_pos"], z=st["fresh_z"])
            st["pid"] = None
        self.emit(now, "person_lost", person["pid"], person["pos"], f"{person['pid']} 추적 끊김: {reason}. 같은 사람을 다시 표시해 주세요.", True)

    def update(self, now, tracks, room, door, bed):
        seen = set()
        for t in tracks:
            tid = t["tid"]
            seen.add(tid)
            x, y, z = t["world"]
            st = self.tracks.get(tid)
            if st is None:
                st = self.tracks[tid] = {
                    "born": now, "first": [x, y], "pid": None, "abandoned": False, "abandon_pos": None, "pos": [x, y], "z": z,
                    "last": now, "fresh_t": now, "fresh_pos": [x, y], "fresh_z": z, "frames": deque(), "left_door": not in_poly(x, y, door),
                    "born_door": in_poly(x, y, door), "moved": False, "outside_since": None, "relink_after": 0.0, "out_door": 0, "abandoned_by": None, "alive_t": now,
                    "anchor": [x, y], "anchor_t": now}
            elif st["pid"] and now - st["last"] < JUMP_GAP_SEC and (math.hypot(x - st["pos"][0], y - st["pos"][1]) > JUMP_M or not Z_MIN <= z <= Z_MAX):
                self._lose(now, self.persons[st["pid"]], f"{tid}번이 튐", st)
            st.update(pos=[x, y], z=z, last=now)
            fresh = t.get("npts", 0) > 0
            if fresh:
                st["fresh_t"], st["fresh_pos"], st["fresh_z"] = now, [x, y], z
                if not in_poly(x, y, door):
                    st["out_door"] += 1
                    if st["out_door"] >= DOOR_FRAMES_OUT:
                        st["left_door"] = True
            st["frames"].append((now, t.get("npts", 0)))
            while st["frames"] and now - st["frames"][0][0] > FRESH_WINDOW_SEC:
                st["frames"].popleft()
            if sum(n for _, n in st["frames"]) / len(st["frames"]) >= ALIVE_MIN_POINTS:
                st["alive_t"] = now
            if now - st["born"] > 1.5 and math.hypot(x - st["first"][0], y - st["first"][1]) > MOVE_MIN_M:
                st["moved"] = True
            elif now - st["born"] <= 1.5:
                st["first"] = [x, y]
            st["outside_since"] = None if in_poly(x, y, room) else (st["outside_since"] or now)
            if math.hypot(x - st["anchor"][0], y - st["anchor"][1]) > STILL_M:
                st["anchor"], st["anchor_t"] = [x, y], now

        # 끝난 번호
        for tid in [k for k, v in self.tracks.items() if now - v["last"] > TRACK_END_SEC]:
            st = self.tracks.pop(tid)
            if st["pid"]:
                person = self.persons[st["pid"]]
                kind = self._exit_kind(st, room, door, bed)
                if kind:
                    self._exit(now, person, st, kind)
                else:
                    self._lose(now, person, f"{tid}번 사라짐", st)

        # 연결된 사람의 나감 판단
        for tid in seen:
            st = self.tracks[tid]
            if not st["pid"]:
                continue
            person = self.persons[st["pid"]]
            if in_poly(*st["pos"], door) and now - st["anchor_t"] >= DOOR_STILL_SEC:
                # 문은 반사점이 계속 나오므로 끊기기를 기다리지 않는다. 사람이 문으로 나간 뒤 문에 번호가 붙은 경우다.
                self._exit(now, person, st, "문")
            elif st["outside_since"] and now - st["outside_since"] >= OUTSIDE_SEC:
                kind = self._exit_kind(st, room, door, bed)
                if kind:
                    self._exit(now, person, st, kind)
                else:
                    self._lose(now, person, f"{tid}번이 벽 밖으로 튐", st)
            elif now - st["fresh_t"] >= STALE_SEC and self._exit_kind(st, room, door, bed):
                self._exit(now, person, st, self._exit_kind(st, room, door, bed))
            else:
                person.update(pos=st["fresh_pos"], z=st["fresh_z"])

        # 새 번호를 사람에 잇기
        for tid in seen:
            st = self.tracks[tid]
            if st["pid"]:
                continue
            if now < st["relink_after"] or not (st["moved"] and in_poly(*st["pos"], room)):
                continue
            counts = [n for _, n in st["frames"]]
            if sum(n > 0 for n in counts) < FRESH_MIN_FRAMES or sum(counts) / max(len(counts), 1) < FRESH_MIN_POINTS:
                continue
            if opening_distance(*st["pos"], room) < OPENING_INSIDE_M:
                continue
            if st["abandoned"]:
                # 나간 사람이 옛 번호로 돌아오는 경우. 문 구역 밖에서 그 자리를 벗어나 움직여야 한다(문 움직임 제외).
                if in_poly(*st["pos"], door) or math.hypot(st["pos"][0] - st["abandon_pos"][0], st["pos"][1] - st["abandon_pos"][1]) <= 0.5:
                    continue
                owner = self.persons.get(st["abandoned_by"])
                st["abandoned"] = False
                if owner and owner["state"] == "out":
                    self._link(now, owner, st, tid, guess=False, reason="옛 번호로 돌아옴")
                    continue
            if st["born_door"] and not st["left_door"]:
                continue
            self._try_relink(now, tid, st, room, door)

        return self.snapshot(now, room, door, bed)

    def _exit_kind(self, st, room, door, bed):
        x, y = st["fresh_pos"]
        if st["fresh_z"] < LOW_Z_M or in_poly(x, y, bed):
            return None
        if in_poly(x, y, door):
            return "문"
        if opening_distance(x, y, room) <= EDGE_M or beyond_opening(x, y, room):
            return "열린 쪽"
        return None

    def _try_relink(self, now, tid, st, room, door):
        x, y = st["pos"]
        lost = [p for p in self.persons.values() if p["state"] == "lost" and now - p["changed"] <= HANDOVER_SEC
                and math.hypot(x - p["pos"][0], y - p["pos"][1]) <= HANDOVER_M]
        if lost:
            self._link(now, min(lost, key=lambda p: math.hypot(x - p["pos"][0], y - p["pos"][1])), st, tid, guess=len(lost) > 1, reason="끊긴 자리 근처")
            return
        # 반사점이 끊긴 채 멈춘 번호에 묶인 사람은 근처의 새 번호로 옮긴다
        stuck = []
        for other in self.tracks.values():
            if other["pid"] and now - other["fresh_t"] >= 2.0 and math.hypot(x - other["fresh_pos"][0], y - other["fresh_pos"][1]) <= HANDOVER_M and st["born"] > other["fresh_t"] - 1.0:
                stuck.append(other)
        if stuck:
            other = min(stuck, key=lambda o: math.hypot(x - o["fresh_pos"][0], y - o["fresh_pos"][1]))
            person = self.persons[other["pid"]]
            other["pid"], other["abandoned"], other["abandon_pos"] = None, True, other["pos"]
            self._link(now, person, st, tid, guess=False, reason="멈춘 번호 근처")
            return
        # 나간 곳으로 들어온 새 번호만 잇는다. 나가기 전부터 있던 번호는 돌아온 사람이 아니다.
        fx, fy = st["first"]
        from_door = st["born_door"] and st["left_door"]
        from_open = opening_distance(fx, fy, room) <= EDGE_M + 0.4
        out = [p for p in self.persons.values() if p["state"] == "out" and st["born"] >= p["changed"] - 1.0
               and ((p["exit_kind"] == "문" and from_door) or (p["exit_kind"] == "열린 쪽" and from_open))]
        if out:
            self._link(now, min(out, key=lambda p: math.hypot(fx - p["pos"][0], fy - p["pos"][1])), st, tid, guess=len(out) > 1, reason="나간 곳으로 들어옴")

    def snapshot(self, now, room, door, bed):
        hidden = {}
        for tid, st in self.tracks.items():
            if st["pid"]:
                continue
            if st["abandoned"]:
                hidden[tid] = "나간 사람의 남은 번호"
            elif st["born_door"] and not st["left_door"]:
                hidden[tid] = "문"
            elif in_poly(*st["pos"], door) and now - st["anchor_t"] >= DOOR_STILL_SEC:
                hidden[tid] = "문"
            elif now - st["alive_t"] >= NO_POINTS_HIDE_SEC and st["fresh_z"] >= LOW_Z_M and not in_poly(*st["fresh_pos"], bed):
                hidden[tid] = "반사점 없는 남은 번호"
        return {
            "tid_to_pid": {tid: st["pid"] for tid, st in self.tracks.items() if st["pid"]},
            "hidden": hidden,
            "full": sum(p["state"] == "in" for p in self.persons.values()) >= self.max_people,
            "persons": [{"pid": p["pid"], "state": p["state"], "tid": p["tid"], "pos": [round(v, 2) for v in p["pos"]], "exit_kind": p["exit_kind"],
                         "guess": p["guess"], "since": round(now - p["changed"], 1)} for p in sorted(self.persons.values(), key=lambda q: int(q["pid"][1:]))],
        }
