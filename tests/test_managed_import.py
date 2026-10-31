"""End-to-end managed storage and HTTP upload regression tests without model weights."""
import io
import json
import shutil
import sys
import tempfile
import time
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from image_library.core import Library
import image_library.web_ui as web_ui
from image_library.managed_storage import ManagedStorage

class FakeTagger:
    def __init__(self, *args, **kwargs):
        self.signature = 'test-weight-v1'
        self.batch_size = 2
        self.auto_batch = False
        self.input_size = 64
    def predict_many(self, images):
        return [['1girl', 'test tag'] for _ in images]

web_ui.DanbooruTagger = FakeTagger

def png(color='red'):
    data=io.BytesIO()
    Image.new('RGB',(44,36),color).save(data,format='PNG')
    return data.getvalue()

def finished(service, timeout=20):
    t=time.monotonic()
    while time.monotonic()-t<timeout:
        if not service.get_state()['scan']['running']:
            assert service.get_state()['scan']['summary']=='분석 완료', service.get_state()['scan']
            return
        time.sleep(.1)
    raise AssertionError('analysis timed out')

with tempfile.TemporaryDirectory(prefix='tea-v9-') as tmp:
    root=Path(tmp)
    original=root/'photos'
    (original/'B').mkdir(parents=True)
    (original/'C').mkdir(parents=True)
    (original/'A.png').write_bytes(png('red'))
    (original/'B'/'B.png').write_bytes(png('blue'))
    (original/'C'/'C.png').write_bytes(png('green'))
    (original/'B'/'image_captions.json').write_text('{"B.png":"1girl, blue eyes"}')
    lib=Library(root/'app_data')
    # Index a legacy external image and hand-edit its tags before migration.
    scanner=web_ui.Scanner(lib,FakeTagger(), emit=lambda e:None)
    scanner.run(original)
    old=lib.get_path(original/'A.png')
    lib.edit_tags(old['id'],add=['special handmade tag'])
    service=web_ui.MORIService(lib)
    result=service.begin_scan(str(original))
    dest=Path(result['folder'])
    assert dest.parent.parent==service.storage.root
    finished(service)
    imported=lib.snapshot(include_features=False)
    assert len(imported)==3,[(r['path'],r['id']) for r in imported]
    assert (dest/'B'/'B.png').exists() and (dest/'C'/'C.png').exists()
    assert (dest/'B'/'image_captions.json').exists()
    assert any(r['id']==old['id'] and 'special handmade tag' in r['tags'] for r in imported)
    assert all(str(service.storage.root) in r['path'] for r in imported)
    assert service.search({'folder':str(dest)})['total']==1
    assert service.search({'folder':str(dest/'B')})['total']==1
    assert service.search({'folder':str(dest/'C')})['total']==1
    # The original can disappear without breaking previews or imported images.
    shutil.rmtree(original)
    image_id=old['id']
    assert len(service.media(image_id, True))>100
    assert service.storage.source_for(dest) is None
    # Upload through actual authenticated HTTP endpoint.
    server=ThreadingHTTPServer(('127.0.0.1',0),web_ui.make_handler(service))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        payload=png('purple')
        req=Request(base+'/api/upload_image',data=payload,method='POST',headers={
            'X-MORI-Token':service.token,'X-File-Name':quote('a single image.png'),
            'Content-Type':'application/octet-stream'})
        with urlopen(req,timeout=10) as res: uploaded=json.load(res)
        assert uploaded['ok'],uploaded
        finished(service)
        assert len(lib.snapshot(include_features=False))==4
        assert service.search({'folder':uploaded['folder']})['total']==1
        # Duplicate file names must not overwrite previous image.
        with urlopen(req,timeout=10) as res: uploaded2=json.load(res)
        finished(service)
        assert len(lib.snapshot(include_features=False))==5
        assert (Path(uploaded['folder'])/'a single image (1).png').exists()
        assert lib.snapshot(include_features=False)[0]['tags']
        # Invalid image never enters managed storage.
        bad=Request(base+'/api/upload_image',data=b'not an image',method='POST',headers={
            'X-MORI-Token':service.token,'X-File-Name':'bad.png'})
        try:
            urlopen(bad)
        except Exception as exc:
            assert getattr(exc,'code',None)==400,exc
        else:
            raise AssertionError('invalid image accepted')
    finally:
        server.shutdown();server.server_close()
    print('PASS: copied managed folder, preserved legacy tags, nested search, offline media, HTTP upload x2, invalid upload rejected')
