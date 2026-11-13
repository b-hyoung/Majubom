# Cut a live capture of the radar's serial bytes into frames and fields. Only byte slicing and struct
# unpacking per TI's output format: no unit multiplication, no coordinate change, no filtering of empty
# track slots, no smoothing. Usage: python live_to_json.py <live_chunks.bin> <out.json>
import json, math, struct, sys, datetime
src, out = sys.argv[1:3]
blob = open(src, "rb").read()
stream, times, pos = bytearray(), [], 0   # times: (byte position in stream, Pi receive time)
while pos < len(blob):
    t, n = struct.unpack_from("<dI", blob, pos); pos += 12
    times.append((len(stream), t)); stream += blob[pos:pos + n]; pos += n
MAGIC = bytes([2, 1, 4, 3, 6, 5, 8, 7])
def recv_time(p):
    t = None
    for start, tt in times:
        if start <= p: t = tt
        else: break
    return t
def num(v): return v if not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else str(v)
HDR = ["version", "totalPacketLen", "platform", "frameNumber", "timeCpuCycles", "numDetectedObj", "numTLVs", "subFrameNumber"]
TGT = ["tid", "posX", "posY", "posZ", "velX", "velY", "velZ", "accX", "accY", "accZ"]
frames, skipped, i = [], [], stream.find(MAGIC)
lead = i
while i >= 0 and i + 40 <= len(stream):
    h = dict(zip(HDR, struct.unpack_from("<8I", stream, i + 8)))
    end = i + h["totalPacketLen"]
    if end > len(stream):
        skipped.append({"at_byte": i, "reason": "frame cut off at the end of the capture"}); break
    fb = bytes(stream[i:end]); off, tl = 40, []
    for _ in range(h["numTLVs"]):
        typ, ln = struct.unpack_from("<2I", fb, off); p = fb[off + 8:off + 8 + ln]; item = {"type": typ, "length": ln}
        if typ == 1010:
            item["targets"] = [{**{k: num(x) for k, x in zip(TGT, v[:10])}, "errorCovariance": [num(x) for x in v[10:26]],
                                "gatingGain": num(v[26]), "confidenceLevel": num(v[27])}
                               for v in (struct.unpack_from("<I27f", p, j * 112) for j in range(ln // 112))]
        elif typ == 1020:
            item["units"] = dict(zip(["elevationUnit", "azimuthUnit", "dopplerUnit", "rangeUnit", "snrUnit"], map(num, struct.unpack_from("<5f", p, 0)))) if ln >= 20 else None
            item["points"] = [list(struct.unpack_from("<bbhHH", p, 20 + j * 8)) for j in range((ln - 20) // 8)] if ln >= 20 else []
        elif typ == 1011:
            item["index"] = list(p)
        elif typ == 1012:
            item["heights"] = [dict(zip(["tid", "maxZ", "minZ"], map(num, struct.unpack_from("<I2f", p, j * 12)))) for j in range(ln // 12)]
        elif typ == 1021:
            item["value"] = struct.unpack_from("<I", p, 0)[0] if ln >= 4 else None
        else:
            item["payload_hex"] = p.hex()
        tl.append(item); off += 8 + ln
    rt = recv_time(i)
    frames.append({"received_at": datetime.datetime.fromtimestamp(rt).isoformat(timespec="milliseconds") if rt else None,
                   "header": {"magicWord": fb[:8].hex(), **h}, "tlvs": tl})
    nxt = stream.find(MAGIC, end)
    if nxt != end and nxt >= 0: skipped.append({"at_byte": end, "reason": f"{nxt - end} extra bytes before next magic word"})
    i = nxt
doc = {
    "about": "Live mmWave radar frames, read from the Pi reader's raw byte tap (127.0.0.1:5003, an unchanged copy of the radar's USB serial data). Each field is cut out of the bytes per TI's output format. Nothing converted or filtered.",
    "field_order": {"point (TLV 1020)": ["elevation", "azimuth", "doppler", "range", "snr"],
                    "note": "point values are integers before multiplying by units; track positions are radar coordinates in m; received_at is the Pi clock when the bytes arrived"},
    "capture": {"bytes": len(stream), "frames": len(frames), "bytes_before_first_frame": lead,
                "from": frames[0]["received_at"] if frames else None, "to": frames[-1]["received_at"] if frames else None, "skipped": skipped},
    "frames": frames,
}
s = json.dumps(doc, ensure_ascii=False, indent=1)
import re
s = re.sub(r"\[\n\s+(-?\d+),\n\s+(-?\d+),\n\s+(-?\d+),\n\s+(\d+),\n\s+(\d+)\n\s+\]", r"[\1, \2, \3, \4, \5]", s)
s = re.sub(r'"index": \[[\d,\s]*\]', lambda m: '"index": ' + json.dumps(json.loads(m.group(0).split(": ", 1)[1])), s)
s = re.sub(r'"errorCovariance": \[[^\]]+\]', lambda m: '"errorCovariance": ' + json.dumps(json.loads(m.group(0).split(": ", 1)[1])), s)
json.loads(s); open(out, "w", encoding="utf-8").write(s)
print(json.dumps(doc["capture"], ensure_ascii=False))
for f in frames[:20]:
    h = f["header"]; print(f["received_at"], h["frameNumber"], h["totalPacketLen"], h["numDetectedObj"], [(t["type"], t["length"]) for t in f["tlvs"]])
