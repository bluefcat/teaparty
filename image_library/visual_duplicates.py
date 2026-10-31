"""Local visual duplicate lookup. Never deduplicate/delete images automatically.

The legacy `images.hash` is an exact SHA-256 of 32x32 RGB pixels; keep it
unchanged for existing tags/cache.  This supplemental perceptual fingerprint
allows resized or recompressed copies to appear in the 'same hash' view.
"""
from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# 8 low-frequency DCT basis vectors of a 32-pixel luminance image.
_DCT = np.array([
    [math.cos((2 * x + 1) * k * math.pi / 64) for x in range(32)]
    for k in range(8)
], dtype=np.float32)


def fingerprint(path):
    """Return a 64-bit pHash and spatial color samples (8x8 RGB).

    Color is kept to distinguish similarly shaped but differently colored
    artwork. EXIF orientation and transparency follow the main image pipeline.
    """
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode in ('RGBA', 'LA', 'P'):
            rgba = image.convert('RGBA')
            canvas = Image.new('RGBA', rgba.size, 'white')
            image = Image.alpha_composite(canvas, rgba)
        image = image.convert('RGB')
        rgb = np.asarray(image.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float32)
    luminance = rgb @ np.array([.299, .587, .114], dtype=np.float32)
    coefficients = _DCT @ luminance @ _DCT.T
    values = coefficients.reshape(-1)[1:]
    median = float(np.median(values))
    bits = 0
    for value in values:
        bits = (bits << 1) | int(value > median)
    # Keep an unused bit so every hash is 16 fixed-width hex digits.
    bits <<= 1
    color = np.clip(np.rint(np.asarray(Image.fromarray(rgb.astype('uint8')).resize(
        (8, 8), Image.Resampling.LANCZOS))), 0, 255).astype('uint8')
    return f'{bits:016x}', color.tobytes()


def visually_matches(a, b):
    """Conservative review candidates, not proof of identity."""
    ahash, acolor = a
    bhash, bcolor = b
    hamming = (int(ahash, 16) ^ int(bhash, 16)).bit_count()
    if hamming > 9:
        return False
    first = np.frombuffer(acolor, dtype=np.uint8).astype(np.float32).reshape(8, 8, 3)
    second = np.frombuffer(bcolor, dtype=np.uint8).astype(np.float32).reshape(8, 8, 3)
    difference = np.sqrt(np.square(first - second).sum(axis=2))
    return float(difference.mean()) <= 19.0 and float(np.percentile(difference, 90)) <= 36.0


class VisualDuplicateIndex:
    """Versioned SQLite fingerprints; older libraries are indexed on first use.

    No new dependency and no modification of the model/scan hash semantics.
    Subsequent searches reuse cached signatures unless the file changed.
    """
    def __init__(self, library):
        self.library = library
        self.lock = threading.RLock()
        with self.library.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS visual_fingerprints (
                image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
                mtime INTEGER, size INTEGER, phash TEXT NOT NULL, color BLOB NOT NULL)''')

    def _ensure(self, rows):
        with self.library.connect() as db:
            cached = {r['image_id']: r for r in db.execute(
                'SELECT image_id,mtime,size,phash,color FROM visual_fingerprints')}
        missing = [r for r in rows if r['status'] == 'ready' and (
            r['id'] not in cached or cached[r['id']]['mtime'] != r['mtime']
            or cached[r['id']]['size'] != r['size'])]
        if missing:
            def calculate(row):
                try:
                    path = Path(row['path'])
                    before = path.stat()
                    if before.st_mtime_ns != row['mtime'] or before.st_size != row['size']:
                        return None
                    thumb = self.library.thumbs / f"{row['id']}.jpg"
                    # Thumbnails are much cheaper than full-resolution source files
                    # when filling a large pre-existing library's visual index.
                    result = fingerprint(thumb if thumb.is_file() else path)
                    after = path.stat()
                    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                        return None
                    return (row['id'], row['mtime'], row['size'], result[0], result[1])
                except (OSError, ValueError, SyntaxError):
                    return None
            # Bounded batches avoid holding all image pixels in memory.
            with ThreadPoolExecutor(max_workers=3) as pool:
                for start in range(0, len(missing), 128):
                    prepared = [x for x in pool.map(calculate, missing[start:start + 128]) if x]
                    if prepared:
                        with self.library.connect() as db:
                            db.executemany('''INSERT INTO visual_fingerprints(image_id,mtime,size,phash,color)
                                VALUES(?,?,?,?,?) ON CONFLICT(image_id) DO UPDATE SET
                                mtime=excluded.mtime,size=excluded.size,
                                phash=excluded.phash,color=excluded.color''', prepared)
        with self.library.connect() as db:
            return {r['image_id']: (r['phash'], r['color']) for r in db.execute(
                'SELECT image_id,phash,color FROM visual_fingerprints')}

    def find(self, digest, rows):
        if not digest:
            return []
        sources = [row for row in rows if row['hash'] == digest]
        if not sources:
            return []
        exact = {row['id'] for row in sources}
        with self.lock:
            fingerprints = self._ensure(rows)
        reference = next((fingerprints[r['id']] for r in sources if r['id'] in fingerprints), None)
        if reference is None:
            return sources
        return [r for r in rows if r['id'] in exact or (
            r['id'] in fingerprints and visually_matches(reference, fingerprints[r['id']]))]
