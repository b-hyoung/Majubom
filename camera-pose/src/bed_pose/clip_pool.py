"""Reference-counted CLIP execution with bounded, ordered per-input requests.

No cross-input posture state is shared. Batches contain only ready requests;
waiting is bounded and never waits for a particular camera or future frame.
"""
from collections import deque
from concurrent.futures import Future
from copy import deepcopy
import math
import threading
import time

_LOCK=threading.Lock()
_ENTRIES={}


def validate_options(batch_size, batch_wait_ms):
    if type(batch_size) is not int or not 1<=batch_size<=4:
        raise ValueError('CLIP batch size must be an integer in [1,4]')
    if type(batch_wait_ms) not in (int,float) or not math.isfinite(batch_wait_ms) or not 0<=batch_wait_ms<=10:
        raise ValueError('CLIP batch wait must be in [0,10] ms')


class _Entry:
    def __init__(self,runtime,batch_size,wait):
        self.runtime=runtime;self.batch_size=batch_size;self.wait=wait/1000
        self.condition=threading.Condition();self.pending=deque();self.references=0;self.stopped=False
        self.close_error=None
        self.thread=threading.Thread(target=self.run,name='bedpose-shared-clip',daemon=True)
        self.thread.start()

    def run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda:self.stopped or self.pending)
                    if self.stopped:return
                    deadline=time.monotonic()+self.wait
                    while len(self.pending)<min(self.batch_size,self.references) and not self.stopped:
                        remaining=deadline-time.monotonic()
                        if remaining<=0:break
                        self.condition.wait(remaining)
                    if self.stopped:return
                    jobs=[self.pending.popleft() for _ in range(min(self.batch_size,len(self.pending)))]
                started=time.perf_counter_ns()
                try:
                    if len(jobs)==1:
                        values=[self.runtime.similarities(jobs[0][0])]
                    else:
                        values=self.runtime.similarities_many([job[0] for job in jobs])
                    if len(values)!=len(jobs):raise ValueError('CLIP batch result count mismatch')
                    metrics=deepcopy(getattr(self.runtime,'last_metrics',None)) or {}
                    for job,value in zip(jobs,values):
                        job[1].set_result((value,dict(metrics,queue_wait_ms=(started-job[2])/1e6,
                                                      batch_size=len(jobs))))
                except Exception as exc:
                    for _,future,_ in jobs:future.set_exception(exc)
        finally:
            with self.condition:
                pending=list(self.pending);self.pending.clear()
            for _,future,_ in pending:future.set_exception(RuntimeError('CLIP runtime closed'))
            try:self.runtime.close()
            except Exception as exc:self.close_error=exc


class RuntimeLease:
    def __init__(self,key,entry):
        self.key=key;self.entry=entry;self.closed=False;self.lock=threading.Lock();self.last_metrics=None
        self.execution_info=dict(getattr(entry.runtime,'execution_info',{}),shared_model=True,
            maximum_batch_size=entry.batch_size,batch_wait_limit_ms=entry.wait*1000)

    def similarities(self,image):
        with self.lock:
            if self.closed:raise RuntimeError('CLIP runtime lease is closed')
            future=Future()
            with self.entry.condition:
                self.entry.pending.append((image,future,time.perf_counter_ns()))
                self.entry.condition.notify()
            value,self.last_metrics=future.result()
            return value

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            with _LOCK:
                self.entry.references-=1
                if self.entry.references==0:
                    _ENTRIES.pop(self.key,None)
                    with self.entry.condition:
                        self.entry.stopped=True;self.entry.condition.notify_all()
            if self.entry.references==0:
                self.entry.thread.join()
                if self.entry.close_error is not None:raise self.entry.close_error


def acquire(key,factory,*,batch_size=1,batch_wait_ms=2.):
    validate_options(batch_size,batch_wait_ms)
    with _LOCK:
        entry=_ENTRIES.get(key)
        if entry is None:
            entry=_Entry(factory(),batch_size,batch_wait_ms);_ENTRIES[key]=entry
        elif entry.batch_size!=batch_size or entry.wait!=batch_wait_ms/1000:
            raise ValueError('Shared CLIP runtime options differ')
        entry.references+=1
        with entry.condition:entry.condition.notify_all()
        return RuntimeLease(key,entry)
