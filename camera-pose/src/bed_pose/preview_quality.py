"""Browser display encoding only; source/inference/recording remain unchanged."""
import base64

PROFILES = {'standard': {'max_side': 1280, 'jpeg_quality': 85},
            'high': {'max_side': 1920, 'jpeg_quality': 95}}
DEFAULT = 'high'


def encode_preview(rgb, quality=DEFAULT):
    import cv2
    if not isinstance(quality, str) or quality not in PROFILES:
        raise ValueError('표준 또는 고화질 표시를 선택하세요.')
    profile = PROFILES[quality]
    height, width = rgb.shape[:2]
    ratio = min(1, profile['max_side']/max(width, height))
    target = (max(1, round(width*ratio)), max(1, round(height*ratio)))
    preview = cv2.resize(rgb, target, interpolation=cv2.INTER_AREA) if ratio < 1 else rgb
    ok, encoded = cv2.imencode('.jpg', cv2.cvtColor(preview, cv2.COLOR_RGB2BGR),
                              [cv2.IMWRITE_JPEG_QUALITY, profile['jpeg_quality']])
    if not ok:
        raise ValueError('브라우저 미리보기 영상을 만들지 못했습니다.')
    return ('data:image/jpeg;base64,'+base64.b64encode(encoded).decode('ascii'),
            {'quality': quality, 'source_width': width, 'source_height': height,
             'width': target[0], 'height': target[1], 'jpeg_quality': profile['jpeg_quality'],
             'max_side': profile['max_side'], 'upscaled': False})
