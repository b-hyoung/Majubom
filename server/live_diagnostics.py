"""Forwarding to the raw dashboard (:5012): latest-only snapshots and the laptop frame stream. No files or database."""
import collections
import json
import threading
import time
import urllib.request


class LatestPublisher:
    def __init__(self, url='http://127.0.0.1:5012/api/ingest'):
        self.url = url
        self.condition = threading.Condition()
        self.pending = {}
        self.worker = None

    def publish(self, kind, snapshot):
        if kind not in ('tof1', 'tof2', 'mmwave'):
            raise ValueError('unknown sensor')
        with self.condition:
            self.pending[kind] = snapshot
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: bool(self.pending))
                batch = self.pending
                self.pending = {}
            for kind, snapshot in batch.items():
                try:
                    body = json.dumps({'kind': kind, 'snapshot': snapshot}, allow_nan=False).encode()
                    request = urllib.request.Request(self.url, data=body, headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=0.3) as response:
                        response.read(256)
                except Exception:
                    # Drop stale frames; diagnostic outages never hold up normal ingestion.
                    pass


publisher = LatestPublisher()


class FrameStreamPublisher:
    """Every frame (not latest-only) to the :5012 laptop stream. RAM queue only; no files or database.

    2026-10-31: 노트북 학습 데이터 수집용. 구독자(노트북)가 없으면 1초에 한 번만 보내 확인하고 나머지는 버린다.
    큐가 넘치면 오래된 프레임부터 버린다(운영 수집을 절대 기다리게 하지 않는다)."""

    def __init__(self, url='http://127.0.0.1:5012/api/stream-ingest', max_queue=600, batch=100):
        self.url = url
        self.condition = threading.Condition()
        self.queue = collections.deque(maxlen=max_queue)
        self.batch = batch
        self.subscribers = 0
        self.last_probe = 0.0
        self.worker = None

    def publish(self, kind, record):
        if kind not in ('tof1', 'tof2', 'mmwave'):
            raise ValueError('unknown sensor')
        now = time.monotonic()
        with self.condition:
            if self.subscribers == 0:
                if now - self.last_probe < 1.0:
                    return
                self.last_probe = now
            self.queue.append({'kind': kind, 'pi_rx_unix': time.time(), **record})
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: bool(self.queue))
                batch = [self.queue.popleft() for _ in range(min(self.batch, len(self.queue)))]
            try:
                body = json.dumps({'frames': batch}, allow_nan=False).encode()
                request = urllib.request.Request(self.url, data=body, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=0.5) as response:
                    subscribers = json.loads(response.read(256)).get('subscribers', 0)
            except Exception:
                subscribers = 0
            with self.condition:
                self.subscribers = subscribers


stream_publisher = FrameStreamPublisher()
