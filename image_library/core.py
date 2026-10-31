from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from .progress import ScanEstimate
from .color_search import pack_grid, distances as color_distances

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff'}


def normalize_tag(tag):
    return ' '.join(tag.strip().lower().replace('_', ' ').split())


def read_rgb(path):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode in ('RGBA', 'LA', 'P'):
            rgba = image.convert('RGBA')
            background = Image.new('RGBA', rgba.size, 'white')
            image = Image.alpha_composite(background, rgba)
        return image.convert('RGB')


def features(path):
    image = read_rgb(path)
    # A deterministic color-and-position fingerprint. This is an exact match
    # of a reduced image, not a promise of perceptual duplicate detection.
    small = image.resize((32, 32), Image.Resampling.LANCZOS)
    digest = hashlib.sha256(small.tobytes()).hexdigest()
    colors, grid = color_features(image)
    return digest, image.width, image.height, colors, grid, image


def dominant_colors(image, count=6):
    quantized = image.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    total = image.width * image.height
    return [{'rgb': palette[index * 3:index * 3 + 3], 'weight': round(number / total, 4)}
            for number, index in sorted(quantized.getcolors(), reverse=True)]


def color_features(image):
    # Keep several actual colors per region: averaging red and blue to purple
    # erased both searchable colors in the original index.
    sample = image.resize((128, 128), Image.Resampling.NEAREST)
    cells = [[dominant_colors(sample.crop((x * 16, y * 16, (x + 1) * 16, (y + 1) * 16)), 6)
              for x in range(8)] for y in range(8)]
    grid = {'version': 2, 'cells': cells}
    return dominant_colors(sample), grid


def color_distance(grid, points):
    """Require each painted position to match a real, nontrivial local color."""
    if not points:
        return 0.0
    distances = []
    for point in points:
        x, y = (min(7, max(0, int(point[axis] * 8))) for axis in ('x', 'y'))
        if isinstance(grid, dict):
            colors = [c['rgb'] for c in grid['cells'][y][x] if c['weight'] >= .03]
        else:  # Backward compatibility while the old index is being upgraded.
            colors = [grid[y][x]]
        if not colors:
            return float('inf')
        differences = np.linalg.norm(rgb_to_lab(colors) - rgb_to_lab(point['rgb']), axis=-1)
        distances.append(float(differences.min()))
    # Mean distance allowed a perfect match to hide an entirely wrong region.
    return max(distances)


def rgb_to_lab(rgb):
    rgb = np.asarray(rgb, dtype=float) / 255
    linear = np.where(rgb > .04045, ((rgb + .055) / 1.055) ** 2.4, rgb / 12.92)
    xyz = linear @ np.array([[.4124564, .3575761, .1804375],
                             [.2126729, .7151522, .0721750],
                             [.0193339, .1191920, .9503041]]).T
    xyz /= np.array([.95047, 1., 1.08883])
    f = np.where(xyz > .008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


class Library:
    def __init__(self, directory=DATA):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.thumbs = self.directory / 'thumbnails'
        self.thumbs.mkdir(exist_ok=True)
        self.path = self.directory / 'library.sqlite3'
        self.mutation_lock = threading.RLock()
        self.deleted_paths = set()
        self.cache_lock = threading.RLock()
        self.metadata_cache = None
        self.metadata_version = None
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                    mtime INTEGER, size INTEGER, hash TEXT, width INTEGER,
                    height INTEGER, colors TEXT, grid TEXT, status TEXT,
                    error TEXT, signature TEXT);
                CREATE INDEX IF NOT EXISTS image_hash ON images(hash);
                CREATE TABLE IF NOT EXISTS tags (
                    image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,
                    tag TEXT NOT NULL, PRIMARY KEY(image_id, tag));
                CREATE INDEX IF NOT EXISTS tag_name ON tags(tag);
                CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS color_index (
                    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
                    lab BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS tag_overrides (
                    image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,
                    tag TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('add','remove')),
                    PRIMARY KEY(image_id, tag));
                CREATE TABLE IF NOT EXISTS image_sequence (id INTEGER PRIMARY KEY AUTOINCREMENT);
                CREATE TABLE IF NOT EXISTS library_revision (id INTEGER PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO library_revision VALUES(1,0);
            ''')
            for table in ('images', 'tags', 'tag_overrides'):
                for action in ('INSERT', 'UPDATE', 'DELETE'):
                    db.execute(f'CREATE TRIGGER IF NOT EXISTS revision_{table}_{action.lower()} '
                               f'AFTER {action} ON {table} BEGIN '
                               'UPDATE library_revision SET value=value+1 WHERE id=1; END')
            maximum = db.execute('SELECT COALESCE(MAX(id),0) FROM images').fetchone()[0]
            sequence = db.execute('SELECT COALESCE(MAX(id),0) FROM image_sequence').fetchone()[0]
            if maximum > sequence:
                db.execute('INSERT INTO image_sequence(id) VALUES(?)', (maximum,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_path(self, path):
        with self.connect() as db:
            row = db.execute('SELECT * FROM images WHERE path=?', (str(path),)).fetchone()
            return dict(row) if row else None

    def pending_count(self, paths, signature):
        with self.connect() as db:
            ready = {row['path']: row for row in db.execute(
                "SELECT path,mtime,size FROM images WHERE signature=? AND status='ready' "
                "AND substr(ltrim(grid),1,1)='{'", (signature,))}
        count = 0
        for path in paths:
            old = ready.get(str(path))
            try:
                stat = path.stat()
                if old and (stat.st_mtime_ns, stat.st_size) == (old['mtime'], old['size']):
                    continue
            except OSError:
                pass
            count += 1
        return count

    def folders(self):
        with self.connect() as db:
            folders = {r[0] for r in db.execute('SELECT path FROM folders')}
            folders.update(str(Path(r[0]).parent) for r in db.execute('SELECT path FROM images'))
            return sorted(folders, key=str.casefold)

    def snapshot(self, include_features=True):
        with self.connect() as db:
            db.execute('BEGIN')
            version = db.execute('SELECT value FROM library_revision WHERE id=1').fetchone()[0]
            if not include_features:
                with self.cache_lock:
                    if self.metadata_version == version:
                        return [dict(row, tags=row['tags'].copy()) for row in self.metadata_cache]
            projection = '*' if include_features else (
                'id,path,mtime,size,hash,width,height,colors,NULL AS grid,status,error,signature')
            rows = [dict(r) for r in db.execute(f'SELECT {projection} FROM images ORDER BY id DESC')]
            tags = {}
            for r in db.execute('SELECT image_id, tag FROM tags ORDER BY tag'):
                tags.setdefault(r[0], []).append(r[1])
            overrides = {}
            for row in db.execute('SELECT image_id,tag,action FROM tag_overrides'):
                overrides.setdefault(row[0], []).append((row[1], row[2]))
        counts = Counter(r['hash'] for r in rows if r['hash'])
        for row in rows:
            effective = set(tags.get(row['id'], []))
            for tag, action in overrides.get(row['id'], []):
                effective.add(tag) if action == 'add' else effective.discard(tag)
            row['tags'] = sorted(effective)
            row['duplicate'] = bool(row['hash'] and counts[row['hash']] > 1)
            if row['duplicate']:
                row['tags'].append('중복')
            row['colors'] = json.loads(row['colors'] or '[]')
            row['grid'] = json.loads(row['grid'] or '[]')
        if not include_features:
            with self.cache_lock:
                if self.metadata_version is None or version >= self.metadata_version:
                    self.metadata_version, self.metadata_cache = version, rows
            return [dict(row, tags=row['tags'].copy()) for row in rows]
        return rows

    def reusable(self, digest, signature):
        with self.connect() as db:
            row = db.execute("SELECT id FROM images WHERE hash=? AND signature=? AND status='ready' LIMIT 1",
                             (digest, signature)).fetchone()
            if row:
                return [r[0] for r in db.execute('SELECT tag FROM tags WHERE image_id=?', (row[0],))]
        return None

    def save(self, path, stat, digest, width, height, colors, grid, tags, status, error, signature):
        packed = pack_grid(grid)
        with self.mutation_lock, self.connect() as db:
            if not Path(path).is_file():
                raise FileNotFoundError(f'이미지가 사라졌습니다: {path}')
            self.deleted_paths.discard(str(path))
            existing = db.execute('SELECT id FROM images WHERE path=?', (str(path),)).fetchone()
            image_id = existing[0] if existing else db.execute('INSERT INTO image_sequence DEFAULT VALUES').lastrowid
            db.execute('''INSERT INTO images(id,path,mtime,size,hash,width,height,colors,grid,status,error,signature)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                mtime=excluded.mtime,size=excluded.size,hash=excluded.hash,width=excluded.width,
                height=excluded.height,colors=excluded.colors,grid=excluded.grid,
                status=excluded.status,error=excluded.error,signature=excluded.signature''',
                       (image_id, str(path), stat.st_mtime_ns, stat.st_size, digest, width, height,
                        json.dumps(colors), json.dumps(grid), status, error, signature))
            image_id = db.execute('SELECT id FROM images WHERE path=?', (str(path),)).fetchone()[0]
            db.execute('DELETE FROM tags WHERE image_id=?', (image_id,))
            db.executemany('INSERT OR IGNORE INTO tags VALUES (?,?)',
                           [(image_id, normalize_tag(t)) for t in tags if normalize_tag(t) and t != '중복'])
            db.execute('INSERT OR REPLACE INTO color_index VALUES(?,?)', (image_id, packed))
        return image_id

    def search(self, tags=(), folder='', points=(), tolerance=35, any_tag=False, rows=None):
        rows = self.snapshot(include_features=False) if rows is None else rows
        wanted = {normalize_tag(t) for t in tags if normalize_tag(t)}
        if folder:
            rows = [r for r in rows if is_under(r['path'], folder)]
        if wanted:
            rows = [r for r in rows if (bool(wanted.intersection(r['tags'])) if any_tag
                                       else wanted.issubset(r['tags']))]
        if points:
            scores = self.color_scores([row['id'] for row in rows], points)
            ranked = [dict(row, distance=scores[row['id']]) for row in rows
                      if scores.get(row['id'], float('inf')) <= tolerance]
            rows = sorted(ranked, key=lambda r: r['distance'])
        return rows

    def color_scores(self, image_ids, points):
        scores = {}
        for start in range(0, len(image_ids), 256):
            with self.connect() as db:
                ids = image_ids[start:start + 256]
                marks = ','.join('?' for _ in ids)
                rows = db.execute(f'SELECT id,lab,CASE WHEN lab IS NULL THEN grid END AS source '
                                  f'FROM images LEFT JOIN color_index ON image_id=id WHERE id IN ({marks})', ids).fetchall()
                records = []
                for row in rows:
                    packed = row['lab']
                    if packed is None:
                        grid = json.loads(row['source'] or '[]')
                        packed = pack_grid(grid)
                        # Only cache the descriptor if the source did not change meanwhile.
                        db.execute('INSERT OR REPLACE INTO color_index SELECT id,? FROM images WHERE id=? AND grid IS ?',
                                   (packed, row['id'], row['source']))
                    records.append((row['id'], packed))
                if records:
                    values = color_distances([record[1] for record in records], points)
                    scores.update((record[0], float(value)) for record, value in zip(records, values))
        return scores

    def ensure_color_index(self, cancel=None):
        cancel = cancel or threading.Event()
        while not cancel.is_set():
            with self.connect() as db:
                rows = db.execute('SELECT id,grid FROM images LEFT JOIN color_index ON image_id=id '
                                  'WHERE lab IS NULL LIMIT 128').fetchall()
            if not rows:
                return
            packed = [(pack_grid(json.loads(row['grid'] or '[]')), row['id'], row['grid']) for row in rows]
            with self.connect() as db:
                db.executemany('INSERT OR REPLACE INTO color_index SELECT id,? FROM images WHERE id=? AND grid IS ?', packed)

    def upgrade_colors(self, cancel=None):
        """Upgrade legacy descriptors without inference or changing tag history."""
        cancel = cancel or threading.Event()
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT id,path,mtime,size FROM images "
                                                    "WHERE substr(ltrim(grid),1,1)='[' AND grid<>'[]'")]
        report = {'updated': 0, 'errors': []}
        def upgrade(row):
            if cancel.is_set():
                return 0, None
            try:
                path = Path(row['path'])
                before = path.stat()
                if (before.st_mtime_ns, before.st_size) != (row['mtime'], row['size']):
                    return 0, None
                colors, grid = color_features(read_rgb(path))
                after = path.stat()
                if (after.st_mtime_ns, after.st_size) != (row['mtime'], row['size']):
                    return 0, None
                with self.connect() as db:
                    result = db.execute('UPDATE images SET colors=?,grid=? WHERE id=? AND mtime=? AND size=?',
                                        (json.dumps(colors), json.dumps(grid), row['id'], row['mtime'], row['size']))
                    if result.rowcount:
                        db.execute('INSERT OR REPLACE INTO color_index VALUES(?,?)', (row['id'], pack_grid(grid)))
                return result.rowcount, None
            except Exception as exc:
                return 0, f"{row['path']}: {exc}"
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='color-upgrade') as pool:
            # Submit a bounded window so a large library does not flood the executor.
            pending = deque()
            iterator = iter(rows)
            while not cancel.is_set():
                while len(pending) < 4:
                    row = next(iterator, None)
                    if row is None:
                        break
                    pending.append(pool.submit(upgrade, row))
                if not pending:
                    break
                count, error = pending.popleft().result()
                report['updated'] += count
                if error:
                    report['errors'].append(error)
        self.ensure_color_index(cancel)
        return report

    def edit_tags(self, image_id, add=(), remove=()):
        additions = {normalize_tag(tag) for tag in add if normalize_tag(tag)}
        removals = {normalize_tag(tag) for tag in remove if normalize_tag(tag)}
        if '중복' in additions | removals:
            raise ValueError('중복은 같은 해시의 이미지에 자동으로 표시되는 태그입니다.')
        if not additions and not removals:
            raise ValueError('변경할 태그를 입력해 주세요.')
        with self.mutation_lock, self.connect() as db:
            if not db.execute('SELECT 1 FROM images WHERE id=?', (image_id,)).fetchone():
                raise ValueError('이미지가 라이브러리에 없습니다.')
            db.executemany('INSERT INTO tag_overrides VALUES(?,?,?) ON CONFLICT(image_id,tag) '
                           'DO UPDATE SET action=excluded.action',
                           [(image_id, tag, 'add') for tag in additions] +
                           [(image_id, tag, 'remove') for tag in removals])

    def trash_image(self, image_id, trash=None):
        if trash is None:
            from send2trash import send2trash
            if os.name == 'nt' and send2trash.__module__ != 'send2trash.win.modern':
                raise RuntimeError('안전한 휴지통 이동 모듈이 필요합니다. start.bat으로 다시 실행해 주세요.')
            trash = send2trash
        with self.mutation_lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT path FROM images WHERE id=?', (image_id,)).fetchone()
            if row is None:
                raise ValueError('이미지가 라이브러리에 없습니다.')
            path = Path(row['path'])
            if path.exists():
                if not path.is_file():
                    raise ValueError('파일이 아닌 경로는 삭제할 수 없습니다.')
                trash(str(path))
            db.execute('DELETE FROM images WHERE id=?', (image_id,))
            self.deleted_paths.add(str(path))
        try:
            (self.thumbs / f'{image_id}.jpg').unlink(missing_ok=True)
        except OSError:
            pass
        return str(path)

    def was_deleted(self, path):
        with self.mutation_lock:
            return str(path) in self.deleted_paths

    def export(self, destination, folder=''):
        rows = self.search(folder=folder)
        names = Counter(Path(r['path']).name for r in rows)
        # Keep the sample's basename format unless names collide.
        result = {(Path(r['path']).name if names[Path(r['path']).name] == 1 else r['path']):
                  ', '.join(r['tags']) for r in rows}
        atomic_json(Path(destination), result)
        return len(result)

    def similar(self, image_id, limit=24):
        """Rank indexed images by weighted tag overlap, without external requests."""
        rows = self.snapshot(include_features=False)
        target = next((row for row in rows if row['id'] == image_id), None)
        if target is None:
            return []
        tag_sets = {row['id']: set(row['tags']) - {'중복'} for row in rows}
        wanted = tag_sets[image_id]
        if not wanted:
            return []
        frequencies = Counter(tag for tags in tag_sets.values() for tag in tags)
        weights = {tag: math.log1p(len(rows) / count) for tag, count in frequencies.items()}
        ranked = []
        for row in rows:
            if row['id'] == image_id or row['status'] != 'ready':
                continue
            tags = tag_sets[row['id']]
            common = wanted & tags
            if not common:
                continue
            row['similarity'] = sum(weights[t] for t in common) / sum(weights[t] for t in wanted | tags)
            row['shared_tags'] = len(common)
            ranked.append(row)
        ranked.sort(key=lambda row: (-row['similarity'], -row['shared_tags'], -row['id']))
        return ranked[:max(0, limit)]


def is_under(path, folder):
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(folder)]) == os.path.normcase(folder)
    except ValueError:
        return False


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Scanner:
    def __init__(self, library, tagger, emit=lambda event: None, workers=2, batch_size=None):
        self.library, self.tagger, self.emit = library, tagger, emit
        self.cancel = threading.Event()
        self.workers = max(1, min(4, workers))
        self.batch_size = max(1, min(128, int(batch_size if batch_size is not None else getattr(tagger, 'batch_size', 1))))
        self.auto_batch = batch_size is None and getattr(tagger, 'auto_batch', False)
        if hasattr(tagger, 'cancel'):
            tagger.cancel = self.cancel

    def prepare(self, path, signature):
        if self.cancel.is_set():
            return {'path': path, 'cancelled': True}
        stat = path.stat()
        old = self.library.get_path(path)
        if (old and old['mtime'] == stat.st_mtime_ns and old['size'] == stat.st_size
                and old['status'] == 'ready' and old['signature'] == signature
                and isinstance(json.loads(old['grid'] or '[]'), dict)):
            return {'path': path, 'skipped': True}
        digest, width, height, colors, grid, image = features(path)
        thumbnail = image.copy()
        thumbnail.thumbnail((400, 600), Image.Resampling.LANCZOS)
        stream = BytesIO()
        thumbnail.save(stream, format='JPEG', quality=85)
        # Bound queued memory and prepare the exact PIL resize used by the model.
        size = getattr(self.tagger, 'input_size', 640)
        resized = image.resize((size, size), Image.Resampling.BILINEAR)
        return dict(path=path, stat=stat, digest=digest, width=width, height=height,
                    colors=colors, grid=grid, image=resized, thumbnail=stream.getvalue())

    def persist(self, item, tags, status, error, signature):
        path, stat = item['path'], item['stat']
        after = path.stat()
        if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
            raise RuntimeError('분석 중 파일이 변경되었습니다. 다시 스캔해 주세요.')
        image_id = self.library.save(path, stat, item['digest'], item['width'], item['height'],
                                    item['colors'], item['grid'], tags, status, error, signature)
        destination = self.library.thumbs / f'{image_id}.jpg'
        temporary = destination.with_suffix('.tmp')
        temporary.write_bytes(item['thumbnail'])
        temporary.replace(destination)
        return path, error

    def record_error(self, path, exc, signature):
        try:
            stat = path.stat()
            self.library.save(path, stat, None, 0, 0, [], [], [], 'error', str(exc), signature)
        except OSError:
            pass
        self.emit({'type': 'file_error', 'path': str(path), 'error': str(exc)})

    def run(self, folder, use_json=False, only_paths=None):
        estimate = ScanEstimate()
        folder = Path(folder).resolve()
        if not folder.is_dir():
            raise ValueError('존재하는 폴더를 선택해 주세요.')
        with self.library.connect() as db:
            db.execute('INSERT OR IGNORE INTO folders VALUES(?)', (str(folder),))
        paths, walk_errors = [], []
        if only_paths is None:
            for base, dirs, files in os.walk(folder, onerror=walk_errors.append):
                dirs[:] = [d for d in dirs if d not in {'.venv', '.venv-gpu', '.git'} and
                           Path(base, d).resolve() != self.library.directory.resolve()]
                if self.cancel.is_set():
                    break
                paths.extend(Path(base, f).resolve() for f in files if Path(f).suffix.lower() in EXTENSIONS)
        else:
            # Incremental managed imports: never remove other library images
            # just because they are not part of this particular import.
            paths = sorted({Path(path).resolve() for path in only_paths})
            if any(not is_under(str(path), str(folder)) for path in paths):
                raise ValueError('분석 경로는 라이브러리 내부여야 합니다.')
            paths = [path for path in paths if path.suffix.lower() in EXTENSIONS]
        paths.sort()
        summary = dict(total=len(paths), processed=0, skipped=0, errors=0, reused=0,
                       cancelled=False, walk_errors=[str(e) for e in walk_errors])
        captions, reusable = {}, {}
        signature = self.tagger.signature + (':json' if use_json else '')
        available_count = self.library.pending_count(paths, signature) if self.auto_batch else len(paths)
        finished = 0
        last_metrics = last_progress = last_indexed = 0

        def metrics(force=False):
            nonlocal last_metrics
            now = time.monotonic()
            value = estimate.observe(finished, len(paths), now)
            if force or now - last_metrics >= .15 or finished == len(paths):
                self.emit({'type': 'scan_metrics', **value})
                last_metrics = now

        metrics(force=True)

        def failure(path, error):
            if self.library.was_deleted(path):
                summary['skipped'] += 1
            else:
                summary['errors'] += 1
                self.record_error(path, error, signature)

        def finish(future, path):
            nonlocal finished, last_indexed
            try:
                _, error = future.result()
                if error:
                    summary['errors'] += 1
                    self.emit({'type': 'file_error', 'path': str(path), 'error': error})
                summary['processed'] += 1
                if time.monotonic() - last_indexed >= .3:
                    self.emit({'type': 'indexed', 'path': str(path)})
                    last_indexed = time.monotonic()
            except Exception as exc:
                failure(path, exc)
            finally:
                finished += 1
                metrics()

        with (ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix='image-prepare') as preparation,
              ThreadPoolExecutor(max_workers=2, thread_name_prefix='image-save') as saving):
            pending, writes = deque(), deque()
            inference = []

            def queue_save(item, tags, status='ready', error=''):
                if status == 'ready':
                    reusable[item['digest']] = tags
                item.pop('image', None)
                writes.append((item['path'], saving.submit(self.persist, item, tags, status, error, signature)))

            def flush_inference():
                if not inference:
                    return
                groups = {}
                for item in inference:
                    groups.setdefault(item['digest'], []).append(item)
                unique = [items[0] for items in groups.values()]
                outcomes = None
                if hasattr(self.tagger, 'predict_many'):
                    try:
                        values = self.tagger.predict_many([item['image'] for item in unique])
                        if len(values) != len(unique):
                            raise RuntimeError('배치 추론 결과 수가 이미지 수와 일치하지 않습니다.')
                        outcomes = [(tags, '') for tags in values]
                    except Exception as exc:
                        self.emit({'type': 'batch_fallback', 'count': len(unique), 'error': str(exc)})
                        if len(unique) == 1:
                            outcomes = [([], str(exc))]
                if outcomes is None:
                    outcomes = []
                    # Isolate a failed image rather than failing its whole batch.
                    for item in unique:
                        try:
                            outcomes.append((self.tagger.predict(item['image']), ''))
                        except Exception as exc:
                            outcomes.append(([], str(exc)))
                for items, (tags, error) in zip(groups.values(), outcomes):
                    if not error:
                        summary['reused'] += len(items) - 1
                    for item in items:
                        queue_save(item, tags, 'error' if error else 'ready', error)
                inference.clear()

            iterator = iter(paths)
            completed = 0
            while not self.cancel.is_set():
                while len(pending) < self.workers * 2:
                    path = next(iterator, None)
                    if path is None:
                        break
                    pending.append((path, preparation.submit(self.prepare, path, signature)))
                if not pending:
                    break
                path, future = pending.popleft()
                if time.monotonic() - last_progress >= .1:
                    self.emit({'type': 'progress', 'current': completed, 'total': len(paths), 'path': str(path)})
                    last_progress = time.monotonic()
                try:
                    item = future.result()
                    if self.cancel.is_set() or item.get('cancelled'):
                        break
                    if item.get('skipped'):
                        summary['skipped'] += 1
                        finished += 1
                        metrics()
                    else:
                        digest = item['digest']
                        tags = reusable.get(digest)
                        if tags is None:
                            tags = self.library.reusable(digest, signature)
                        if tags is not None:
                            summary['reused'] += 1
                        elif use_json:
                            json_path = path.parent / 'image_captions.json'
                            if json_path not in captions:
                                data = json.loads(json_path.read_text(encoding='utf-8-sig')) if json_path.exists() else {}
                                if not isinstance(data, dict):
                                    raise ValueError(f'{json_path}: JSON 객체가 필요합니다.')
                                captions[json_path] = data
                            entry = captions[json_path].get(path.name)
                            if isinstance(entry, str):
                                tags = entry.split(',')
                        if tags is None:
                            if self.auto_batch and not self.tagger.batch_configured:
                                self.batch_size = self.tagger.configure_batch(item['image'], max(1, available_count))
                                if self.cancel.is_set():
                                    break
                            inference.append(item)
                            target = getattr(self.tagger, 'effective_batch_size', self.batch_size)
                            if len(inference) >= target:
                                flush_inference()
                        else:
                            queue_save(item, tags)
                    completed += 1
                except Exception as exc:
                    failure(path, exc)
                    completed += 1
                    finished += 1
                    metrics()
                # Bounded writes overlap later inference, but cannot grow without limit.
                while writes and (writes[0][1].done() or len(writes) >= 4):
                    written_path, write = writes.popleft()
                    finish(write, written_path)
            for _, future in pending:
                future.cancel()
            if not self.cancel.is_set():
                flush_inference()
            # Flush already inferred images before announcing completion/cancellation.
            for path, future in writes:
                finish(future, path)
        summary['cancelled'] = summary['cancelled'] or self.cancel.is_set()
        summary['elapsed'] = estimate.observe(finished, len(paths))['elapsed']
        # Only prune missing paths after a complete traversal without inaccessible directories.
        if only_paths is None and not summary['cancelled'] and not walk_errors:
            found = {str(p) for p in paths}
            with self.library.connect() as db:
                stale = [(r['id'],) for r in db.execute('SELECT id,path FROM images')
                         if is_under(r['path'], str(folder)) and r['path'] not in found]
                db.executemany('DELETE FROM images WHERE id=?', stale)
        self.emit({'type': 'complete', **summary})
        return summary
