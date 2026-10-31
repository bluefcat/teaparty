"""Offline installer flow tests; no packages are downloaded."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

INSTALLER = Path(__file__).resolve().parent.parent / 'setup_teaparty.py'
spec = importlib.util.spec_from_file_location('teaparty_setup_test_subject', INSTALLER)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class SetupFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.stamp = Path(self.temporary.name) / 'status.json'
        self.patch_stamp = patch.object(subject, 'STAMP', self.stamp)
        self.patch_stamp.start()
        self.addCleanup(self.patch_stamp.stop)
        self.patch_venv = patch.object(subject.sys, 'prefix', str(self.temporary.name))
        self.patch_venv.start()
        self.addCleanup(self.patch_venv.stop)

    def test_dry_run_never_installs(self):
        with patch.object(sys, 'argv', ['setup_teaparty.py', '--dry-run']), \
             patch.object(subject, 'pip') as pip, \
             patch.object(subject, 'has_nvidia', return_value=False):
            self.assertEqual(subject.main(), 0)
            pip.assert_not_called()

    def test_cuda_path_writes_ready_stamp(self):
        with patch.object(sys, 'argv', ['setup_teaparty.py']), \
             patch.object(subject, 'pip') as pip, \
             patch.object(subject, 'has_nvidia', return_value=True), \
             patch.object(subject, 'installed_cuda_works', return_value=True), \
             patch.object(subject, 'check_imports'):
            self.assertEqual(subject.main(), 0)
            calls = [str(c) for c in pip.call_args_list]
            self.assertTrue(any('cu128' in c for c in calls))
            self.assertFalse(any('cpu' in c.lower() and 'index-url' in c for c in calls))
            self.assertEqual(json.loads(self.stamp.read_text())['backend'], 'cuda')

    def test_cuda_failure_falls_back_to_cpu(self):
        commands = []
        def fake_pip(*args):
            commands.append(args)
            if subject.CUDA_INDEX in args:
                raise subprocess.CalledProcessError(1, args)
        with patch.object(sys, 'argv', ['setup_teaparty.py']), \
             patch.object(subject, 'pip', side_effect=fake_pip), \
             patch.object(subject, 'has_nvidia', return_value=True), \
             patch.object(subject, 'check_imports'):
            self.assertEqual(subject.main(), 0)
            self.assertTrue(any(subject.CPU_INDEX in c for c in commands))
            self.assertEqual(json.loads(self.stamp.read_text())['backend'], 'cpu')

    def test_ready_stamp_skips_install(self):
        subject.write_stamp('cpu')
        with patch.object(sys, 'argv', ['setup_teaparty.py']), \
             patch.object(subject, 'pip') as pip, \
             patch.object(subject, 'check_imports'):
            self.assertEqual(subject.main(), 0)
            pip.assert_not_called()


if __name__ == '__main__':
    unittest.main()
