import contextlib
import threading
import types
import unittest
import warnings
from unittest.mock import MagicMock, patch

from PIL import Image

from image_gen_workbench.engine import QwenEngine
from image_gen_workbench.jobs import Cancelled, validate_spec


class EngineTests(unittest.TestCase):
    def test_model_loads_once_and_forwards_text_edit_and_cpu_seed(self):
        torch = MagicMock()
        torch.backends.mps.is_available.return_value = True
        torch.inference_mode.side_effect = contextlib.nullcontext
        pipeline = MagicMock()
        image = Image.new('RGB', (256, 256))
        pipeline.return_value.images = [image]
        pipeline_class = MagicMock()
        pipeline_class.from_pretrained.return_value = pipeline
        modules = {'torch': torch, 'diffusers': types.SimpleNamespace(QwenImage21Pipeline=pipeline_class)}
        spec = validate_spec({'prompt': 'a garden', 'seed': 123})
        progress = MagicMock()
        with patch.dict('sys.modules', modules):
            engine = QwenEngine(offline=True)
            self.assertIs(engine.generate(spec, None, progress, threading.Event()), image)
            self.assertNotIn('image', pipeline.call_args.kwargs)
            self.assertIs(engine.generate(spec, image, progress, threading.Event()), image)
        pipeline_class.from_pretrained.assert_called_once_with('Qwen/Qwen-Image-2.1', dtype=torch.bfloat16,
                                                             device_map=None, local_files_only=True)
        pipeline.to.assert_called_once_with('mps')
        torch.Generator.assert_called_with(device='cpu')
        torch.Generator.return_value.manual_seed.assert_called_with(123)
        kwargs = pipeline.call_args.kwargs
        self.assertIs(kwargs['image'], image)
        self.assertEqual(kwargs['negative_prompt'], '')
        self.assertEqual(kwargs['true_cfg_scale'], 1)
        self.assertEqual(kwargs['num_inference_steps'], 12)
        self.assertNotIn('strength', kwargs)
        latent = {'latents': object()}
        self.assertIs(kwargs['callback_on_step_end'](pipeline, 3, None, latent), latent)
        progress.assert_called_with(4, 12)

    def test_cancellation_after_loading_prevents_inference(self):
        engine = QwenEngine()
        engine.pipe = MagicMock()
        cancelled = threading.Event()
        cancelled.set()
        with patch.dict('sys.modules', {'torch': MagicMock()}), self.assertRaises(Cancelled):
            engine.generate(validate_spec({'prompt': 'x'}), None, MagicMock(), cancelled)
        engine.pipe.assert_not_called()

    def test_explicit_precision_is_supported(self):
        torch = MagicMock()
        pipeline = MagicMock()
        with patch.dict('sys.modules', {'torch': torch, 'diffusers': types.SimpleNamespace(QwenImage21Pipeline=pipeline)}):
            engine = QwenEngine(device='mps', dtype='float16')
            engine.load()
        self.assertEqual(pipeline.from_pretrained.call_args.kwargs['dtype'], torch.float16)

    def test_invalid_numeric_output_is_not_reported_as_success(self):
        torch = MagicMock()
        torch.inference_mode.side_effect = contextlib.nullcontext
        engine = QwenEngine()
        engine.pipe = MagicMock(side_effect=lambda **kwargs: warnings.warn('invalid value encountered in cast', RuntimeWarning))
        with patch.dict('sys.modules', {'torch': torch}), self.assertRaisesRegex(RuntimeError, 'Image conversion'):
            engine.generate(validate_spec({'prompt': 'x'}), None, MagicMock(), threading.Event())
