"""Deterministic concurrency/resource ownership, not hardware speed tests."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from bed_pose.clip_pool import acquire, _ENTRIES, validate_options


class Runtime:
    def __init__(self):
        self.execution_info={'device':'cpu','precision':'fp32'}
        self.calls=[];self.closed=False;self.active=0;self.peak=0
    def similarities_many(self,images):
        self.active+=1;self.peak=max(self.peak,self.active)
        try:
            self.calls.append(tuple(images));time.sleep(.003)
            if 'bad' in images:raise ValueError('inference failed')
            self.last_metrics={'batch_size':len(images),'total_ms':3}
            return [(image,10) for image in images]
        finally:self.active-=1
    def similarities(self,image):return self.similarities_many([image])[0]
    def close(self):self.closed=True


class PoolTests(unittest.TestCase):
    def test_share_ownership_isolation_and_single_input_has_no_batch_wait(self):
        runtime=Runtime();created=[]
        def factory():created.append(True);return runtime
        a=acquire(('a',),factory,batch_size=2,batch_wait_ms=10)
        b=acquire(('a',),factory,batch_size=2,batch_wait_ms=10)
        try:
            self.assertEqual(len(created),1)
            self.assertEqual(a.similarities('first'),('first',10))
            a.close();self.assertFalse(runtime.closed)
            self.assertEqual(b.similarities('second'),('second',10))
            self.assertEqual(b.last_metrics['batch_size'],1)
            self.assertEqual(a.last_metrics['batch_size'],1)
            with self.assertRaises(RuntimeError):a.similarities('closed')
        finally:a.close();b.close()
        self.assertTrue(runtime.closed);self.assertNotIn(('a',),_ENTRIES)

    def test_ready_requests_batch_results_keep_order_and_errors_recover(self):
        runtime=Runtime();barrier=threading.Barrier(2)
        a=acquire(('batch',),lambda:runtime,batch_size=2,batch_wait_ms=10)
        b=acquire(('batch',),lambda:self.fail('loaded twice'),batch_size=2,batch_wait_ms=10)
        def run(lease,value):barrier.wait();return lease.similarities(value)
        try:
            with ThreadPoolExecutor(2) as pool:
                fa=pool.submit(run,a,'camera-a');fb=pool.submit(run,b,'camera-b')
                self.assertEqual(fa.result(timeout=2),('camera-a',10));self.assertEqual(fb.result(timeout=2),('camera-b',10))
            self.assertEqual(len(runtime.calls),1);self.assertEqual(a.last_metrics['batch_size'],2)
            with self.assertRaises(ValueError):a.similarities('bad')
            self.assertEqual(b.similarities('after-error'),('after-error',10))
            self.assertEqual(runtime.peak,1)
            other=Runtime();c=acquire(('different-precision',),lambda:other)
            try:self.assertIsNot(c.entry,a.entry)
            finally:c.close()
        finally:a.close();b.close()

    def test_bounds_and_failed_load_do_not_leak(self):
        for size,wait in [(0,2),(5,2),(True,2),(2,float('nan')),(2,11)]:
            with self.assertRaises(ValueError):validate_options(size,wait)
        with self.assertRaisesRegex(RuntimeError,'load'):
            acquire(('failed',),lambda:(_ for _ in ()).throw(RuntimeError('load')))
        self.assertNotIn(('failed',),_ENTRIES)
