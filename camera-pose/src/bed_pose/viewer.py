"""Portable Korean frame viewer, with no server, CDN or external data requests."""
from importlib.resources import files
import base64
from .io_utils import json_text


def viewer_html(manifest, source, catalog, svg_frames):
    if not catalog or len(catalog) != len(svg_frames):
        raise ValueError('Viewer requires one SVG per catalog frame')
    payload = {'run_id': manifest['run_id'], 'run_status': manifest['run_status'],
               'execution_kind': manifest['execution_kind'],
               'total_source_frames': manifest.get('expected_frames', len(catalog)),
               'width': source['canonical_width'], 'height': source['canonical_height'],
               'frames': [dict(frame, svg=base64.b64encode(svg.encode('utf-8')).decode('ascii'))
                          for frame, svg in zip(catalog, svg_frames)]}
    # Script data must never close its script element, even for arbitrary IDs.
    data = json_text(payload).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = files('bed_pose').joinpath('templates/viewer.html').read_text(encoding='utf-8')
    synthetic = manifest['execution_kind'] == 'synthetic'
    badge = '합성 데이터 · 실제 인식 아님' if synthetic else '모델 출력 · 정답 미확인'
    context = ('생성 영상에 합성 관절을 표시한 예시입니다. 실제 사람의 자세 인식 결과가 아닙니다.' if synthetic else
               '관절 출력은 자세 판정이나 정확도 검증 결과가 아닙니다. 후보 ID는 프레임 안에서만 유효합니다.')
    # Replace template-only markers before inserting arbitrary data.
    return (template.replace('__FIRST_FRAME__', payload['frames'][0]['svg'])
            .replace('__DATA_BADGE__', badge).replace('__CONTEXT_NOTE__', context)
            .replace('__VIEWER_DATA__', data))
