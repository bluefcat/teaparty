"""Simple first-run source checks; no downloads or GUI rendering."""
from __future__ import annotations

from pathlib import Path
import unittest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent


class FirstRunFilesTests(unittest.TestCase):
    def test_all_required_files_exist(self):
        for name in ('README.md', 'requirements.txt', 'teaparty.bat',
                     'setup_teaparty.py', 'main.py', 'image_library/web_ui.py'):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_unified_dependencies_parse(self):
        entries = [Requirement(line.strip()) for line in (ROOT / 'requirements.txt').read_text(encoding='utf8').splitlines()
                   if line.strip() and not line.strip().startswith('#')]
        packages = {item.name.lower() for item in entries}
        self.assertNotIn('pywebview', packages)
        for required in ('torch', 'torchvision', 'pillow', 'numpy', 'timm', 'einops',
                         'tqdm', 'send2trash'):
            self.assertIn(required, packages)

    def test_bat_calls_virtual_environment_setup(self):
        script = (ROOT / 'teaparty.bat').read_text(encoding='ascii')
        self.assertIn('-m venv', script)
        self.assertIn('setup_teaparty.py', script)
        self.assertIn('main.py', script)
        self.assertIn('--browser', script)
        self.assertNotIn('--desktop', script)
        self.assertNotIn('--classic', script)


if __name__ == '__main__':
    unittest.main()
