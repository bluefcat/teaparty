"""Packed Lab descriptors and vectorized region matching."""
import numpy as np

SHAPE = (64, 6, 3)
BYTES = 64 * 6 * 3 * 4


def pack_grid(grid):
    from .core import rgb_to_lab
    lab = np.full(SHAPE, np.inf, dtype='<f4')
    if isinstance(grid, dict):
        positions, colors = [], []
        for y, row in enumerate(grid['cells']):
            for x, cell in enumerate(row):
                values = [value['rgb'] for value in cell if value['weight'] >= .03][:6]
                for index, rgb in enumerate(values):
                    positions.append((y * 8 + x, index))
                    colors.append(rgb)
        if colors:
            indices, slots = zip(*positions)
            lab[indices, slots] = rgb_to_lab(colors)
    elif grid:
        lab[:, 0] = rgb_to_lab(np.asarray(grid).reshape(64, 3))
    return lab.tobytes()


def distances(blobs, points):
    from .core import rgb_to_lab
    labs = np.frombuffer(b''.join(blobs), dtype='<f4').reshape((-1,) + SHAPE)
    cells = [min(7, max(0, int(p['y'] * 8))) * 8 + min(7, max(0, int(p['x'] * 8))) for p in points]
    targets = np.asarray(rgb_to_lab([p['rgb'] for p in points]), dtype=np.float32)
    delta = labs[:, cells] - targets[None, :, None, :]
    squared = np.einsum('...c,...c->...', delta, delta)
    # Min over local colors, max over required positions; identical semantics
    # to the scalar reference, with a single square root per image.
    return np.sqrt(squared.min(axis=2).max(axis=1))
