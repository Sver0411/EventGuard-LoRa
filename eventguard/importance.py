from __future__ import annotations

from dataclasses import dataclass

from .model import Importance, Sample


@dataclass
class ImportanceConfig:
    important_threshold: float = 0.85
    critical_threshold: float = 3.0
    scales: dict[str, float] | None = None
    baseline_alpha: float = 0.06

    def __post_init__(self):
        if self.scales is None:
            self.scales = {"temperature": 0.5, "humidity": 5.0, "light": 100.0, "soil_moisture": 8.0}


class ImportanceClassifier:
    """Explainable stateful classifier using change, rate, co-change, and persistence."""

    KEYS = ("temperature", "humidity", "light", "soil_moisture")

    def __init__(self, config: ImportanceConfig | None = None):
        self.config = config or ImportanceConfig()
        self.previous: Sample | None = None
        self.baseline: dict[str, float] | None = None
        self.persistence = 0
        self.last_score = 0.0

    def classify(self, sample: Sample) -> tuple[Importance, float]:
        values = {key: float(getattr(sample, key)) for key in self.KEYS}
        if self.previous is None:
            self.baseline = values.copy()
            self.previous = sample
            self.last_score = 0.0
            return Importance.NORMAL, 0.0
        assert self.baseline is not None
        elapsed_s = (sample.timestamp_ms - self.previous.timestamp_ms) / 1000.0
        if elapsed_s <= 0:
            raise ValueError("sample timestamps must increase strictly")
        normalized_change = []
        normalized_level = []
        for key in self.KEYS:
            scale = max(1e-6, self.config.scales[key])
            delta = abs(values[key] - getattr(self.previous, key)) / scale
            level = abs(values[key] - self.baseline[key]) / scale
            normalized_change.append(min(4.0, delta))
            normalized_level.append(min(4.0, level))
        normalized_rate = [min(4.0, change / max(0.25, elapsed_s / 5.0)) for change in normalized_change]
        simultaneous = sum(value >= 0.25 for value in normalized_change)
        score = (
            0.45 * sum(normalized_change)
            + 0.25 * sum(normalized_level)
            + 0.40 * sum(normalized_rate)
            + (0.70 if simultaneous >= 2 else 0.0)
            + 0.10 * min(self.persistence, 5)
        )
        # A large change can be important without being hazardous (especially
        # during recovery). Critical requires a sustained, directional level.
        risk = max(max(0.0, self.baseline["soil_moisture"] - values["soil_moisture"]) / 8.0,
                   max(0.0, values["temperature"] - self.baseline["temperature"]) / 3.0
                   + max(0.0, values["humidity"] - self.baseline["humidity"]) / 10.0)
        if risk >= self.config.critical_threshold:
            importance = Importance.CRITICAL
            self.persistence += 1
        elif score >= self.config.important_threshold:
            importance = Importance.IMPORTANT
            self.persistence += 1
        else:
            importance = Importance.NORMAL
            self.persistence = 0
            alpha = self.config.baseline_alpha
            for key in self.KEYS:
                self.baseline[key] = (1 - alpha) * self.baseline[key] + alpha * values[key]
        self.previous = sample
        self.last_score = score
        return importance, score
