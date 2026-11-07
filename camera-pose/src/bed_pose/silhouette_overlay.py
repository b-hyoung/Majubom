"""Transient, bounded silhouette previews. Never record or classify from these."""
import base64
import numpy as np
from .silhouette import PROFILE

VERSION = 'silhouette-overlay-0.1'
MAX_SIDE = 640


def capture_preview(result, candidates, model_input):
    """Detach SDK masks before their lifetime ends; keep at most 640px PNGs."""
    import cv2
    frame = model_input.frame
    packet = {'version': VERSION, 'stream_id': frame.video_id,
              'stream_frame_id': frame.frame_id,
              'model_timestamp_ms': model_input.model_timestamp_ms,
              'width': frame.width, 'height': frame.height, 'candidates': []}
    masks = getattr(result, 'segmentation_masks', None)
    if masks is None or len(masks) != len(candidates):
        return packet
    for candidate, mask in zip(candidates, masks):
        try:
            values = mask.numpy_view()
            if values.shape == (frame.height, frame.width, 1):
                values = values[:, :, 0]
            if (values.shape != (frame.height, frame.width) or values.dtype.kind != 'f'
                    or not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1)):
                continue
            foreground = (values >= PROFILE['foreground_threshold']).astype(np.uint8)
            if not PROFILE['coverage_min'] <= float(foreground.mean()) <= PROFILE['coverage_max']:
                continue
            scale = min(1, MAX_SIDE / max(frame.width, frame.height))
            width, height = max(1, round(frame.width*scale)), max(1, round(frame.height*scale))
            if scale < 1:
                foreground = cv2.resize(foreground, (width, height), interpolation=cv2.INTER_NEAREST)
            # BGRA: violet (distinct from the blue bed region), transparent background.
            rgba = np.empty((height, width, 4), dtype=np.uint8)
            rgba[:, :, :3] = (245, 110, 170)
            rgba[:, :, 3] = foreground * 105
            ok, encoded = cv2.imencode('.png', rgba)
            if ok:
                packet['candidates'].append({'candidate_id': candidate['candidate_id'],
                    'image': 'data:image/png;base64,' + base64.b64encode(encoded).decode('ascii')})
        except Exception:
            # Auxiliary display failures must not discard valid pose inference.
            continue
    return packet


def select_preview(packet, row, width, height):
    """Only show the current, unambiguous/selected person's matching mask."""
    if not isinstance(packet, dict) or row['processing_status'] != 'ok':
        return None
    if row.get('target_tracking', {}).get('status') == 'held':
        return None
    assessment = row['posture_assessment']
    if (assessment.get('stream_frame_id'), assessment.get('clock_id'), assessment.get('received_ns')) != (
            row['stream_frame_id'], row['clock_id'], row['received_ns']):
        return None
    candidate_id = assessment.get('candidate_id')
    if candidate_id is None:
        return None
    expected = (VERSION, row['stream_id'], row['stream_frame_id'], row['model_timestamp_ms'], width, height)
    actual = (packet.get('version'), packet.get('stream_id'), packet.get('stream_frame_id'),
              str(packet.get('model_timestamp_ms')), packet.get('width'), packet.get('height'))
    if actual != expected:
        return None
    matches = [c for c in packet.get('candidates', []) if c.get('candidate_id') == candidate_id]
    if len(matches) != 1 or not matches[0].get('image', '').startswith('data:image/png;base64,'):
        return None
    return {'version': VERSION, 'stream_frame_id': row['stream_frame_id'],
            'candidate_id': candidate_id, 'image': matches[0]['image']}
