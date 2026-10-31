"""Measure real model forward calls; excludes loading and file decoding from timings."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from image_library.core import ROOT, read_rgb
from image_library.model import DanbooruTagger

parser = argparse.ArgumentParser()
parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
parser.add_argument('--output', required=True)
args = parser.parse_args()
source = ROOT / 'ML-Danbooru/TaggingSample'
expected = json.loads((source / 'image_captions.json').read_text(encoding='utf-8'))
images = [read_rgb(source / name) for name in expected]
tagger = DanbooruTagger(device=args.device, batch_size=8)
tagger.load()
torch = tagger.torch
for size in (1, 8):
    tagger.effective_batch_size = size
    tagger.predict_many(images[:size])  # Warm up kernels and convolution selection.

records = []
for size in (1, 8):
    tagger.effective_batch_size = size
    calls = []
    handle = tagger.model.register_forward_pre_hook(
        lambda module, inputs: calls.append({'shape': list(inputs[0].shape), 'device': str(inputs[0].device)}))
    if tagger.device.type == 'cuda':
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    try:
        tags = tagger.predict_many(images)
    finally:
        handle.remove()
    if tagger.device.type == 'cuda':
        torch.cuda.synchronize()
    seconds = time.perf_counter() - started
    correct = sum(set(actual) == set(value.split(', ')) for actual, value in zip(tags, expected.values()))
    record = {'requested_batch_size': size, 'forward_calls': calls, 'seconds': seconds,
              'images_per_second': len(images) / seconds, 'sample_matches': correct,
              'peak_vram_mb': torch.cuda.max_memory_allocated() / 1024 ** 2 if tagger.device.type == 'cuda' else None}
    records.append(record)
    print(json.dumps(record, ensure_ascii=True), flush=True)
    assert correct == len(images)
    if size == 8:
        assert len(calls) == 1 and calls[0]['shape'] == [8, 3, 448, 448], calls
result = {'torch': torch.__version__, 'device': str(tagger.device),
          'gpu': torch.cuda.get_device_name() if tagger.device.type == 'cuda' else None,
          'records': records}
Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
