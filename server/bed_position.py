"""침대 가운데(레이더 바닥 좌표 x, y m) — mmWave/bed_center.json 한 곳에서 읽는다(2026-10-31 천장 이전).

예전에는 send_mmw.BED_CENTER·mmw_server.BED_CENTER_XY·tof_autocal.BED_CENTER 세 곳에 같은 값을 손으로 맞췄다.
레이더 위치가 바뀌면 예전 좌표는 틀린 값이 되므로, 파일이 없으면 None을 돌려주고 침대 위치를 쓰는 규칙은 꺼진다
(틀린 좌표면 침대에 누운 환자를 '침대 근처에 아무도 없음 → 재실 아님'으로 바꾸는 등 위험하다).
파일은 tools/set_bed_center.py로 만든다. 파일이 바뀌면 재시작 없이 2초 안에 반영된다.
"""
import json
import os
import time

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mmWave", "bed_center.json")
_cache = {"checked": 0.0, "mtime": None, "value": None}


def bed_center():
    """(x, y) 또는 None(아직 이 설치에서 재지 않음)."""
    now = time.monotonic()
    if now - _cache["checked"] < 2.0:
        return _cache["value"]
    _cache["checked"] = now
    try:
        mtime = os.path.getmtime(PATH)
        if mtime != _cache["mtime"]:
            with open(PATH, encoding="utf-8") as fh:
                d = json.load(fh)
            _cache["value"] = (float(d["x"]), float(d["y"]))
            _cache["mtime"] = mtime
    except FileNotFoundError:
        _cache["value"], _cache["mtime"] = None, None
    except Exception:
        _cache["value"], _cache["mtime"] = None, None   # 깨진 파일은 '모름'으로 — 틀린 좌표를 쓰지 않는다
    return _cache["value"]
