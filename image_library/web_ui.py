"""MORI Studio: local, browser-rendered GUI for the existing image-library engine.

No cloud UI, no image upload. The server listens only on loopback and requires an
unpredictable per-launch request token for every API operation.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections import Counter, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image

from .core import DATA, ROOT, Library, Scanner, atomic_json, normalize_tag, read_rgb
from .model import DanbooruTagger
from .checkpoints import DEFAULT_CHECKPOINT, resolve_checkpoint
from .semantic import semantic_tags
from .managed_storage import ManagedStorage, MAX_UPLOAD_BYTES, ImportCancelled
from .visual_duplicates import VisualDuplicateIndex

ASSETS = Path(__file__).resolve().parent / 'web_assets'
DEFAULTS = {
    'checkpoint': str(DEFAULT_CHECKPOINT),
    'architecture': 'caformer_m36', 'threshold': .7, 'device': 'auto',
    'batch_size': 'auto', 'base_url': 'https://api.openai.com/v1',
    'api_model': '', 'use_json': False,
}


def public_image(row):
    return {
        'id': row['id'], 'name': Path(row['path']).name, 'path': row['path'],
        'width': row['width'], 'height': row['height'], 'size': row['size'],
        'mtime': row['mtime'], 'tags': row['tags'], 'duplicate': row['duplicate'],
        'status': row['status'], 'error': row['error'],
        'hash': row['hash'], 'colors': row['colors'],
    }


class MORIService:
    def __init__(self, library=None):
        self.library = library or Library()
        self.storage = ManagedStorage(self.library)
        self.visual_duplicates = VisualDuplicateIndex(self.library)
        self._import_cancel = threading.Event()
        self._upload_lock = threading.Lock()
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(36)
        self.settings = self.read_settings()
        self.api_key = os.environ.get('OPENAI_API_KEY', '')
        self.scan = {'running': False, 'status': '준비됨', 'completed': 0,
                     'total': 0, 'elapsed': 0, 'remaining': None, 'device': '장치 확인 전',
                     'batch': '자동', 'summary': '', 'folder': '', 'revision': 0}
        self.scanner = None
        self.events = deque(maxlen=300)
        self.errors = deque(maxlen=120)
        self.started = 0.0
        self._media_lock = threading.Lock()
        self._media_cache = {}
        self._media_order = deque()
        self._upgrade_thread = threading.Thread(target=self._upgrade_colors, daemon=True)
        self._upgrade_thread.start()

    def _upgrade_colors(self):
        try:
            result = self.library.upgrade_colors()
            if result.get('updated'):
                self.emit({'type': 'upgrade', 'updated': result['updated']})
            for error in result.get('errors', []):
                self.emit({'type': 'file_error', 'error': error})
        except Exception as exc:
            self.emit({'type': 'upgrade_error', 'error': str(exc)})

    def read_settings(self):
        values = dict(DEFAULTS)
        path = self.library.directory / 'settings.json'
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                values.update({k: v for k, v in saved.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass
        values['checkpoint'] = str(resolve_checkpoint(values['checkpoint']))
        return values

    def get_state(self):
        with self.library.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM images').fetchone()[0]
        with self.lock:
            scan = dict(self.scan)
            if scan['running']:
                scan['elapsed'] = max(0, round(time.monotonic() - self.started, 1))
            return {
                'total': total,
                'settings': dict(self.settings), 'key_configured': bool(self.api_key),
                'scan': scan, 'events': list(self.events)[-90:],
                'errors': list(self.errors)[-35:],
            }

    def search(self, payload):
        query = str(payload.get('query') or '').strip()
        mode = payload.get('mode', 'tag')
        digest = payload.get('digest')
        colors = payload.get('points') or []
        if not isinstance(colors, list) or len(colors) > 64:
            raise ValueError('색상 검색 조건이 잘못되었습니다.')
        points = []
        for point in colors:
            rgb = point['rgb']
            if len(rgb) != 3 or any(not isinstance(x, int) or not 0 <= x <= 255 for x in rgb):
                raise ValueError('색상은 RGB 값이어야 합니다.')
            x, y = int(point['x']), int(point['y'])
            if not (0 <= x < 8 and 0 <= y < 8):
                raise ValueError('색상 검색 위치가 잘못되었습니다.')
            points.append({'x': (x + .5) / 8, 'y': (y + .5) / 8, 'rgb': rgb})
        tolerance = max(5, min(100, float(payload.get('tolerance', 35))))
        rows = self.library.snapshot(include_features=False)
        counts = Counter(tag for row in rows for tag in row['tags'])
        tags = [normalize_tag(t) for t in query.split(',') if t.strip()]
        if mode == 'natural' and query:
            with self.lock:
                settings, key = dict(self.settings), self.api_key
            tags = semantic_tags(query, counts, key, settings['base_url'], settings['api_model'])
            if not tags:
                return {'images': [], 'total': 0, 'tags': counts.most_common(150),
                        'resolved_tags': [], 'message': '검색 문장과 일치하는 태그가 없습니다.'}
        if digest:
            found = self.visual_duplicates.find(digest, rows)
        else:
            found = self.library.search(tags=tags, points=points,
                                        tolerance=tolerance, rows=rows)
        sort = payload.get('sort', 'newest')
        if sort == 'oldest':
            found.sort(key=lambda r: r['id'])
        elif sort == 'name':
            found.sort(key=lambda r: Path(r['path']).name.casefold())
        elif sort == 'largest':
            found.sort(key=lambda r: -(r['size'] or 0))
        elif sort == 'newest' and not points:
            found.sort(key=lambda r: -r['id'])
        page = max(0, min(10000, int(payload.get('page', 0))))
        limit = max(12, min(160, int(payload.get('limit', 100))))
        return {'images': [public_image(r) for r in found[page * limit:(page + 1) * limit]],
                'total': len(found), 'page': page, 'limit': limit,
                'tags': counts.most_common(200), 'resolved_tags': tags,
                'duplicate_count': max(0, len(found)-1) if digest else None,
                'message': ''}

    def detail(self, image_id):
        row = self._by_id(image_id)
        return {'image': public_image(row),
                'similar': [public_image(r) | {'similarity': round(r['similarity'] * 100)}
                            for r in self.library.similar(image_id)]}

    def _by_id(self, image_id):
        # ID lookup deliberately never accepts an arbitrary filesystem path.
        with self.library.connect() as db:
            exists = db.execute('SELECT id FROM images WHERE id=?', (image_id,)).fetchone()
        if not exists:
            raise FileNotFoundError('이미지가 라이브러리에 없습니다.')
        row = next((r for r in self.library.snapshot(include_features=False) if r['id'] == image_id), None)
        if row is None:
            raise FileNotFoundError('이미지가 라이브러리에 없습니다.')
        return row

    def media(self, image_id, full=False):
        # Media requests are frequent. Query just one DB row instead of cloning
        # the entire metadata snapshot for each visible thumbnail.
        with self.library.connect() as db:
            item = db.execute('SELECT path,mtime,size FROM images WHERE id=?', (image_id,)).fetchone()
        if item is None:
            raise FileNotFoundError('이미지가 라이브러리에 없습니다.')
        row = dict(item)
        path = self.library.thumbs / f'{image_id}.jpg'
        if full or not path.is_file():
            path = Path(row['path'])
        stamp = (row['mtime'], row['size'], str(path), bool(full))
        cache_key = (image_id, stamp)
        with self._media_lock:
            value = self._media_cache.get(cache_key)
            if value is not None:
                return value
        # Old/corrupt thumbnail caches must not hide a valid original image.
        # This is the only media-serving change; the indexing engine is untouched.
        try:
            image = read_rgb(path)
        except (OSError, ValueError):
            original = Path(row['path'])
            if path == original:
                raise
            image = read_rgb(original)
        image.thumbnail((1900, 1900) if full else (560, 850), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=89 if full else 83, optimize=True)
        raw = output.getvalue()
        with self._media_lock:
            if cache_key not in self._media_cache:
                self._media_cache[cache_key] = raw
                self._media_order.append(cache_key)
            while len(self._media_order) > 100:
                old = self._media_order.popleft()
                self._media_cache.pop(old, None)
        return raw

    def edit_tags(self, payload):
        image_id = int(payload['id'])
        additions = payload.get('add') or []
        removals = payload.get('remove') or []
        if not isinstance(additions, list) or not isinstance(removals, list):
            raise ValueError('태그 목록 형식이 올바르지 않습니다.')
        self.library.edit_tags(image_id, add=additions, remove=removals)
        return self.detail(image_id)

    def delete_image(self, image_id):
        # Do not delete a file that may still be written by the import/scanner.
        # Lock against both single uploads and new folder scans while deleting.
        with self._upload_lock, self.lock:
            if self.scan['running']:
                raise RuntimeError('이미지 분석이 완료된 후 삭제해 주세요.')
            path = self.storage.delete_image(image_id)
        # Purge cached JPEG bytes as well as the thumbnail on disk.
        with self._media_lock:
            keys = [key for key in self._media_cache if key[0] == image_id]
            for key in keys:
                self._media_cache.pop(key, None)
            self._media_order = deque(key for key in self._media_order if key[0] != image_id)
        return {'ok': True, 'deleted': image_id, 'path': path}

    def export(self):
        output = io.StringIO()
        rows = self.library.search()
        names = Counter(Path(r['path']).name for r in rows)
        data = {(Path(r['path']).name if names[Path(r['path']).name] == 1 else r['path']):
                ', '.join(r['tags']) for r in rows}
        return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')

    def save_settings(self, payload):
        with self.lock:
            values = dict(self.settings)
            values.update({k: v for k, v in payload.items() if k in DEFAULTS})
        values['threshold'] = float(values['threshold'])
        if not 0 < values['threshold'] < 1:
            raise ValueError('태그 확률 기준은 0과 1 사이여야 합니다.')
        if values['architecture'] not in ('caformer_m36', 'tresnet_d'):
            raise ValueError('지원하지 않는 모델 구조입니다.')
        if values['device'] not in ('auto', 'cpu', 'cuda'):
            raise ValueError('분석 장치가 올바르지 않습니다.')
        if values['batch_size'] != 'auto':
            values['batch_size'] = int(values['batch_size'])
            if not 1 <= values['batch_size'] <= 128:
                raise ValueError('동시 분석 수는 1~128 사이여야 합니다.')
        if not Path(values['checkpoint']).is_file():
            raise ValueError('체크포인트 파일이 존재하지 않습니다. 설정에서 경로를 확인해 주세요.')
        values['use_json'] = bool(values['use_json'])
        atomic_json(self.library.directory / 'settings.json', values)
        with self.lock:
            self.settings = values
            # API key stays only in memory. Empty input leaves the old key unchanged.
            if payload.get('api_key'):
                self.api_key = str(payload['api_key']).strip()
        return {'settings': values, 'key_configured': bool(self.api_key)}

    def choose_folder(self):
        # Native folder selection lets a browser-backed local app obtain the real path.
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes('-topmost', True)
            return filedialog.askdirectory(title='TEA PARTY · 이미지 폴더 선택', parent=root) or ''
        finally:
            root.destroy()

    def emit(self, event):
        kind = event.get('type', '')
        now = time.strftime('%H:%M:%S')
        with self.lock:
            if kind == 'scan_metrics':
                self.scan['completed'] = event['completed']
                self.scan['total'] = event['total']
                self.scan['remaining'] = (round(event['remaining'], 1)
                                          if event['remaining'] is not None else None)
                self.scan['revision'] += 1
                return
            if kind == 'model_ready':
                self.scan['device'] = ('GPU' if event['device'].startswith('cuda') else 'CPU')
                self.scan['batch'] = ('자동 측정' if event.get('auto') else f"{event['batch_size']}장")
            if kind == 'batch_probe':
                self.scan['status'] = f"안전한 배치 측정 중 · {event['count']}장"
            if kind == 'batch_selected':
                self.scan['batch'] = f"자동 {event['count']}장"
            if kind == 'inference_start':
                self.scan['status'] = f"{self.scan['device']} · {event['count']}장 추론 중"
                self.scan['batch'] = f"실제 {event['count']}장"
            if kind == 'progress':
                self.scan['status'] = Path(event['path']).name
                return
            if kind == 'indexed':
                self.scan['revision'] += 1
                return
            if kind == 'file_error':
                self.errors.append(f"{event.get('path','')} · {event.get('error','')}")
            if kind in ('complete', 'scan_error'):
                self.scan['running'] = False
                self.scanner = None
                if kind == 'complete':
                    self.scan['summary'] = ('중지됨' if event.get('cancelled') else '분석 완료')
                    self.scan['status'] = (f"처리 {event['processed']} · 건너뜀 {event['skipped']} · "
                                           f"재사용 {event['reused']} · 오류 {event['errors']}")
                    for error in event.get('walk_errors', []):
                        self.errors.append(error)
                else:
                    self.scan['summary'] = '분석 실패'
                    self.scan['status'] = str(event.get('error', '알 수 없는 오류'))
                    self.errors.append(self.scan['status'])
            # Avoid enormous log payloads; inspect errors in the separate error list.
            note = event.get('error') or event.get('message') or event.get('path') or ''
            if kind in ('batch_profile', 'inference_complete'):
                note = f"{event.get('count',0)}장 · {event.get('seconds',0):.2f}초"
            self.events.append({'time': now, 'type': kind, 'message': str(note)[:350],
                                'name': kind.replace('_', ' ')})
            self.scan['revision'] += 1

    def _start_scan(self, source=None, *, single_path=None):
        """Import external images if supplied, then index the whole flat store."""
        source = Path(source).expanduser().resolve() if source is not None else None
        if source is not None and not source.is_dir():
            raise ValueError('존재하는 폴더를 선택해 주세요.')
        destination = self.storage.root
        with self.lock:
            if self.scan['running']:
                raise RuntimeError('다른 이미지 분석이 진행 중입니다.')
            settings = dict(self.settings)
            self.events.clear()
            self.errors.clear()
            self._import_cancel.clear()
            self.started = time.monotonic()
            self.scan.update(running=True, folder=str(source) if source else '이미지 라이브러리', completed=0, total=0,
                             elapsed=0, remaining=None, device='분석 준비 중', batch='자동',
                             summary='', status='이미지를 내부 저장소에 준비하고 있습니다…')
            self.scanner = None

        def worker():
            try:
                only_paths = [single_path] if single_path is not None else None
                if source:
                    self.emit({'type': 'importing', 'message': f'{source} → {destination}'})
                    def progress(current, total, path):
                        with self.lock:
                            self.scan['status'] = f'내부 저장소로 복사 중 · {path.name}'
                            self.scan['completed'] = current
                            self.scan['total'] = total
                            self.scan['revision'] += 1
                    result = self.storage.import_folder(source, self._import_cancel, progress)
                    only_paths = result['paths']
                    self.emit({'type': 'import_complete', 'message':
                               f"복사 {result['copied']}장 · 기존 {result['unchanged']}장 · 중복 {result.get('duplicates', 0)}장"})
                if self._import_cancel.is_set():
                    raise ImportCancelled()
                tagger = DanbooruTagger(settings['checkpoint'], settings['threshold'],
                                       settings['architecture'], settings['device'],
                                       batch_size=settings['batch_size'], emit=self.emit)
                scanner = Scanner(self.library, tagger, emit=self.emit)
                with self.lock:
                    self.scanner = scanner
                    if self._import_cancel.is_set():
                        scanner.cancel.set()
                if not self._import_cancel.is_set():
                    scanner.run(destination, use_json=settings.get('use_json', False),
                                only_paths=only_paths)
                else:
                    raise ImportCancelled()
            except ImportCancelled:
                self.emit({'type': 'complete', 'cancelled': True, 'processed': 0,
                           'skipped': 0, 'reused': 0, 'errors': 0})
            except Exception as exc:
                self.emit({'type': 'scan_error', 'error': f'{exc}\n{traceback.format_exc(limit=2)}'})
        threading.Thread(target=worker, name='tea-party-scan', daemon=True).start()
        return {'ok': True, 'folder': str(destination)}

    def begin_scan(self, folder):
        return self._start_scan(folder)

    def upload_image(self, name, content):
        with self._upload_lock:
            with self.lock:
                if self.scan['running']:
                    raise RuntimeError('분석 중에는 이미지 업로드를 시작할 수 없습니다.')
            stored = self.storage.store_upload(name, content, return_info=True)
            path = stored['path']
            if stored.get('duplicate'):
                return {'ok': True, 'duplicate': True, 'path': str(path), 'message': '동일한 이미지가 이미 등록되어 있습니다.'}
            # Uploads and folder imports share the same flat index.
            result = self._start_scan(single_path=path)
            result.update({'duplicate': False, 'path': str(path)})
            return result

    def stop_scan(self):
        with self.lock:
            self._import_cancel.set()
            if self.scanner is not None:
                self.scanner.cancel.set()
                self.scan['status'] = '현재 배치 작업을 마치고 중지합니다…'
        return {'ok': True}

    def launch_original(self, image_id, reveal=False):
        row = self._by_id(image_id)
        path = str(Path(row['path']).resolve())
        if not Path(path).is_file():
            raise FileNotFoundError('원본 이미지가 존재하지 않습니다.')
        if sys.platform == 'win32':
            if reveal:
                subprocess.Popen(['explorer.exe', '/select,', path])
            else:
                os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R' if reveal else '', path] if reveal else ['open', path])
        else:
            subprocess.Popen(['xdg-open', str(Path(path).parent) if reveal else path])
        return {'ok': True}


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'MORI Studio/2'
        def log_message(self, fmt, *args):
            if '/api/state' not in str(args) and '/api/media/' not in str(args):
                super().log_message(fmt, *args)

        def _body(self):
            n = int(self.headers.get('Content-Length', 0))
            if n < 0 or n > 1_000_000:
                raise ValueError('요청 크기가 너무 큽니다.')
            return json.loads(self.rfile.read(n) or b'{}')

        def _respond(self, content, status=200, mime='application/json; charset=utf-8', filename=None):
            raw = (json.dumps(content, ensure_ascii=False).encode('utf-8')
                   if isinstance(content, (dict, list)) else content)
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store' if mime.startswith('application/json') else 'private, max-age=900')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _auth(self):
            # Header on every API call prevents cross-site HTML forms from changing state.
            provided = self.headers.get('X-MORI-Token', '')
            # Image elements cannot set custom headers; use a random session token
            # in local-only image URLs, never for mutation endpoints.
            if urlparse(self.path).path.startswith('/api/media/') and not provided:
                provided = parse_qs(urlparse(self.path).query).get('token', [''])[0]
            if not secrets.compare_digest(provided, service.token):
                self._respond({'error': '접근 권한이 없습니다.'}, 403)
                return False
            return True

        def do_GET(self):
            path = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            try:
                if path == '/':
                    page = (ASSETS / 'index.html').read_text(encoding='utf-8')
                    page = page.replace('%%MORI_TOKEN%%', service.token)
                    self._respond(page.encode('utf-8'), mime='text/html; charset=utf-8')
                    return
                if path in ('/style.css', '/app.js'):
                    name = path.lstrip('/')
                    mime = 'text/css; charset=utf-8' if name.endswith('.css') else 'application/javascript; charset=utf-8'
                    self._respond((ASSETS / name).read_bytes(), mime=mime)
                    return
                if not path.startswith('/api/'):
                    self._respond({'error': '찾을 수 없습니다.'}, 404)
                    return
                if not self._auth():
                    return
                if path == '/api/state':
                    self._respond(service.get_state())
                elif path.startswith('/api/media/'):
                    image_id = int(path.split('/')[-1]); full = query.get('full', ['0'])[0] == '1'
                    self._respond(service.media(image_id, full), mime='image/jpeg')
                elif path == '/api/detail':
                    self._respond(service.detail(int(query['id'][0])))
                elif path == '/api/export':
                    self._respond(service.export(),
                                  mime='application/json; charset=utf-8', filename='image_captions.json')
                else:
                    self._respond({'error': '찾을 수 없습니다.'}, 404)
            except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
                self._respond({'error': str(exc)}, 400 if not isinstance(exc, FileNotFoundError) else 404)
            except Exception as exc:
                traceback.print_exc()
                self._respond({'error': f'서버 오류: {exc}'}, 500)

        def do_POST(self):
            if not self._auth():
                return
            path = urlparse(self.path).path
            try:
                if path == '/api/upload_image':
                    size = int(self.headers.get('Content-Length', '0'))
                    if size <= 0 or size > MAX_UPLOAD_BYTES:
                        raise ValueError('이미지 파일은 최대 40MB까지 업로드할 수 있습니다.')
                    name = unquote(self.headers.get('X-File-Name', ''))
                    self._respond(service.upload_image(name, self.rfile.read(size)))
                    return
                body = self._body()
                if path == '/api/search':
                    result = service.search(body)
                elif path == '/api/edit_tags':
                    result = service.edit_tags(body)
                elif path == '/api/delete':
                    result = service.delete_image(int(body['id']))
                elif path == '/api/settings':
                    result = service.save_settings(body)
                elif path == '/api/choose_folder':
                    result = {'folder': service.choose_folder()}
                elif path == '/api/scan':
                    result = service.begin_scan(body['folder'])
                elif path == '/api/stop':
                    result = service.stop_scan()
                elif path == '/api/open':
                    result = service.launch_original(int(body['id']), bool(body.get('reveal')))
                else:
                    return self._respond({'error': '찾을 수 없습니다.'}, 404)
                self._respond(result)
            except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
                self._respond({'error': str(exc)}, 400 if not isinstance(exc, FileNotFoundError) else 404)
            except Exception as exc:
                traceback.print_exc()
                self._respond({'error': f'서버 오류: {exc}'}, 500)
    return Handler


def run_app(library=None, *, port=0, open_browser=True):
    """Serve TEA PARTY through a normal browser (no legacy/native GUI)."""
    service = MORIService(library)
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(service))
    server.daemon_threads = True
    host = f'http://127.0.0.1:{server.server_port}/'
    print(f'\nTEA PARTY · {host}\n창을 닫아도 프로그램은 실행 중입니다. 종료: Ctrl+C\n', flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(host, new=1)).start()
    try:
        server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop_scan()
        server.server_close()
    return server


def main():
    parser = argparse.ArgumentParser(description='TEA PARTY - 로컬 이미지 라이브러리')
    parser.add_argument('--browser', action='store_true', help='기본 브라우저에서 열기 (기본 동작)')
    parser.add_argument('--no-browser', action='store_true', help='브라우저 자동 실행 없이 서버만 실행')
    parser.add_argument('--port', type=int, default=0, help='로컬 서버 포트 (0: 자동 선택)')
    args = parser.parse_args()
    if args.browser and args.no_browser:
        parser.error('--browser 와 --no-browser 는 함께 사용할 수 없습니다.')
    return run_app(port=args.port, open_browser=not args.no_browser)


if __name__ == '__main__':
    main()
