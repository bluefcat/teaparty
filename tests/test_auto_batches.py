import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from image_library.batching import fastest_profile, memory_budget, next_probe
from image_library.core import Library, Scanner
from image_library.model import DanbooruTagger

GiB = 1024 ** 3


class OutOfMemory(RuntimeError):
    pass


class AutoBatchTests(unittest.TestCase):
    def tagger(self, gpu=True):
        tagger = DanbooruTagger(batch_size='auto')
        tagger.model = object()
        tagger.device = SimpleNamespace(type='cuda' if gpu else 'cpu')
        tagger.torch = SimpleNamespace(OutOfMemoryError=OutOfMemory, cuda=SimpleNamespace(
            empty_cache=lambda: None, memory_allocated=lambda _: int(.3 * GiB),
            memory_reserved=lambda _: int(.4 * GiB), mem_get_info=lambda _: (6 * GiB, 8 * GiB)))
        return tagger

    def test_memory_budget_reserves_space_for_other_apps(self):
        self.assertAlmostEqual(memory_budget(GiB, 2 * GiB, 5 * GiB, 8 * GiB) / GiB, 5.)
        self.assertLessEqual(memory_budget(GiB, GiB, 7 * GiB, 8 * GiB), 8 * GiB * .65)

    def test_memory_forecast_stops_before_a_batch_exceeds_budget(self):
        profiles = [{'count': 16, 'peak_bytes': 2.3 * GiB, 'rate': 10}]
        candidate = next_probe(profiles, 128, .3 * GiB, 5 * GiB)
        self.assertGreater(candidate, 16)
        self.assertLess(candidate, 32)
        self.assertLessEqual(.3 * GiB + candidate * (.125 * GiB) * 1.5, 5 * GiB)
        self.assertIsNone(next_probe(profiles, 16, .3 * GiB, 5 * GiB))

    def test_cpu_can_choose_one_image_when_larger_batches_are_slower(self):
        tagger = self.tagger(gpu=False)
        def profile(_, count):
            return {'count': count, 'peak_bytes': 0, 'rate': {1: 10, 2: 9.5, 4: 8}[count], 'seconds': 1}
        with patch.object(tagger, '_profile_batch', side_effect=profile):
            self.assertEqual(tagger.configure_batch(None, 100), 1)
        self.assertTrue(tagger.batch_configured)

    def test_gpu_calibration_grows_beyond_eight_and_uses_best_throughput(self):
        tagger = self.tagger()
        def profile(_, count):
            return {'count': count, 'peak_bytes': int((.3 + count * .125) * GiB),
                    'rate': count if count <= 32 else 31, 'seconds': 1}
        with patch.object(tagger, '_profile_batch', side_effect=profile):
            chosen = tagger.configure_batch(None, 100)
        self.assertGreater(chosen, 16)
        self.assertLess(chosen, 32)
        self.assertEqual(tagger.effective_batch_size, chosen)

    def test_oom_during_probe_keeps_last_successful_sizes(self):
        tagger = self.tagger()
        def profile(_, count):
            if count >= 16:
                raise OutOfMemory()
            return {'count': count, 'peak_bytes': int((.3 + count * .1) * GiB), 'rate': count, 'seconds': 1}
        with patch.object(tagger, '_profile_batch', side_effect=profile):
            self.assertEqual(tagger.configure_batch(None, 100), 8)

    def test_gpu_throughput_collapse_stops_larger_probes(self):
        tagger = self.tagger()
        def profile(_, count):
            return {'count': count, 'peak_bytes': int((.3 + count * .1) * GiB),
                    'rate': count if count <= 8 else 3, 'seconds': 1}
        with patch.object(tagger, '_profile_batch', side_effect=profile):
            self.assertEqual(tagger.configure_batch(None, 100), 8)
        self.assertEqual(max(p['count'] for p in tagger.batch_profiles), 16)

    def test_small_workload_and_cancellation_do_not_run_large_probes(self):
        tagger = self.tagger()
        with patch.object(tagger, '_profile_batch', side_effect=lambda _, count:
                               {'count': count, 'peak_bytes': int(.5 * GiB), 'rate': count, 'seconds': 1}):
            self.assertEqual(tagger.configure_batch(None, 3), 3)
        tagger = self.tagger()
        tagger.cancel = threading.Event()
        tagger.cancel.set()
        with patch.object(tagger, '_profile_batch') as probe:
            tagger.configure_batch(None, 100)
            probe.assert_not_called()

    def test_scanner_applies_calibrated_size_and_skips_tuning_cached_images(self):
        class AutoTagger:
            signature = 'auto-test'
            batch_size = effective_batch_size = 1
            auto_batch = True
            batch_configured = False
            def __init__(self):
                self.sizes, self.limits = [], []
            def configure_batch(self, sample, available_count):
                self.limits.append(available_count)
                self.batch_size = self.effective_batch_size = min(12, available_count)
                self.batch_configured = True
                return self.batch_size
            def predict_many(self, images):
                self.sizes.append(len(images))
                return [['solo'] for _ in images]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'images'
            folder.mkdir()
            for i in range(16):
                Image.new('RGB', (32, 32), (i * 10, 5, 90)).save(folder / f'{i:02}.png')
            library = Library(root / 'db')
            tagger = AutoTagger()
            report = Scanner(library, tagger).run(folder)
            self.assertEqual(report['errors'], 0)
            self.assertEqual(tagger.sizes, [12, 4])
            self.assertEqual(tagger.limits, [16])
            cached = AutoTagger()
            self.assertEqual(Scanner(library, cached).run(folder)['skipped'], 16)
            self.assertEqual(cached.limits, [])
            Image.new('RGB', (32, 32), 'white').save(folder / 'new.png')
            Scanner(library, cached).run(folder)
            self.assertEqual(cached.limits, [1])


if __name__ == '__main__':
    unittest.main()
