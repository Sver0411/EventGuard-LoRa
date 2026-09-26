"""Engineering-only diagnostics for the frozen ESP32-S3/E220 firmware pair."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .hardware_validation import (
    FROZEN_STAGE1_FIRMWARE, ROOT, _gateway_end_counter_issues, _parse_samples, _run_one,
    _run_config, _verify_stage1_firmware, _status, atomic_json, calendar_hash,
    frozen_guard, probe_mac, sha,
)
from .host import SerialLogReader, _parse_metrics, discover_boards, load_config
from .model import Strategy
from .simulator import run_reference
from .trace import generate_trace, trace_fingerprint

CONDITION = ("UNIFORM_BUDGET", .20, "RANDOM_COPY", 31)
RUN_ID = "uniform_budget_random_copy_20_seed31"


def _elapsed(marks: dict, start: str, finish: str) -> float | None:
    if start not in marks or finish not in marks:
        return None
    a, b = marks[start].get("host_monotonic"), marks[finish].get("host_monotonic")
    return round((b - a) * 1000, 3) if a is not None and b is not None else None


def _render_report(output: Path, record: dict | None, failure: dict | None = None) -> dict:
    raw_path = output / "raw" / "diagnostic" / f"{RUN_ID}.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else {}
    sensor, gateway = raw.get("sensor", []), raw.get("gateway", [])
    prefixes = ("EVT,", "TX_BEGIN,", "D_TX_BEGIN,", "D_TX_UART_DONE,", "D_TX_AUX_READY,", "TX,",
                "D_ACK_WAIT_BEGIN,", "D_ACK_RECEIVED,", "D_ACK_TIMEOUT,", "ACK,", "TIMEOUT,", "SAMPLE,",
                "END,", "UART_BUFFER,", "UART_DIAG,", "ROLE,SENSOR,", "E220_READY", "E220_ERROR,",
                "D_RX_FIRST_BYTE,", "D_RX_HEADER_COMPLETE,", "D_RX_FRAME_COMPLETE,", "D_RX_CRC_OK,",
                "RX,", "DROP,DATA,", "ACK_TX,", "DELIVER,")
    timeline = [{"host_monotonic": item.get("host_monotonic"), "role": role, "line": item.get("line", "")}
                for role, items in (("SENSOR", sensor), ("GATEWAY", gateway)) for item in items
                if item.get("line", "").startswith(prefixes)]
    timeline.sort(key=lambda row: row.get("host_monotonic") or 0)
    last30 = timeline[-30:]

    marks_by_copy: dict[tuple[int, int], dict] = defaultdict(dict)
    for item in sensor:
        line = item.get("line", "")
        fields = line.split(",")
        if fields[0] not in ("D_TX_BEGIN", "D_TX_UART_DONE", "D_TX_AUX_READY", "D_ACK_WAIT_BEGIN",
                             "D_ACK_RECEIVED", "D_ACK_TIMEOUT") or len(fields) < 4:
            continue
        try:
            key = (int(fields[1]), int(fields[2]))
            marks_by_copy[key][fields[0]] = {"host_monotonic": item.get("host_monotonic"),
                                             "mcu_timestamp_us": int(fields[4]) if len(fields) > 4 else None}
        except (ValueError, IndexError):
            continue
    phase_rows = []
    for (sample_id, copy_index), marks in sorted(marks_by_copy.items()):
        outcome = "D_ACK_RECEIVED" if "D_ACK_RECEIVED" in marks else (
            "D_ACK_TIMEOUT" if "D_ACK_TIMEOUT" in marks else None)
        phase_rows.append({"sample_id": sample_id, "copy_index": copy_index,
            "tx_begin_to_uart_done_ms": _elapsed(marks, "D_TX_BEGIN", "D_TX_UART_DONE"),
            "uart_done_to_aux_ready_ms": _elapsed(marks, "D_TX_UART_DONE", "D_TX_AUX_READY"),
            "aux_ready_to_ack_wait_ms": _elapsed(marks, "D_TX_AUX_READY", "D_ACK_WAIT_BEGIN"),
            "ack_wait_to_outcome_ms": _elapsed(marks, "D_ACK_WAIT_BEGIN", outcome) if outcome else None,
            "ack_outcome": "ACK" if outcome == "D_ACK_RECEIVED" else
                "TIMEOUT" if outcome == "D_ACK_TIMEOUT" else "OPEN_AT_END"})
    sample_count = sum(item.get("line", "").startswith("SAMPLE,") for item in sensor)
    sensor_end = [item.get("line") for item in sensor if item.get("line", "").startswith("END,")]
    gateway_end = [item.get("line") for item in gateway if item.get("line", "").startswith("END,")]
    serial_errors = [item.get("line") for item in sensor + gateway
                     if item.get("line", "").startswith(("HOST_SERIAL_ERROR,", "ERR,UART", "ERR,PACKET"))]
    report = {"run_id": RUN_ID, "status": record.get("status") if record else "FAILED",
              "failure": failure or (record.get("failure") if record else raw.get("failure")),
              "sample_records_completed": sample_count, "expected_samples": 54,
              "sensor_end_lines": sensor_end, "gateway_end_lines": gateway_end,
              "last_30_state_events": last30, "copy_phase_timings": phase_rows,
              "progress_diagnostics": raw.get("progress_diagnostics", []), "serial_errors": serial_errors,
              "raw_sha256": sha(raw_path) if raw_path.is_file() else None,
              "raw_log": f"raw/diagnostic/{RUN_ID}.json", "run_manifest": f"runs/diagnostic/{RUN_ID}.json",
              "maximum_runtime_seconds": raw.get("maximum_runtime_seconds",
                  (record or {}).get("maximum_runtime_seconds")),
              "deadline_calculation": raw.get("deadline_calculation",
                  (record or {}).get("deadline_calculation"))}
    atomic_json(output / "phase_timings.json", {"copy_phase_timings": phase_rows})
    csv_path = output / "phase_timings.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    import csv
    fields = ["sample_id", "copy_index", "tx_begin_to_uart_done_ms", "uart_done_to_aux_ready_ms",
              "aux_ready_to_ack_wait_ms", "ack_wait_to_outcome_ms", "ack_outcome"]
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(phase_rows)
    atomic_json(output / "diagnostic_manifest.json", report)
    event_text = [f"- `{row['role']}` {row['host_monotonic']}: `{row['line']}`" for row in last30]
    phase_text = [f"| {row['sample_id']} | {row['copy_index']} | {row['tx_begin_to_uart_done_ms']} | "
                  f"{row['uart_done_to_aux_ready_ms']} | {row['ack_wait_to_outcome_ms']} | {row['ack_outcome']} |"
                  for row in phase_rows]
    failure_obj = failure or report.get("failure") or {}
    lines = ["# END-timeout engineering diagnostic", "",
        "Condition: `UNIFORM_BUDGET / RANDOM_COPY / 20% / seed31`.",
        f"Outcome: **{report['status']}**; completed SAMPLE records: {sample_count}/54.",
        f"Sensor END: `{sensor_end[-1] if sensor_end else 'not observed'}`.",
        f"Runner watchdog: {report['maximum_runtime_seconds']} seconds.",
        f"Failure: `{failure_obj.get('message', 'none')}`.",
        "This engineering diagnostic is separate from the formal Stage 1 matrix.", "",
        "## Per-copy phase timing (host monotonic clock)", "",
        "| Sample | Copy | TX begin→UART done ms | UART done→AUX ready ms | ACK wait→outcome ms | Outcome |",
        "|---:|---:|---:|---:|---:|---|"] + phase_text + ["", "## Last 30 state events", ""] + event_text + [
        "", "## Interpretation", "",
        "A normal lost-copy path must emit `D_ACK_TIMEOUT` and `TIMEOUT` after the configured ACK timeout, "
        "then proceed to the next copy/sample and emit `END,54,...`. The runner probes `STATUS` once if an ACK "
        "wait exceeds the configured timeout by 5 seconds; no ACK outcome after a further 7 seconds is treated "
        "as a stalled receive path, not as an undersized whole-run deadline.",
        "No treatment conclusions are drawn from this diagnostic."]
    (output / "diagnostic_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def run_end_timeout_diagnostic(output_dir: Path | None = None) -> dict:
    """Run only the failed condition using the exact recovered smoke image pair."""
    study_root = ROOT / "results/hardware_validation_v2"
    output = output_dir or study_root / "end_timeout_diagnostic_v1"
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Diagnostic output already exists; preserving it: {output}")
    output.mkdir(parents=True, exist_ok=True)
    study = json.loads((study_root / "hardware_manifest.json").read_text(encoding="utf-8"))
    smoke_state = study.get("stages", {}).get("smoke", {})
    if smoke_state.get("gate_status") != "PASS" or smoke_state.get("passed_runs") != 12:
        raise RuntimeError("Diagnostic requires the retained v2 12/12 smoke gate")
    provenance = frozen_guard()
    provenance["hardware_runner_sha256"] = sha(Path(__file__).with_name("hardware_validation.py"))
    provenance["hardware_analysis_sha256"] = sha(ROOT / "eventguard/hardware_analysis.py")
    images, recovery = _verify_stage1_firmware(study)
    provenance["firmware_images_sha256"] = {role: sha(path) for role, path in images.items()}
    provenance["firmware_image_manifest_commit"] = study.get("firmware_image_manifest_commit")
    provenance["firmware_provenance"] = recovery
    provenance["firmware_hash_source"] = "frozen device-recovered v2 smoke artifacts; diagnostic does not build or flash"
    provenance["firmware_artifact_directory"] = str(FROZEN_STAGE1_FIRMWARE.relative_to(ROOT))

    stage1_root = study_root / "stage1"
    budget_doc = json.loads((stage1_root / "budget_table.json").read_text(encoding="utf-8"))
    budget_row = next((row for row in budget_doc["conditions"] if row["loss_model"] == "RANDOM_COPY" and
                       row["loss_rate"] == .20 and row["seed"] == 31), None)
    if not budget_row:
        raise RuntimeError("Frozen EventGuard reference budget for the diagnostic condition is missing")
    cfg = load_config()
    samples = generate_trace(31, 6)
    budget = int(budget_row["eventguard_reference_data_budget"])
    config = replace(_run_config(cfg, "UNIFORM_BUDGET", .20, "RANDOM_COPY", 31), data_copy_budget=budget)
    eg_config = replace(config, strategy=Strategy.EVENTGUARD, data_copy_budget=None)
    reference_budget = int(run_reference(samples, eg_config, cfg["uart_baud"])["metrics"]["physical_data_transmissions"])
    if reference_budget != budget or budget_row["trace_sha256"] != trace_fingerprint(samples) or \
            budget_row["loss_calendar_sha256"] != calendar_hash(config, len(samples)):
        raise RuntimeError("Frozen trace, loss calendar, or EventGuard budget mismatch in diagnostic preflight")

    mapping, discovery_readers = discover_boards(3)
    for reader in discovery_readers.values(): reader.close()
    mapping["chip_macs"] = {role: probe_mac(mapping[f"{role}_port"]) for role in ("sensor", "gateway")}
    recovered_devices = recovery["device_partition_map"]["devices"]
    if any(mapping["chip_macs"][role].lower() != recovered_devices[role]["mac"].lower()
           for role in ("sensor", "gateway")):
        raise RuntimeError("Diagnostic boards differ from the devices used for v2 smoke firmware recovery")
    sensor = SerialLogReader(mapping["sensor_port"])
    gateway = SerialLogReader(mapping["gateway_port"])
    record = None
    try:
        readiness = _status(sensor, gateway)
        atomic_json(output / "diagnostic_preflight.json", {"status": "PASS",
            "at_utc": datetime.now(timezone.utc).isoformat(), "sensor_mac": mapping["chip_macs"]["sensor"],
            "gateway_mac": mapping["chip_macs"]["gateway"],
            "serial_ports": {"sensor": mapping["sensor_port"], "gateway": mapping["gateway_port"]},
            "readiness": readiness, "firmware_images_sha256": provenance["firmware_images_sha256"],
            "budget": budget, "trace_sha256": trace_fingerprint(samples),
            "loss_calendar_sha256": calendar_hash(config, len(samples))})
        record = _run_one(sensor, gateway, config, samples, mapping, provenance, 0,
                          "diagnostic", budget, output)
    except BaseException as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
        run_path = output / "runs" / "diagnostic" / f"{RUN_ID}.json"
        if run_path.is_file():
            record = json.loads(run_path.read_text(encoding="utf-8"))
        report = _render_report(output, record, failure)
        atomic_json(output / "diagnostic_manifest.json", {**report, "preflight": "PASS",
            "provenance": provenance, "exception": failure,
            "status": record.get("status", "FAILED") if record else "PRESTART_FAILED"})
        raise
    finally:
        sensor.close(); gateway.close()
    report = _render_report(output, record)
    atomic_json(output / "diagnostic_manifest.json", {**report, "preflight": "PASS",
        "provenance": provenance, "status": record["status"],
        "firmware_images_sha256": provenance["firmware_images_sha256"],
        "physical_anomalies": record.get("physical_anomalies", []),
        "issues": record.get("issues", [])})
    return {"status": record["status"], "run_id": RUN_ID,
            "completed_samples": record["metrics"]["logical_packets"],
            "physical_anomalies": record.get("physical_anomalies", []),
            "issues": record.get("issues", []), "report": str(output / "diagnostic_report.md")}


def finalize_saved_diagnostic(output_dir: Path | None = None) -> dict:
    """Analyze already captured serial evidence; never opens ports or sends commands."""
    output = output_dir or (ROOT / "results/hardware_validation_v2/end_timeout_diagnostic_v1")
    raw_path = output / "raw" / "diagnostic" / f"{RUN_ID}.json"
    run_path = output / "runs" / "diagnostic" / f"{RUN_ID}.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    started = json.loads(run_path.read_text(encoding="utf-8"))
    if not any(item.get("line", "").startswith("END,") for item in raw.get("sensor", [])):
        raise RuntimeError("Saved diagnostic has no Sensor END; do not convert it to a completed attempt")
    cfg = load_config()
    samples = generate_trace(31, 6)
    budget = int(started["configured_data_copy_budget"])
    config = replace(_run_config(cfg, "UNIFORM_BUDGET", .20, "RANDOM_COPY", 31), data_copy_budget=budget)
    reference = run_reference(samples, config, cfg["uart_baud"])
    sensor_lines = [(row["host_monotonic"], row["line"]) for row in raw.get("sensor", [])]
    gateway_lines = [(row["host_monotonic"], row["line"]) for row in raw.get("gateway", [])]
    start = float(raw["start_host_monotonic"])
    end = max(row[0] for row in sensor_lines + gateway_lines)
    events, differences, issues, anomalies = _parse_samples(samples, sensor_lines, gateway_lines, config, reference)
    metrics = _parse_metrics(samples, events, sensor_lines, gateway_lines, start, end, config,
                             started["trace_sha256"])
    sensor_end = [line for _, line in sensor_lines if line.startswith("END,")]
    gateway_end = [line for _, line in gateway_lines if line.startswith("END,")]
    if len(sensor_end) != 1 or len(gateway_end) != 1:
        issues.append(f"missing or duplicate END lines sensor={sensor_end}, gateway={gateway_end}")
    if metrics["physical_data_transmissions"] != sum(e["physical_data_copies"] for e in events):
        issues.append("physical DATA count does not match per-sample logs")
    if metrics["logical_packets"] != len(events):
        issues.append("incomplete per-sample log")
    if metrics["invalid_packets"] or metrics["out_of_order_packets"]:
        issues.append("invalid packet or sequence/order anomaly")
    if sensor_end and gateway_end:
        sensor_totals = [int(value) for value in sensor_end[0].split(",")[1:]]
        gateway_totals = [int(value) for value in gateway_end[0].split(",")[1:]]
        if sensor_totals[:3] != [len(samples), metrics["physical_data_transmissions"], metrics["accepted_ack"]] or sensor_totals[3] != 0:
            issues.append(f"sensor END counters disagree: {sensor_totals}")
        issues.extend(_gateway_end_counter_issues(gateway_totals, len(samples), metrics))

    uart_diagnostics = {}
    crc_failures = metrics["crc_errors"]
    parser_errors = []
    for role, lines in (("sensor", sensor_lines), ("gateway", gateway_lines)):
        for _, line in lines:
            if line.startswith("UART_DIAG,"):
                fields = line.split(",")
                try:
                    counters = dict(zip(fields[1::2], (int(value) for value in fields[2::2])))
                    uart_diagnostics[role] = counters
                    crc_failures += counters.get("crc_failures", 0)
                    for name in ("parser_resyncs", "invalid_type", "invalid_version"):
                        if counters.get(name, 0): parser_errors.append(f"{role} E220 {name}={counters[name]}")
                except ValueError:
                    issues.append(f"{role} malformed UART diagnostics: {line}")
    if parser_errors:
        issues.extend(parser_errors)
    if crc_failures >= 3 or crc_failures / max(1, metrics["physical_data_transmissions"] + metrics["ack_count"]) > .01:
        issues.append(f"CRC storm: {crc_failures} CRC failures")
    if differences:
        issues.append(f"host/firmware policy divergence at {len(differences)} sample(s)")
    metrics.update({"physical_ack_frames": metrics["ack_count"],
        "ack_timeout": sum(line.startswith("TIMEOUT,") for _, line in sensor_lines),
        "uncontrolled_physical_ack_missing": max(0, metrics["ack_count"] - metrics["physical_ack_received"]),
        "uncontrolled_physical_data_missing": max(0, metrics["physical_data_transmissions"] - metrics["physical_data_before_injection"]),
        "uncontrolled_physical_data_missing_rate": max(0, metrics["physical_data_transmissions"] - metrics["physical_data_before_injection"]) / max(1, metrics["physical_data_transmissions"]),
        "uncontrolled_physical_ack_missing_rate": max(0, metrics["ack_count"] - metrics["physical_ack_received"]) / max(1, metrics["ack_count"]),
        "crc_failure_count_including_parser": crc_failures, "uart_diagnostics": uart_diagnostics,
        "estimated_communication_time_ms": (metrics["data_bytes_transmitted"] + metrics["ack_bytes_transmitted"]) * 10 * 1000 / cfg["uart_baud"],
        "estimated_data_uart_time_ms": metrics["data_bytes_transmitted"] * 10 * 1000 / cfg["uart_baud"],
        "estimated_ack_uart_time_ms": metrics["ack_bytes_transmitted"] * 10 * 1000 / cfg["uart_baud"],
        "good_count": sum(e["link_state_before"] == "GOOD" for e in events),
        "degraded_count": sum(e["link_state_before"] == "DEGRADED" for e in events),
        "bad_count": sum(e["link_state_before"] == "BAD" for e in events),
        "link_state_transitions": sum(a["link_state_before"] != b["link_state_before"] for a,b in zip(events,events[1:]))})
    record = {**started, "status": "complete" if not issues else "failed", "duration_s": end-start,
        "sample_count": len(samples), "metrics": metrics, "sample_events": events,
        "sample_differences": differences, "physical_anomalies": anomalies, "issues": issues,
        "reference_metrics": reference["metrics"], "raw_sha256": sha(raw_path),
        "analysis_recovered_from_complete_raw": True}
    atomic_json(run_path, record)
    report = _render_report(output, record)
    atomic_json(output / "diagnostic_manifest.json", {**report, "status": record["status"],
        "preflight": "PASS", "firmware_images_sha256": record["firmware_images_sha256"],
        "physical_anomalies": anomalies, "issues": issues,
        "analysis_recovered_from_complete_raw": True})
    return {"status": record["status"], "completed_samples": metrics["logical_packets"],
            "data_tx": metrics["physical_data_transmissions"],
            "data_missing": metrics["uncontrolled_physical_data_missing"],
            "ack_missing": metrics["uncontrolled_physical_ack_missing"],
            "crc_errors": metrics["crc_failure_count_including_parser"],
            "issues": issues, "report": str(output / "diagnostic_report.md")}
