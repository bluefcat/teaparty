"""Flat, copy-on-import image storage for TEA PARTY.

Imported images live directly in data/managed_images/, regardless of their
source directory. Source files are never moved or removed. The optional source
index is private implementation metadata, not a library folder hierarchy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

from .core import EXTENSIONS, atomic_json

MAX_UPLOAD_BYTES = 40 * 1024 * 1024


class ImportCancelled(Exception):
    pass


class ManagedStorage:
    def __init__(self, library):
        self.library = library
        self.root = library.directory.resolve() / 'managed_images'
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.root / 'imports.json'
        self.journal_path = self.root / 'imports.journal'
        self.legacy_registry_path = self.root / 'sources.json'
        self.captions_path = self.root / 'image_captions.json'
        self.hashes_path = self.root / 'content_hashes.json'
        self.lock = threading.RLock()
        self.migrate_legacy()

    @staticmethod
    def inside(path, parent):
        try:
            Path(path).resolve().relative_to(Path(parent).resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _read_dict(path):
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


    @staticmethod
    def _sha256_file(path):
        digest = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sha256_bytes(content):
        return hashlib.sha256(content).hexdigest()

    def _read_hash_index(self):
        hashes = {str(digest): str(filename)
                  for digest, filename in self._read_dict(self.hashes_path).items()
                  if isinstance(digest, str) and isinstance(filename, str)}
        valid = {}
        indexed = set()
        for digest, filename in hashes.items():
            target = self.root / filename
            if target.parent == self.root and target.is_file() and target.suffix.lower() in EXTENSIONS:
                valid[digest] = target.name
                indexed.add(target.name)
        changed = valid != hashes
        for target in sorted(self.root.iterdir()):
            if (not target.is_file() or target.is_symlink() or target.suffix.lower() not in EXTENSIONS
                    or target.name in indexed):
                continue
            valid[self._sha256_file(target)] = target.name
            indexed.add(target.name)
            changed = True
        if changed or not self.hashes_path.exists():
            atomic_json(self.hashes_path, valid)
        return valid

    def _read_indices(self):
        imports = self._read_dict(self.registry_path)
        captions = self._read_dict(self.captions_path)
        # Durable append-only journal avoids rewriting an ever-growing JSON
        # registry for each of thousands of imported images.
        try:
            with self.journal_path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                        if item.get('source'):
                            imports[str(item['source'])] = item['filename']
                        if isinstance(item.get('caption'), str):
                            captions[item['filename']] = item['caption']
                    except (ValueError, KeyError, TypeError):
                        continue  # ignore any incomplete final journal line
        except FileNotFoundError:
            pass
        return imports, captions

    def _record_import(self, source, filename, caption):
        item = {'source': str(source) if source is not None else None,
                'filename': filename, 'caption': caption}
        with self.journal_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + '\n')

    def _commit_indices(self, imports, captions):
        # Do not drop the journal unless *both* compact indexes are saved.
        atomic_json(self.registry_path, imports)
        if captions or self.captions_path.exists():
            atomic_json(self.captions_path, captions)
        self.journal_path.unlink(missing_ok=True)

    @staticmethod
    def _safe_filename(filename):
        name = Path(str(filename).replace('\\', '/')).name
        suffix = Path(name).suffix.lower()
        if suffix not in EXTENSIONS:
            raise ValueError('지원하지 않는 이미지 파일 형식입니다.')
        stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', Path(name).stem).strip(' .')[:110] or 'image'
        return stem, suffix

    def _unique_destination(self, filename):
        stem, suffix = self._safe_filename(filename)
        for index in range(100000):
            name = f'{stem}{suffix}' if index == 0 else f'{stem} ({index}){suffix}'
            dest = self.root / name
            if not dest.exists():
                return dest
        raise RuntimeError('새 이미지의 파일 이름을 지정할 수 없습니다.')

    def _atomic_copy(self, source, target, stat=None):
        temp = self.root / f'.{uuid.uuid4().hex}.partial'
        try:
            shutil.copy2(source, temp)
            if stat:
                after = source.stat()
                if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                    raise RuntimeError(f'복사 중 원본 이미지가 변경되었습니다: {source}')
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def _migrate_index(self, source, dest):
        """Preserve IDs, generated tags, and manual edits for indexed images."""
        with self.library.mutation_lock, self.library.connect() as db:
            old = db.execute('SELECT id FROM images WHERE path=?', (str(source),)).fetchone()
            new = db.execute('SELECT id FROM images WHERE path=?', (str(dest),)).fetchone()
            if old and not new:
                # A changed original is re-inferred on the next scan. Overrides
                # and the original image ID remain associated with this image.
                db.execute('UPDATE images SET path=? WHERE id=?', (str(dest), old['id']))

    def migrate_legacy(self):
        """Safely flatten v9/v10 managed folders on startup, without re-inference.

        Copy first, update the DB, then remove only the old *managed* copy.
        External originals are never touched. Re-running is safe.
        """
        with self.lock:
            old_sources = self._read_dict(self.legacy_registry_path)
            imports, captions = self._read_indices()
            # Source paths in the old registry point to original directories.
            # Longest registered root must win for nested source folders.
            roots = sorted(((Path(d).resolve(), Path(s).resolve()) for d, s in old_sources.items()),
                           key=lambda pair: -len(str(pair[0])))
            for path in sorted(self.root.rglob('*')):
                if not path.is_file() or path.is_symlink() or path.parent == self.root:
                    continue
                if path.suffix.lower() not in EXTENSIONS:
                    continue
                dest = self._unique_destination(path.name)
                legacy_caption = self._read_dict(path.parent / 'image_captions.json').get(path.name)
                original = None
                for old_root, source_root in roots:
                    if self.inside(path, old_root):
                        original = source_root / path.relative_to(old_root)
                        break
                # Do not remove old managed files until DB paths are updated.
                self._atomic_copy(path, dest)
                try:
                    self._migrate_index(path, dest)
                except BaseException:
                    dest.unlink(missing_ok=True)
                    raise
                if original is not None:
                    imports[str(original)] = dest.name
                if isinstance(legacy_caption, str):
                    captions[dest.name] = legacy_caption
                # The journal preserves source mappings and captions if
                # migration is interrupted before the final compact write.
                self._record_import(original, dest.name, legacy_caption)
                path.unlink()
            self._commit_indices(imports, captions)
            self._read_hash_index()
            # Remove old JSON sidecars only when their data has been folded
            # into the root caption index; never delete unrecognized files.
            for child in sorted(self.root.rglob('image_captions.json')):
                if child.parent != self.root and child.is_file():
                    entries = self._read_dict(child)
                    if not entries or all(not (child.parent / key).exists() for key in entries):
                        child.unlink()
            for directory in sorted((p for p in self.root.rglob('*') if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    # Unknown data is preserved, never removed to force flatness.
                    pass

    def import_folder(self, source, cancel=None, progress=None):
        source = Path(source).expanduser().resolve()
        if not source.is_dir():
            raise ValueError('가져올 폴더를 찾을 수 없습니다.')
        if self.inside(source, self.library.directory) or self.inside(self.root, source):
            raise ValueError('프로그램 데이터 폴더는 이미지 원본 폴더로 추가할 수 없습니다.')
        cancel = cancel or threading.Event()
        paths = []
        for base, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = [d for d in dirs if d not in ('.venv', '.venv-gpu', '.git')
                       and not Path(base, d).is_symlink()
                       and not self.inside(Path(base, d), self.library.directory)]
            if cancel.is_set():
                raise ImportCancelled()
            for filename in files:
                path = Path(base, filename)
                if path.suffix.lower() in EXTENSIONS and not path.is_symlink():
                    paths.append(path)
        paths.sort()
        copied = skipped = duplicates = 0
        targets = []
        with self.lock:
            imports, captions = self._read_indices()
            hashes = self._read_hash_index()
            caption_maps = {}
            for index, path in enumerate(paths):
                if cancel.is_set():
                    raise ImportCancelled()
                stat = path.stat()
                source_hash = self._sha256_file(path)
                mapped = imports.get(str(path))
                target = self.root / mapped if isinstance(mapped, str) else None
                if target is not None and (target.parent != self.root or target.suffix.lower() not in EXTENSIONS):
                    target = None  # ignore malformed/stale source index entries
                existing_name = hashes.get(source_hash)
                existing = self.root / existing_name if isinstance(existing_name, str) else None
                if existing is not None and (existing.parent != self.root or not existing.exists()
                                             or existing.suffix.lower() not in EXTENSIONS):
                    existing = None
                # Merge old caption tags by the new unique destination filename.
                if path.parent not in caption_maps:
                    caption_maps[path.parent] = self._read_dict(path.parent / 'image_captions.json')
                caption_value = caption_maps[path.parent].get(path.name)
                if existing is not None and (target is None or target.resolve() != existing.resolve()):
                    duplicates += 1
                    self._migrate_index(path, existing)
                    if isinstance(caption_value, str):
                        captions.setdefault(existing.name, caption_value)
                    imports[str(path)] = existing.name
                    self._record_import(path, existing.name, caption_value)
                    if progress:
                        progress(index + 1, len(paths), path)
                    continue
                if target is None or not target.exists():
                    target = self._unique_destination(path.name)
                cached = target.stat() if target.exists() else None
                if cached and (stat.st_size, stat.st_mtime_ns) == (cached.st_size, cached.st_mtime_ns):
                    skipped += 1
                else:
                    self._atomic_copy(path, target, stat)
                    copied += 1
                self._migrate_index(path, target)
                targets.append(target)
                if isinstance(caption_value, str):
                    captions[target.name] = caption_value
                imports[str(path)] = target.name
                hashes[source_hash] = target.name
                self._record_import(path, target.name, caption_value)
                if progress:
                    progress(index + 1, len(paths), path)
            self._commit_indices(imports, captions)
            atomic_json(self.hashes_path, hashes)
        return {'copied': copied, 'unchanged': skipped, 'duplicates': duplicates, 'total': len(paths), 'paths': targets}

    @staticmethod
    def _restore_metadata_files(snapshots):
        """Restore metadata byte-for-byte if the SQLite transaction fails."""
        for destination, content in snapshots.items():
            if content is None:
                destination.unlink(missing_ok=True)
                continue
            temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.restore')
            try:
                temporary.write_bytes(content)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

    def delete_image(self, image_id, *, trash=None):
        """Remove one managed image from disk, SQLite, JSON indices, and thumbs.

        Image backup + transactional SQLite/JSON changes allow restoration on
        common failures (trash operation, metadata write, SQLite commit).
        Other source originals are never deleted.
        """
        if trash is None:
            from send2trash import send2trash
            trash = send2trash
        # Imports use storage lock -> DB mutation lock; preserve that order.
        with self.lock, self.library.mutation_lock:
            with self.library.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT path FROM images WHERE id=?', (image_id,)).fetchone()
                if row is None:
                    raise FileNotFoundError('이미지가 라이브러리에 없습니다.')
                target = Path(row['path']).absolute()
                if target.parent != self.root or target.is_symlink():
                    raise ValueError('외부 경로의 이미지는 내부 저장소로 가져온 후 삭제해 주세요.')
                if not target.is_file():
                    raise FileNotFoundError('내부 저장소의 원본 이미지가 없습니다.')

                imports, captions = self._read_indices()
                hashes = self._read_hash_index()
                updated_imports = {source: name for source, name in imports.items()
                                   if name != target.name}
                updated_captions = {name: caption for name, caption in captions.items()
                                    if name != target.name}
                updated_hashes = {digest: name for digest, name in hashes.items()
                                  if name != target.name}
                metadata_paths = (self.registry_path, self.captions_path,
                                  self.journal_path, self.hashes_path)
                snapshots = {path: path.read_bytes() if path.is_file() else None
                             for path in metadata_paths}

                # Keep one temporary copy until both the recycle-bin operation
                # and SQLite COMMIT have completed. Never touch an external original.
                backup_dir = self.library.directory / '.delete_backups'
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / f'{uuid.uuid4().hex}.backup'
                thumbnail = self.library.thumbs / f'{image_id}.jpg'
                thumb_backup = backup_dir / f'{uuid.uuid4().hex}.thumb-backup'
                shutil.copy2(target, backup)
                committed = False
                try:
                    if thumbnail.is_file():
                        shutil.copy2(thumbnail, thumb_backup)
                        thumbnail.unlink()
                    self._commit_indices(updated_imports, updated_captions)
                    atomic_json(self.hashes_path, updated_hashes)
                    trash(str(target))
                    db.execute('DELETE FROM images WHERE id=?', (image_id,))
                    # Explicit commit while backup remains available for rescue.
                    db.commit()
                    committed = True
                    self.library.deleted_paths.add(str(target))
                except BaseException:
                    # The active SQLite transaction rolls back on context exit.
                    if not committed:
                        if not target.is_file() and backup.is_file():
                            shutil.copy2(backup, target)
                        if not thumbnail.exists() and thumb_backup.is_file():
                            shutil.copy2(thumb_backup, thumbnail)
                        self._restore_metadata_files(snapshots)
                    raise
                finally:
                    # If restoration failed, leave the backup for manual recovery.
                    if committed or target.is_file():
                        backup.unlink(missing_ok=True)
                    if committed or thumbnail.is_file():
                        thumb_backup.unlink(missing_ok=True)

            return str(target)

    def store_upload(self, filename, content, *, return_info=False):
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ValueError('이미지 파일 크기는 40MB 이하여야 합니다.')
        stem, suffix = self._safe_filename(filename)
        try:
            with Image.open(BytesIO(content)) as image:
                image.verify()
        except (OSError, ValueError, SyntaxError) as exc:
            raise ValueError('유효하지 않은 이미지 파일입니다.') from exc
        content_hash = self._sha256_bytes(content)
        with self.lock:
            hashes = self._read_hash_index()
            existing_name = hashes.get(content_hash)
            existing = self.root / existing_name if isinstance(existing_name, str) else None
            if existing is not None and existing.parent == self.root and existing.exists() and existing.suffix.lower() in EXTENSIONS:
                return {'path': existing, 'duplicate': True} if return_info else existing
            target = self._unique_destination(f'{stem}{suffix}')
            # Atomic creation: only complete images are visible to the scanner.
            temp = self.root / f'.{uuid.uuid4().hex}.partial'
            try:
                temp.write_bytes(content)
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
            hashes[content_hash] = target.name
            atomic_json(self.hashes_path, hashes)
            return {'path': target, 'duplicate': False} if return_info else target
