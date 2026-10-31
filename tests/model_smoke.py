"""Optional integration check with the real bundled checkpoint and all samples."""
import json
import sys
import tempfile
import time
import warnings
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from image_library.core import Library, ROOT, Scanner, read_rgb
from image_library.model import DanbooruTagger

with tempfile.TemporaryDirectory() as directory:
    library = Library(directory)
    model = DanbooruTagger(batch_size=2)
    source = ROOT / 'ML-Danbooru/TaggingSample'
    expected = json.loads((source / 'image_captions.json').read_text(encoding='utf-8'))
    start = time.monotonic()
    console = StringIO()
    with warnings.catch_warnings(record=True) as recorded, redirect_stdout(console):
        warnings.simplefilter('always')
        # Registry entries must retain their original timm builders after the
        # custom architecture is imported and used for real inference.
        from timm.models import model_entrypoint
        before = {name: model_entrypoint(name) for name in ('caformer_m36', 'convformer_s36', 'poolformerv2_s12')}
        batch_sizes = []
        infer_batch = model._predict_batch
        def record_batch(images):
            batch_sizes.append(len(images))
            return infer_batch(images)
        with patch.object(model, '_predict_batch', side_effect=record_batch):
            report = Scanner(library, model).run(source)
        assert max(batch_sizes) > 1, batch_sizes
        assert all(model_entrypoint(name) is builder for name, builder in before.items())
    assert not any('Overwriting' in str(warning.message) for warning in recorded)
    assert 'xformers not find' not in console.getvalue()
    rows = library.snapshot()
    assert report['errors'] == 0 and report['processed'] == len(expected), report
    for row in rows:
        assert set(row['tags']) == set(expected[Path(row['path']).name].split(', ')), row['path']
        assert row['grid']['version'] == 2
        assert (library.thumbs / f"{row['id']}.jpg").is_file()
    repeated = Scanner(library, model).run(source)
    assert repeated['skipped'] == len(expected) and repeated['processed'] == 0, repeated
    tresnet = DanbooruTagger(ROOT / 'ML-Danbooru/TResnet-D-FLq_ema_6-30000.ckpt', architecture='tresnet_d', batch_size=2)
    tresnet_tags = tresnet.predict_many([read_rgb(source / '1.png'), read_rgb(source / '1.png')])
    assert all('1girl' in tags for tags in tresnet_tags)
    assert tresnet_tags[0] == tresnet_tags[1]
    print(f'PASS: {len(rows)} CAFormer batch outputs match sample JSON; TResNet batch inference; timm registry unchanged; '
          f'no registry/xformers messages; v2 colors, thumbnails, rescan skip; {time.monotonic() - start:.2f}s')
