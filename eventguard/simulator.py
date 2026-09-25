from __future__ import annotations

import math
from dataclasses import asdict

from .faults import LossPlan
from .importance import ImportanceClassifier, ImportanceConfig
from .model import GroundTruth, Importance, LinkState, LossModel, RunConfig, Sample, Strategy
from .protocol import DATA_FRAME_SIZE, ACK_FRAME_SIZE, DuplicateTracker
from .strategy import LinkQualityEstimator, choose_redundancy


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def run_reference(samples: list[Sample], config: RunConfig, uart_baud: int = 9600,
                  ack_timeout_ms: int | None = None, link_window: int | None = None) -> dict:
    """Discrete event reference with the same key schedule as the device fault injector."""
    if not samples:
        raise ValueError("trace cannot be empty")
    plan = LossPlan(config.seed, config.loss_rate, config.loss_model, len(samples), config.burst_length)
    classifier = ImportanceClassifier(ImportanceConfig(config.important_threshold, config.critical_threshold,
                                                       config.importance_scales, config.importance_baseline_alpha))
    link = LinkQualityEstimator(link_window or config.link_window, config.link_degraded_threshold,
                                config.link_bad_threshold, config.link_bad_fail_streak)
    tracker = DuplicateTracker()
    truth_totals = {key.value: 0 for key in GroundTruth}
    truth_delivered = {key.value: 0 for key in GroundTruth}
    event_results = []
    logical_packets = 0
    physical_data_tx = 0
    physical_data_rx = 0
    ack_count = 0
    ack_rx = 0
    data_bytes = 0
    ack_bytes = 0
    retransmissions = 0
    duplicate_packets = 0
    data_drops = 0
    ack_drops = 0
    latencies: list[float] = []
    ack_timeout_ms = config.ack_timeout_ms if ack_timeout_ms is None else ack_timeout_ms
    airtime_ms = lambda size: (size + 4) * 10_000 / max(1, uart_baud)

    for sequence, sample in enumerate(samples):
        logical_packets += 1
        truth_totals[sample.truth.value] += 1
        importance, score = classifier.classify(sample)
        redundancy = choose_redundancy(config.strategy, importance, link.state,
                                       config.fixed_redundancy, config.max_redundancy)
        delivered = False
        ack_received = False
        first_arrival_ms: float | None = None
        elapsed = 0.0
        copies_used = 0
        sample_tracker_key = (1, sequence)
        for copy_index in range(redundancy):
            copies_used += 1
            physical_data_tx += 1
            data_bytes += DATA_FRAME_SIZE
            elapsed += airtime_ms(DATA_FRAME_SIZE)
            if plan.drops("DATA", sequence, copy_index):
                data_drops += 1
                elapsed += ack_timeout_ms
                continue
            physical_data_rx += 1
            if sample_tracker_key in tracker.delivered:
                tracker.duplicates += 1
                duplicate_packets += 1
            else:
                tracker.accept(1, sequence)
                delivered = True
                first_arrival_ms = elapsed
            # Gateway re-ACKs duplicate logical packets; ACK loss is an independent keyed process.
            ack_count += 1
            ack_bytes += ACK_FRAME_SIZE
            elapsed += airtime_ms(ACK_FRAME_SIZE)
            if plan.drops("ACK", sequence, copy_index):
                ack_drops += 1
                elapsed += ack_timeout_ms
                continue
            ack_rx += 1
            ack_received = True
        if copies_used > 1:
            retransmissions += copies_used - 1
        if delivered:
            truth_delivered[sample.truth.value] += 1
            latencies.append(first_arrival_ms or elapsed)
        link.observe(ack_received)
        event_results.append({
            "sample_id": sample.sample_id,
            "sequence": sequence,
            "truth": sample.truth.value,
            "importance": importance.name,
            "importance_score": score,
            "link_state": link.state.name,
            "redundancy_selected": redundancy,
            "copies_transmitted": copies_used,
            "delivered": delivered,
            "ack_received": ack_received,
        })

    rates = {key: (truth_delivered[key] / count if count else 0.0) for key, count in truth_totals.items()}
    delivered_total = sum(truth_delivered.values())
    critical_delivered = truth_delivered[GroundTruth.CRITICAL.value]
    metrics = {
        "logical_packets": logical_packets,
        "delivered_packets": delivered_total,
        "lost_logical_packets": logical_packets - delivered_total,
        "sequence_gaps": max(0, logical_packets - delivered_total),
        "out_of_order_packets": tracker.out_of_order,
        "overall_delivery_ratio": delivered_total / logical_packets if logical_packets else 0.0,
        "critical_event_delivery_ratio": rates[GroundTruth.CRITICAL.value],
        "important_event_delivery_ratio": rates[GroundTruth.IMPORTANT.value],
        "normal_delivery_ratio": rates[GroundTruth.NORMAL.value],
        "critical_event_miss_rate": 1.0 - rates[GroundTruth.CRITICAL.value],
        "physical_data_transmissions": physical_data_tx,
        "physical_data_received": physical_data_rx,
        "total_bytes_transmitted": data_bytes + ack_bytes,
        "data_bytes_transmitted": data_bytes,
        "ack_bytes_transmitted": ack_bytes,
        "redundancy_overhead": (physical_data_tx / logical_packets - 1.0) if logical_packets else 0.0,
        "ack_count": ack_count,
        "ack_received": ack_rx,
        "mean_delivery_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "p50_delivery_latency_ms": _percentile(latencies, 0.50),
        "p95_delivery_latency_ms": _percentile(latencies, 0.95),
        "p99_delivery_latency_ms": _percentile(latencies, 0.99),
        "retransmissions": retransmissions,
        "duplicate_packets": duplicate_packets,
        "crc_errors": 0,
        "invalid_packets": 0,
        "data_injected_drops": data_drops,
        "ack_injected_drops": ack_drops,
        "communication_cost_per_delivered_critical_event": (data_bytes + ack_bytes) / critical_delivered if critical_delivered else None,
        "truth_counts": truth_totals,
        "truth_delivered": truth_delivered,
    }
    return {"metrics": metrics, "events": event_results}
