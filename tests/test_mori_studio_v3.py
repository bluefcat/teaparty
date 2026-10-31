"""v3 UI/media regressions. Uses no GPU, browser or remote services."""
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from image_library.core import Library, color_features
from image_library.web_ui import MORIService


class StudioImageDisplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.library = Library(self.folder / 'database')
        self.original = self.folder / 'portrait_image.png'
        source = Image.new('RGB', (80, 640), '#cc6688')
        source.save(self.original)
        colors, grid = color_features(source)
        self.id = self.library.save(self.original, self.original.stat(), 'fixture-hash',
                                    80, 640, colors, grid, ['portrait'], 'ready', '', 'test')
        self.service = MORIService(self.library)
        self.service._upgrade_thread.join(3)

    def tearDown(self):
        self.temp.cleanup()

    def test_uncropped_tall_image_media(self):
        with Image.open(io.BytesIO(self.service.media(self.id))) as image:
            self.assertEqual(image.size, (80, 640))
        with Image.open(io.BytesIO(self.service.media(self.id, full=True))) as image:
            self.assertEqual(image.size, (80, 640))

    def test_corrupt_thumbnail_falls_back_to_original(self):
        (self.library.thumbs / f'{self.id}.jpg').write_bytes(b'corrupted cached thumbnail')
        with Image.open(io.BytesIO(self.service.media(self.id))) as image:
            self.assertEqual(image.size, (80, 640))

    def test_original_analysis_module_not_replaced(self):
        self.assertEqual(self.service.search({'query': 'portrait'})['total'], 1)


if __name__ == '__main__':
    unittest.main()
