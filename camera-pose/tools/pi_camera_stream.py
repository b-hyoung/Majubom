"""Serve one local USB camera as token-protected MJPEG; no recording."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading


class Camera:
    def __init__(self, device, width, height, fps):
        self.condition = threading.Condition()
        self.latest = None
        self.sequence = 0
        self.error = None
        self.process = subprocess.Popen([
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'v4l2',
            '-input_format', 'yuyv422', '-video_size', f'{width}x{height}', '-framerate', str(fps),
            '-i', device, '-an', '-c:v', 'mjpeg', '-q:v', '5', '-f', 'image2pipe', 'pipe:1',
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._capture, daemon=True).start()

    def _capture(self):
        buffer = bytearray()
        try:
            while True:
                chunk = self.process.stdout.read1(65536)
                if not chunk: raise RuntimeError('camera encoder stopped')
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b'\xff\xd8')
                    if start < 0:
                        if len(buffer) > 2_000_000: buffer.clear()
                        break
                    end = buffer.find(b'\xff\xd9', start + 2)
                    if end < 0:
                        if len(buffer) > 2_000_000: buffer.clear()
                        break
                    image = bytes(buffer[start:end + 2]); del buffer[:end + 2]
                    with self.condition:
                        self.latest = image; self.sequence += 1; self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = str(exc); self.condition.notify_all()

    def close(self):
        self.process.terminate()
        try: self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait()
        self.process.stdout.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', default='/dev/video0')
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--fps', type=int, default=15)
    args = parser.parse_args()
    with open(args.token_file, encoding='ascii') as f: token = f.read().strip()
    if len(token) < 32 or not token.isalnum(): raise ValueError('Invalid camera access token')
    camera = Camera(args.device, 640, 480, args.fps)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if not hmac.compare_digest(self.path, '/' + token):
                self.send_error(404); return
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            sequence = 0
            try:
                while True:
                    with camera.condition:
                        camera.condition.wait_for(lambda: camera.sequence > sequence or camera.error, timeout=10)
                        if camera.error or camera.sequence <= sequence: break
                        image, sequence = camera.latest, camera.sequence
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(image)).encode() + b'\r\n\r\n' + image + b'\r\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError): pass

    try:
        server = ThreadingHTTPServer((args.bind, args.port), Handler)
        server.daemon_threads = True
        try: server.serve_forever()
        finally: server.server_close()
    finally: camera.close()


if __name__ == '__main__': main()
