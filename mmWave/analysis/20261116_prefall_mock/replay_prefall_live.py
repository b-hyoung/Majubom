"""Replay a whole recorded scenario round through the reader's own patient selection (RoomPipeline) and the live
pre-fall wrapper (PatientPreFall), as send_mmw.py runs it on the Pi. Prints every prefall event next to the trial it
falls in (from marks.json), so you can see alarms before mat sit-downs and in control trials.
The viewer recording keeps tracker speed but not its x/y velocity, so velX = speed, velY = 0 here.
Usage: python replay_prefall_live.py <session_dir> [...]
"""
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import send_mmw  # noqa: E402
from people_pipeline import RoomPipeline  # noqa: E402
from prefall_predictor import PatientPreFall  # noqa: E402


def main():
    for ses in sys.argv[1:]:
        S = json.load(open(os.path.join(ses, "session.json"), encoding="utf-8"))
        M = json.load(open(os.path.join(ses, "marks.json"), encoding="utf-8-sig"))
        day = dt.datetime.fromtimestamp(S["started_at"]).date()
        ts = lambda h: dt.datetime.combine(day, dt.datetime.strptime(h, "%H:%M:%S.%f").time()).timestamp()
        raw = M["marks"]
        if isinstance(raw, dict):   # walking recordings (Record-CamMmw.ps1): {label: time}
            raw = [{"time": v, "trial": None, "kind": "walk", "label": k} for k, v in raw.items()]
        marks = sorted((ts(m["time"]), m["trial"], m["kind"], m["label"]) for m in raw)
        pipe = RoomPipeline(send_mmw.ROOM_POLYGON, send_mmw.BED_POLYGON, send_mmw.DOOR_POLYGON)
        pre = PatientPreFall()
        events, scored = [], 0
        for line in open(os.path.join(ses, "frames.jsonl"), encoding="utf-8"):
            f = json.loads(line)
            if "tracks" not in f:
                continue
            tg = [{"tid": t["tid"], "posX": t["world"][0], "posY": t["world"][1], "posZ": t["world"][2],
                   "velX": t.get("speed", 0.0), "velY": 0.0, "velZ": t.get("vz", 0.0), "npts": t["npts"],
                   "linked_npts": t["npts"], "top_z": t.get("head_z")} for t in f["tracks"]]
            r = pipe.update(f["t"], tg)
            last, ev = pre.feed(f["t"], r["patient_target"], r["patient"].get("pid"))
            scored += last is not None and abs(last["t"] - f["t"]) < 0.06
            if ev:
                cue = max((m for m in marks if m[0] <= ev["at"]), default=None, key=lambda m: m[0])
                events.append(ev)
                where = f"trial {cue[1]} {cue[2]} after '{cue[3]}' +{ev['at'] - cue[0]:.1f}s" if cue else "before first cue"
                print(f"{os.path.basename(ses)} {dt.datetime.fromtimestamp(ev['at']):%H:%M:%S} prefall prob {ev['prob']} | {where}")
        print(f"{os.path.basename(ses)}: {len(events)} prefall events, {scored} scored windows")


if __name__ == "__main__":
    main()
