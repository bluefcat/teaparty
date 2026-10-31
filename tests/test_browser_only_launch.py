"""Browser-only launch-mode regression checks (no server is started)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from image_library import web_ui

ROOT = Path(__file__).resolve().parent.parent


class BrowserOnlyLaunchTests(unittest.TestCase):
    def test_default_launches_browser(self):
        with patch.object(sys, 'argv', ['main.py']), patch.object(web_ui, 'run_app') as run:
            web_ui.main()
            run.assert_called_once_with(port=0, open_browser=True)

    def test_explicit_browser_launches_browser(self):
        with patch.object(sys, 'argv', ['main.py', '--browser']), patch.object(web_ui, 'run_app') as run:
            web_ui.main()
            run.assert_called_once_with(port=0, open_browser=True)

    def test_no_browser_launches_server_only(self):
        with patch.object(sys, 'argv', ['main.py', '--no-browser', '--port', '8999']), \
             patch.object(web_ui, 'run_app') as run:
            web_ui.main()
            run.assert_called_once_with(port=8999, open_browser=False)

    def test_legacy_options_rejected(self):
        for mode in ('--classic', '--desktop'):
            with self.subTest(mode=mode), patch.object(sys, 'argv', ['main.py', mode]):
                with self.assertRaises(SystemExit) as caught:
                    web_ui.main()
                self.assertEqual(caught.exception.code, 2)

    def test_old_ui_and_webview_dependency_removed(self):
        for filename in ('image_library/ui.py', 'image_library/widgets.py', 'image_library/color_picker.py'):
            self.assertFalse((ROOT / filename).exists(), filename)
        requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8').lower()
        self.assertNotIn('pywebview', requirements)


if __name__ == '__main__':
    unittest.main()
