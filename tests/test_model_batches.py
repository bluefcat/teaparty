import unittest
from types import SimpleNamespace
from unittest.mock import patch

from image_library.model import DanbooruTagger


class OutOfMemory(RuntimeError):
    pass


class BatchRecoveryTests(unittest.TestCase):
    def tagger(self):
        tagger = DanbooruTagger(batch_size=8)
        tagger.model = object()
        tagger.torch = SimpleNamespace(OutOfMemoryError=OutOfMemory)
        tagger.device = SimpleNamespace(type='cpu')
        return tagger

    def test_memory_reduction_preserves_every_image_and_output_order(self):
        tagger = self.tagger()
        sizes = []
        def infer(images):
            sizes.append(len(images))
            if len(images) > 2:
                raise OutOfMemory('reduce batch')
            return [[str(image)] for image in images]
        with patch.object(tagger, '_predict_batch', side_effect=infer):
            self.assertEqual(tagger.predict_many(list(range(11))), [[str(i)] for i in range(11)])
        self.assertEqual(tagger.effective_batch_size, 2)
        self.assertIn(8, sizes)

    def test_single_image_memory_failure_is_reported(self):
        tagger = self.tagger()
        with patch.object(tagger, '_predict_batch', side_effect=OutOfMemory('single image too large')):
            with self.assertRaises(OutOfMemory):
                tagger.predict_many([1])

    def test_unrelated_model_errors_are_not_silenced(self):
        tagger = self.tagger()
        with patch.object(tagger, '_predict_batch', side_effect=ValueError('bad output')):
            with self.assertRaisesRegex(ValueError, 'bad output'):
                tagger.predict_many([1, 2])


if __name__ == '__main__':
    unittest.main()
