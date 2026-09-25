from __future__ import annotations

import random
from typing import Iterable

from .model import GroundTruth, Sample


_PHASES = (
    ("STABLE", 0, 120, GroundTruth.NORMAL),
    ("SLOW_CHANGE", 120, 180, GroundTruth.IMPORTANT),
    ("RAPID_CHANGE", 180, 200, GroundTruth.IMPORTANT),
    ("STABLE_WAIT", 200, 240, GroundTruth.NORMAL),
    ("CRITICAL_SOIL", 240, 260, GroundTruth.CRITICAL),
    ("RECOVERY_SOIL", 260, 300, GroundTruth.NORMAL),
    ("CRITICAL_MULTI", 300, 320, GroundTruth.CRITICAL),
    ("RECOVERY", 320, 360, GroundTruth.IMPORTANT),
    ("STABLE_FINAL", 360, 420, GroundTruth.NORMAL),
)


def _interpolate(start: tuple[float, ...], end: tuple[float, ...], fraction: float) -> tuple[float, ...]:
    return tuple(a + (b - a) * fraction for a, b in zip(start, end))


def generate_trace(seed: int, samples_per_phase: int = 6) -> list[Sample]:
    """Generate a fixed multi-sensor trace; identical seed and options yield identical rows."""
    if samples_per_phase < 2:
        raise ValueError("samples_per_phase must be at least 2")
    rng = random.Random(seed)
    stable = (24.0, 48.0, 120.0, 62.0)
    slow_end = (28.0, 54.0, 125.0, 60.0)
    rapid_peak = (29.5, 56.0, 950.0, 59.0)
    soil_peak = (24.2, 49.0, 122.0, 24.0)
    multi_peak = (31.0, 70.0, 1100.0, 30.0)
    rows: list[Sample] = []
    sample_id = 0
    phase_ends = {
        "STABLE": stable,
        "SLOW_CHANGE": slow_end,
        "RAPID_CHANGE": rapid_peak,
        "STABLE_WAIT": stable,
        "CRITICAL_SOIL": soil_peak,
        "RECOVERY_SOIL": stable,
        "CRITICAL_MULTI": multi_peak,
        "RECOVERY": stable,
        "STABLE_FINAL": stable,
    }
    previous = stable
    for phase, begin, end, truth in _PHASES:
        target = phase_ends[phase]
        for j in range(samples_per_phase):
            f = j / (samples_per_phase - 1)
            if phase in ("CRITICAL_SOIL", "CRITICAL_MULTI"):
                # The critical value changes at the phase boundary, matching the labeled event onset.
                f = 1.0
            values = _interpolate(previous, target, f)
            # Tiny seeded sensor quantization noise is deterministic and below event thresholds.
            temp = values[0] + rng.uniform(-0.025, 0.025)
            humidity = values[1] + rng.uniform(-0.10, 0.10)
            light = max(0.0, values[2] + rng.uniform(-1.0, 1.0))
            soil = min(100.0, max(0.0, values[3] + rng.uniform(-0.10, 0.10)))
            timestamp_ms = round((begin + f * (end - begin)) * 1000)
            rows.append(Sample(sample_id, timestamp_ms, round(temp, 2), round(humidity, 2), round(light, 1), round(soil, 1), truth, phase))
            sample_id += 1
        previous = target
    return rows


def trace_fingerprint(rows: Iterable[Sample]) -> str:
    import hashlib
    import json

    payload = json.dumps([row.as_dict() for row in rows], sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
