"""Generate a short nonhuman, variable-interval video for pipeline checks."""
from fractions import Fraction
from pathlib import Path
import av
import numpy as np


def create_demo_video(path):
    path = Path(path)
    if path.exists():
        raise FileExistsError('Demo video already exists')
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('ffv1', rate=25)
        stream.width, stream.height = 96, 64
        stream.pix_fmt = 'bgr0'
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for index, pts in enumerate((0, 40, 100, 140, 220, 260)):
            pixels = np.zeros((64, 96, 3), dtype=np.uint8)
            pixels[:, :, index % 3] = 120
            pixels[10:25, 5 + index * 3:20 + index * 3] = [240, 80, 30]
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts, frame.time_base = pts, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path
