"""Postprocess verified frames into self-contained SVG overlays, preserving PTS."""
import base64
from html import escape
from pathlib import Path
import struct
import zlib
from .contracts import SourceError
from .joint_inspection import JOINT_LABELS_KO, inspect_joints
from .evaluation import read_run
from .io_utils import atomic_json, file_hash
from .video import VideoSource

# COCO17 topology, for visual inspection only; no anatomical acceptance rule.
EDGES = ((0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9),
         (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13),
         (13, 15), (12, 14), (14, 16))
COLORS = ('#00ffb3', '#ffbf00', '#90b7ff', '#ff81e3')
BANNER = 0  # Compatibility constant; metadata no longer occupies the image geometry.


def png_bytes(rgb):
    """Lossless RGB PNG using the standard library; no plotting dependency."""
    height, width, channels = rgb.shape
    if channels != 3 or rgb.dtype.name != 'uint8':
        raise ValueError('Expected uint8 RGB')
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    scanlines = b''.join(b'\x00' + rgb[y].tobytes() for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))


def frame_svg(packet, row, mapped, execution_kind, run_status):
    width, height = packet.width, packet.height
    synthetic = execution_kind == 'synthetic'
    label = 'SYNTHETIC' if synthetic else 'MODEL OUTPUT'
    description = (f"{execution_kind}; run={run_status}; frame={row['frame_id']}; "
                   f"status={row['processing_status']}; candidates={row['candidate_count']}; "
                   f"PTS={row['source_pts']} x {row['time_base_num']}/{row['time_base_den']}; "
                   f"failure={row['failure_code']}; target=not_configured; not verified GT")
    elements = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">',
                f'<title>{escape(description)}</title>',
                f'<image x="0" y="0" width="{width}" height="{height}" href="data:image/png;base64,{base64.b64encode(png_bytes(packet.rgb)).decode()}"/>',
                '<g id="pose-layer">']
    radius = min(width, height) / 120
    for index, person in enumerate(mapped['people'] if mapped else []):
        color = COLORS[index % len(COLORS)]
        candidate_id = escape(person['candidate_id'], quote=True)
        points = {j: (joint['x_px'], joint['y_px'])
                  for j, joint in enumerate(person['joints'])
                  if joint['availability'] == 'predicted' and
                  0 <= joint['x_px'] < width and 0 <= joint['y_px'] < height}
        for a, b in EDGES:
            if a in points and b in points:
                x1, y1 = points[a]
                x2, y2 = points[b]
                elements.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2" vector-effect="non-scaling-stroke"/>')
        for j, (x, y) in points.items():
            elements.append(f'<circle class="joint-point" data-candidate-id="{candidate_id}" data-joint-index="{j}" cx="{x}" cy="{y}" r="{radius}" fill="{color}" stroke="#0c1420" stroke-width="{radius / 4}"><title>{escape(person["candidate_id"])} · {JOINT_LABELS_KO[j]} (COCO {j})</title></circle>')
        font = min(width, height) / 38
        for j, (x, y) in points.items():
            label_text = f"{person['candidate_id']} · {JOINT_LABELS_KO[j]}"
            label_x = max(font / 2, min(x + radius * 2, width - len(label_text) * font))
            label_y = max(font, min(y - radius * 2, height - font / 3))
            elements.append(f'<text class="joint-label" data-candidate-id="{candidate_id}" data-joint-index="{j}" x="{label_x}" y="{label_y}" font-size="{font}" font-family="sans-serif" fill="white" stroke="#0c1420" stroke-width="{font / 5}" paint-order="stroke" style="display:none">{escape(label_text)}</text>')
    elements.append('</g>')
    font_size = min(width, height) / 32
    badge_width, badge_height = (len(label) * 0.65 + 2) * font_size, font_size * 1.8
    x, y = width - badge_width - font_size, font_size
    elements.append(f'<g id="source-kind"><rect x="{x}" y="{y}" width="{badge_width}" height="{badge_height}" rx="{font_size / 3}" fill="#101c2e" opacity="0.9"/><text x="{x + font_size}" y="{y + font_size * 1.25}" fill="white" font-size="{font_size}" font-family="sans-serif">{label}</text></g>')
    return '\n'.join(elements) + '\n</svg>\n'


def render_overlay(video_path, run_dir, output, max_frames=None):
    if max_frames is not None and (type(max_frames) is not int or max_frames < 1):
        raise ValueError('max_frames must be a positive integer')
    bundle = read_run(run_dir, include_keypoints=True, include_raw=True)
    manifest, source = bundle['manifest'], bundle['source']
    video_path, output = Path(video_path).resolve(), Path(output).resolve()
    if file_hash(video_path) != source['video_sha256']:
        raise SourceError('Render video does not match the run input')
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / 'manifest.json', {'complete': False, 'run_id': manifest['run_id']})
    mapped = {row['frame_id']: row for row in bundle['keypoints']}
    raw = {row['frame_id']: row for row in bundle['raw_results']}
    decoder = VideoSource(source, bundle['input_frames'], video_path)
    hashes, catalog, svg_frames = {}, [], []
    limit = min(max_frames or len(bundle['frames']), len(bundle['frames']))
    try:
        decoder.open()
        for row in bundle['frames'][:limit]:
            packet = decoder.read_next()
            if packet is None:
                raise SourceError('Render source ended before recorded frames')
            name = f"frame_{row['frame_id']:08d}.svg"
            svg = frame_svg(packet, row, mapped.get(row['frame_id']), manifest['execution_kind'], manifest['run_status'])
            (output / name).write_text(svg, encoding='utf-8')
            svg_frames.append(svg)
            people = mapped.get(row['frame_id'], {}).get('people', [])
            drawable_count = sum(j['availability'] == 'predicted' and
                                 0 <= j['x_px'] < packet.width and 0 <= j['y_px'] < packet.height
                                 for person in people for j in person['joints'])
            hashes[name] = file_hash(output / name)
            catalog.append({'file': name, **{k: row[k] for k in ('frame_id', 'source_pts', 'time_base_num',
                'time_base_den', 'source_duration_pts', 'timestamp_source',
                'processing_status', 'candidate_count', 'failure_code')},
                'candidate_ids': [person['candidate_id'] for person in people],
                'joint_inspection': inspect_joints(mapped.get(row['frame_id']), raw.get(row['frame_id']),
                    manifest['mapping_profile'].get('image_block_id', 'image')),
                'drawable_joint_count': drawable_count if row['keypoints_ref'] is not None else None})
        if limit == len(bundle['frames']) and decoder.read_next() is not None:
            raise SourceError('Render source has unrecorded frames')
    finally:
        decoder.close()
    if file_hash(video_path) != source['video_sha256'] or file_hash(Path(run_dir) / 'manifest.json') != bundle['manifest_sha256']:
        raise SourceError('Render inputs changed during rendering')
    from .viewer import viewer_html
    html = viewer_html(manifest, source, catalog, svg_frames)
    (output / 'index.html').write_text(html, encoding='utf-8')
    atomic_json(output / 'frames.json', catalog)
    for name in ('index.html', 'frames.json'):
        hashes[name] = file_hash(output / name)
    from .runner import _code_version
    report = {'complete': True, 'renderer_code': _code_version(), 'run_id': manifest['run_id'], 'run_status': manifest['run_status'],
              'execution_kind': manifest['execution_kind'], 'run_manifest_sha256': bundle['manifest_sha256'],
              'video_sha256': source['video_sha256'], 'input_manifest_sha256': manifest['input_manifest_sha256'],
              'frame_count': len(catalog), 'source_frame_count': len(bundle['frames']),
              'rendered_frame_range': [0, limit], 'format': 'svg_with_lossless_rgb_png', 'playback': 'manual_frame_selection',
              'artifact_sha256': hashes}
    atomic_json(output / 'manifest.json', report)
    return output, report
