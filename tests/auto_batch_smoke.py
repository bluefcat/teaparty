"""Profile the real GPU with a representative image, then verify real tags."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from image_library.core import ROOT, read_rgb
from image_library.model import DanbooruTagger

events = []
def emit(event):
    events.append(event)
    if event['type'] in ('batch_probe', 'batch_profile', 'batch_probe_failed', 'batch_selected'):
        print(json.dumps(event, ensure_ascii=True), flush=True)
model = DanbooruTagger(batch_size='auto', device='cuda', emit=emit)
source = ROOT / 'ML-Danbooru/TaggingSample'
expected = json.loads((source / 'image_captions.json').read_text(encoding='utf-8'))
images = [read_rgb(source / name) for name in expected]
selected = model.configure_batch(images[0], available_count=128)
actual = model.predict_many(images)
assert all(set(tags) == set(value.split(', ')) for tags, value in zip(actual, expected.values()))
assert selected == model.effective_batch_size
calls = []
hook = model.model.register_forward_pre_hook(lambda module, args: calls.append(list(args[0].shape)))
try:
    full_batch = model.predict_many([images[i % len(images)] for i in range(selected)])
finally:
    hook.remove()
expected_tags = list(expected.values())
assert all(set(tags) == set(expected_tags[i % len(images)].split(', ')) for i, tags in enumerate(full_batch))
assert calls == [[selected, 3, 448, 448]], calls
result = {'gpu': model.torch.cuda.get_device_name(), 'selected': selected,
          'profiles': model.batch_profiles, 'events': events, 'actual_forward_calls': calls}
(ROOT / 'data/auto_batch_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'selected': selected, 'profiles': model.batch_profiles,
                  'selection': next(event for event in events if event['type'] == 'batch_selected'),
                  'sample_matches': len(actual)}, ensure_ascii=True), flush=True)
