#!/usr/bin/env python3
"""Offline audit and final analysis for the balanced EventGuard hardware set.

This tool never opens serial ports or starts a device run. It only reads the
preserved v2 Stage 1 raw logs/manifests, replays the frozen host reference, and
writes derived artifacts beneath results/final_hardware_v1/.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eventguard.hardware_analysis import _write_csv  # noqa: E402
from eventguard.hardware_validation import (  # noqa: E402
    STAGE1_FIRMWARE_SHA256,
    _gateway_end_counter_issues,
    _parse_samples,
    _validate_console_capture,
    calendar_hash,
    plan,
    sha,
)
from eventguard.host import _parse_metrics, _run_config, load_config  # noqa: E402
from eventguard.protocol import ACK_FRAME_SIZE, DATA_FRAME_SIZE  # noqa: E402
from eventguard.research_analysis import _frontier, describe, wilcoxon_exact  # noqa: E402
from eventguard.simulator import run_reference  # noqa: E402
from eventguard.trace import generate_trace, trace_fingerprint  # noqa: E402


SOURCE = ROOT / "results/hardware_validation_v2_policy_v2"
OUT = ROOT / "results/final_hardware_v1"
SEEDS = tuple(range(31, 37))
STRATEGIES = ("EVENTGUARD", "IMPORTANCE_ONLY", "UNIFORM_BUDGET", "RANDOM_BUDGET")
CONDITIONS = tuple((model, rate) for model in ("RANDOM_COPY", "BURST_SAMPLE")
                   for rate in (.20, .30))
SUMMARY_METRICS = (
    "critical_event_delivery_ratio", "important_event_delivery_ratio",
    "overall_delivery_ratio", "critical_event_miss_rate",
    "physical_data_transmissions", "redundant_copies", "physical_ack_frames",
    "data_bytes_transmitted", "ack_bytes_transmitted", "total_bytes_transmitted",
    "estimated_data_uart_time_ms", "estimated_ack_uart_time_ms",
    "estimated_communication_time_ms", "critical_delivery_per_1000_bytes",
    "critical_traffic_share", "first_copy_success_ratio", "accepted_ack", "good_count", "degraded_count",
    "bad_count", "link_state_transitions",
)
PAIRED_METRICS = (
    "critical_event_delivery_ratio", "overall_delivery_ratio",
    "physical_data_transmissions", "total_bytes_transmitted",
    "estimated_communication_time_ms", "critical_traffic_share",
)
COMPARATORS = ("IMPORTANCE_ONLY", "UNIFORM_BUDGET", "RANDOM_BUDGET")
METRIC_RECOMPUTE_MAP = {
    "logical_packets": "logical_packets",
    "delivered_packets": "delivered_packets",
    "overall_delivery_ratio": "overall_delivery_ratio",
    "critical_event_delivery_ratio": "critical_event_delivery_ratio",
    "important_event_delivery_ratio": "important_event_delivery_ratio",
    "critical_event_miss_rate": "critical_event_miss_rate",
    "physical_data_transmissions": "physical_data_transmissions",
    "physical_data_received": "physical_data_received",
    "physical_data_before_injection": "physical_data_before_injection",
    "data_injected_drops": "data_injected_drops",
    "physical_ack_received": "physical_ack_received",
    "accepted_ack": "accepted_ack",
    "ack_injected_drops": "ack_injected_drops",
    "duplicate_packets": "duplicate_packets",
    "crc_errors": "crc_errors",
    "invalid_packets": "invalid_packets",
    "data_bytes_transmitted": "data_bytes_transmitted",
    "ack_bytes_transmitted": "ack_bytes_transmitted",
    "total_bytes_transmitted": "total_bytes_transmitted",
    "critical_delivery_per_1000_bytes": "critical_delivery_per_1000_bytes",
    "truth_counts": "truth_counts",
    "truth_delivered": "truth_delivered",
}


def _close(left, right, *, abs_tol: float = 1e-9) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_close(left[k], right[k], abs_tol=abs_tol) for k in left)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=abs_tol)
    return left == right


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _diagnostics(lines: list[str]) -> dict | None:
    matches = [line for line in lines if line.startswith("UART_DIAG,")]
    if len(matches) != 1:
        return None
    fields = matches[0].split(",")
    if len(fields) < 3 or (len(fields) - 1) % 2:
        return None
    try:
        return dict(zip(fields[1::2], (int(value) for value in fields[2::2])))
    except ValueError:
        return None


def _end(lines: list[str], prefix: str = "END,") -> list[int] | None:
    matches = [line for line in lines if line.startswith(prefix)]
    if len(matches) != 1:
        return None
    try:
        return [int(value) for value in matches[0].split(",")[1:]]
    except ValueError:
        return None


def _manifest_budget_table() -> dict[tuple[str, float, int], dict]:
    payload = _read_json(SOURCE / "stage1/budget_table.json")
    return {(item["loss_model"], float(item["loss_rate"]), int(item["seed"])): item
            for item in payload["conditions"]}


def _expected_plan() -> list[tuple[str, float, int, str, int]]:
    return [item for item in plan("stage1") if item[2] in SEEDS]


def _run_config_and_reference(cfg: dict, strategy: str, rate: float, model: str,
                              seed: int, budget: int | None, samples: list) -> tuple[object, dict]:
    config = _run_config(cfg, strategy, rate, model, seed)
    if strategy in ("UNIFORM_BUDGET", "RANDOM_BUDGET"):
        from dataclasses import replace
        config = replace(config, data_copy_budget=budget)
    return config, run_reference(samples, config, cfg["uart_baud"])


def _compare_saved_events(saved: list[dict], parsed: list[dict]) -> list[str]:
    errors = []
    if len(saved) != len(parsed):
        return [f"saved sample_events count {len(saved)} != raw-reparsed event count {len(parsed)}"]
    fields = ("sample_id", "ground_truth", "predicted_importance", "selected_copies",
              "first_copy_sent", "first_copy_data_delivered", "first_copy_ack_physical_received",
              "first_copy_ack_accepted", "link_state_before", "link_state_after",
              "link_state_before_simulation", "link_state_after_simulation", "delivered",
              "physical_data_copies", "delivered_simulation")
    for idx, (old, new) in enumerate(zip(saved, parsed)):
        for field in fields:
            if old.get(field) != new.get(field):
                errors.append(f"sample {idx} saved {field}={old.get(field)!r} != raw {new.get(field)!r}")
                break
    return errors


def audit_run(item: tuple[str, float, int, str, int], cfg: dict,
              budgets: dict[tuple[str, float, int], dict], expected_firmware: dict) -> tuple[dict, dict | None, dict | None]:
    model, rate, seed, strategy, order = item
    run_id = f"{strategy.lower()}_{model.lower()}_{int(rate * 100):02d}_seed{seed}"
    run_path = SOURCE / "runs/stage1" / f"{run_id}.json"
    raw_path = SOURCE / "raw/stage1" / f"{run_id}.json"
    result = {"run_id": run_id, "loss_model": model, "loss_rate": rate,
              "seed": seed, "strategy": strategy, "expected_execution_order": order,
              "run_manifest": str(run_path.relative_to(ROOT)), "raw_log": str(raw_path.relative_to(ROOT)),
              "audit_status": "FAIL", "audit_errors": []}
    if not run_path.is_file() or not raw_path.is_file():
        result["audit_errors"].append("run manifest or raw serial log is missing")
        return result, None, None

    record = _read_json(run_path)
    raw = _read_json(raw_path)
    errors = result["audit_errors"]
    digest = sha(raw_path)
    if record.get("status") != "complete": errors.append(f"run status is {record.get('status')!r}")
    if record.get("raw_sha256") != digest: errors.append("raw SHA256 differs from run manifest")
    if record.get("run_id") != run_id or raw.get("run_id") != run_id: errors.append("run_id mismatch")
    for key, expected in (("execution_order", order), ("strategy", strategy), ("loss_model", model),
                          ("loss_rate", rate), ("seed", seed), ("stage", "stage1")):
        if record.get(key) != expected: errors.append(f"manifest {key}={record.get(key)!r}, expected {expected!r}")
    if record.get("source") != "REAL_E220_WITH_APPLICATION_LAYER_INJECTION":
        errors.append("run source is not recorded as real E220 with application-layer injection")
    if record.get("firmware_images_sha256") != expected_firmware:
        errors.append("firmware hashes differ from the frozen v2 smoke pair")
    if record.get("algorithm_version") != "EventGuard-v1": errors.append("algorithm version is not EventGuard-v1")
    if record.get("algorithm_spec_v1_sha256") != sha(ROOT / "docs/algorithm_spec_v1.md"):
        errors.append("algorithm specification hash differs from the current frozen spec")
    if record.get("config_sha256") != sha(ROOT / "configs/default.json"):
        errors.append("default config hash differs from the run provenance")
    for relative_path, frozen_hash in record.get("frozen_core_sha256", {}).items():
        source_path = ROOT / relative_path
        if not source_path.is_file() or sha(source_path) != frozen_hash:
            errors.append(f"frozen core source hash mismatch: {relative_path}")

    samples = generate_trace(seed, 6)
    trace_hash = trace_fingerprint(samples)
    budget_row = budgets.get((model, rate, seed))
    if budget_row is None:
        errors.append("frozen EventGuard budget entry missing")
        reference_budget = None
    else:
        reference_budget = int(budget_row["eventguard_reference_data_budget"])
        if budget_row.get("trace_sha256") != trace_hash: errors.append("budget-table trace hash mismatch")
    config, reference = _run_config_and_reference(cfg, strategy, rate, model, seed,
                                                   reference_budget, samples)
    expected_calendar = calendar_hash(config, len(samples))
    if record.get("trace_sha256") != trace_hash: errors.append("trace hash does not match frozen trace")
    if record.get("loss_calendar_sha256") != expected_calendar: errors.append("loss calendar hash does not match frozen calendar")
    if budget_row and budget_row.get("loss_calendar_sha256") != expected_calendar:
        errors.append("budget-table loss-calendar hash mismatch")
    if record.get("reference_eventguard_data_copy_budget") != reference_budget:
        errors.append("recorded EventGuard reference budget differs from frozen budget table")
    expected_config_budget = reference_budget if strategy in ("UNIFORM_BUDGET", "RANDOM_BUDGET") else None
    if record.get("configured_data_copy_budget") != expected_config_budget:
        errors.append("configured DATA-copy budget differs from precomputed fairness budget")

    sensor_lines = [(float(x.get("host_monotonic", idx)), x.get("line", ""))
                    for idx, x in enumerate(raw.get("sensor", []))]
    gateway_lines = [(float(x.get("host_monotonic", idx)), x.get("line", ""))
                     for idx, x in enumerate(raw.get("gateway", []))]
    sensor_text = [line for _, line in sensor_lines]
    gateway_text = [line for _, line in gateway_lines]
    if not raw.get("reset", {}).get("sensor_reset") or "RESET,OK" not in raw["reset"]["sensor_reset"]:
        errors.append("sensor RESET acknowledgement missing")
    if not raw.get("reset", {}).get("gateway_reset") or "RESET,OK" not in raw["reset"]["gateway_reset"]:
        errors.append("gateway RESET acknowledgement missing")
    for label, entries in (("sensor", raw.get("configured_sensor", [])),
                           ("gateway", raw.get("configured_gateway", []))):
        prefix = ["CONFIGURED", strategy, f"{rate:.4f}", model, str(seed),
                  str(cfg["burst_length"]), str(cfg["fixed_redundancy"]), str(cfg["max_redundancy"])]
        if not entries or entries[0].split(",")[:len(prefix)] != prefix:
            errors.append(f"{label} CONFIGURED acknowledgement mismatch")
    if raw.get("configured_sensor") != raw.get("configured_gateway"):
        errors.append("Sensor and Gateway CONFIGURED acknowledgements differ")
    if f"TRACE_READY,{len(samples)}" not in raw.get("trace_ready", []): errors.append("TRACE_READY count mismatch")
    if "ARMED,54" not in gateway_text: errors.append("Gateway ARMED acknowledgement missing")
    if record.get("issues"): errors.extend(f"recorded run issue: {issue}" for issue in record["issues"])
    if record.get("sample_differences"): errors.append(f"saved host/firmware policy divergence count {len(record['sample_differences'])}")

    parsed_events, divergences, parser_issues, physical_anomalies = _parse_samples(
        samples, sensor_lines, gateway_lines, config, reference)
    errors.extend(f"raw reparse: {issue}" for issue in parser_issues)
    if divergences: errors.append(f"raw reparse found {len(divergences)} importance/copy/link divergences")
    errors.extend(_compare_saved_events(record.get("sample_events", []), parsed_events))
    if physical_anomalies != record.get("physical_anomalies", []):
        errors.append("physical anomaly list differs between raw reparse and run manifest")

    start = float(raw.get("start_host_monotonic", 0))
    duration = float(record.get("duration_s", 0))
    parsed_metrics = _parse_metrics(samples, parsed_events, sensor_lines, gateway_lines,
                                    start, start + duration, config, trace_hash)
    sensor_end = _end(sensor_text)
    gateway_end = _end(gateway_text)
    metrics = record.get("metrics", {})
    if sensor_end is None or len(sensor_end) != 4:
        errors.append("Sensor END missing, duplicated, malformed, or wrong field count")
    elif sensor_end[:3] != [len(samples), parsed_metrics["physical_data_transmissions"], parsed_metrics["accepted_ack"]] or sensor_end[3] != 0:
        errors.append(f"Sensor END counters disagree with raw logs: {sensor_end}")
    if gateway_end is None or len(gateway_end) != 9:
        errors.append("Gateway END missing, duplicated, malformed, or wrong field count")
    else:
        errors.extend(_gateway_end_counter_issues(gateway_end, len(samples), parsed_metrics))
    if len([line for line in sensor_text if line.startswith("EVT,")]) != len(samples): errors.append("EVT count != completed trace samples")
    if len([line for line in sensor_text if line.startswith("SAMPLE,")]) != len(samples): errors.append("SAMPLE count != completed trace samples")
    errors.extend(_validate_console_capture(sensor_lines, gateway_lines,
                                            [line for line in sensor_text if line.startswith("END,")],
                                            [line for line in gateway_text if line.startswith("END,")],
                                            metrics.get("uart_diagnostics", {})))

    parsed_metrics["physical_ack_frames"] = sum(line.startswith("ACK_TX,") for line in gateway_text)
    parsed_metrics["ack_timeout"] = sum(line.startswith("TIMEOUT,") for line in sensor_text)
    parsed_metrics["uncontrolled_physical_ack_missing"] = max(
        0, parsed_metrics["physical_ack_frames"] - parsed_metrics["physical_ack_received"])
    parsed_metrics["estimated_data_uart_time_ms"] = parsed_metrics["data_bytes_transmitted"] * 10 * 1000 / cfg["uart_baud"]
    parsed_metrics["estimated_ack_uart_time_ms"] = parsed_metrics["ack_bytes_transmitted"] * 10 * 1000 / cfg["uart_baud"]
    parsed_metrics["estimated_communication_time_ms"] = (
        parsed_metrics["estimated_data_uart_time_ms"] + parsed_metrics["estimated_ack_uart_time_ms"])
    parsed_metrics["redundant_copies"] = max(0, parsed_metrics["physical_data_transmissions"] - len(samples))
    for key, parsed_key in METRIC_RECOMPUTE_MAP.items():
        if key not in metrics or not _close(metrics[key], parsed_metrics[parsed_key]):
            errors.append(f"stored metric {key}={metrics.get(key)!r} != raw-recomputed {parsed_metrics[parsed_key]!r}")
    for key in ("physical_ack_frames", "ack_timeout", "uncontrolled_physical_data_missing",
                "uncontrolled_physical_ack_missing", "estimated_data_uart_time_ms",
                "estimated_ack_uart_time_ms", "estimated_communication_time_ms", "redundant_copies"):
        if key not in metrics or not _close(metrics[key], parsed_metrics[key]):
            errors.append(f"stored metric {key}={metrics.get(key)!r} != raw-recomputed {parsed_metrics[key]!r}")

    # Verify the firmware-owned UART diagnostics against the frame-level logs.
    sensor_diag, gateway_diag = _diagnostics(sensor_text), _diagnostics(gateway_text)
    if sensor_diag is None or gateway_diag is None:
        errors.append("UART_DIAG line missing, duplicated, or malformed")
    else:
        checks = (("sensor", sensor_diag, sum(line.startswith("D_ACK_RECEIVED,") for line in sensor_text), ACK_FRAME_SIZE),
                  ("gateway", gateway_diag, sum(line.startswith("D_RX_FRAME_COMPLETE,") for line in gateway_text), DATA_FRAME_SIZE))
        for role, diag, frame_lines, frame_size in checks:
            if diag.get("frames_started") != diag.get("frames_completed"):
                errors.append(f"{role} UART_DIAG frames_started/frames_completed mismatch")
            if diag.get("frames_completed") != frame_lines:
                errors.append(f"{role} UART_DIAG frames_completed {diag.get('frames_completed')} != timestamped frame count {frame_lines}")
            if diag.get("uart_bytes_received") != diag.get("frames_completed", -1) * frame_size:
                errors.append(f"{role} UART_DIAG byte count does not equal complete framed bytes")
            for key in ("partial_header_timeouts", "partial_body_timeouts", "short_reads", "parser_resyncs",
                        "crc_failures", "invalid_type", "invalid_version", "aux_ring_overflow"):
                if diag.get(key) != 0: errors.append(f"{role} UART_DIAG {key}={diag.get(key)}")
            if diag != metrics.get("uart_diagnostics", {}).get(role):
                errors.append(f"{role} UART_DIAG differs from saved summary metrics")

    expected_data_tx = int(reference["metrics"]["physical_data_transmissions"])
    if strategy in ("UNIFORM_BUDGET", "RANDOM_BUDGET") and expected_data_tx != reference_budget:
        errors.append(f"budget strategy host reference emits {expected_data_tx} DATA copies != fixed budget {reference_budget}")
    actual_data_tx = int(metrics.get("physical_data_transmissions", -1))
    if actual_data_tx != expected_data_tx:
        errors.append(f"actual DATA-copy count {actual_data_tx} != strategy host reference {expected_data_tx}")
    if strategy in ("UNIFORM_BUDGET", "RANDOM_BUDGET") and actual_data_tx != reference_budget:
        errors.append(f"actual DATA-copy budget {actual_data_tx} != frozen EventGuard budget {reference_budget}")
    saved_ref = record.get("reference_metrics", {})
    for key in ("critical_event_delivery_ratio", "overall_delivery_ratio", "physical_data_transmissions",
                "total_bytes_transmitted", "physical_data_received", "accepted_ack", "ack_injected_drops",
                "data_injected_drops"):
        if key in saved_ref and not _close(saved_ref[key], reference["metrics"].get(key)):
            errors.append(f"stored host reference {key} differs from regenerated frozen host reference")

    result["raw_sha256"] = digest
    result["firmware_images_sha256"] = record.get("firmware_images_sha256")
    result["physical_data_missing"] = metrics.get("uncontrolled_physical_data_missing", "")
    result["physical_ack_missing"] = metrics.get("uncontrolled_physical_ack_missing", "")
    result["recorded_execution_order"] = record.get("execution_order")
    result["recorded_data_copies"] = actual_data_tx
    result["eventguard_reference_budget"] = reference_budget
    result["trace_sha256"] = trace_hash
    result["loss_calendar_sha256"] = expected_calendar
    result["audit_errors"] = list(dict.fromkeys(errors))
    result["audit_status"] = "PASS" if not errors else "FAIL"
    record["_audit_metrics"] = metrics
    record["_regenerated_host_metrics"] = reference["metrics"]
    return result, record, reference["metrics"]


def _summary_rows(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["loss_model"], row["loss_rate"], row["strategy"])].append(row)
    output = []
    for (model, rate, strategy), group in sorted(groups.items()):
        item = {"loss_model": model, "loss_rate": rate, "strategy": strategy, "n_seeds": len(group),
                "seeds": ";".join(str(row["seed"]) for row in sorted(group, key=lambda r: r["seed"]))}
        for metric in SUMMARY_METRICS:
            stats = describe([row["_audit_metrics"][metric] for row in group])
            for field in ("mean", "median", "std", "ci95_low", "ci95_high"):
                item[f"{metric}_{field}"] = stats[field]
        output.append(item)
    return output


def _paired_rows(rows: list[dict]) -> list[dict]:
    index = {(row["loss_model"], row["loss_rate"], row["seed"], row["strategy"]): row for row in rows}
    output = []
    for model, rate in CONDITIONS:
        for baseline in COMPARATORS:
            pairs = [(index[(model, rate, seed, "EVENTGUARD")], index[(model, rate, seed, baseline)])
                     for seed in SEEDS if (model, rate, seed, "EVENTGUARD") in index
                     and (model, rate, seed, baseline) in index]
            if len(pairs) != len(SEEDS):
                continue
            for metric in PAIRED_METRICS:
                diffs = [float(left["_audit_metrics"][metric]) - float(right["_audit_metrics"][metric])
                         for left, right in pairs]
                desc = describe(diffs)
                test = wilcoxon_exact(diffs)
                output.append({"loss_model": model, "loss_rate": rate,
                               "comparison": f"EVENTGUARD - {baseline}", "metric": metric,
                               "n_seed_pairs": len(pairs), "wins": sum(x > 1e-12 for x in diffs),
                               "ties": sum(abs(x) <= 1e-12 for x in diffs),
                               "losses": sum(x < -1e-12 for x in diffs),
                               "mean_paired_difference": desc["mean"],
                               "median_paired_difference": desc["median"],
                               "std_paired_difference": desc["std"],
                               "ci95_low": desc["ci95_low"], "ci95_high": desc["ci95_high"],
                               **test})
    return output


def _simulation_hardware_rows(rows: list[dict]) -> list[dict]:
    index = {(row["loss_model"], row["loss_rate"], row["seed"], row["strategy"]): row for row in rows}
    output = []
    metrics = ("critical_event_delivery_ratio", "overall_delivery_ratio",
               "physical_data_transmissions", "total_bytes_transmitted", "accepted_ack",
               "critical_traffic_share")
    for model, rate in CONDITIONS:
        for strategy in STRATEGIES:
            group = [index[(model, rate, seed, strategy)] for seed in SEEDS
                     if (model, rate, seed, strategy) in index]
            for metric in metrics:
                hardware_values = [float(row["_audit_metrics"][metric]) for row in group]
                simulation_values = [float(row["_regenerated_host_metrics"][metric]) for row in group]
                hw, sim = describe(hardware_values), describe(simulation_values)
                output.append({"row_type": "strategy_mean", "loss_model": model, "loss_rate": rate,
                               "seed": "", "strategy": strategy, "comparison": "", "metric": metric,
                               "n": len(group), "hardware_mean": hw["mean"], "simulation_mean": sim["mean"],
                               "hardware_minus_simulation": hw["mean"] - sim["mean"],
                               "hardware_rank": "", "simulation_rank": "", "same_direction": ""})
        for baseline in COMPARATORS:
            for metric in ("critical_event_delivery_ratio", "overall_delivery_ratio",
                           "physical_data_transmissions", "total_bytes_transmitted", "critical_traffic_share"):
                hw_diff, sim_diff = [], []
                for seed in SEEDS:
                    eg = index[(model, rate, seed, "EVENTGUARD")]
                    base = index[(model, rate, seed, baseline)]
                    hw_diff.append(float(eg["_audit_metrics"][metric]) - float(base["_audit_metrics"][metric]))
                    sim_diff.append(float(eg["_regenerated_host_metrics"][metric]) -
                                    float(base["_regenerated_host_metrics"][metric]))
                hstats, sstats = describe(hw_diff), describe(sim_diff)
                hsign = 1 if hstats["mean"] > 1e-12 else -1 if hstats["mean"] < -1e-12 else 0
                ssign = 1 if sstats["mean"] > 1e-12 else -1 if sstats["mean"] < -1e-12 else 0
                output.append({"row_type": "paired_strategy_contrast", "loss_model": model,
                               "loss_rate": rate, "seed": "", "strategy": "EVENTGUARD",
                               "comparison": f"EVENTGUARD - {baseline}", "metric": metric,
                               "n": len(hw_diff), "hardware_mean": hstats["mean"],
                               "simulation_mean": sstats["mean"],
                               "hardware_minus_simulation": hstats["mean"] - sstats["mean"],
                               "hardware_rank": "EG>base" if hsign > 0 else "EG<base" if hsign < 0 else "tie",
                               "simulation_rank": "EG>base" if ssign > 0 else "EG<base" if ssign < 0 else "tie",
                               "same_direction": hsign == ssign})
        for metric in ("critical_event_delivery_ratio", "overall_delivery_ratio",
                       "physical_data_transmissions", "total_bytes_transmitted", "critical_traffic_share"):
            mean_by = {}
            for strategy in STRATEGIES:
                mean_by[strategy] = sum(float(index[(model, rate, seed, strategy)]["_audit_metrics"][metric])
                                        for seed in SEEDS) / len(SEEDS)
            sim_mean_by = {}
            for strategy in STRATEGIES:
                sim_mean_by[strategy] = sum(float(index[(model, rate, seed, strategy)]["_regenerated_host_metrics"][metric])
                                            for seed in SEEDS) / len(SEEDS)
            higher_is_better = metric not in ("physical_data_transmissions", "total_bytes_transmitted")
            hw_rank = _rank_label(mean_by, higher_is_better)
            sim_rank = _rank_label(sim_mean_by, higher_is_better)
            output.append({"row_type": "strategy_ranking", "loss_model": model, "loss_rate": rate,
                           "seed": "", "strategy": "", "comparison": "", "metric": metric,
                           "n": len(SEEDS), "hardware_mean": "", "simulation_mean": "",
                           "hardware_minus_simulation": "", "hardware_rank": hw_rank,
                           "simulation_rank": sim_rank, "same_direction": hw_rank == sim_rank})
    return output


def _rank_label(values: dict[str, float], higher_is_better: bool) -> str:
    ordered = sorted(values, key=lambda strategy: values[strategy], reverse=higher_is_better)
    groups: list[list[str]] = []
    for strategy in ordered:
        if not groups or not math.isclose(values[strategy], values[groups[-1][0]], rel_tol=1e-10, abs_tol=1e-10):
            groups.append([strategy])
        else:
            groups[-1].append(strategy)
    return " > ".join(" = ".join(group) for group in groups)


def _pareto(summary: list[dict]) -> tuple[list[dict], dict]:
    rows, counts = [], {"data_copies": defaultdict(int), "total_bytes": defaultdict(int)}
    for model, rate in CONDITIONS:
        group = [row for row in summary if row["loss_model"] == model and row["loss_rate"] == rate]
        for cost_name, cost_field in (("data_copies", "physical_data_transmissions_mean"),
                                      ("total_bytes", "total_bytes_transmitted_mean")):
            points = [{"strategy": row["strategy"], "cost": row[cost_field],
                       "delivery": row["critical_event_delivery_ratio_mean"]} for row in group]
            front, _ = _frontier(points, "cost", "delivery")
            front_names = {point["strategy"] for point in front}
            for point in points:
                rows.append({"loss_model": model, "loss_rate": rate, "cost_type": cost_name,
                             "strategy": point["strategy"], "mean_cost": point["cost"],
                             "mean_critical_delivery": point["delivery"],
                             "pareto_frontier": point["strategy"] in front_names})
                if point["strategy"] in front_names:
                    counts[cost_name][point["strategy"]] += 1
    return rows, {cost: dict(values) for cost, values in counts.items()}


def _plot_pareto(pareto_rows: list[dict]) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    OUT.mkdir(parents=True, exist_ok=True)
    plots = OUT / "plots"
    plots.mkdir(parents=True, exist_ok=True)
    palette = {"EVENTGUARD": "#d62728", "IMPORTANCE_ONLY": "#1f77b4",
               "UNIFORM_BUDGET": "#2ca02c", "RANDOM_BUDGET": "#9467bd"}
    markers = {"EVENTGUARD": "D", "IMPORTANCE_ONLY": "o",
               "UNIFORM_BUDGET": "s", "RANDOM_BUDGET": "^"}
    short_names = {"EVENTGUARD": "EG", "IMPORTANCE_ONLY": "IO",
                   "UNIFORM_BUDGET": "Uniform", "RANDOM_BUDGET": "Random"}
    outputs = []
    for cost_type, filename, xlabel in (
        ("data_copies", "pareto_critical_vs_copies.png", "Mean physical DATA copies per run"),
        ("total_bytes", "pareto_critical_vs_bytes.png", "Mean transmitted bytes per run"),
    ):
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharey=True)
        for ax, (model, rate) in zip(axes.flat, CONDITIONS):
            group = [row for row in pareto_rows if row["loss_model"] == model and
                     row["loss_rate"] == rate and row["cost_type"] == cost_type]
            cost_span = max(float(row["mean_cost"]) for row in group) - min(float(row["mean_cost"]) for row in group)
            label_merge_tolerance = max(1e-6, cost_span * .035)
            plotted_groups: list[list[dict]] = []
            for row in sorted(group, key=lambda value: (float(value["mean_critical_delivery"]), float(value["mean_cost"]))):
                matching = next((members for members in plotted_groups
                                 if abs(float(members[0]["mean_critical_delivery"]) -
                                        float(row["mean_critical_delivery"])) < 1e-8
                                 and abs(sum(float(member["mean_cost"]) for member in members) / len(members) -
                                         float(row["mean_cost"])) <= label_merge_tolerance), None)
                if matching is None:
                    plotted_groups.append([row])
                else:
                    matching.append(row)
            plotted_groups.sort(key=lambda values: (float(values[0]["mean_cost"]),
                                                     float(values[0]["mean_critical_delivery"])))
            for rank, members in enumerate(plotted_groups):
                x = sum(float(member["mean_cost"]) for member in members) / len(members)
                y = sum(float(member["mean_critical_delivery"]) for member in members) / len(members)
                for row in members:
                    ax.scatter(float(row["mean_cost"]), float(row["mean_critical_delivery"]), s=82, linewidth=1.5,
                               facecolors=palette[row["strategy"]] if row["pareto_frontier"] else "white",
                               edgecolors=palette[row["strategy"]], marker=markers[row["strategy"]], zorder=3)
                label = " / ".join(short_names[row["strategy"]] for row in members)
                cost_midpoint = (min(float(row["mean_cost"]) for row in group) +
                                 max(float(row["mean_cost"]) for row in group)) / 2
                dx = 7 if x <= cost_midpoint else -8
                delivery_midpoint = (min(float(row["mean_critical_delivery"]) for row in group) +
                                     max(float(row["mean_critical_delivery"]) for row in group)) / 2
                dy = 14 if y <= delivery_midpoint else -17
                horizontal = "left" if dx >= 0 else "right"
                ax.annotate(label, (x, y), xytext=(dx, dy), textcoords="offset points",
                            ha=horizontal, va="center", fontsize=8,
                            bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "alpha": .85, "edgecolor": "none"},
                            arrowprops={"arrowstyle": "-", "color": "#666666", "lw": .7}, zorder=4)
            ax.set_title(f"{model}, {rate:.0%}")
            ax.set_xlabel(xlabel)
            ax.grid(alpha=.25)
        axes[0, 0].set_ylabel("Mean critical-event delivery ratio")
        axes[1, 0].set_ylabel("Mean critical-event delivery ratio")
        from matplotlib.lines import Line2D
        strategy_handles = [Line2D([0], [0], marker=markers[strategy], linestyle="none",
                                   markerfacecolor=palette[strategy], markeredgecolor=palette[strategy],
                                   markersize=8, label=strategy) for strategy in STRATEGIES]
        frontier_handles = [Line2D([0], [0], marker="o", linestyle="none", markerfacecolor="#555555",
                                   markeredgecolor="#555555", markersize=7, label="Pareto frontier"),
                           Line2D([0], [0], marker="o", linestyle="none", markerfacecolor="white",
                                  markeredgecolor="#555555", markersize=7, label="Dominated")]
        fig.legend(handles=strategy_handles + frontier_handles, loc="lower center", ncol=3,
                   frameon=False, bbox_to_anchor=(.5, .012))
        fig.suptitle("Balanced real-hardware Pareto by matched loss condition", y=.99)
        fig.tight_layout(rect=(0, .10, 1, .94))
        path = plots / filename
        fig.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        outputs.append(path.name)
    return outputs


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    budgets = _manifest_budget_table()
    hw_manifest = _read_json(SOURCE / "hardware_manifest.json")
    stage_manifest = _read_json(SOURCE / "stage1/stage1_manifest.json")
    expected_firmware = hw_manifest.get("firmware_images_sha256", STAGE1_FIRMWARE_SHA256)
    global_checks = []
    for key, actual in (("algorithm_spec_sha256", sha(ROOT / "docs/algorithm_spec_v1.md")),
                        ("config_sha256", sha(ROOT / "configs/default.json")),
                        ("trace_sha256", sha(ROOT / "eventguard/trace.py")),
                        ("loss_calendar_implementation_sha256", sha(ROOT / "eventguard/faults.py"))):
        if stage_manifest.get(key) != actual:
            global_checks.append(f"study provenance {key}={stage_manifest.get(key)!r} != current {actual!r}")
    if expected_firmware != STAGE1_FIRMWARE_SHA256:
        global_checks.append("hardware study manifest firmware hashes differ from frozen v2 smoke firmware")
    inventory, records, references = [], [], {}
    for item in _expected_plan():
        audit, record, reference = audit_run(item, cfg, budgets, expected_firmware)
        if global_checks:
            audit["audit_errors"] = list(dict.fromkeys(audit["audit_errors"] + global_checks))
            audit["audit_status"] = "FAIL"
        inventory.append(audit)
        if record is not None and reference is not None:
            records.append(record)
            references[record["run_id"]] = reference

    # The primary dataset is seed-balanced: any seed with one unavailable or
    # inconsistent strategy/condition is excluded as a whole paired unit.
    run_lookup = {(r["loss_model"], float(r["loss_rate"]), int(r["seed"]), r["strategy"]): r
                  for r in records}
    audit_lookup = {(r["loss_model"], float(r["loss_rate"]), int(r["seed"]), r["strategy"]): r
                    for r in inventory}
    usable_seeds = []
    for seed in SEEDS:
        expected = [(model, rate, seed, strategy) for model, rate in CONDITIONS for strategy in STRATEGIES]
        if all(key in run_lookup and audit_lookup.get(key, {}).get("audit_status") == "PASS" for key in expected):
            usable_seeds.append(seed)
    selected = [row for row in records if row["seed"] in usable_seeds and
                audit_lookup[(row["loss_model"], float(row["loss_rate"]), int(row["seed"]), row["strategy"])]
                ["audit_status"] == "PASS"]
    complete_expected = len(SEEDS) * len(CONDITIONS) * len(STRATEGIES)
    main_status = "PASS" if len(selected) == complete_expected else "PARTIAL"
    selected_ids = {row["run_id"] for row in selected}
    selected_payload = {
        "dataset_id": "FINAL_BALANCED_HARDWARE_SET_V1",
        "dataset_status": main_status,
        "selection_rule": "Seeds 31-36 are the earliest contiguous evaluation seeds completed in Stage 1 execution order with all four conditions and all four strategies present; inclusion is based on completeness and offline consistency only, not observed outcomes.",
        "selection_rationale": "The balanced seed window is the first six contiguous seeds in the interleaved Stage 1 sequence. Seed 37 expansion records, including completed partial-condition runs and the incomplete run 103, are excluded from paired primary statistics.",
        "seeds_expected": list(SEEDS), "usable_paired_seeds": usable_seeds,
        "strategies": list(STRATEGIES), "conditions": [{"loss_model": m, "loss_rate": r} for m, r in CONDITIONS],
        "expected_runs": complete_expected, "usable_runs": len(selected),
        "source_study": str(SOURCE.relative_to(ROOT)),
        "firmware_images_sha256": expected_firmware,
        "provenance": {
            "git_commit": next((row.get("git_commit") for row in selected), None),
            "branch": next((row.get("branch") for row in selected), None),
            "algorithm_version": stage_manifest.get("algorithm_version"),
            "algorithm_spec_sha256": sha(ROOT / "docs/algorithm_spec_v1.md"),
            "config_sha256": sha(ROOT / "configs/default.json"),
            "trace_implementation_sha256": sha(ROOT / "eventguard/trace.py"),
            "loss_calendar_implementation_sha256": sha(ROOT / "eventguard/faults.py"),
            "e220_configuration": next((row.get("e220_configuration") for row in selected), None),
            "sensor_mac": next((row.get("sensor_mac") for row in selected), None),
            "gateway_mac": next((row.get("gateway_mac") for row in selected), None),
            "serial_ports": next((row.get("serial_ports") for row in selected), None),
        },
        "runs": [{"run_id": row["run_id"], "loss_model": row["loss_model"], "loss_rate": row["loss_rate"],
                  "seed": row["seed"], "strategy": row["strategy"],
                  "execution_order": row["execution_order"], "raw_sha256": row["raw_sha256"],
                  "trace_sha256": row["trace_sha256"], "loss_calendar_sha256": row["loss_calendar_sha256"],
                  "audit_status": "PASS"} for row in sorted(selected, key=lambda x: x["execution_order"])],
        "excluded_seed37_note": "All seed 37 records are excluded from paired primary statistics. Later expansion run 103 (UNIFORM_BUDGET / RANDOM_COPY / 30% / seed 37) stalled and remains an incomplete engineering anomaly with raw evidence preserved.",
    }
    _write_json(OUT / "selected_runs.json", selected_payload)
    _write_csv(OUT / "audit_runs.csv", inventory)

    summary = _summary_rows(selected)
    paired = _paired_rows(selected)
    comparison = _simulation_hardware_rows(selected)
    pareto_rows, frontier_counts = _pareto(summary)
    _write_csv(OUT / "summary.csv", summary)
    _write_csv(OUT / "paired_tests.csv", paired)
    _write_csv(OUT / "simulation_hardware_comparison.csv", comparison)
    _write_csv(OUT / "pareto_points.csv", pareto_rows)
    plot_files = _plot_pareto(pareto_rows) if selected else []

    failed_audits = [row for row in inventory if row["audit_status"] != "PASS"]
    physical_data_missing = sum(int(row.get("physical_data_missing") or 0) for row in selected)
    physical_ack_missing = sum(int(row.get("physical_ack_missing") or 0) for row in selected)
    sample_policy_divergences = sum(len(row.get("sample_differences", [])) for row in selected)
    sample_outcome_divergences = sum(sum(bool(event.get("physical_outcome_differs"))
                                         for event in row.get("sample_events", [])) for row in selected)
    audit_lines = [
        "# Offline audit: FINAL_BALANCED_HARDWARE_SET_V1", "",
        f"Generated at {datetime.now(timezone.utc).isoformat()}. No hardware was contacted and no run was repeated.", "",
        f"- Expected records: {complete_expected}",
        f"- Raw logs and manifests available: {len(records)}",
        f"- Passed current offline checker: {sum(r['audit_status'] == 'PASS' for r in inventory)} / {len(inventory)}",
        f"- Usable paired seeds: {', '.join(map(str, usable_seeds)) if usable_seeds else 'none'}",
        f"- Usable balanced runs: {len(selected)} / {complete_expected}",
        f"- Physical DATA missing in selected logs: {physical_data_missing}",
        f"- Physical ACK missing in selected logs: {physical_ack_missing}",
        f"- Saved importance/copy/link policy divergences: {sample_policy_divergences}",
        f"- Sample delivery outcomes different from host reference: {sample_outcome_divergences}",
        "",
        "The checker revalidated manifest/raw SHA256, run identity and execution order, frozen firmware hashes, trace and loss-calendar hashes, reset/configuration/trace-ready acknowledgements, 54 EVT/SAMPLE records, per-copy DATA/ACK fault-calendar outcomes, importance/copy/link replay, Sensor and Gateway END counters, UART_DIAG frame/byte/error counters, exact budget totals, and stored metrics against a fresh parse of each raw log.",
        "",
        "The Gateway END receive counter is compared with post-injection `physical_data_received`. Pre-injection physical frame count is separately checked as post-injection RX plus planned DATA drops.",
        "",
        "No independent USB serial reader byte-count capture was persisted in the legacy raw JSON schema; therefore its historical byte-read telemetry cannot be reconstructed. Firmware-owned UART_DIAG byte/frame counters and serial event lines were cross-checked instead.",
    ]
    if failed_audits:
        audit_lines += ["", "## Failed audit records", ""]
        for row in failed_audits:
            audit_lines.append(f"- `{row['run_id']}`: " + "; ".join(row["audit_errors"]))
    audit_lines += ["", "## Selection and exclusions", "",
                    "Seeds 31–36 were selected as the earliest six contiguous evaluation seeds in the preregistered interleaved Stage 1 order. Selection did not inspect comparative outcomes. Every selected seed contains all four strategies in all four model/rate conditions.",
                    "",
                    "Seed 37 is not part of the paired primary set. Its partial extension records are retained in the original study directory. Run 103, `UNIFORM_BUDGET / RANDOM_COPY / 30% / seed37`, is retained as an incomplete engineering anomaly after a low-frequency ACK receive-path stall; this report does not claim that stall was resolved."]
    (OUT / "audit_report.md").write_text("\n".join(audit_lines) + "\n", encoding="utf-8")

    if main_status != "PASS":
        print(f"AUDIT_INCOMPLETE: usable {len(selected)}/{complete_expected}; details in {OUT}")
        return 2

    _write_final_report(selected, summary, paired, comparison, pareto_rows, frontier_counts,
                        plot_files, physical_data_missing, physical_ack_missing,
                        sample_policy_divergences, sample_outcome_divergences)
    print(f"FINAL_BALANCED_HARDWARE_SET_V1: {len(selected)}/{complete_expected} PASS; seeds={usable_seeds}")
    return 0


def _metric_mean(summary: list[dict], condition: tuple[str, float], strategy: str, metric: str) -> float:
    return next(row[f"{metric}_mean"] for row in summary if row["loss_model"] == condition[0]
                and row["loss_rate"] == condition[1] and row["strategy"] == strategy)


def _paired_lookup(paired: list[dict], model: str, rate: float, comparator: str, metric: str) -> dict:
    return next(row for row in paired if row["loss_model"] == model and row["loss_rate"] == rate
                and row["comparison"] == f"EVENTGUARD - {comparator}" and row["metric"] == metric)


def _write_final_report(rows: list[dict], summary: list[dict], paired: list[dict],
                        comparison: list[dict], pareto_rows: list[dict], frontier_counts: dict,
                        plots: list[str], data_missing: int, ack_missing: int,
                        policy_divergences: int, outcome_divergences: int) -> None:
    lines = [
        "# Final hardware validation report", "",
        "## Dataset and provenance", "",
        "The primary hardware dataset is `FINAL_BALANCED_HARDWARE_SET_V1`: 96 completed runs from seeds 31–36, four strategies, two loss models, and 20%/30% application-layer loss. The six seeds are the first contiguous evaluation seeds completed in Stage 1 execution order and are complete across every condition; no seed was chosen based on treatment outcome.", "",
        "The runs used two ESP32-S3 boards and E220-400T22D radios. Firmware hashes are recorded per run in `selected_runs.json` and match the v2 smoke pair. The data are real hardware executions with software-injected loss; configured percentages are not measured RF packet-error rates.", "",
        "A fresh offline audit passed every selected raw log and run manifest. It rechecked sample counts, END counters, UART_DIAG consistency, importance/copy/link replay, deterministic loss calendars, budget exactness, and metric recomputation. No selected run had an uncontrolled DATA or ACK loss, no policy divergence, and no sample delivery outcome differed from its frozen host reference.", "",
        "## Statistical protocol", "",
        "One seed/run pair is the statistical unit (`n=6` per condition). Summary rows report mean, median, sample standard deviation, and 95% t confidence interval. Paired comparisons use exact two-sided Wilcoxon signed-rank tests and rank-biserial effect size. With six seeds, p-values are coarse; interpretation emphasizes direction, magnitude, and paired consistency, not a claim of definitive significance. The CSV reports all ties and non-ties.", "",
        "## Main comparisons", "",
        "Positive paired difference below means EVENTGUARD's metric is higher; for copy/byte/time metrics that means higher cost, not a strategy win. Delivery differences are fractions (0.01 = one percentage point). Counts are paired seeds where the metric is higher/equal/lower for EventGuard; exact p-values are coarse at n=6.", "",
        "| Condition | Comparison | Critical delivery Δ (mean; median) | Wins/ties/losses | Exact p | Rank-biserial | DATA copies Δ | Total bytes Δ |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model, rate in CONDITIONS:
        for baseline in COMPARATORS:
            d = _paired_lookup(paired, model, rate, baseline, "critical_event_delivery_ratio")
            c = _paired_lookup(paired, model, rate, baseline, "physical_data_transmissions")
            b = _paired_lookup(paired, model, rate, baseline, "total_bytes_transmitted")
            lines.append(f"| {model} {rate:.0%} | EG − {baseline} | {d['mean_paired_difference']:.4f}; {d['median_paired_difference']:.4f} | {d['wins']}/{d['ties']}/{d['losses']} | {d['p_two_sided']:.4f} | {d['rank_biserial']:.3f} | {c['mean_paired_difference']:.2f} | {b['mean_paired_difference']:.1f} |")

    lines += ["", "## Q1: EventGuard vs Importance Only", "",
              "Critical-event delivery is exactly tied between EVENTGUARD and IMPORTANCE_ONLY in all six paired seeds in all four conditions (0/6/0 higher/equal/lower). Importance Only therefore retains the observed critical-delivery result with lower cost in this dataset. EventGuard sends an additional mean 30.50, 33.00, 21.17, and 27.17 DATA copies in RANDOM_COPY 20%, RANDOM_COPY 30%, BURST_SAMPLE 20%, and BURST_SAMPLE 30%, respectively. Its corresponding additional total bytes are 1,098.5, 1,120.2, 773.5, and 951.2 bytes per run. These are consistent cost increases, not extra critical-event delivery. The evaluated link adaptation does not add critical-delivery benefit under these tested conditions.", "",
              "## Q2: Exact DATA-copy budget", "",
              "UNIFORM_BUDGET and RANDOM_BUDGET match EventGuard's host-reference DATA-copy count exactly in every seed and condition. Against UNIFORM_BUDGET, EventGuard ties critical delivery in all six seeds for three conditions; at RANDOM_COPY 30%, it is higher in two seeds and tied in four, with mean paired gain 0.0256 and no losses (exact p=0.500). Against RANDOM_BUDGET, EventGuard has mean gains of 0.0128 at RANDOM_COPY 20% (one higher, five tied) and 0.0256 at RANDOM_COPY 30% (two higher, four tied); it ties all six seeds in both BURST_SAMPLE conditions. With n=6, these are small-sample directional observations, not definitive evidence of general superiority. Equal DATA-copy counts are exact; ACK activity can make total transmitted bytes differ.", "",
              "Retrospective allocation analysis confirms EventGuard placed a larger fraction of its DATA traffic on ground-truth CRITICAL samples than either event-blind budget baseline in all four conditions. Ground-truth labels are used only for this post-run metric and were not available to either budget allocator:", ""]
    for model, rate in CONDITIONS:
        eg_share = _metric_mean(summary, (model, rate), "EVENTGUARD", "critical_traffic_share")
        uniform_share = _metric_mean(summary, (model, rate), "UNIFORM_BUDGET", "critical_traffic_share")
        random_share = _metric_mean(summary, (model, rate), "RANDOM_BUDGET", "critical_traffic_share")
        io_share = _metric_mean(summary, (model, rate), "IMPORTANCE_ONLY", "critical_traffic_share")
        lines.append(f"- {model} {rate:.0%}: EventGuard {eg_share:.1%}; Uniform Budget {uniform_share:.1%}; Random Budget {random_share:.1%}; Importance Only {io_share:.1%}.")
    lines += ["", "## Q3: Burst-sample loss", "",
              "Under BURST_SAMPLE, all four strategies have identical mean critical delivery within each tested rate: 0.7821 at 20% and 0.7179 at 30%. EventGuard and each budget baseline are paired ties in all six seeds. This supports the expected property of this deterministic model: when every copy opportunity for a logical sample is erased together, extra copies of that same sample cannot recover it. It does not establish that redundancy is ineffective for all real burst channels.", "",
              "## Pareto analysis", "",
              "The plots use per-condition mean critical-event delivery versus mean DATA copies and total bytes. A strategy is marked on the empirical frontier if no observed strategy has both lower/equal cost and higher/equal critical delivery with one strict improvement. This is a descriptive frontier over four treatment means, not a confidence region.", "",
              f"- EventGuard frontier appearances: {frontier_counts.get('data_copies', {}).get('EVENTGUARD', 0)}/4 by DATA copies; {frontier_counts.get('total_bytes', {}).get('EVENTGUARD', 0)}/4 by bytes.",
              f"- Importance Only frontier appearances: {frontier_counts.get('data_copies', {}).get('IMPORTANCE_ONLY', 0)}/4 by DATA copies; {frontier_counts.get('total_bytes', {}).get('IMPORTANCE_ONLY', 0)}/4 by bytes.",
              "",
              "![Critical delivery versus DATA copies](plots/pareto_critical_vs_copies.png)", "",
              "![Critical delivery versus bytes](plots/pareto_critical_vs_bytes.png)", "",
              "## Host simulation agreement", "",
              f"The selected set has {policy_divergences} importance/copy/link replay divergences and {outcome_divergences} per-sample delivery outcome differences. The comparison CSV reports per-strategy host/hardware means, matched paired contrasts, and strategy ranking by condition. This is strong execution-level agreement for the tested deterministic setup; it does not establish agreement under uncontrolled RF fading, interference, range changes, or other hardware.", "",
              "## Engineering anomaly outside the primary set", "",
              "In a later extension run, one low-frequency ACK receive-path stall was observed at seed 37. The incomplete run 103 (`UNIFORM_BUDGET / RANDOM_COPY / 30% / seed37`) and its raw logs/diagnosis remain preserved and excluded from the balanced paired dataset. The root cause is unconfirmed and this report does not claim it was fixed.", "",
              "## Interpretation and limitations", "",
              "The most defensible contribution is event-importance-aware redundancy allocation for critical IoT events, evaluated against exact-budget event-blind allocations. In RANDOM_COPY 30%, EventGuard's mean critical-delivery advantage over both equal-budget baselines is 0.0256, but it occurs in only two of six paired seeds and is tied in the other four. At the other random-loss condition and under both burst conditions, gains are absent or smaller. Link adaptation is an evaluated ablation component and did not improve critical delivery over Importance Only in any tested paired seed; it increased resource use.", "",
              "The study uses six paired seeds, one ESP32-S3 pair, a short synthetic trace, and deterministic application-layer erasures. It does not measure RF packet-error rate, range, interference robustness, power, or true RF airtime. Reported communication time is a UART serialization proxy, not measured PHY airtime; no Joule energy claim is made.", "",
              "## Reproducibility artifacts", "",
              "- `selected_runs.json`: exact run IDs, execution order, hashes, and inclusion rationale.",
              "- `audit_report.md` and `audit_runs.csv`: offline checker results.",
              "- `summary.csv`: per-strategy descriptive statistics.",
              "- `paired_tests.csv`: all preregistered paired condition comparisons.",
              "- `simulation_hardware_comparison.csv`: host/hardware means, paired differences, and rankings.",
              "- `pareto_points.csv` and `plots/`: condition-wise Pareto points and figures.", "",
              f"There were {data_missing} uncontrolled physical DATA misses and {ack_missing} uncontrolled physical ACK misses among the selected 96 runs."]
    (OUT / "final_hardware_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
