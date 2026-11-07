"""Precision policy and local comparison contracts; not pretrained accuracy."""
import importlib.util
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from bed_pose.clip_posture import policy,validate_policy,decide
from test_clip_posture import configuration,source_assets
from bed_pose.clip_assets import setup_assets


class OptimizationTests(unittest.TestCase):
    def test_amp_profile_is_explicit_and_decisions_replay_in_float_scores(self):
        base=configuration();amp=dict(base,**policy(device='cuda',precision='amp-fp16'))
        validate_policy(base);validate_policy(amp)
        self.assertEqual(base['version'],'clip-posture-0.2');self.assertEqual(amp['version'],'clip-posture-0.3')
        scores={k:0. for k in ('lying','sitting','standing','other','supine','side','prone')}
        scores.update(lying=.6,supine=.6)
        for cfg in (base,amp):
            self.assertEqual(decide(scores,10,cfg)['lying_direction'],'supine')
        for device in ('cpu','mps'):
            with self.assertRaises(ValueError):policy(device=device,precision='amp-fp16')
        bad=dict(amp,dtype='float32')
        with self.assertRaises(ValueError):validate_policy(bad)

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'Pillow missing')
    def test_benchmark_keeps_input_pairing_and_reports_changes_not_accuracy(self):
        from PIL import Image
        from bed_pose.clip_benchmark import run
        class Runtime:
            instances=[]
            def __init__(self,model,device,precision='fp32'):
                self.precision=precision;self.execution_info={'device':device};self.last_metrics=None;self.closed=False
                self.instances.append(self)
            def similarities_many(self,images):
                self.last_metrics={'batch_size':len(images),'total_ms':1}
                results=[]
                for image in images:
                    scores={k:0. for k in ('lying','sitting','standing','other','supine','side','prone')}
                    scores['lying']=.6;scores['supine']=.6
                    if self.precision!='fp32':scores['side']=.9
                    results.append((scores,10.))
                return results
            def close(self):self.closed=True
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);setup_assets(root/'model',source=source_assets(root/'source'))
            inputs=[]
            for i in range(2):
                path=root/f'{i}.png';Image.fromarray(np.full((20,30,3),i*255,np.uint8)).save(path);inputs.append(path)
            with patch('bed_pose.clip_benchmark.ClipRuntime',Runtime),patch('importlib.metadata.version',return_value='stub'):
                report=run(root/'model',inputs,device='cuda',precision='amp-fp16',batch_size=2,iterations=2)
            self.assertEqual(report['comparison_count'],4)
            self.assertEqual(report['changed_state_or_direction_count'],4)
            self.assertFalse(report['accuracy_validated']);self.assertEqual(len(report['inputs']),2)
            self.assertTrue(all(r.closed for r in Runtime.instances))
            self.assertEqual(report['candidate']['batch_size'],2)
            self.assertGreater(report['max_cosine_absolute_delta'],0)
