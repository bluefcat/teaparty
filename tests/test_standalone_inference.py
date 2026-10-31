"""Regression tests for inference without ML-Danbooru source directory."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
from PIL import Image

from image_library.checkpoints import DEFAULT_CHECKPOINT, resolve_checkpoint
from image_library.model import DanbooruTagger


class CheckpointStandaloneTests(unittest.TestCase):
    def test_model_code_and_tag_vocabulary_are_bundled(self):
        import image_library.inference as bundled
        root = Path(bundled.__file__).parent
        tags = json.loads((root / 'class.json').read_text(encoding='utf-8'))
        self.assertEqual(len(tags), 12547)
        self.assertEqual(tags['0'], '1girl')
        self.assertTrue((root / 'models/caformer/metaformer_baselines.py').is_file())
        self.assertTrue((root / 'models/tresnet/tresnet_f.py').is_file())

    def test_new_and_legacy_checkpoint_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = root / 'sample.ckpt'
            expected.write_bytes(b'sample')
            from image_library import checkpoints
            with patch.object(checkpoints, 'CHECKPOINT_DIR', root):
                self.assertEqual(resolve_checkpoint(expected), expected)
                self.assertEqual(resolve_checkpoint(r'C:\\old\\ML-Danbooru\\sample.ckpt'), expected)
                self.assertEqual(resolve_checkpoint(root / 'ML-Danbooru' / 'sample.ckpt'), expected)
                self.assertEqual(resolve_checkpoint('unrelated_missing.ckpt'), Path('unrelated_missing.ckpt'))

    def test_strict_loading_without_ml_danbooru_repository(self):
        """Use a tiny synthetic weight file to exercise adapter loading (not real architecture)."""
        from types import ModuleType
        class TinyModel(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.bias = torch.nn.Parameter(torch.ones(1))

            def forward(self, x):
                return self.bias.expand(x.shape[0], 12547)

        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'tiny.ckpt'
            torch.save({'model': TinyModel().state_dict()}, file)
            fake_module = ModuleType('image_library.inference.models')
            fake_module.create_model = lambda args, load_head: TinyModel()
            with patch.dict(sys.modules, {'image_library.inference.models': fake_module}):
                model = DanbooruTagger(file, device='cpu', batch_size=1)
                result = model.predict(Image.new('RGB', (64, 64), 'white'))
                self.assertTrue(model.model is not None)
                self.assertEqual(len(model.class_map), 12547)
                self.assertTrue(len(result) > 1)

    def test_deployment_import_without_legacy_directory(self):
        """Copy only application sources to a new location; leave legacy repo absent."""
        package = Path(__file__).resolve().parents[1] / 'image_library'
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary) / 'image_library'
            shutil.copytree(package, dest, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            # The project starts without installing/loading ML-Danbooru's Python sources.
            proc = subprocess.run(
                [sys.executable, '-c',
                 "from image_library.checkpoints import DEFAULT_CHECKPOINT; "
                 "from image_library.model import DanbooruTagger; "
                 "from image_library.web_ui import MORIService; "
                 "from image_library.core import Library; "
                 "s=MORIService(Library()); "
                 "assert 'scan' in s.get_state(); print('STANDALONE_OK')"],
                cwd=temporary, capture_output=True, text=True, timeout=45,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('STANDALONE_OK', proc.stdout)


if __name__ == '__main__':
    unittest.main()
