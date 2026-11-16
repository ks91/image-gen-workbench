import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin


class InspectPngTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / '画像 sample.png'

    def write_png(self, generation=None):
        metadata = PngImagePlugin.PngInfo()
        if generation is not None:
            metadata.add_text('generation', generation)
        Image.new('RGB', (8, 8)).save(self.path, pnginfo=metadata)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'image_gen_workbench.inspect_png',
                               *map(str, args)], capture_output=True, text=True)

    def test_prints_unicode_generation_settings_without_modifying_file(self):
        spec = {'prompt': '赤い花', 'negative_prompt': '文字', 'seed': 42}
        self.write_png(json.dumps(spec, ensure_ascii=False))
        original = self.path.read_bytes()
        result = self.run_cli(self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), spec)
        self.assertIn('赤い花', result.stdout)
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_metadata_does_not_claim_all_prompts_are_absent(self):
        self.write_png()
        result = self.run_cli(self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No generation metadata', result.stdout)
        self.assertIn('Other metadata fields are not checked', result.stdout)

    def test_invalid_metadata_and_unreadable_files_have_friendly_errors(self):
        for value in ('not JSON', ''):
            self.write_png(value)
            result = self.run_cli(self.path)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Invalid generation metadata', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        for data in (None, b'not a PNG'):
            if data is None:
                self.path.unlink()
            else:
                self.path.write_bytes(data)
            result = self.run_cli(self.path)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('Traceback', result.stderr)
        Image.new('RGB', (8, 8)).save(self.path, 'JPEG')
        self.assertEqual(self.run_cli(self.path).returncode, 1)

    def test_help_needs_no_model_or_server(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PNG', result.stdout)
