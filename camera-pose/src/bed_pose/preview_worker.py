"""One encoder and one replaceable pending job per input; no frame backlog."""
import threading

class PreviewWorker:
    def __init__(self,encode,deliver):
        self.encode,self.deliver=encode,deliver
        self.condition=threading.Condition();self.pending=None;self.stopped=False
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()

    def submit(self,job):
        with self.condition:
            if not self.stopped:
                self.pending=job
                self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopped=True;self.pending=None;self.condition.notify()

    def run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.stopped or self.pending is not None)
                if self.stopped:return
                job=self.pending;self.pending=None
            try:
                result=self.encode(job['frame']['rgb'],job['quality']);error=False
            except Exception:
                result=(None,None);error=True
            self.deliver(job,result,error)
