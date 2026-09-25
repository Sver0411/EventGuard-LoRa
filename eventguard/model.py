from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum, StrEnum


class Importance(IntEnum):
    NORMAL = 0
    IMPORTANT = 1
    CRITICAL = 2


class GroundTruth(StrEnum):
    NORMAL = "NORMAL"
    IMPORTANT = "IMPORTANT"
    CRITICAL = "CRITICAL"


class Strategy(StrEnum):
    NO_PROTECTION = "NO_PROTECTION"
    FIXED_REDUNDANCY = "FIXED_REDUNDANCY"
    FIXED_1 = "FIXED_1"
    FIXED_2 = "FIXED_2"
    FIXED_3 = "FIXED_3"
    IMPORTANCE_ONLY = "IMPORTANCE_ONLY"
    LINK_ONLY = "LINK_ONLY"
    EVENTGUARD = "EVENTGUARD"
    UNIFORM_BUDGET = "UNIFORM_BUDGET"
    RANDOM_BUDGET = "RANDOM_BUDGET"


class LossModel(StrEnum):
    RANDOM = "RANDOM"
    BURST = "BURST"
    RANDOM_COPY = "RANDOM_COPY"
    BURST_COPY = "BURST_COPY"
    BURST_SAMPLE = "BURST_SAMPLE"


class LinkState(IntEnum):
    GOOD = 0
    DEGRADED = 1
    BAD = 2


@dataclass(frozen=True)
class Sample:
    sample_id: int
    timestamp_ms: int
    temperature: float
    humidity: float
    light: float
    soil_moisture: float
    truth: GroundTruth
    phase: str

    def as_dict(self) -> dict:
        result = asdict(self)
        result["truth"] = self.truth.value
        return result


@dataclass(frozen=True)
class RunConfig:
    strategy: Strategy
    loss_rate: float
    loss_model: LossModel
    seed: int
    burst_length: int = 3
    fixed_redundancy: int = 2
    max_redundancy: int = 3
    trace_mode: str = "TRACE_MODE"
    important_threshold: float = 0.85
    critical_threshold: float = 3.0
    importance_scales: dict[str, float] = field(default_factory=lambda: {
        "temperature": 0.5, "humidity": 5.0, "light": 100.0, "soil_moisture": 8.0
    })
    importance_baseline_alpha: float = 0.06
    link_window: int = 12
    link_degraded_threshold: float = 0.80
    link_bad_threshold: float = 0.50
    link_bad_fail_streak: int = 3
    ack_timeout_ms: int = 180
    data_copy_budget: int | None = None
