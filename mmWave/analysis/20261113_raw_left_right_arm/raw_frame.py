# Decode one radar frame straight from raw.bin (exact UART bytes) into JSON.
# No filtering, no unit conversion, no coordinate change: every field as the firmware sent it.
# Usage: python raw_frame.py <session_dir> <out.json>
import json, math, struct, sys, os
ses, out = sys.argv[1], sys.argv[2]
frames = [json.loads(l) for l in open(os.path.join(ses, "frames.jsonl"), encoding="utf-8")]
marks = json.load(open(os.path.join(ses, "marks.json"), encoding="utf-8"))

# Pick the same 누움 frame used on the report site: patient track with world z 0.662, head_z 1.13, 6 points.
pick = None
for f in frames:
    for t in f.get("tracks") or []:
        if t.get("head_z") == 1.13 and t.get("npts") == 6 and abs(t["world"][2] - 0.662) < 0.0006:
            pick = (f, t); break
    if pick: break
f, tr = pick
raw = open(os.path.join(ses, "raw.bin"), "rb").read()[f["offset"]:f["offset"] + f["length"]]
assert raw[:8] == bytes([2, 1, 4, 3, 6, 5, 8, 7])

def num(v):  # keep float32 values exactly; JSON has no NaN/Inf, so write them as strings
    return v if not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else str(v)

H = struct.unpack_from("<8I", raw, 8)
hdr = dict(zip(["version", "totalPacketLen", "platform", "frameNumber", "timeCpuCycles", "numDetectedObj", "numTLVs", "subFrameNumber"], H))
TF = ["tid", "posX", "posY", "posZ", "velX", "velY", "velZ", "accX", "accY", "accZ"]
tlvs, off = [], 40
for _ in range(hdr["numTLVs"]):
    typ, ln = struct.unpack_from("<2I", raw, off); p = raw[off + 8:off + 8 + ln]
    item = {"type": typ, "length_bytes": ln}
    if typ == 1010:
        item["name"] = "target list (3D People Tracking, 112 bytes per slot, '<I27f')"
        item["targets"] = []
        for i in range(ln // 112):
            v = struct.unpack_from("<I27f", p, i * 112)
            item["targets"].append({**{k: num(x) for k, x in zip(TF, v[:10])},
                                    "errorCovariance": [num(x) for x in v[10:26]], "gatingGain": num(v[26]), "confidenceLevel": num(v[27])})
    elif typ == 1011:
        item["name"] = "target index (one uint8 per point of the PREVIOUS frame; 253/254/255 = not assigned)"
        item["index"] = list(p)
    elif typ == 1012:
        item["name"] = "target height ('<I2f' per target)"
        item["heights"] = [dict(zip(["tid", "maxZ", "minZ"], map(num, struct.unpack_from("<I2f", p, i * 12)))) for i in range(ln // 12)]
    elif typ == 1020:
        item["name"] = "compressed point cloud: 5 float units, then '<bbhHH' per point (integers before multiplying by units)"
        item["units"] = dict(zip(["elevationUnit", "azimuthUnit", "dopplerUnit", "rangeUnit", "snrUnit"], map(num, struct.unpack_from("<5f", p, 0))))
        item["points"] = [dict(zip(["elevation", "azimuth", "doppler", "range", "snr"], struct.unpack_from("<bbhHH", p, 20 + i * 8)))
                          for i in range((ln - 20) // 8)]
    elif typ == 1021:
        item["name"] = "presence indication (uint32)"
        item["value"] = struct.unpack_from("<I", p, 0)[0] if ln >= 4 else None
    else:
        item["name"] = "unknown"
    item["payload_hex"] = p.hex()
    tlvs.append(item); off += 8 + ln

doc = {
    "about": "One mmWave radar frame exactly as the IWR6843AOP sent it over USB serial. Decoded field by field from the saved bytes; nothing filtered, converted or smoothed.",
    "source": {"recording": os.path.basename(ses), "participant": "vic", "raw_file": "raw.bin",
               "byte_offset": f["offset"], "byte_length": f["length"], "received_at_unix": f["t"],
               "cued_posture": "누움", "matches_site_example": {"track_id": tr["tid"], "z": tr["world"][2], "head_z": tr["head_z"], "npts": tr["npts"]}},
    "frame_header": {"magicWord_hex": raw[:8].hex(), **hdr},
    "tlvs": tlvs,
    "frame_bytes_hex": raw.hex(),
}
json.dump(doc, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(hdr, [(t["type"], t["length_bytes"]) for t in tlvs])
