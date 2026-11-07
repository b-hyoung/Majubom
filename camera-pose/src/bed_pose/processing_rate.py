"""Recent completed-frame throughput; never exposure or browser FPS."""
from collections import deque

class ProcessingRate:
    def __init__(self):self.samples=deque(maxlen=30)
    def reset(self):self.samples.clear()
    def observe(self,time_ns):
        if self.samples and time_ns<=self.samples[-1]:self.reset()
        self.samples.append(time_ns)
        if len(self.samples)<2:return None
        return (len(self.samples)-1)*1e9/(self.samples[-1]-self.samples[0])
