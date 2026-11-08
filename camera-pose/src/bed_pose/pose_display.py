"""Full native landmarks for display; common analysis coordinates stay stable."""
import math
from .pose.mediapipe import MP33

MP33_EDGES = ((0,1),(1,2),(2,3),(3,7),(0,4),(4,5),(5,6),(6,8),
 (9,10),(11,12),(11,13),(13,15),(15,17),(15,19),(15,21),(17,19),
 (12,14),(14,16),(16,18),(16,20),(16,22),(18,20),
 (11,23),(12,24),(23,24),(23,25),(24,26),(25,27),(26,28),
 (27,29),(28,30),(29,31),(30,32),(27,31),(28,32))


def display_people(row, width, height):
    """Derive MP33 from the same recorded frame, without modifying analysis data."""
    raw = {p['candidate_id']: p for p in row.get('raw_candidates') or []}
    people = []
    for person in row.get('mapped_people') or []:
        native = raw.get(person['candidate_id'], {}).get('sdk_landmarks', {}).get('image')
        if not isinstance(native, list) or len(native) != 33:
            people.append(person)
            continue
        joints = []
        for index, point in enumerate(native):
            x, y = point.get('x'), point.get('y')
            finite = all(type(v) in (int, float) and math.isfinite(v) for v in (x, y))
            joints.append({'source_index': index, 'name': MP33[index],
                'x_px': x * width if finite else None, 'y_px': y * height if finite else None,
                'availability': 'predicted' if finite else 'missing',
                'visibility': point.get('visibility'), 'presence': point.get('presence')})
        people.append(dict(person, joints=joints, joint_definition_id='mediapipe33',
                           edges=MP33_EDGES))
    return people
