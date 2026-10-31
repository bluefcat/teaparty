"""Elapsed time and a rolling estimate, independent of UI refresh frequency."""
import math
import time
from collections import deque


class ScanEstimate:
    def __init__(self, now=None):
        self.started = self.previous_time = time.monotonic() if now is None else now
        self.previous_count = 0
        self.samples = deque(maxlen=20)
        self.observations = 0

    def observe(self, completed, total, now=None):
        now = time.monotonic() if now is None else now
        completed = max(0, min(total, completed))
        if completed > self.previous_count:
            self.samples.append(max(0, now - self.previous_time) / (completed - self.previous_count))
            self.observations += 1
            self.previous_count, self.previous_time = completed, now
        samples = list(self.samples)
        # Once warmed up, do not extrapolate one-time model loading to every file.
        if self.observations >= 3 and self.observations <= 20:
            samples = samples[1:]
        remaining = (sum(samples) / len(samples) * (total - completed)) if samples else None
        if completed == total:
            remaining = 0.
        elapsed = max(0, now - self.started)
        return dict(completed=completed, total=total, elapsed=elapsed, remaining=remaining,
                    estimated_total=elapsed + remaining if remaining is not None else None)


def format_duration(seconds):
    seconds = max(0, math.ceil(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f'{hours}시간 {minutes:02d}분 {seconds:02d}초'
    if minutes:
        return f'{minutes}분 {seconds:02d}초'
    return f'{seconds}초'
