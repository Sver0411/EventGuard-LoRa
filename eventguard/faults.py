from __future__ import annotations

import hashlib
import math

from .model import LossModel

MAX_COPIES = 3


def hash32(seed: int, kind: str, slot: int) -> int:
    """Small SplitMix-style 32-bit hash mirrored verbatim by firmware C."""
    salt = 0x44415441 if kind.upper() == "DATA" else 0x41434B
    value = (int(seed) ^ salt ^ ((int(slot) * 0x9E3779B9) & 0xFFFFFFFF)) & 0xFFFFFFFF
    value = (value + 0x9E3779B9) & 0xFFFFFFFF
    value = ((value ^ (value >> 16)) * 0x85EBCA6B) & 0xFFFFFFFF
    value = ((value ^ (value >> 13)) * 0xC2B2AE35) & 0xFFFFFFFF
    return (value ^ (value >> 16)) & 0xFFFFFFFF


class LossPlan:
    """Strategy-independent loss calendar keyed by logical sample and copy opportunity."""
    def __init__(self, seed: int, loss_rate: float, model: LossModel, trace_length: int, burst_length: int = 3):
        if not 0 <= loss_rate <= 1:
            raise ValueError("loss_rate must be between 0 and 1")
        self.seed = int(seed)
        self.loss_rate = loss_rate
        self.model = LossModel(model)
        self.trace_length = trace_length
        self.burst_length = max(2, int(burst_length))
        count = trace_length * MAX_COPIES
        self._data = self._build(count, "DATA")
        self._ack = self._build(count, "ACK")

    def _build(self, count: int, kind: str) -> tuple[bool, ...]:
        if self.loss_rate == 0:
            return (False,) * count
        if self.model in (LossModel.RANDOM, LossModel.RANDOM_COPY):
            threshold = math.floor(self.loss_rate * 0x100000000)
            return tuple(hash32(self.seed, kind, slot) < threshold for slot in range(count))
        if self.model == LossModel.BURST_SAMPLE:
            samples = count // MAX_COPIES
            target_samples = math.floor(samples * self.loss_rate + 0.5)
            sample_mask = self._burst_mask(samples, target_samples, kind)
            return tuple(sample_mask[i // MAX_COPIES] for i in range(count))
        return tuple(self._burst_mask(count, math.floor(count * self.loss_rate + 0.5), kind))

    def _burst_mask(self, count: int, target: int, kind: str) -> list[bool]:
        mask = [False] * count
        remaining = target
        attempts = 0
        while remaining and attempts < count * 20:
            attempts += 1
            length = min(self.burst_length, remaining)
            if length > count:
                length = count
            start = hash32(self.seed ^ 0xB17B17, kind, attempts - 1) % (count - length + 1)
            if not any(mask[start:start + length]):
                for index in range(start, start + length):
                    mask[index] = True
                remaining -= length
        # Fill any gaps deterministically if dense overlap prevented placement.
        for index in range(count):
            if remaining == 0:
                break
            if not mask[index]:
                mask[index] = True
                remaining -= 1
        return tuple(mask)

    def drops(self, kind: str, sample_id: int, copy_index: int) -> bool:
        slot = sample_id * MAX_COPIES + copy_index
        calendar = self._data if kind.upper() == "DATA" else self._ack
        return 0 <= slot < len(calendar) and calendar[slot]


def should_drop(seed: int, loss_rate: float, model: LossModel, kind: str, sample_id: int, copy_index: int,
                trace_length: int, burst_length: int = 3) -> bool:
    return LossPlan(seed, loss_rate, model, trace_length, burst_length).drops(kind, sample_id, copy_index)
