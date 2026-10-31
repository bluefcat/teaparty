import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from image_library.core import Library, Scanner, color_distance, features
from image_library.semantic import semantic_tags


class FakeTagger:
    signature = 'test-model-v1'

    def __init__(self):
        self.calls = 0

    def predict(self, image):
        self.calls += 1
        return ['1girl', 'blue_eyes']


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.images = self.root / 'images'
        self.images.mkdir()
        self.library = Library(self.root / 'database')
        self.tagger = FakeTagger()

    def tearDown(self):
        self.temporary.cleanup()

    def image(self, name, color='red', size=(80, 100)):
        path = self.images / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', size, color).save(path)
        return path

    def scan(self, **kwargs):
        return Scanner(self.library, self.tagger).run(self.images, **kwargs)

    def test_duplicates_reuse_tags_on_all_members_and_remove_after_deletion(self):
        first = self.image('a.png')
        shutil.copyfile(first, self.images / 'b.png')
        report = self.scan()
        self.assertEqual(self.tagger.calls, 1)
        self.assertEqual(report['reused'], 1)
        self.assertEqual(len(self.library.search(['중복', 'blue eyes'])), 2)
        (self.images / 'b.png').unlink()
        self.scan()
        self.assertEqual(len(self.library.search(['중복'])), 0)

    def test_unchanged_skipped_changed_and_new_model_reprocessed(self):
        self.image('a.png')
        self.scan()
        self.assertEqual(self.scan()['skipped'], 1)
        self.assertEqual(self.tagger.calls, 1)
        self.image('a.png', 'blue', (81, 100))
        self.scan()
        self.assertEqual(self.tagger.calls, 2)
        self.tagger.signature = 'test-model-v2'
        self.scan()
        self.assertEqual(self.tagger.calls, 3)

    def test_changed_file_breaks_previous_duplicate_group(self):
        a = self.image('a.png')
        shutil.copyfile(a, self.images / 'b.png')
        self.scan()
        self.image('b.png', 'green')
        self.scan()
        self.assertEqual(self.library.search(['중복']), [])

    def test_recursive_folder_and_exact_tag_matching(self):
        self.image('sub/a.png')
        self.image('other/b.png', 'blue')
        self.scan()
        self.assertEqual(len(self.library.search(['1girl', 'blue_eyes'])), 2)
        self.assertEqual(len(self.library.search(['girl'])), 0)
        self.assertEqual(len(self.library.search(folder=str(self.images / 'sub'))), 1)
        # This spelling is not normalized: scanner paths *are* normalized.
        self.assertEqual(len(self.library.search(folder=str(self.images / 'other' / '..' / 'sub'))), 1)
        self.assertEqual(len(self.library.search(folder=str(self.images / 'su'))), 0)

    def test_similar_images_rank_tag_overlap_exclude_self_and_nonmatches(self):
        for name, color in [('a.png', 'red'), ('b.png', 'blue'), ('c.png', 'green'), ('d.png', 'white')]:
            self.image(name, color)
        self.scan()
        ids = {Path(row['path']).name: row['id'] for row in self.library.snapshot()}
        tags = {'a.png': ['1girl', 'blue hair', 'guitar'],
                'b.png': ['1girl', 'blue hair', 'guitar', 'sitting'],
                'c.png': ['1girl'], 'd.png': ['tree']}
        with self.library.connect() as db:
            db.execute('DELETE FROM tags')
            db.executemany('INSERT INTO tags VALUES(?,?)',
                           [(ids[name], tag) for name, values in tags.items() for tag in values])
        rows = self.library.similar(ids['a.png'])
        self.assertEqual([Path(row['path']).name for row in rows], ['b.png', 'c.png'])
        self.assertGreater(rows[0]['similarity'], rows[1]['similarity'])
        self.assertEqual(rows[0]['shared_tags'], 3)
        self.assertEqual(len(self.library.similar(ids['a.png'], limit=1)), 1)

    def test_similar_images_do_not_treat_duplicate_marker_as_a_content_tag(self):
        path = self.image('a.png')
        shutil.copyfile(path, self.images / 'b.png')
        self.scan()
        image_id = self.library.snapshot()[0]['id']
        with self.library.connect() as db:
            db.execute('DELETE FROM tags')
        self.assertIn('중복', self.library.snapshot()[0]['tags'])
        self.assertEqual(self.library.similar(image_id), [])
        self.assertEqual(self.library.similar(-1), [])

    def test_color_position_matters(self):
        path = self.image('left.png', 'blue', (80, 80))
        image = Image.open(path)
        image.paste('red', (0, 0, 40, 80))
        image.save(path)
        image.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(self.images / 'right.png')
        self.scan()
        points = [{'x': .1, 'y': .5, 'rgb': [255, 0, 0]}]
        rows = self.library.search(points=points, tolerance=5)
        self.assertEqual([Path(r['path']).name for r in rows], ['left.png'])

    def test_mixed_region_matches_real_color_instead_of_only_average(self):
        path = self.image('mixed.png', 'blue', (128, 128))
        with Image.open(path) as image:
            image.paste('red', (0, 0, 4, 16))
            image.save(path)
        self.image('blue.png', 'blue', (128, 128))
        self.scan()
        point = [{'x': .0625, 'y': .0625, 'rgb': [255, 0, 0]}]
        mean_grid = [[[64, 0, 191]] * 8 for _ in range(8)]
        self.assertGreater(color_distance(mean_grid, point), 35)
        self.assertEqual([Path(r['path']).name for r in self.library.search(points=point, tolerance=5)],
                         ['mixed.png'])
        self.assertEqual(self.library.search(points=[dict(point[0], x=.9)], tolerance=5), [])

    def test_each_painted_region_must_match(self):
        self.image('red.png', 'red')
        self.scan()
        points = [{'x': .1, 'y': .1, 'rgb': [255, 0, 0]}] * 20
        points += [{'x': .9, 'y': .9, 'rgb': [0, 0, 255]}]
        self.assertEqual(self.library.search(points=points, tolerance=35), [])

    def test_legacy_color_upgrade_preserves_model_tags_and_history(self):
        self.image('red.png', 'red')
        self.scan()
        row = self.library.snapshot()[0]
        with self.library.connect() as db:
            db.execute('UPDATE images SET grid=?', (json.dumps([[[255, 0, 0]] * 8 for _ in range(8)]),))
        report = self.library.upgrade_colors()
        upgraded = self.library.snapshot()[0]
        self.assertEqual(report, {'updated': 1, 'errors': []})
        self.assertEqual(upgraded['tags'], row['tags'])
        self.assertEqual(upgraded['signature'], row['signature'])
        self.assertEqual(upgraded['grid']['version'], 2)
        self.assertEqual(self.scan()['skipped'], 1)
        self.assertEqual(self.tagger.calls, 1)

    def test_pipeline_parallel_preparation_serial_model_and_overlapping_save(self):
        from image_library.core import features as real_features
        for i in range(6):
            self.image(f'{i}.png', (i * 30, 10, 100))
        parallel, release = threading.Event(), threading.Event()
        save_started, second_inference = threading.Event(), threading.Event()
        guard = threading.Lock()
        preparing = 0
        peak = 0
        inference_threads = []
        def prepare(path):
            nonlocal preparing, peak
            with guard:
                preparing += 1
                peak = max(peak, preparing)
                if preparing == 2:
                    parallel.set()
            if not release.wait(4):
                raise RuntimeError('preparation did not overlap')
            try:
                return real_features(path)
            finally:
                with guard:
                    preparing -= 1
        scanner = Scanner(self.library, self.tagger, workers=2)
        persist = scanner.persist
        predict = self.tagger.predict
        def save(item, *args):
            if item['path'].name == '0.png':
                save_started.set()
                if not second_inference.wait(4):
                    raise RuntimeError('save blocked model inference')
            return persist(item, *args)
        def infer(image):
            inference_threads.append(threading.get_ident())
            if self.tagger.calls == 1:
                if not save_started.wait(4):
                    raise RuntimeError('save did not overlap inference')
                second_inference.set()
            return predict(image)
        reports, failures = [], []
        def run():
            try:
                reports.append(scanner.run(self.images))
            except BaseException as exc:
                failures.append(exc)
        with patch('image_library.core.features', side_effect=prepare), \
             patch.object(scanner, 'persist', side_effect=save), \
             patch.object(self.tagger, 'predict', side_effect=infer):
            worker = threading.Thread(target=run)
            worker.start()
            overlap = parallel.wait(4)
            release.set()
            worker.join(15)
        self.assertTrue(overlap)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(reports[0]['errors'], 0)
        self.assertEqual(reports[0]['processed'], 6)
        self.assertEqual(peak, 2)
        self.assertEqual(len(set(inference_threads)), 1)
        self.assertTrue(save_started.is_set() and second_inference.is_set())

    def test_cancel_during_inference_flushes_only_current_image(self):
        for i in range(10):
            self.image(f'{i}.png', (i * 20, 10, 100))
        events = []
        scanner = Scanner(self.library, self.tagger, events.append)
        predict = self.tagger.predict
        def cancel_after_current(image):
            scanner.cancel.set()
            return predict(image)
        with patch.object(self.tagger, 'predict', side_effect=cancel_after_current):
            report = scanner.run(self.images)
        self.assertEqual(self.tagger.calls, 1)
        self.assertEqual(report['processed'], 1)
        self.assertTrue(report['cancelled'])
        self.assertEqual(len(self.library.snapshot()), 1)
        self.assertEqual(events[-1]['type'], 'complete')
        self.assertEqual(sum(e['type'] == 'indexed' for e in events), 1)

    def test_batch_inference_preserves_image_mapping_and_deduplicates(self):
        for i in range(5):
            self.image(f'{i}.png', (i * 30 + 10, 0, 0))
        shutil.copyfile(self.images / '0.png', self.images / '0-copy.png')
        class BatchTagger:
            signature = 'batch-model'
            batch_size = 3
            def __init__(self):
                self.sizes = []
            def predict_many(self, images):
                self.sizes.append(len(images))
                return [[f'red {image.getpixel((0, 0))[0]}'] for image in images]
        tagger = BatchTagger()
        report = Scanner(self.library, tagger).run(self.images)
        self.assertEqual(report['errors'], 0)
        self.assertEqual(report['processed'], 6)
        self.assertEqual(report['reused'], 1)
        self.assertEqual(sum(tagger.sizes), 5)
        self.assertGreater(max(tagger.sizes), 1)
        for row in self.library.snapshot():
            expected = 10 if Path(row['path']).name == '0-copy.png' else int(Path(row['path']).stem) * 30 + 10
            self.assertIn(f'red {expected}', row['tags'])

    def test_cancel_finishes_current_batch_without_starting_another(self):
        for i in range(5):
            self.image(f'{i}.png', (i * 30 + 10, 0, 0))
        class BatchTagger:
            signature = 'cancel-batch'
            batch_size = 2
            def predict_many(self, images):
                scanner.cancel.set()
                return [['solo'] for _ in images]
        scanner = Scanner(self.library, BatchTagger())
        report = scanner.run(self.images)
        self.assertTrue(report['cancelled'])
        self.assertEqual(report['processed'], 2)
        self.assertEqual(len(self.library.snapshot()), 2)

    def test_failed_batch_falls_back_to_individual_images(self):
        for i in range(3):
            self.image(f'{i}.png', (i * 30 + 10, 0, 0))
        class BatchTagger:
            signature = 'batch-fallback'
            batch_size = 3
            def predict_many(self, images):
                raise RuntimeError('batch unsupported')
            def predict(self, image):
                if image.getpixel((0, 0))[0] == 40:
                    raise RuntimeError('bad image')
                return ['solo']
        report = Scanner(self.library, BatchTagger()).run(self.images)
        self.assertEqual(report['processed'], 3)
        self.assertEqual(report['errors'], 1)
        self.assertEqual(len(self.library.search(['solo'])), 2)

    def test_export_compatible_format_and_filename_collision_preserves_both(self):
        self.image('one/a.png')
        self.image('two/a.png', 'blue')
        self.image('unique.png', 'green')
        self.scan()
        destination = self.root / 'captions.json'
        self.assertEqual(self.library.export(destination), 3)
        data = json.loads(destination.read_text(encoding='utf-8'))
        self.assertIn('unique.png', data)
        self.assertEqual(len(data), 3)
        self.assertTrue(all(isinstance(value, str) for value in data.values()))

    def test_failed_model_is_retryable(self):
        self.image('a.png')
        with patch.object(self.tagger, 'predict', side_effect=RuntimeError('missing weights')):
            self.assertEqual(self.scan()['errors'], 1)
        self.assertEqual(self.library.snapshot()[0]['status'], 'error')
        self.scan()
        self.assertEqual(self.library.snapshot()[0]['status'], 'ready')

    def test_corrupt_replacement_removes_stale_tags(self):
        path = self.image('a.png')
        self.scan()
        path.write_bytes(b'broken')
        self.assertEqual(self.scan()['errors'], 1)
        self.assertEqual(self.library.search(['1girl']), [])

    def test_cancellation_does_not_prune_existing_library(self):
        self.image('a.png')
        self.scan()
        scanner = Scanner(self.library, self.tagger)
        scanner.cancel.set()
        self.assertTrue(scanner.run(self.images)['cancelled'])
        self.assertEqual(len(self.library.snapshot()), 1)

    def test_json_opt_in_and_return_to_real_inference(self):
        self.image('a.png')
        (self.images / 'image_captions.json').write_text(json.dumps({'a.png': 'solo, smile'}))
        self.scan(use_json=True)
        self.assertEqual(self.tagger.calls, 0)
        self.assertEqual(len(self.library.search(['solo'])), 1)
        self.scan(use_json=False)
        self.assertEqual(self.tagger.calls, 1)
        self.assertEqual(len(self.library.search(['solo'])), 0)

    def test_alpha_compositing_and_hash_are_consistent(self):
        transparent = self.images / 'alpha.png'
        Image.new('RGBA', (40, 40), (0, 0, 0, 0)).save(transparent)
        opaque = self.image('white.png', 'white', (40, 40))
        self.assertEqual(features(transparent)[0], features(opaque)[0])


class SemanticTests(unittest.TestCase):
    def test_api_key_required(self):
        with self.assertRaises(ValueError):
            semantic_tags('파란 눈', ['blue eyes'], '', 'https://example.com/v1', 'model')

    def test_plain_http_remote_rejected(self):
        with self.assertRaises(ValueError):
            semantic_tags('파란 눈', ['blue eyes'], 'test-key', 'http://example.com/v1', 'model')

    def test_api_response_filtered_to_existing_vocabulary(self):
        from io import BytesIO
        response = {'choices': [{'message': {'content': json.dumps({'tags': ['blue_eyes', 'invented']})}}]}
        with patch('urllib.request.build_opener') as factory:
            factory.return_value.open.return_value = BytesIO(json.dumps(response).encode())
            result = semantic_tags('파란 눈', ['blue eyes'], 'test-key', 'https://example.com/v1', 'model')
        self.assertEqual(result, ['blue eyes'])



if __name__ == '__main__':
    unittest.main()
