"""Checkpoint lookup independent from the ML-Danbooru source repository."""
from pathlib import Path, PureWindowsPath

from .core import ROOT

CHECKPOINT_DIR = ROOT / 'checkpoints'
DEFAULT_CHECKPOINT = CHECKPOINT_DIR / 'ml_caformer_m36_dec-5-97527.ckpt'


def resolve_checkpoint(value=None):
    """Resolve saved/explicit paths, including stale paths from older installations.

    Existing paths take priority. A missing legacy path is mapped by filename
    only, never by loading unrelated checkpoints. Other invalid paths remain
    invalid so that users receive a useful error instead of a model mismatch.
    """
    path = Path(value) if value else DEFAULT_CHECKPOINT
    if path.is_file():
        return path
    # Relocation on a different PC, or migration from an ML-Danbooru folder.
    # A legacy filename will be accepted only if that exact filename exists.
    is_legacy = ('ml-danbooru' in [part.lower() for part in path.parts] or
                 'ml-danbooru' in [part.lower() for part in PureWindowsPath(str(path)).parts])
    if is_legacy:
        filename = PureWindowsPath(str(path)).name
        relocated = CHECKPOINT_DIR / filename
        if relocated.is_file():
            return relocated
        relocated = ROOT / filename
        if relocated.is_file():
            return relocated
    if not value or path == DEFAULT_CHECKPOINT:
        root_file = ROOT / DEFAULT_CHECKPOINT.name
        if root_file.is_file():
            return root_file
    return path
