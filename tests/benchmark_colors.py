"""Compare scalar JSON color matching and the packed index on this library."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from image_library.core import Library, ROOT, color_distance

library = Library()
start = time.perf_counter()
library.ensure_color_index()
index_seconds = time.perf_counter() - start
with library.connect() as db:
    sources = db.execute("SELECT id,grid FROM images WHERE grid LIKE '%cells%' ORDER BY id DESC LIMIT 1500").fetchall()
assert sources
first = json.loads(sources[0]['grid'])
points = [{'x': (x + .5) / 8, 'y': (y + .5) / 8, 'rgb': first['cells'][y][x][0]['rgb']}
          for y in range(0, 8, 2) for x in range(0, 8, 2)]
start = time.perf_counter()
reference = {row['id']: color_distance(json.loads(row['grid']), points) for row in sources}
old_seconds = time.perf_counter() - start
ids = [row['id'] for row in sources]
start = time.perf_counter()
actual = library.color_scores(ids, points)
new_seconds = time.perf_counter() - start
assert {key for key, score in reference.items() if score <= 35} == {key for key, score in actual.items() if score <= 35}
error = max(abs(reference[key] - actual[key]) for key in reference)
assert error < .0002, error
start = time.perf_counter()
full_results = library.search(points=points, tolerance=35)
full_seconds = time.perf_counter() - start
start = time.perf_counter()
warm_results = library.search(points=points, tolerance=35)
warm_seconds = time.perf_counter() - start
assert [row['id'] for row in warm_results] == [row['id'] for row in full_results]
result = {'images': len(ids), 'painted_cells': len(points), 'index_backfill_seconds': index_seconds,
          'old_color_stage_seconds': old_seconds, 'new_color_stage_seconds': new_seconds,
          'speedup': old_seconds / new_seconds, 'maximum_distance_difference': error,
          'full_library_search_seconds': full_seconds, 'full_library_warm_search_seconds': warm_seconds,
          'full_library_matches': len(full_results)}
(ROOT / 'data/color_search_benchmark.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result), flush=True)
