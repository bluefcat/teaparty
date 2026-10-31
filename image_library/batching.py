"""Decisions for bounded, memory-aware batch calibration."""
import math


def memory_budget(allocated, reserved, free, total):
    # Count our unused allocator cache as reusable, but leave memory for Windows
    # and other applications. This is an operating budget, not an OOM target.
    headroom = max(2 * 1024 ** 3, total * .25)
    available = allocated + free + max(0, reserved - allocated) - headroom
    return max(allocated, min(total * .65, available))


def next_probe(profiles, limit, baseline=0, budget=None):
    previous = profiles[-1]
    count = previous['count']
    candidate = min(limit, count * 2)
    if budget is not None:
        per_image = max(1, (previous['peak_bytes'] - baseline) / count)
        if len(profiles) >= 2:
            earlier = profiles[-2]
            slope = (previous['peak_bytes'] - earlier['peak_bytes']) / (count - earlier['count'])
            per_image = max(per_image, slope)
        # Workspace requirements can change with shape; use more than a linear
        # extrapolation to avoid Windows spilling allocations into shared RAM.
        candidate = min(candidate, math.floor((budget - baseline) / (per_image * 1.5)))
    return candidate if candidate > count else None


def fastest_profile(profiles):
    best = max(profile['rate'] for profile in profiles)
    # Use the largest safe batch near peak throughput; do not mistake small
    # timing noise for a reason to discard useful parallel capacity.
    return max((p for p in profiles if p['rate'] >= best * .97), key=lambda p: p['count'])
