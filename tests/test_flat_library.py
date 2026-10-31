"""Regression tests for the unified, folder-free managed image library."""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from image_library.core import Library, Scanner
from image_library.managed_storage import ManagedStorage
from image_library.web_ui import MORIService


class FakeTagger:
    signature = 'mock-checkpoint'
    batch_size = 1
    auto_batch = False
    def __init__(self, *args, **kwargs):
        pass
    def predict(self, image):
        return ['test_tag']


class FlatStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.library = Library(self.base/'data')
        self.folder = self.base/'originals'
        self.folder.mkdir()

    def picture(self, path, color='red'):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB',(64,64),color).save(path)
        return path

    def test_flat_import_collision_repeat_and_single_upload(self):
        a = self.picture(self.folder/'A'/'same.png')
        b = self.picture(self.folder/'B'/'same.png','blue')
        storage=ManagedStorage(self.library)
        result=storage.import_folder(self.folder)
        self.assertEqual(result['copied'],2)
        self.assertEqual(len(list(storage.root.glob('*.png'))),2)
        self.assertEqual([p for p in storage.root.iterdir() if p.is_dir()],[])
        self.assertEqual(storage.import_folder(self.folder)['unchanged'],2)
        with a.open('rb') as stream:
            uploaded=storage.store_upload('same.png',stream.read())
        self.assertEqual(uploaded.parent,storage.root)
        self.assertEqual(len(list(storage.root.glob('*.png'))),2)
        self.assertEqual(a.read_bytes(),uploaded.read_bytes())
        self.assertTrue(b.is_file())

    def test_existing_id_and_manual_tags_migrate_on_import(self):
        original=self.picture(self.folder/'a.png')
        Scanner(self.library,FakeTagger()).run(self.folder)
        old=self.library.snapshot()[0]
        self.library.edit_tags(old['id'],add=['my manual tag'])
        storage=ManagedStorage(self.library)
        storage.import_folder(self.folder)
        new=self.library.snapshot()[0]
        self.assertEqual(new['id'],old['id'])
        self.assertIn('my manual tag',new['tags'])
        self.assertEqual(Path(new['path']).parent,storage.root)
        self.assertTrue(original.exists())
        self.assertEqual(Scanner(self.library,FakeTagger()).run(storage.root)['skipped'],1)

    def test_migrate_old_nested_structure_without_losing_database(self):
        storage_root=self.library.directory/'managed_images'
        old_root=storage_root/'123456789012'/'Some Folder'
        legacy=self.picture(old_root/'sub'/'old.png')
        original=self.picture(self.folder/'sub'/'old.png')
        Scanner(self.library,FakeTagger()).run(old_root)
        old=self.library.snapshot()[0]
        self.library.edit_tags(old['id'],add=['keep me'])
        (storage_root/'sources.json').write_text(json.dumps({str(old_root):str(self.folder)}),encoding='utf8')
        (legacy.parent/'image_captions.json').write_text(json.dumps({'old.png':'archived_tag'}),encoding='utf8')
        storage=ManagedStorage(self.library)
        self.assertEqual(len(list(storage.root.glob('*.png'))),1)
        self.assertFalse(legacy.exists())
        self.assertTrue(original.exists())
        self.assertEqual([p for p in storage.root.rglob('*.png') if p.parent!=storage.root],[])
        record=self.library.snapshot()[0]
        self.assertEqual(record['id'],old['id'])
        self.assertIn('keep me',record['tags'])
        self.assertEqual(Path(record['path']).parent,storage.root)
        self.assertEqual(json.loads(storage.registry_path.read_text())[str(original.resolve())],Path(record['path']).name)
        self.assertEqual(json.loads(storage.captions_path.read_text())[Path(record['path']).name],'archived_tag')
        # A repeated import must reuse the flattened managed filename.
        self.assertEqual(storage.import_folder(self.folder)['total'],1)
        self.assertEqual(len(list(storage.root.glob('*.png'))),1)

    def test_service_scans_unified_root_and_ignores_old_folder_filters(self):
        self.picture(self.folder/'A'/'one.png')
        self.picture(self.folder/'B'/'two.png','blue')
        service=MORIService(self.library)
        with patch('image_library.web_ui.DanbooruTagger',FakeTagger):
            result=service.begin_scan(str(self.folder))
            self.assertEqual(Path(result['folder']),service.storage.root)
            until=time.monotonic()+12
            while time.monotonic()<until:
                state=service.get_state()
                if not state['scan']['running']:
                    break
                time.sleep(.05)
            self.assertFalse(service.get_state()['scan']['running'])
        self.assertEqual(len(service.search({'folder':'nonexistent'})['images']),2)
        self.assertEqual(len(service.search({})['images']),2)
        self.assertNotIn('folders',service.get_state())
        self.assertEqual(len(list(service.storage.root.rglob('*.png'))),2)
        self.assertEqual([p for p in service.storage.root.iterdir() if p.is_dir()],[])


if __name__=='__main__':
    unittest.main()
