from __future__ import annotations

import math
from dataclasses import replace

from .faults import LossPlan, hash32
from .importance import ImportanceClassifier, ImportanceConfig
from .model import GroundTruth, Importance, RunConfig, Sample, Strategy
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


def budget_allocation(strategy: Strategy, count: int, budget: int, seed: int, maximum: int = 3) -> list[int]:
    """Allocate a fixed DATA-copy budget without reading labels, values or delivery outcomes."""
    if strategy not in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET):
        raise ValueError("not a budget strategy")
    if not count <= budget <= count * maximum:
        raise ValueError("budget outside feasible range")
    result = [1] * count
    remaining = budget - count
    for layer in range(1, maximum):
        order = list(range(count))
        if strategy == Strategy.RANDOM_BUDGET:
            order.sort(key=lambda i: (hash32(seed ^ 0xB0D6E7, "DATA", layer * count + i), i))
        for index in order:
            if not remaining:
                break
            result[index] += 1
            remaining -= 1
    assert sum(result) == budget
    return result


def run_reference(samples: list[Sample], config: RunConfig, uart_baud: int = 9600,
                  ack_timeout_ms: int | None = None, link_window: int | None = None) -> dict:
    """Canonical opportunity simulation. Every strategy sees the same (sample, copy) losses."""
    if not samples:
        raise ValueError("trace cannot be empty")
    if config.strategy in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET) and config.data_copy_budget is None:
        eventguard = run_reference(samples, replace(config, strategy=Strategy.EVENTGUARD), uart_baud,
                                   ack_timeout_ms, link_window)
        budget = eventguard["metrics"]["physical_data_transmissions"]
    else:
        budget = config.data_copy_budget
    allocation = (budget_allocation(config.strategy, len(samples), budget, config.seed, config.max_redundancy)
                  if config.strategy in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET) else None)
    plan = LossPlan(config.seed, config.loss_rate, config.loss_model, len(samples), config.burst_length)
    classifier = ImportanceClassifier(ImportanceConfig(config.important_threshold, config.critical_threshold,
                                                       config.importance_scales, config.importance_baseline_alpha))
    link = LinkQualityEstimator(link_window or config.link_window, config.link_degraded_threshold,
                                config.link_bad_threshold, config.link_bad_fail_streak)
    tracker = DuplicateTracker()
    truth_totals = {key.value: 0 for key in GroundTruth}
    truth_delivered = {key.value: 0 for key in GroundTruth}
    event_results = []
    physical_data_tx = physical_data_rx = ack_count = physical_ack_received = accepted_ack = 0
    data_drops = ack_drops = duplicate_packets = 0
    normal_copies = critical_copies = 0
    latencies: list[float] = []
    ack_timeout_ms = config.ack_timeout_ms if ack_timeout_ms is None else ack_timeout_ms
    airtime_ms = lambda size: (size + 4) * 10_000 / max(1, uart_baud)

    for sequence, sample in enumerate(samples):
        truth_totals[sample.truth.value] += 1
        importance, score = classifier.classify(sample)
        selected_link = link.state
        redundancy = allocation[sequence] if allocation is not None else choose_redundancy(
            config.strategy, importance, selected_link, config.fixed_redundancy, config.max_redundancy)
        delivered = False
        accepted_any = False
        first_copy_accepted = False
        first_arrival_ms: float | None = None
        elapsed = 0.0
        sample_tracker_key = (1, sequence)
        for copy_index in range(redundancy):
            physical_data_tx += 1
            normal_copies += sample.truth == GroundTruth.NORMAL
            critical_copies += sample.truth == GroundTruth.CRITICAL
            elapsed += airtime_ms(DATA_FRAME_SIZE)
            accepted = False
            if plan.drops("DATA", sequence, copy_index):
                data_drops += 1
                elapsed += ack_timeout_ms
            else:
                physical_data_rx += 1
                if sample_tracker_key in tracker.delivered:
                    tracker.duplicates += 1
                    duplicate_packets += 1
                else:
                    tracker.accept(1, sequence)
                    delivered = True
                    first_arrival_ms = elapsed
                ack_count += 1
                elapsed += airtime_ms(ACK_FRAME_SIZE)
                physical_ack_received += 1
                if plan.drops("ACK", sequence, copy_index):
                    ack_drops += 1
                    elapsed += ack_timeout_ms
                else:
                    accepted_ack += 1
                    accepted_any = accepted = True
            if copy_index == 0:
                first_copy_accepted = accepted
            link.observe_copy(accepted, copy_index == 0)
        if delivered:
            truth_delivered[sample.truth.value] += 1
            latencies.append(first_arrival_ms or elapsed)
        event_results.append({
            "sample_id": sample.sample_id, "sequence": sequence,
            "truth": sample.truth.value, "importance": importance.name,
            "importance_score": score, "link_state": selected_link.name,
            "next_link_state": link.state.name, "redundancy_selected": redundancy,
            "copies_transmitted": redundancy, "delivered": delivered,
            "first_copy_ack_accepted": first_copy_accepted,
            "accepted_ack_any": accepted_any,
        })

    logical_packets = len(samples)
    delivered_total = sum(truth_delivered.values())
    critical_delivered = truth_delivered[GroundTruth.CRITICAL.value]
    total_bytes = physical_data_tx * DATA_FRAME_SIZE + ack_count * ACK_FRAME_SIZE
    rates = {key: truth_delivered[key] / count if count else 0.0 for key, count in truth_totals.items()}
    first_data_drop = sum(plan.drops("DATA", i, 0) for i in range(logical_packets))
    first_ack_drop = sum(plan.drops("ACK", i, 0) for i in range(logical_packets))
    labels = tuple(label.value for label in GroundTruth)
    confusion = {truth: {pred: 0 for pred in labels} for truth in labels}
    for event in event_results:
        confusion[event["truth"]][event["importance"]] += 1
    class_scores = []
    for label in labels:
        tp = confusion[label][label]
        predicted = sum(confusion[truth][label] for truth in labels)
        actual = sum(confusion[label].values())
        precision = tp / predicted if predicted else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        class_scores.append((precision, recall, f1))
    metrics = {
        "logical_packets": logical_packets, "delivered_packets": delivered_total,
        "lost_logical_packets": logical_packets - delivered_total,
        "sequence_gaps": logical_packets - delivered_total, "out_of_order_packets": tracker.out_of_order,
        "overall_delivery_ratio": delivered_total / logical_packets,
        "critical_event_delivery_ratio": rates[GroundTruth.CRITICAL.value],
        "important_event_delivery_ratio": rates[GroundTruth.IMPORTANT.value],
        "normal_delivery_ratio": rates[GroundTruth.NORMAL.value],
        "critical_event_miss_rate": 1 - rates[GroundTruth.CRITICAL.value],
        "physical_data_transmissions": physical_data_tx, "physical_data_received": physical_data_rx,
        "total_bytes_transmitted": total_bytes, "data_bytes_transmitted": physical_data_tx * DATA_FRAME_SIZE,
        "ack_bytes_transmitted": ack_count * ACK_FRAME_SIZE,
        "redundancy_overhead": physical_data_tx / logical_packets - 1,
        "redundant_copies": physical_data_tx - logical_packets,
        "ack_count": ack_count, "physical_ack_received": physical_ack_received,
        "accepted_ack": accepted_ack, "ack_injected_drops": ack_drops,
        "mean_delivery_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "p50_delivery_latency_ms": _percentile(latencies, .5),
        "p95_delivery_latency_ms": _percentile(latencies, .95),
        "p99_delivery_latency_ms": _percentile(latencies, .99),
        "duplicate_packets": duplicate_packets, "crc_errors": 0, "invalid_packets": 0,
        "data_injected_drops": data_drops,
        "configured_loss_rate": config.loss_rate,
        "effective_data_drop_rate": data_drops / physical_data_tx,
        "effective_ack_drop_rate": ack_drops / physical_ack_received if physical_ack_received else 0.0,
        "first_copy_data_drop_rate": first_data_drop / logical_packets,
        "first_copy_ack_drop_rate": first_ack_drop / logical_packets,
        "first_copy_success_ratio": sum(e["first_copy_ack_accepted"] for e in event_results) / logical_packets,
        "copy_attempts": link.copy_attempts, "copy_ack_success": link.copy_ack_success,
        "copy_failures": link.copy_failures, "consecutive_copy_failures": link.consecutive_copy_failures,
        "normal_traffic_share": normal_copies / physical_data_tx,
        "critical_traffic_share": critical_copies / physical_data_tx,
        "critical_delivery_per_1000_bytes": critical_delivered * 1000 / total_bytes if total_bytes else 0.0,
        "critical_delivery_per_data_copy": critical_delivered / physical_data_tx,
        "importance_precision": sum(x[0] for x in class_scores) / 3,
        "importance_recall": sum(x[1] for x in class_scores) / 3,
        "importance_f1": sum(x[2] for x in class_scores) / 3,
        "communication_cost_per_delivered_critical_event": total_bytes / critical_delivered if critical_delivered else None,
        "truth_counts": truth_totals, "truth_delivered": truth_delivered,
    }
    return {"metrics": metrics, "events": event_results}
