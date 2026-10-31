"""Hash-based duplicate filtering during import/upload."""
import io
import json
import sys
import tempfile
import time
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from image_library.core import Library
import image_library.web_ui as web_ui


class FakeTagger:
    def __init__(self, *args, **kwargs):
        self.signature = 'test-weight-v1'
        self.batch_size = 2
        self.auto_batch = False
        self.input_size = 64

    def predict_many(self, images):
        return [['tagged'] for _ in images]


web_ui.DanbooruTagger = FakeTagger


def png(color='red'):
    data = io.BytesIO()
    Image.new('RGB', (32, 32), color).save(data, format='PNG')
    return data.getvalue()


def wait_done(service, timeout=15):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if not service.get_state()['scan']['running']:
            return
        time.sleep(0.05)
    raise AssertionError('analysis timed out')


with tempfile.TemporaryDirectory(prefix='tea-v11-dedup-') as tmp:
    root = Path(tmp)
    src = root / 'src'
    (src / 'sub').mkdir(parents=True)
    (src / 'a.png').write_bytes(png('red'))
    (src / 'sub' / 'dup.png').write_bytes(png('red'))  # exact duplicate bytes
    (src / 'b.png').write_bytes(png('blue'))

    lib = Library(root / 'app_data')
    service = web_ui.MORIService(lib)
    result = service.begin_scan(str(src))
    wait_done(service)

    rows = lib.snapshot(include_features=False)
    assert len(rows) == 2, rows
    assert result['ok'], result
    assert sum(1 for p in (service.storage.root).iterdir() if p.is_file() and p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}) == 2

    # Duplicate upload should be rejected before scan.
    server = ThreadingHTTPServer(('127.0.0.1', 0), web_ui.make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        req = Request(base + '/api/upload_image', data=png('red'), method='POST', headers={
            'X-MORI-Token': service.token,
            'X-File-Name': quote('red duplicate.png'),
            'Content-Type': 'application/octet-stream',
        })
        with urlopen(req, timeout=10) as res:
            payload = json.load(res)
        assert payload['ok'] and payload['duplicate'], payload
        assert len(lib.snapshot(include_features=False)) == 2
    finally:
        server.shutdown(); server.server_close()

print('PASS: duplicate hash filtering for folder import and single upload')
