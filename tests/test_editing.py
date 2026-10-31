import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from image_library.core import Library, Scanner, color_distance
from image_library.color_search import pack_grid, distances


class Tagger:
    signature = 'edit-model'
    def predict(self, image):
        return ['solo', 'blue eyes', 'smile']


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.folder = self.root / 'images'
        self.folder.mkdir()
        self.image = self.folder / 'a.png'
        Image.new('RGB', (80, 80), 'red').save(self.image)
        self.library = Library(self.root / 'db')
        self.tagger = Tagger()
        Scanner(self.library, self.tagger).run(self.folder)
        self.image_id = self.library.snapshot()[0]['id']

    def tearDown(self):
        self.temporary.cleanup()

    def test_manual_add_remove_survive_reanalysis_export_and_reopen(self):
        self.library.edit_tags(self.image_id, add=['My_Tag', 'my tag'], remove=['blue_eyes'])
        self.assertEqual(len(self.library.search(['my tag'])), 1)
        self.assertEqual(self.library.search(['blue eyes']), [])
        self.tagger.signature = 'model-v2'
        Scanner(self.library, self.tagger).run(self.folder)
        reopened = Library(self.root / 'db')
        row = reopened.snapshot()[0]
        self.assertIn('my tag', row['tags'])
        self.assertNotIn('blue eyes', row['tags'])
        destination = self.root / 'tags.json'
        reopened.export(destination)
        self.assertIn('my tag', json.loads(destination.read_text())['a.png'])
        reopened.edit_tags(self.image_id, add=['blue eyes'], remove=['my tag'])
        self.assertEqual(len(reopened.search(['blue eyes'])), 1)
        self.assertEqual(reopened.search(['my tag']), [])

    def test_manual_changes_are_per_image_not_copied_to_duplicates(self):
        self.library.edit_tags(self.image_id, add=['custom'], remove=['smile'])
        shutil.copyfile(self.image, self.folder / 'b.png')
        Scanner(self.library, self.tagger).run(self.folder)
        other = next(row for row in self.library.snapshot() if row['id'] != self.image_id)
        self.assertNotIn('custom', other['tags'])
        self.assertIn('smile', other['tags'])
        with self.assertRaises(ValueError):
            self.library.edit_tags(self.image_id, remove=['중복'])

    def test_trash_failure_preserves_original_and_database(self):
        with self.assertRaises(PermissionError):
            self.library.trash_image(self.image_id, trash=lambda path: (_ for _ in ()).throw(PermissionError('locked')))
        self.assertTrue(self.image.exists())
        self.assertEqual(len(self.library.snapshot()), 1)
        self.assertFalse(self.library.was_deleted(self.image))

    def test_trash_removes_index_tags_and_duplicate_marker_and_ids_are_not_reused(self):
        shutil.copyfile(self.image, self.folder / 'b.png')
        Scanner(self.library, self.tagger).run(self.folder)
        self.library.edit_tags(self.image_id, add=['custom'])
        highest = max(row['id'] for row in self.library.snapshot())
        trash = self.root / 'trash'
        trash.mkdir()
        self.library.trash_image(highest, trash=lambda path: shutil.move(path, trash / Path(path).name))
        self.assertEqual(len(self.library.snapshot()), 1)
        self.assertEqual(self.library.search(['중복']), [])
        with self.library.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM color_index WHERE image_id=?', (highest,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tags WHERE image_id=?', (highest,)).fetchone()[0], 0)
        Image.new('RGB', (80, 80), 'blue').save(self.folder / 'new.png')
        Scanner(self.library, self.tagger).run(self.folder)
        self.assertGreater(max(row['id'] for row in self.library.snapshot()), highest)

    def test_trashing_during_inference_does_not_resurrect_file_or_report_model_error(self):
        self.tagger.signature = 'changed-model'
        trash = self.root / 'trash.png'
        def predict(image):
            self.library.trash_image(self.image_id, trash=lambda path: shutil.move(path, trash))
            return ['solo']
        with patch.object(self.tagger, 'predict', side_effect=predict):
            report = Scanner(self.library, self.tagger).run(self.folder)
        self.assertEqual(self.library.snapshot(), [])
        self.assertEqual(report['errors'], 0)
        self.assertEqual(report['skipped'], 1)

    def test_vectorized_search_matches_reference_and_cache_rebuild_and_update(self):
        rng = np.random.default_rng(4)
        grids = []
        for _ in range(20):
            cells = [[[
                {'rgb': rgb.tolist(), 'weight': .15} for rgb in rng.integers(0, 256, (6, 3))]
                for x in range(8)] for y in range(8)]
            grids.append({'version': 2, 'cells': cells})
        points = [{'x': float(x), 'y': float(y), 'rgb': rgb.tolist()} for x, y, rgb in
                  zip(rng.random(16), rng.random(16), rng.integers(0, 256, (16, 3)))]
        expected = [color_distance(grid, points) for grid in grids]
        np.testing.assert_allclose(distances([pack_grid(grid) for grid in grids], points), expected, atol=.0001)
        with self.library.connect() as db:
            db.execute('DELETE FROM color_index')
        red = [{'x': .5, 'y': .5, 'rgb': [255, 0, 0]}]
        self.assertEqual(len(self.library.search(points=red, tolerance=1)), 1)
        Image.new('RGB', (81, 81), 'blue').save(self.image)
        Scanner(self.library, self.tagger).run(self.folder)
        self.assertEqual(self.library.search(points=red, tolerance=1), [])
        self.assertEqual(len(self.library.search(points=[dict(red[0], rgb=[0, 0, 255])], tolerance=1)), 1)

    def test_missing_null_color_grid_can_be_backfilled_without_a_loop(self):
        with self.library.connect() as db:
            db.execute('DELETE FROM color_index')
            db.execute('UPDATE images SET grid=NULL')
        self.library.ensure_color_index()
        self.assertEqual(self.library.search(points=[{'x': .5, 'y': .5, 'rgb': [0, 0, 0]}]), [])

    def test_metadata_cache_observes_edits_from_another_library_and_is_not_mutated_by_search(self):
        cached = self.library.snapshot(include_features=False)
        cached[0]['tags'].append('should not persist')
        self.assertNotIn('should not persist', self.library.snapshot(include_features=False)[0]['tags'])
        other = Library(self.root / 'db')
        other.edit_tags(self.image_id, add=['external'])
        self.assertEqual(len(self.library.search(['external'])), 1)
        with other.connect() as db:
            db.execute('DELETE FROM tags WHERE tag=?', ('solo',))
        self.assertEqual(self.library.search(['solo']), [])


if __name__ == '__main__':
    unittest.main()
