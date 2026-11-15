import time
import warnings

from PIL import Image, ImageDraw

from .jobs import Cancelled
from .i18n import GenerationError


class QwenEngine:
    name = 'Qwen/Qwen-Image-2.1'

    def __init__(self, model=None, device='auto', offline=False, dtype='auto'):
        self.name = model or self.name
        self.device = device
        self.offline = offline
        self.dtype = dtype
        self.pipe = None

    def load(self):
        if self.pipe is not None:
            return
        import torch
        from diffusers import QwenImage21Pipeline
        device = self.device
        if device == 'auto':
            device = 'mps' if torch.backends.mps.is_available() else 'cuda' if torch.cuda.is_available() else 'cpu'
        precision = self.dtype if self.dtype != 'auto' else ('float32' if device == 'cpu' else 'bfloat16')
        dtype = getattr(torch, precision)
        pipe = QwenImage21Pipeline.from_pretrained(
            self.name, dtype=dtype, device_map=None, local_files_only=self.offline)
        pipe.to(device)
        self.pipe = pipe

    def generate(self, spec, source, progress, cancelled):
        import torch
        self.load()
        if cancelled.is_set():
            raise Cancelled()

        def callback(pipe, step, timestep, kwargs):
            progress(step + 1, spec['steps'])
            return kwargs

        kwargs = dict(prompt=spec['prompt'], negative_prompt=spec['negative_prompt'],
                      true_cfg_scale=spec['cfg'], width=spec['width'], height=spec['height'],
                      num_inference_steps=spec['steps'],
                      generator=torch.Generator(device='cpu').manual_seed(spec['seed']),
                      callback_on_step_end=callback)
        if source is not None:
            kwargs['image'] = source
        with torch.inference_mode(), warnings.catch_warnings():
            warnings.filterwarnings('error', message='invalid value encountered in cast', category=RuntimeWarning)
            try:
                return self.pipe(**kwargs).images[0]
            except RuntimeWarning as exc:
                raise GenerationError('error_numeric') from exc


class DemoEngine:
    """Explicit UI smoke-test mode, never presented as model inference."""
    name = 'DEMO (no model)'

    def generate(self, spec, source, progress, cancelled):
        for i in range(spec['steps']):
            time.sleep(.04)
            progress(i + 1, spec['steps'])
        color = tuple((spec['seed'] >> shift) % 160 + 40 for shift in (0, 8, 16))
        image = Image.new('RGB', (spec['width'], spec['height']), color)
        if source:
            image.paste(source.resize(image.size))
        ImageDraw.Draw(image).text((24, 24), 'DEMO - no model inference', fill='white', font_size=22)
        return image
