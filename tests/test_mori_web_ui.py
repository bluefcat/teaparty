"""Smoke/regression tests for the new GUI service. No model or network needed."""
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from image_library.core import Library, color_features
from image_library.web_ui import MORIService


class TestStudioService(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        folder = base / 'pictures'
        folder.mkdir()
        self.library = Library(base / 'data')
        self.ids = []
        for index, (color, tags) in enumerate([
            ('red', ['1girl', 'red hair', 'smile']),
            ('blue', ['1girl', 'blue eyes', 'smile']),
            ('red', ['1girl', 'red hair', 'white shirt']),
        ]):
            path = folder / f'pic{index}.png'
            image = Image.new('RGB', (32, 40), color)
            image.save(path)
            colors, grid = color_features(image)
            self.ids.append(self.library.save(path, path.stat(), f'hash-{index}', 32, 40,
                                              colors, grid, tags, 'ready', '', 'test'))
        self.service = MORIService(self.library)
        self.service._upgrade_thread.join(3)

    def tearDown(self):
        self.temp.cleanup()

    def test_search_tag_and_color(self):
        q = self.service.search({'query': '1girl, red hair'})
        self.assertEqual(q['total'], 2)
        self.assertEqual(q['resolved_tags'], ['1girl', 'red hair'])
        point = {'x': 2, 'y': 4, 'rgb': [255, 0, 0]}
        c = self.service.search({'points': [point], 'tolerance': 8})
        self.assertEqual(c['total'], 2)
        self.assertTrue(all('red hair' in i['tags'] for i in c['images']))
        self.assertEqual(self.service.search({'query': 'not a tag'})['total'], 0)

    def test_details_tags_export_and_media(self):
        id_ = self.ids[0]
        before = self.service.detail(id_)
        self.assertTrue(before['similar'])
        self.service.edit_tags({'id': id_, 'add': ['favorite'], 'remove': ['red hair']})
        after = self.service.detail(id_)
        self.assertIn('favorite', after['image']['tags'])
        self.assertNotIn('red hair', after['image']['tags'])
        exported = self.service.export()
        self.assertIn(b'pic0.png', exported)
        self.assertTrue(self.service.media(id_).startswith(b'\xff\xd8'))
        self.assertTrue(self.service.media(id_, full=True).startswith(b'\xff\xd8'))

    def test_sorts_and_validation(self):
        q = self.service.search({'sort': 'oldest'})
        self.assertEqual([r['id'] for r in q['images']], self.ids)
        q = self.service.search({'sort': 'newest'})
        self.assertEqual([r['id'] for r in q['images']], list(reversed(self.ids)))
        with self.assertRaises(ValueError):
            self.service.edit_tags({'id': self.ids[0], 'remove': ['중복']})
        with self.assertRaises(ValueError):
            self.service.search({'points': [{'x': 20, 'y': 3, 'rgb': [1, 2, 3]}]})


if __name__ == '__main__':
    unittest.main()
