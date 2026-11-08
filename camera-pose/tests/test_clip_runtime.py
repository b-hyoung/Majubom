"""Real Transformers graph/local reload with tiny RANDOM weights, not pretrained accuracy."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

RUNTIME=all(importlib.util.find_spec(x) for x in ('torch','transformers','PIL'))


@unittest.skipUnless(RUNTIME,'Optional CLIP dependencies are not installed')
class ClipRuntimeTests(unittest.TestCase):
    def test_local_graph_text_cached_image_once_and_no_gradients(self):
        import torch
        from transformers import CLIPModel,CLIPConfig,CLIPTextConfig,CLIPVisionConfig,CLIPTokenizer,CLIPImageProcessor,CLIPProcessor
        from transformers.models.clip.tokenization_clip import bytes_to_unicode
        from bed_pose.clip_posture import ClipRuntime,square_image,STATE_NAMES,DIRECTION_NAMES
        import json
        previous=torch.get_num_threads();torch.set_num_threads(1)
        self.addCleanup(lambda:torch.set_num_threads(previous))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);tokens=list(bytes_to_unicode().values())
            tokens=tokens+[x+'</w>' for x in tokens]+['<|startoftext|>','<|endoftext|>']
            (path/'vocab.json').write_text(json.dumps(dict(zip(tokens,range(len(tokens))))),encoding='utf-8')
            (path/'merges.txt').write_text('#version: 0.2\n',encoding='utf-8')
            tokenizer=CLIPTokenizer(vocab_file=str(path/'vocab.json'),merges_file=str(path/'merges.txt'))
            CLIPProcessor(tokenizer=tokenizer,image_processor=CLIPImageProcessor()).save_pretrained(path)
            config=CLIPConfig(text_config=CLIPTextConfig(vocab_size=len(tokens),hidden_size=16,intermediate_size=32,
                num_hidden_layers=1,num_attention_heads=2,bos_token_id=512,eos_token_id=513).to_dict(),
                vision_config=CLIPVisionConfig(hidden_size=16,intermediate_size=32,num_hidden_layers=1,
                num_attention_heads=2,image_size=224,patch_size=32).to_dict(),projection_dim=8)
            torch.manual_seed(1);CLIPModel(config).save_pretrained(path)
            runtime=ClipRuntime(path,'cpu')
            self.assertIsNone(runtime.model.text_model)
            self.assertIsNone(runtime.model.text_projection)
            reference=CLIPModel.from_pretrained(path,local_files_only=True).float().eval()
            self.assertLess(sum(p.numel() for p in runtime.model.parameters()),sum(p.numel() for p in reference.parameters()))
            pixels=runtime.processor.image_processor(images=square_image(np.zeros((96,160,3),np.uint8)),
                do_resize=False,do_center_crop=False,return_tensors='pt')['pixel_values']
            with torch.inference_mode():
                expected=reference.get_image_features(pixel_values=pixels)
                expected=expected/expected.norm(dim=-1,keepdim=True)
                expected=(expected@runtime.text_features.T)[0].tolist()
            actual,_=runtime.similarities(square_image(np.zeros((96,160,3),np.uint8)))
            self.assertEqual(list(actual.values()),expected)
            batch=runtime.similarities_many([square_image(np.zeros((96,160,3),np.uint8))]*2)
            for values,_ in batch:
                np.testing.assert_allclose(list(values.values()),list(actual.values()),rtol=1e-6,atol=1e-6)
            self.assertEqual(runtime.last_metrics['batch_size'],2)
            self.assertGreaterEqual(runtime.last_metrics['preprocess_ms'],0)
            self.assertEqual(runtime.execution_info['score_dtype'],'float32')
            from bed_pose.clip_posture import policy,validate_policy
            with self.assertRaises(ValueError):ClipRuntime(path,'cpu','amp-fp16')
            if torch.cuda.is_available():
                gpu=ClipRuntime(path,'cuda','amp-fp16')
                try:
                    gpu_values,_=gpu.similarities(square_image(np.zeros((96,160,3),np.uint8)))
                    np.testing.assert_allclose(list(gpu_values.values()),list(actual.values()),rtol=2e-3,atol=2e-3)
                    self.assertEqual(gpu.last_metrics['resources']['scope'],'process_cuda_allocator')
                finally:gpu.close()
            del reference
            if torch.backends.mps.is_available():
                apple=ClipRuntime(path,'mps')
                try:
                    apple_scores,_=apple.similarities(square_image(np.zeros((96,160,3),np.uint8)))
                    np.testing.assert_allclose(list(apple_scores.values()),list(actual.values()),rtol=1e-4,atol=1e-4)
                finally:apple.close()
            else:
                with self.assertRaisesRegex(ValueError,'MPS'):ClipRuntime(path,'mps')

            with (patch.object(runtime.model,'get_text_features',wraps=runtime.model.get_text_features) as text,
                  patch.object(runtime.model,'get_image_features',wraps=runtime.model.get_image_features) as image):
                rgb=np.zeros((96,160,3),np.uint8)
                a,scale=runtime.similarities(square_image(rgb));b,_=runtime.similarities(square_image(rgb))
                self.assertEqual(a,b);self.assertEqual(set(a),set(STATE_NAMES+DIRECTION_NAMES));self.assertGreater(scale,0)
                self.assertEqual(text.call_count,0);self.assertEqual(image.call_count,2)
                self.assertTrue(all(p.grad is None for p in runtime.model.parameters()))
            runtime.close()
            from bed_pose.clip_pool import acquire
            from concurrent.futures import ThreadPoolExecutor
            import threading
            a=acquire(('tiny-local-graph',),lambda:ClipRuntime(path,'cpu'),batch_size=2,batch_wait_ms=10)
            b=acquire(('tiny-local-graph',),lambda:self.fail('duplicate model'),batch_size=2,batch_wait_ms=10)
            barrier=threading.Barrier(2)
            def shared_call(lease):
                barrier.wait()
                return lease.similarities(square_image(np.zeros((96,160,3),np.uint8)))
            try:
                with ThreadPoolExecutor(2) as executor:
                    jobs=[executor.submit(shared_call,lease) for lease in (a,b)]
                    for job in jobs:
                        values,_=job.result(timeout=10)
                        np.testing.assert_allclose(list(values.values()),list(actual.values()),rtol=1e-6,atol=1e-6)
                self.assertIs(a.entry,b.entry)
            finally:a.close();b.close()


if __name__=='__main__':unittest.main()
