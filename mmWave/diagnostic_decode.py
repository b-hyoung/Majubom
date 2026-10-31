"""Stateless diagnostic TLV decode; no I/O or persistence."""
import math
import struct
import send_mmw as protocol

MAX_POINTS = 2048


def finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    return value


def decode_frame(header, frame, height=2.5, tilt=50.0, min_snr=0.0):
    """Raw compressed samples + filtered, mount-corrected cloud and tracks (track_to_world; 2026-10-12 tracks were sensor-frame)."""
    offset = protocol.FRAME_HEADER_LEN
    tlvs, samples, points, tracks, units = [], [], [], [], None
    rejected = 0
    a = math.radians(tilt)
    malformed = False
    for _ in range(header['numTLVs']):
        if offset + 8 > len(frame):
            malformed = True
            break
        kind, length = struct.unpack_from('<II', frame, offset)
        offset += 8
        if offset + length > len(frame):
            malformed = True
            break
        payload = frame[offset:offset + length]
        offset += length
        tlvs.append({'type': kind, 'payload_bytes': length})
        if kind == 1020 and len(payload) >= 20:
            units = list(struct.unpack_from('<5f', payload))
            e_u, a_u, d_u, r_u, s_u = units
            malformed |= (len(payload) - 20) % 8 != 0
            for i in range((len(payload) - 20) // 8):
                el, az, dop, ran, snr_i = struct.unpack_from('<bbhHH', payload, 20 + i * 8)
                if len(samples) < MAX_POINTS:
                    samples.append({'elevation': el, 'azimuth': az, 'doppler': dop, 'range': ran, 'snr': snr_i})
                r, snr = ran * r_u, snr_i * s_u
                elev, azim, velocity = el * e_u, az * a_u, dop * d_u
                if not all(math.isfinite(v) for v in [r, snr, elev, azim, velocity]) or not 0 < r <= 20 or snr < min_snr:
                    rejected += 1
                    continue
                if protocol.MOUNT_UPSIDE_DOWN:   # 거꾸로 장착: 방위각·고도각 부호 반전(send_mmw.track_to_world와 같은 보정)
                    elev, azim = -elev, -azim
                x = r * math.cos(elev) * math.sin(azim)
                y = r * math.cos(elev) * math.cos(azim)
                z = r * math.sin(elev)
                # Sensor forward/down tilted relative to world: X right, Y forward, Z from floor.
                world = [x, y * math.cos(a) + z * math.sin(a), height - y * math.sin(a) + z * math.cos(a)]
                if len(points) < MAX_POINTS:
                    points.append({'sensor': [x, y, z], 'world': world, 'doppler': velocity, 'snr': snr})
        if kind == 1010:
            malformed |= len(payload) % protocol.TARGET_SIZE != 0
            for i in range(min(len(payload) // protocol.TARGET_SIZE, MAX_POINTS)):
                row = struct.unpack_from(protocol.TARGET_STRUCT, payload, i * protocol.TARGET_SIZE)
                tracks.append({'tid': row[0], 'values': list(row[1:])})
    clean_tracks = []
    for t in tracks:
        if t['tid'] > 252 or not all(math.isfinite(v) and abs(v) <= 100 for v in t['values'][:6]):
            continue
        v = t['values']
        w = protocol.track_to_world({'posX': v[0], 'posY': v[1], 'posZ': v[2],
                                     'velX': v[3], 'velY': v[4], 'velZ': v[5]}, height, tilt)
        clean_tracks.append({'tid': t['tid'], 'world': [w['posX'], w['posY'], w['posZ']],
                             'velocity': [w['velX'], w['velY'], w['velZ']], 'sensor': v[:3]})
    return finite_json({'header': header, 'tlvs': tlvs, 'units': units,
                        'raw_points': samples, 'raw_tracks': tracks, 'points': points,
                        'tracks': clean_tracks, 'rejected_points': rejected,
                        'malformed': malformed, 'display_limit': MAX_POINTS})
