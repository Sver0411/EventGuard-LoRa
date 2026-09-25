from __future__ import annotations

from collections import deque

from .model import Importance, LinkState, Strategy


class LinkQualityEstimator:
    def __init__(self, window: int = 12, degraded_threshold: float = 0.80, bad_threshold: float = 0.50, bad_fail_streak: int = 3):
        self.acks = deque(maxlen=window)
        self.degraded_threshold = degraded_threshold
        self.bad_threshold = bad_threshold
        self.bad_fail_streak = bad_fail_streak
        self.fail_streak = 0
        self.copy_attempts = 0
        self.copy_ack_success = 0
        self.copy_failures = 0
        self.consecutive_copy_failures = 0

    @property
    def success_ratio(self) -> float:
        return sum(self.acks) / len(self.acks) if self.acks else 1.0

    def observe_copy(self, ack_success: bool, first_copy: bool) -> LinkState:
        self.copy_attempts += 1
        self.copy_ack_success += bool(ack_success)
        self.copy_failures += not ack_success
        self.consecutive_copy_failures = 0 if ack_success else self.consecutive_copy_failures + 1
        if not first_copy:
            return self.state
        self.acks.append(bool(ack_success))
        self.fail_streak = 0 if ack_success else self.fail_streak + 1
        return self.state

    def observe(self, ack_success: bool) -> LinkState:
        return self.observe_copy(ack_success, True)

    @property
    def state(self) -> LinkState:
        if self.fail_streak >= self.bad_fail_streak or self.success_ratio < self.bad_threshold:
            return LinkState.BAD
        if self.success_ratio < self.degraded_threshold:
            return LinkState.DEGRADED
        return LinkState.GOOD


def choose_redundancy(strategy: Strategy, importance: Importance, link: LinkState, fixed: int = 2, maximum: int = 3) -> int:
    if maximum < 1:
        raise ValueError("maximum redundancy must be positive")
    if strategy == Strategy.NO_PROTECTION:
        return 1
    if strategy in (Strategy.FIXED_1, Strategy.FIXED_2, Strategy.FIXED_3):
        return min(maximum, int(strategy.value[-1]))
    if strategy == Strategy.FIXED_REDUNDANCY:
        return min(maximum, max(1, fixed))
    if strategy == Strategy.IMPORTANCE_ONLY:
        return min(maximum, int(importance) + 1)
    if strategy == Strategy.LINK_ONLY:
        return min(maximum, int(link) + 1)
    if strategy in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET):
        raise ValueError("budget strategy requires a precomputed allocation")
    base = {Importance.NORMAL: 1, Importance.IMPORTANT: 2, Importance.CRITICAL: 3}[importance]
    if link == LinkState.DEGRADED:
        base += 1
    elif link == LinkState.BAD and importance != Importance.NORMAL:
        base += 2
    return min(maximum, base)
