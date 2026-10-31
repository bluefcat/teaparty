"""Adapter using bundled inference architectures and strict checkpoint loading."""
import hashlib
import json
import os
import warnings
import time
from pathlib import Path
from types import SimpleNamespace

from .core import ROOT
from .batching import memory_budget, next_probe, fastest_profile
from .checkpoints import resolve_checkpoint


class DanbooruTagger:
    def __init__(self, checkpoint=None, threshold=.7, architecture='caformer_m36', device='auto', batch_size='auto', emit=None):
        self.checkpoint = resolve_checkpoint(checkpoint)
        self.threshold, self.architecture, self.device_name = threshold, architecture, device
        self.model = None
        self.input_size = 448 if architecture.startswith('caformer') else 640
        self.load_error = None
        self.auto_batch = batch_size == 'auto'
        self.batch_size = 1 if self.auto_batch else max(1, min(128, int(batch_size)))
        self.effective_batch_size = self.batch_size
        self.batch_configured = not self.auto_batch
        self.batch_profiles = []
        self.cancel = None
        self.profiling_seconds = 0.
        self.emit = emit or (lambda event: None)
        stat = self.checkpoint.stat() if self.checkpoint.exists() else None
        metadata = (str(self.checkpoint.resolve()), stat.st_size if stat else 0,
                    stat.st_mtime_ns if stat else 0, threshold, architecture, 'rgb-exif-white-v1')
        self.signature = hashlib.sha256(repr(metadata).encode()).hexdigest()

    def load(self):
        with warnings.catch_warnings():
            # The bundled legacy architecture/timm still uses JIT decorators.
            # Ignore only these deprecation notices during loading, never model
            # errors, checkpoint mismatches, or unrelated warnings.
            warnings.filterwarnings('ignore', category=FutureWarning,
                                    message=r'`torch\.jit\.(script|interface)` is deprecated\..*',
                                    module=r'torch\.jit\._script')
            self._load_model()

    def _load_model(self):
        import torch
        from torchvision import transforms
        if not self.checkpoint.exists():
            raise FileNotFoundError(f'체크포인트를 찾을 수 없습니다: {self.checkpoint}\ncheckpoints/ 폴더에 .ckpt 파일을 넣거나 설정에서 경로를 지정해 주세요.')
        from .inference.models import create_model

        self.torch = torch
        # Leave CPU capacity for image preparation, search, and the Tk event loop.
        torch.set_num_threads(max(1, min(4, (os.cpu_count() or 4) // 2)))
        self.device = torch.device('cuda' if self.device_name == 'auto' and torch.cuda.is_available()
                                   else 'cpu' if self.device_name == 'auto' else self.device_name)
        self.class_map = json.loads((Path(__file__).resolve().parent / 'inference' / 'class.json').read_text(encoding='utf-8'))
        caformer = self.architecture.startswith('caformer')
        args = SimpleNamespace(model_name=self.architecture, num_classes=len(self.class_map),
                               model_path=None, frelu=True, xformers=False,
                               use_ml_decoder=0 if caformer else 1, decoder_embedding=384 if caformer else 1024,
                               num_layers_decoder=4, num_head_decoder=8, num_queries=80,
                               scale_skip=1, num_of_groups=32, zsl=0)
        model = create_model(args, load_head=True)
        # weights_only avoids executing arbitrary pickle objects from a checkpoint.
        state = torch.load(self.checkpoint, map_location='cpu', weights_only=True)
        for key in ('ema', 'model', 'state_dict'):
            if key in state:
                state = state[key]
                break
        state = {k.removeprefix('module.'): v for k, v in state.items()}
        prefix = 'head.decoder.layers.0.multihead_attn.'
        if prefix + 'in_proj_container.q_proj.weight' in state:
            for suffix in ('weight', 'bias'):
                state[prefix + 'in_proj_' + suffix] = torch.cat([
                    state.pop(prefix + 'in_proj_container.' + p + '_proj.' + suffix)
                    for p in ('q', 'k', 'v')], dim=0)
                state[prefix + 'out_proj.' + suffix] = state.pop(prefix + 'proj.' + suffix)
        model.load_state_dict(state, strict=True)
        self.model = model.to(self.device).eval()
        size = 448 if caformer else 640
        self.transform = transforms.Compose([transforms.Resize((size, size)), transforms.ToTensor()])
        self.emit({'type': 'model_ready', 'device': str(self.device),
                   'name': torch.cuda.get_device_name(self.device) if self.device.type == 'cuda' else 'CPU',
                   'batch_size': self.batch_size, 'threads': torch.get_num_threads(), 'auto': self.auto_batch})

    def predict(self, image):
        return self.predict_many([image])[0]

    def predict_many(self, images):
        if not images:
            return []
        self._ensure_loaded()
        if not self.batch_configured:
            self.configure_batch(images[0], len(images))
        result = []
        offset = 0
        while offset < len(images):
            count = min(self.effective_batch_size, len(images) - offset)
            result.extend(self._predict_resilient(images[offset:offset + count]))
            offset += count
        return result

    def _ensure_loaded(self):
        if self.load_error:
            raise RuntimeError(self.load_error)
        if self.model is None:
            try:
                self.load()
            except Exception as exc:
                self.load_error = str(exc)
                raise

    def configure_batch(self, sample, available_count):
        if self.batch_configured:
            return self.effective_batch_size
        self._ensure_loaded()
        started = time.monotonic()
        gpu = self.device.type == 'cuda'
        limit = max(1, min(128 if gpu else 4, available_count))
        baseline, budget = 0, None
        if gpu:
            self.torch.cuda.empty_cache()
            baseline = self.torch.cuda.memory_allocated(self.device)
        profiles, count = [], 1
        while count:
            if self.cancel is not None and self.cancel.is_set():
                return 1
            if gpu:
                free, total = self.torch.cuda.mem_get_info(self.device)
                budget = memory_budget(self.torch.cuda.memory_allocated(self.device),
                                       self.torch.cuda.memory_reserved(self.device), free, total)
            self.emit({'type': 'batch_probe', 'count': count, 'limit': limit, 'device': str(self.device),
                       'budget_bytes': budget})
            try:
                profile = self._profile_batch(sample, count)
            except self.torch.OutOfMemoryError:
                self.emit({'type': 'batch_probe_failed', 'count': count})
                if gpu:
                    self.torch.cuda.empty_cache()
                break
            profiles.append(profile)
            self.emit({'type': 'batch_profile', **profile})
            if gpu:
                # Release unused shape-specific workspaces between probes.
                self.torch.cuda.empty_cache()
            if gpu and profile['peak_bytes'] > budget:
                # A competing app may have consumed memory during the probe.
                if len(profiles) > 1:
                    profiles.pop()
                break
            if len(profiles) > 1 and profile['rate'] < max(p['rate'] for p in profiles[:-1]) * .9:
                break
            if profile['seconds'] >= 2 or time.monotonic() - started >= 20:
                break
            count = next_probe(profiles, limit, baseline, budget)
        if not profiles:
            # If even a single image fails, normal inference reports its real error.
            self.batch_configured = True
            return 1
        best = fastest_profile(profiles)
        self.batch_profiles = profiles
        self.batch_size = self.effective_batch_size = best['count']
        self.batch_configured = True
        if gpu:
            self.torch.cuda.empty_cache()
        self.profiling_seconds += time.monotonic() - started
        self.emit({'type': 'batch_selected', 'count': best['count'], 'safe_max': max(p['count'] for p in profiles),
                   'rate': best['rate'], 'budget_bytes': budget, 'device': str(self.device),
                   'seconds': time.monotonic() - started})
        return best['count']

    def _profile_batch(self, sample, count):
        torch = self.torch
        gpu = self.device.type == 'cuda'
        with torch.inference_mode():
            batch = self.transform(sample).unsqueeze(0).repeat(count, 1, 1, 1).to(self.device)
            if gpu:
                torch.cuda.reset_peak_memory_stats(self.device)
            # Warm up this shape before measuring; no results are registered.
            del_result = self.model(batch)
            del del_result
            if gpu:
                torch.cuda.synchronize(self.device)
            started = time.perf_counter()
            output = self.model(batch)
            if gpu:
                torch.cuda.synchronize(self.device)
            seconds = time.perf_counter() - started
            del output
            peak = torch.cuda.max_memory_allocated(self.device) if gpu else 0
        return {'count': count, 'seconds': seconds, 'rate': count / max(seconds, 1e-9), 'peak_bytes': peak}

    def _predict_resilient(self, images):
        try:
            return self._predict_batch(images)
        except self.torch.OutOfMemoryError:
            if len(images) == 1:
                raise
        # Leave the exception scope before retrying so failed tensors can be freed.
        self.effective_batch_size = max(1, len(images) // 2)
        self.emit({'type': 'batch_reduced', 'before': len(images), 'after': self.effective_batch_size})
        if self.device.type == 'cuda':
            self.torch.cuda.empty_cache()
        middle = len(images) // 2
        return self._predict_resilient(images[:middle]) + self._predict_resilient(images[middle:])

    def _predict_batch(self, images):
        with self.torch.inference_mode():
            batch = self.torch.stack([self.transform(image) for image in images]).to(self.device)
            self.emit({'type': 'inference_start', 'count': len(images), 'shape': list(batch.shape),
                       'device': str(batch.device)})
            started = time.perf_counter()
            probabilities = self.torch.sigmoid(self.model(batch)).cpu().tolist()
            seconds = time.perf_counter() - started
            self.emit({'type': 'inference_complete', 'count': len(images), 'device': str(batch.device),
                       'seconds': seconds, 'rate': len(images) / max(seconds, 1e-9)})
        return [[self.class_map[str(i)].replace('_', ' ') for i in
                 sorted(range(len(values)), key=lambda i: values[i], reverse=True)
                 if values[i] >= self.threshold] for values in probabilities]
