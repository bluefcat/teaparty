"""Visual hash lookup tests: resized copies, color sensitivity and legacy hashes."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from image_library.core import Library, features
from image_library.visual_duplicates import fingerprint, visually_matches, VisualDuplicateIndex
from image_library.web_ui import MORIService


class DuplicateLookupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lib = Library(self.root/'data')
        self.image = Image.new('RGB', (640, 440), '#eee1c7')
        d = ImageDraw.Draw(self.image)
        for i in range(35):
            x = 15 + i * 13
            d.ellipse((x, 10 + (i * 11) % 380, x + 120, 150 + (i * 11) % 380),
                      fill=(i * 7 % 255, i * 29 % 255, i * 5 % 255))

    def add(self, name, image, fmt='PNG'):
        path = self.root/name
        image.save(path,format=fmt,quality=88)
        stat = path.stat()
        digest,w,h,colors,grid,_ = features(path)
        return self.lib.save(path, stat, digest,w,h,colors,grid,['test'], 'ready','', 'mock'), digest

    def test_resized_and_compressed_are_candidates(self):
        id1,d1 = self.add('first.png',self.image)
        id2,d2 = self.add('resize.png',self.image.resize((320,220),Image.Resampling.LANCZOS))
        id3,d3 = self.add('other.jpg',self.image.resize((380,261),Image.Resampling.LANCZOS),'JPEG')
        different = Image.new('RGB',(640,440),'#eee1c7')
        ImageDraw.Draw(different).rectangle((0,0,250,400),fill='#5d95d3')
        id4,_ = self.add('different.png',different)
        self.assertNotEqual(d1,d3, 'legacy SHA digest should not accidentally be used as perceptual matcher')
        index = VisualDuplicateIndex(self.lib)
        rows = self.lib.snapshot(include_features=False)
        found = index.find(d1,rows)
        self.assertEqual({r['id'] for r in found},{id1,id2,id3})
        # Cached result should still work, and deletion cascades to the new table.
        self.assertEqual({r['id'] for r in index.find(d1,rows)},{id1,id2,id3})
        self.lib.trash_image(id3, trash=lambda path:Path(path).unlink())
        remaining = self.lib.snapshot(include_features=False)
        self.assertEqual({r['id'] for r in index.find(d1,remaining)},{id1,id2})
        with self.lib.connect() as db:
            self.assertIsNone(db.execute('SELECT image_id FROM visual_fingerprints WHERE image_id=?', (id3,)).fetchone())

    def test_search_api_reports_review_candidates(self):
        id1,d1=self.add('a.png',self.image)
        self.add('b.png',self.image.resize((320,220),Image.Resampling.LANCZOS))
        service=MORIService(self.lib)
        result=service.search({'digest':d1,'limit':100})
        self.assertEqual(result['total'],2)
        self.assertEqual(result['duplicate_count'],1)
        self.assertIn(id1,[i['id'] for i in result['images']])


if __name__=='__main__':
    unittest.main()
