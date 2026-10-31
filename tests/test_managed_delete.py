"""Managed-image deletion: disk, SQLite, indexes, thumbnails, rollback."""
import io
import json
import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from image_library.core import Library, Scanner
from image_library.managed_storage import ManagedStorage
from image_library.web_ui import MORIService


class FakeTagger:
    signature = 'delete-smoke'
    batch_size = 1
    auto_batch = False
    input_size = 64

    def predict(self, image):
        return ['teacup', 'flower']


def png(color):
    stream = io.BytesIO()
    Image.new('RGB', (42, 37), color).save(stream, format='PNG')
    return stream.getvalue()


class ManagedDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.library = Library(self.home / 'data')
        self.storage = ManagedStorage(self.library)
        self.original = self.home / 'external' / 'tea.png'
        self.original.parent.mkdir()
        self.original.write_bytes(png('red'))
        self.other = self.home / 'external' / 'nested' / 'same-tea.png'
        self.other.parent.mkdir()
        self.other.write_bytes(png('red'))
        (self.original.parent / 'image_captions.json').write_text(
            json.dumps({'tea.png': 'cup, drink'}), encoding='utf-8')
        self.tagger = FakeTagger()
        self.trashed = self.home / 'fake-recycle'
        self.trashed.mkdir()

    def trash(self, path):
        destination = self.trashed / Path(path).name
        shutil.move(str(path), str(destination))

    def imported(self):
        result = self.storage.import_folder(self.original.parent)
        self.assertEqual(result['copied'], 1)
        self.assertEqual(result['duplicates'], 1)
        target = result['paths'][0]
        Scanner(self.library, self.tagger).run(self.storage.root, only_paths=[target])
        item = self.library.snapshot(include_features=False)[0]
        return target, item['id']

    def test_removes_every_persisted_reference_and_reimport_is_possible(self):
        target, image_id = self.imported()
        self.library.edit_tags(image_id, add=['handwritten'])
        self.library.thumbs.joinpath(f'{image_id}.jpg').write_bytes(b'thumbnail')
        self.assertEqual(len(self.storage._read_indices()[0]), 2)
        self.assertTrue(self.storage._read_hash_index())
        self.storage.delete_image(image_id, trash=self.trash)
        self.assertFalse(target.exists())
        self.assertFalse((self.library.thumbs / f'{image_id}.jpg').exists())
        self.assertTrue(self.original.exists(), 'external originals must be preserved')
        self.assertTrue(self.other.exists())
        self.assertEqual(self.library.snapshot(include_features=False), [])
        with self.library.connect() as db:
            for table in ('images', 'tags', 'tag_overrides', 'color_index'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        for path in (self.storage.registry_path, self.storage.captions_path,
                     self.storage.hashes_path):
            if path.exists():
                self.assertNotIn(target.name, path.read_text(encoding='utf-8'))
        self.assertFalse(self.storage.journal_path.exists())
        self.assertEqual(self.storage._read_indices()[0], {})
        self.assertEqual(self.storage._read_hash_index(), {})
        self.assertTrue((self.trashed / target.name).exists())
        again = self.storage.import_folder(self.original.parent)
        self.assertEqual(again['copied'], 1)
        self.assertEqual(again['duplicates'], 1)
        self.assertEqual(len(again['paths']), 1)

    def test_trash_failure_restores_all_state(self):
        target, image_id = self.imported()
        records_before = self.storage._read_indices()
        hashes_before = self.storage._read_hash_index()
        content = target.read_bytes()
        with self.assertRaises(PermissionError):
            self.storage.delete_image(image_id, trash=lambda _path: (_ for _ in ()).throw(PermissionError('locked')))
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(self.storage._read_indices(), records_before)
        self.assertEqual(self.storage._read_hash_index(), hashes_before)
        self.assertEqual(len(self.library.snapshot()), 1)
        self.assertFalse(self.library.was_deleted(target))

    def test_json_write_failure_restores_sqlite_and_file(self):
        target, image_id = self.imported()
        before = {p: p.read_bytes() if p.exists() else None for p in
                  (self.storage.registry_path, self.storage.captions_path,
                   self.storage.hashes_path, self.storage.journal_path)}
        from image_library import managed_storage as module
        original_atomic = module.atomic_json
        def fails_once(path, value):
            if Path(path) == self.storage.hashes_path:
                raise OSError('simulated disk problem')
            return original_atomic(path, value)
        with patch.object(module, 'atomic_json', side_effect=fails_once):
            # Rollback restore is a byte-for-byte replacement rather than atomic_json.
            with self.assertRaisesRegex(OSError, 'simulated disk problem'):
                self.storage.delete_image(image_id, trash=self.trash)
        self.assertTrue(target.exists())
        self.assertEqual(len(self.library.snapshot()), 1)
        for path, content in before.items():
            self.assertEqual(path.read_bytes() if path.exists() else None, content)
        self.assertEqual(list(self.trashed.iterdir()), [])

    def test_sqlite_delete_failure_restores_image_thumbnail_and_json(self):
        target, image_id = self.imported()
        thumbnail = self.library.thumbs / f'{image_id}.jpg'
        self.assertTrue(thumbnail.exists())
        expected_thumb = thumbnail.read_bytes()
        expected_image = target.read_bytes()
        expected_imports = self.storage._read_indices()
        expected_hashes = self.storage._read_hash_index()
        with self.library.connect() as db:
            db.execute('''CREATE TRIGGER prevent_delete BEFORE DELETE ON images
                           BEGIN SELECT RAISE(FAIL, 'simulated sqlite failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.storage.delete_image(image_id, trash=self.trash)
        self.assertEqual(target.read_bytes(), expected_image)
        self.assertEqual(thumbnail.read_bytes(), expected_thumb)
        self.assertEqual(self.storage._read_indices(), expected_imports)
        self.assertEqual(self.storage._read_hash_index(), expected_hashes)
        self.assertEqual(len(self.library.snapshot()), 1)
        self.assertFalse(self.library.was_deleted(target))

    def test_service_eviction_and_no_deletion_while_scan_running(self):
        target, image_id = self.imported()
        service = MORIService(self.library)
        service.storage = self.storage
        service.media(image_id, False)
        self.assertTrue(service._media_cache)
        service.scan['running'] = True
        with self.assertRaises(RuntimeError):
            service.delete_image(image_id)
        self.assertTrue(target.exists())
        service.scan['running'] = False
        real_delete = self.storage.delete_image
        with patch.object(self.storage, 'delete_image', side_effect=lambda id: real_delete(id, trash=self.trash)):
            self.assertEqual(service.delete_image(image_id)['deleted'], image_id)
        self.assertFalse(service._media_cache)
        self.assertFalse(service._media_order)
        self.assertEqual(len(self.library.snapshot()), 0)

    def test_rejects_deletion_of_external_path(self):
        Scanner(self.library, self.tagger).run(self.original.parent)
        outside = next(r for r in self.library.snapshot() if Path(r['path']).resolve() == self.original.resolve())
        with self.assertRaises(ValueError):
            self.storage.delete_image(outside['id'], trash=self.trash)
        self.assertTrue(self.original.exists())
        self.assertEqual(len(self.library.snapshot()), 2)


if __name__ == '__main__':
    unittest.main()
