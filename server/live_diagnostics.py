"""Best-effort latest-only forwarding to the raw dashboard. No files or database."""
import json
import threading
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
