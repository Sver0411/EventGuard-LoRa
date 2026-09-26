#!/usr/bin/env python3
"""Run isolated E220 receive-path diagnostics; results are not paper treatments."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import statistics
import sys
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eventguard.faults import LossPlan
from eventguard.hardware_validation import _lines
from eventguard.host import (ROOT, SerialLogReader, _config_command, _run_config,
                             _wait_for_ack, build_firmware, discover_boards,
                             flash_firmware, load_config, probe_mac)
from eventguard.model import LossModel

OUT = ROOT / "results/e220_receive_diagnostic"
FRAMES = 500
PERIOD_MS = 1000
DEFAULT_ACK_TIMEOUT_MS = 1000
DEFAULT_POLL_TIMEOUT_MS = 250
SWEEP_ACK_TIMEOUTS = (900, 950, 1000, 1025, 1050, 1100)
SWEEP_POLL_TIMEOUTS = (100, 200, 250, 300)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_at_commit(commit: str, relative_path: str) -> str:
    content = subprocess.check_output(["git", "show", f"{commit}:{relative_path}"], cwd=ROOT)
    return hashlib.sha256(content).hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _await(reader: SerialLogReader, token: str, timeout: float = 12.0) -> list[tuple[float, str]]:
    deadline = time.monotonic() + timeout
    collected: list[tuple[float, str]] = []
    while time.monotonic() < deadline:
        batch = reader.drain()
        collected.extend(batch)
        if any(line.startswith("ERR,") or line.startswith("E220_ERROR,") for _, line in batch):
            errors = [line for _, line in batch if line.startswith(("ERR,", "E220_ERROR,"))]
            raise RuntimeError(f"Board returned an error while waiting for {token}: {errors}")
        if any(line.startswith(token) for _, line in batch):
            return collected
        time.sleep(.02)
    raise TimeoutError(f"Timed out waiting for {token}; tail={[line for _, line in collected[-20:]]}")


def _write_raw(stream, batch: list[tuple[float, str]]) -> None:
    for host_time, line in batch:
        stream.write(json.dumps({"host_monotonic": host_time, "line": line}, ensure_ascii=False) + "\n")
    stream.flush()


def _capture_command(reader: SerialLogReader, command: str, token: str,
                     prelude: list[tuple[float, str]], timeout: float = 12.0) -> None:
    reader.write(command)
    prelude.extend(_await(reader, token, timeout))


def _parse_int_events(lines: list[str], prefix: str, indexes: tuple[int, ...]) -> list[tuple[int, ...]]:
    parsed = []
    for line in lines:
        fields = line.split(",")
        if not fields or fields[0] != prefix:
            continue
        try:
            parsed.append(tuple(int(fields[index]) for index in indexes))
        except (ValueError, IndexError):
            continue
    return parsed


def _uart_diagnostics(lines: list[str]) -> dict[str, int]:
    for line in reversed(lines):
        if not line.startswith("UART_DIAG,"):
            continue
        fields = line.split(",")
        try:
            return {fields[i]: int(fields[i + 1]) for i in range(1, len(fields) - 1, 2)}
        except (ValueError, IndexError):
            return {"malformed": 1}
    return {}


def _aux_stats(lines: list[str]) -> dict:
    edges = _parse_int_events(lines, "AUX", (1, 2))
    low_start = None
    low_durations = []
    for timestamp, level in edges:
        if level == 0:
            low_start = timestamp
        elif low_start is not None:
            low_durations.append(max(0, timestamp - low_start))
            low_start = None
    return {"edge_count": len(edges), "low_edges": sum(level == 0 for _, level in edges),
            "high_edges": sum(level == 1 for _, level in edges),
            "completed_low_intervals": len(low_durations),
            "aux_low_duration_us_mean": statistics.mean(low_durations) if low_durations else None,
            "aux_low_duration_us_median": statistics.median(low_durations) if low_durations else None,
            "aux_low_duration_us_max": max(low_durations) if low_durations else None}


def _test_metrics(test: str, sensor_lines: list[str], gateway_lines: list[str],
                  frames: int, rate: float, seed: int = 31) -> dict:
    tx_begin_rows = _parse_int_events(sensor_lines, "D_TX_BEGIN", (1, 2, 3, 4))
    tx_done_rows = _parse_int_events(sensor_lines, "DIAG_TX_DONE", (1, 2))
    rx_rows = _parse_int_events(gateway_lines, "D_RX_CRC_OK", (1, 2, 3, 4))
    ack_rows = _parse_int_events(sensor_lines, "DIAG_ACK", (1,))
    ack_received_rows = _parse_int_events(sensor_lines, "D_ACK_RECEIVED", (1, 2, 3, 4))
    ack_timeout_rows = _parse_int_events(sensor_lines, "D_ACK_TIMEOUT", (1, 2, 3, 4))
    rx_drop_rows = _parse_int_events(gateway_lines, "D_RX_INJECT_DROP", (1, 2, 3, 4))
    suppress_rows = _parse_int_events(gateway_lines, "ACK_SUPPRESS", (1,))
    ack_done_rows = _parse_int_events(gateway_lines, "D_ACK_DONE", (1, 2, 3, 4))
    drop_lines = [line for line in gateway_lines if line.startswith("DROP,DATA,")]
    suppress = {row[0] for row in suppress_rows}
    planned_drop = {row[2] for row in rx_drop_rows}
    physical_rx = {row[2] for row in rx_rows}
    successful_tx = {row[0] for row in tx_done_rows}
    missing = sorted(successful_tx - physical_rx)
    loss = LossPlan(seed, rate, LossModel.RANDOM_COPY, frames, 3)
    expected_drop = {seq for seq in range(frames) if loss.drops("DATA", seq, 0)}
    ack_success = {row[0] for row in ack_rows if len(row) > 0}
    tx_times = {row[0]: row[1] for row in tx_done_rows}
    rx_times = {row[2]: row[3] for row in rx_rows}
    first_rx_times = {row[2]: row[3] for row in _parse_int_events(
        gateway_lines, "D_RX_FIRST_BYTE", (1, 2, 3, 4))}
    aux_edges = _parse_int_events(gateway_lines, "AUX", (1, 2))
    aux_high = [timestamp for timestamp, level in aux_edges if level == 1]
    uart_done = {row[2]: row[3] for row in _parse_int_events(sensor_lines, "D_TX_UART_DONE", (1, 2, 3, 4))}
    aux_ready = {row[2]: row[3] for row in _parse_int_events(sensor_lines, "D_TX_AUX_READY", (1, 2, 3, 4))}
    aux_wait_ms = [(aux_ready[seq] - uart_done[seq]) / 1000 for seq in uart_done.keys() & aux_ready.keys()]
    missing_after_no_ack = [seq for seq in missing if seq - 1 in suppress]
    missing_after_data_drop = [seq for seq in missing if seq - 1 in expected_drop]
    missing_windows = []
    for seq in missing:
        previous = seq - 1
        successor = seq + 1
        successor_rx = first_rx_times.get(successor)
        prior_aux = max((ts for ts in aux_high if successor_rx is not None and ts <= successor_rx), default=None)
        missing_windows.append({
            "sequence": seq,
            "previous_sequence": previous if previous >= 0 else None,
            "previous_condition": ("ACK_SUPPRESSED" if previous in suppress else
                                   "DATA_DROP" if previous in expected_drop else
                                   "ACK_SUCCESS" if previous in ack_success else "other"),
            "time_since_previous_TX_ms": ((tx_times[seq] - tx_times[previous]) / 1000
                                           if previous in tx_times and seq in tx_times else None),
            "previous_gateway_RX_timestamp_us": rx_times.get(previous),
            "next_gateway_RX_after_gap_timestamp_us": successor_rx,
            "gateway_RX_gap_span_ms": ((first_rx_times[successor] - first_rx_times[previous]) / 1000
                                        if previous in first_rx_times and successor in first_rx_times else None),
            "previous_AUX_high_before_successor_us": prior_aux,
            "successor_RX_minus_previous_AUX_high_ms": ((successor_rx - prior_aux) / 1000
                                                        if successor_rx is not None and prior_aux is not None else None),
        })

    def conditional_probability(condition: set[int]) -> dict:
        cases = {seq for seq in range(1, frames) if seq - 1 in condition}
        observed = sum(seq in missing for seq in cases)
        return {"missing": observed, "eligible": len(cases),
                "probability": observed / len(cases) if cases else None}

    gateway_end = next((line for line in reversed(gateway_lines) if line.startswith("END,")), None)
    end_fields = [int(x) for x in gateway_end.split(",")[1:]] if gateway_end else []
    sensor_end = next((line for line in reversed(sensor_lines) if line.startswith("DIAG_END,")), None)
    sensor_end_fields = [int(x) for x in sensor_end.split(",")[1:]] if sensor_end else []
    sensor_uart = _uart_diagnostics(sensor_lines)
    gateway_uart = _uart_diagnostics(gateway_lines)
    parser_errors = sum(sensor_uart.get(key, 0) + gateway_uart.get(key, 0)
                        for key in ("crc_failures", "invalid_type", "invalid_version"))
    ack_timeouts = {row[2] for row in ack_timeout_rows}
    expected_no_ack = suppress if test == "B" else expected_drop if test == "C" else set()
    unexpected_ack_timeouts = sorted(ack_timeouts - expected_no_ack)
    expected_ack = physical_rx - expected_no_ack
    sensor_ack_ok = {row[0] for row in ack_rows}
    missing_ack = sorted(expected_ack - sensor_ack_ok)
    if test == "B":
        planned_behavior_errors = sorted(suppress.symmetric_difference({seq for seq in range(frames) if seq % 5 == 0}))
    elif test == "C":
        planned_behavior_errors = sorted(planned_drop.symmetric_difference(expected_drop))
    else:
        planned_behavior_errors = sorted(planned_drop)
    expected_post_injection_rx = frames - len(expected_drop)
    errors = []
    if len(tx_begin_rows) != frames or successful_tx != set(range(frames)):
        errors.append("Sensor did not log every DATA send attempt and completion")
    if missing:
        errors.append(f"uncontrolled physical DATA missing: {missing[:25]}")
    if parser_errors:
        errors.append(f"parser errors: {parser_errors}")
    if unexpected_ack_timeouts:
        errors.append(f"unexpected ACK timeouts: {unexpected_ack_timeouts[:25]}")
    if missing_ack:
        errors.append(f"expected physical ACK missing at Sensor: {missing_ack[:25]}")
    if planned_behavior_errors:
        errors.append(f"diagnostic fault/ACK suppression mismatch: {planned_behavior_errors[:25]}")
    if end_fields and (end_fields[1] != expected_post_injection_rx or end_fields[6] != len(expected_drop)):
        errors.append(f"Gateway END disagrees with physical injection accounting: {end_fields}")
    if len(rx_rows) + len(missing) != len(successful_tx):
        errors.append("Gateway physical receive count does not match Sensor DATA send count")
    return {
        "test": test, "frames_requested": frames,
        "sensor_data_tx_attempts": len(tx_begin_rows), "sensor_data_tx_completed": len(successful_tx),
        "gateway_crc_valid_data_frames": len(rx_rows), "gateway_post_injection_rx": end_fields[1] if len(end_fields) > 1 else None,
        "planned_data_drops": len(expected_drop), "observed_data_drops": len(planned_drop),
        "ack_suppressed": len(suppress), "gateway_ack_tx": end_fields[4] if len(end_fields) > 4 else len(ack_done_rows),
        "sensor_ack_frames_received": len(ack_received_rows), "sensor_accepted_ack": len(ack_success),
        "sensor_ack_timeouts": len(ack_timeout_rows), "unexpected_ack_timeouts": unexpected_ack_timeouts,
        "uncontrolled_physical_data_missing": len(missing), "missing_sequences": missing,
        "missing_after_previous_ack_suppressed": missing_after_no_ack,
        "missing_after_previous_data_drop": missing_after_data_drop,
        "p_missing_given_previous_ack_success": conditional_probability(ack_success),
        "p_missing_given_previous_ack_suppressed": conditional_probability(suppress),
        "p_missing_given_previous_data_drop": conditional_probability(expected_drop),
        "missing_frame_windows": missing_windows,
        "sensor_aux": _aux_stats(sensor_lines), "gateway_aux": _aux_stats(gateway_lines),
        "tx_uart_done_to_aux_ready_ms_mean": statistics.mean(aux_wait_ms) if aux_wait_ms else None,
        "tx_uart_done_to_aux_ready_ms_median": statistics.median(aux_wait_ms) if aux_wait_ms else None,
        "tx_uart_done_to_aux_ready_ms_max": max(aux_wait_ms) if aux_wait_ms else None,
        "sensor_uart_diagnostics": sensor_uart, "gateway_uart_diagnostics": gateway_uart,
        "parser_error_total": parser_errors, "gateway_end": end_fields, "sensor_diag_end": sensor_end_fields,
        "status": "pass" if not errors else "fail", "issues": errors,
    }


def _preserve_existing(path: Path) -> None:
    if not path.exists():
        return
    attempt_dir = OUT / "raw" / "attempts"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    shutil.copy2(path, attempt_dir / f"{stamp}_{path.name}")


def _completed_test(test: str, frames: int, rate: float, ack_timeout_ms: int,
                    poll_timeout_ms: int, suffix: str, allow_resume: bool,
                    firmware_hashes: dict[str, str]) -> dict | None:
    if not allow_resume:
        return None
    run_id = f"test_{test}{suffix}_ack{ack_timeout_ms}_poll{poll_timeout_ms}"
    sensor_raw = OUT / "raw" / f"{run_id}_sensor.jsonl"
    gateway_raw = OUT / "raw" / f"{run_id}_gateway.jsonl"
    metric_path = OUT / "metrics" / f"{run_id}.json"
    if not sensor_raw.exists() or not gateway_raw.exists() or not metric_path.exists():
        return None
    try:
        saved = json.loads(metric_path.read_text(encoding="utf-8"))
        if (saved.get("status") != "pass" or saved.get("test") != test or
                saved.get("frames_requested") != frames or saved.get("data_loss_rate") != rate or
                saved.get("ack_timeout_ms") != ack_timeout_ms or
                saved.get("rx_poll_timeout_ms") != poll_timeout_ms or
                saved.get("sensor_raw_sha256") != sha(sensor_raw) or
                saved.get("gateway_raw_sha256") != sha(gateway_raw)):
            return None
        saved_hashes = saved.get("firmware_images_sha256")
        if saved_hashes is not None and saved_hashes != firmware_hashes:
            return None
        return saved
    except (OSError, ValueError, TypeError):
        return None


def _run_test(sensor: SerialLogReader, gateway: SerialLogReader, test: str,
              frames: int, rate: float, ack_timeout_ms: int, poll_timeout_ms: int,
              suffix: str = "", firmware_images_sha256: dict[str, str] | None = None) -> dict:
    run_id = f"test_{test}{suffix}_ack{ack_timeout_ms}_poll{poll_timeout_ms}"
    sensor_raw = OUT / "raw" / f"{run_id}_sensor.jsonl"
    gateway_raw = OUT / "raw" / f"{run_id}_gateway.jsonl"
    metric_path = OUT / "metrics" / f"{run_id}.json"
    _preserve_existing(sensor_raw); _preserve_existing(gateway_raw); _preserve_existing(metric_path)
    sensor_raw.parent.mkdir(parents=True, exist_ok=True)
    pre_sensor: list[tuple[float, str]] = []
    pre_gateway: list[tuple[float, str]] = []
    config = _run_config(load_config(), "FIXED_2", rate, "RANDOM_COPY", 31)
    sensor.drain(); gateway.drain()
    sensor.write("RESET"); gateway.write("RESET")
    pre_sensor.extend(_await(sensor, "RESET,OK")); pre_gateway.extend(_await(gateway, "RESET,OK"))
    sensor.write(_config_command(config)); gateway.write(_config_command(config))
    pre_sensor.extend(_await(sensor, "CONFIGURED,")); pre_gateway.extend(_await(gateway, "CONFIGURED,"))
    _capture_command(gateway, f"DIAG_GATEWAY,{test},{poll_timeout_ms}", "DIAG_GATEWAY_READY,", pre_gateway)
    _capture_command(gateway, f"TRACE_COUNT,{frames}", "TRACE_COUNT,", pre_gateway)
    _capture_command(gateway, "ARM", "ARMED,", pre_gateway)
    sensor.write(f"E220_RX_DIAGNOSTIC,{test},{frames},{PERIOD_MS},{ack_timeout_ms}")
    pre_sensor.extend(_await(sensor, "E220_RX_DIAGNOSTIC,STARTED,"))

    all_sensor: list[str] = []
    all_gateway: list[str] = []
    start_utc = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    deadline = start + frames * (max(PERIOD_MS, ack_timeout_ms) + 250) / 1000 + 180
    with sensor_raw.open("w", encoding="utf-8") as sf, gateway_raw.open("w", encoding="utf-8") as gf:
        _write_raw(sf, pre_sensor); _write_raw(gf, pre_gateway)
        all_sensor.extend(line for _, line in pre_sensor)
        all_gateway.extend(line for _, line in pre_gateway)
        finished = False
        while time.monotonic() < deadline:
            sb, gb = sensor.drain(), gateway.drain()
            if sb:
                _write_raw(sf, sb); all_sensor.extend(line for _, line in sb)
            if gb:
                _write_raw(gf, gb); all_gateway.extend(line for _, line in gb)
            if any(line.startswith("DIAG_END,") for _, line in sb):
                finished = True
                break
            time.sleep(.05)
        if not finished:
            sb, gb = sensor.drain(), gateway.drain()
            _write_raw(sf, sb); _write_raw(gf, gb)
            all_sensor.extend(line for _, line in sb); all_gateway.extend(line for _, line in gb)
            raise TimeoutError(f"{run_id} timed out waiting for Sensor DIAG_END")
        time.sleep(.5)
        gb = gateway.drain()
        _write_raw(gf, gb); all_gateway.extend(line for _, line in gb)
        gateway.write("ENDRUN")
        end_batch = _await(gateway, "END,", 15)
        _write_raw(gf, end_batch); all_gateway.extend(line for _, line in end_batch)
        tail_s, tail_g = sensor.drain(), gateway.drain()
        _write_raw(sf, tail_s); _write_raw(gf, tail_g)
        all_sensor.extend(line for _, line in tail_s); all_gateway.extend(line for _, line in tail_g)
    metrics = _test_metrics(test, all_sensor, all_gateway, frames, rate)
    metrics.update({"run_id": run_id, "start_time_utc": start_utc,
                    "duration_s": time.monotonic() - start,
                    "ack_timeout_ms": ack_timeout_ms, "rx_poll_timeout_ms": poll_timeout_ms,
                    "data_loss_rate": rate, "seed": 31,
                    "firmware_images_sha256": firmware_images_sha256 or {},
                    "sensor_raw": str(sensor_raw.relative_to(ROOT)),
                    "gateway_raw": str(gateway_raw.relative_to(ROOT)),
                    "sensor_raw_sha256": sha(sensor_raw), "gateway_raw_sha256": sha(gateway_raw)})
    atomic_json(metric_path, metrics)
    return metrics


def _prepare_boards(skip_flash: bool, flashed_manifest: dict | None = None) -> tuple[dict, dict[str, SerialLogReader], dict]:
    mapping, discovered = discover_boards()
    for reader in discovered.values():
        reader.close()
    if "chip_macs" not in mapping:
        mapping["chip_macs"] = {"sensor": probe_mac(mapping["sensor_port"]),
                                "gateway": probe_mac(mapping["gateway_port"])}
    else:
        mapping["chip_macs"]["sensor"] = probe_mac(mapping["sensor_port"])
        mapping["chip_macs"]["gateway"] = probe_mac(mapping["gateway_port"])
    config = load_config()
    current_sources = {
        "sensor_firmware_source_sha256": sha(ROOT / "firmware/main/main.c"),
        "gateway_firmware_source_sha256": sha(ROOT / "firmware/main/main.c"),
        "e220_driver_source_sha256": sha(ROOT / "firmware/common/e220.c"),
        "stream_parser_source_sha256": sha(ROOT / "firmware/common/e220_stream_parser.c"),
        "config_sha256": sha(ROOT / "configs/default.json"),
        "algorithm_spec_v1_sha256": sha(ROOT / "docs/algorithm_spec_v1.md"),
    }
    if skip_flash:
        if not flashed_manifest:
            raise RuntimeError("--skip-flash requires a prior manifest attesting the currently flashed images")
        mismatches = [key for key, value in current_sources.items() if flashed_manifest.get(key) != value]
        if mismatches:
            raise RuntimeError(f"--skip-flash source/config differs from the flashed-image manifest: {mismatches}")
        expected_macs = flashed_manifest.get("chip_macs") or {
            "sensor": flashed_manifest.get("sensor_mac"),
            "gateway": flashed_manifest.get("gateway_mac"),
        }
        if expected_macs and any(expected_macs.get(role) != mapping["chip_macs"][role]
                                 for role in ("sensor", "gateway")):
            raise RuntimeError("--skip-flash board MACs differ from the flashed-image manifest")
        image_hashes = flashed_manifest.get("firmware_images_sha256")
        if not isinstance(image_hashes, dict) or set(image_hashes) != {"sensor", "gateway"}:
            raise RuntimeError("--skip-flash prior manifest has no complete firmware image hashes")
        image_manifest_commit = flashed_manifest.get("firmware_image_manifest_commit",
                                                     flashed_manifest.get("git_commit"))
        firmware_hash_source = "previous diagnostic manifest; no flash operation since that attestation"
    else:
        outputs = build_firmware(config, OUT, diagnostic_mode=True)
        image_hashes = {role: sha(path) for role, path in outputs.items()}
        flash_firmware(outputs, mapping, config, OUT)
        image_manifest_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        firmware_hash_source = "images built and flashed in this diagnostic run"
    readers = {"sensor": SerialLogReader(mapping["sensor_port"]),
               "gateway": SerialLogReader(mapping["gateway_port"])}
    status = {}
    for role, reader in readers.items():
        expected_role = "SENSOR" if role == "sensor" else "GATEWAY"
        reader.drain()
        reader.write("STATUS")
        lines = _await(reader, f"ROLE,{expected_role},", 15)
        values = [line for _, line in lines]
        if not any(line == "E220_READY" for line in values):
            values.extend(line for _, line in _await(reader, "E220_READY", 5))
        status[role] = values
    provenance = {
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        "firmware_image_manifest_commit": image_manifest_commit,
        "firmware_hash_source": firmware_hash_source,
        "algorithm_version": json.loads((ROOT / "results/experiment_manifest.json").read_text())["algorithm_version"],
        "algorithm_spec_v1_sha256": sha(ROOT / "docs/algorithm_spec_v1.md"),
        "config_sha256": sha(ROOT / "configs/default.json"),
        "sensor_firmware_source_sha256": sha(ROOT / "firmware/main/main.c"),
        "gateway_firmware_source_sha256": sha(ROOT / "firmware/main/main.c"),
        "e220_driver_source_sha256": sha(ROOT / "firmware/common/e220.c"),
        "stream_parser_source_sha256": sha(ROOT / "firmware/common/e220_stream_parser.c"),
        "firmware_images_sha256": image_hashes,
        "sensor_mac": mapping["chip_macs"]["sensor"], "gateway_mac": mapping["chip_macs"]["gateway"],
        "serial_ports": {"sensor": mapping["sensor_port"], "gateway": mapping["gateway_port"]},
        "e220_configuration": config["e220"], "board_status": status,
        "flash_skipped": skip_flash,
    }
    return mapping, readers, provenance


def _write_report(manifest: dict, tests: list[dict], sweep: list[dict]) -> None:
    test_lookup = {entry["test"]: entry for entry in tests}
    lines = [
        "# E220 Receive-Path Diagnostic Report",
        "",
        "> 这些是 E220/UART/parser 工程诊断数据，不是 EventGuard treatment 或论文结果。",
        "",
        "## Provenance",
        "",
        f"- Git commit: `{manifest['git_commit']}` (`{manifest['branch']}`)",
        f"- Firmware image manifest commit: `{manifest.get('firmware_image_manifest_commit', 'not recorded')}`; hash source: `{manifest.get('firmware_hash_source', 'not recorded')}`.",
        f"- Sensor image SHA256: `{manifest['firmware_images_sha256']['sensor']}`",
        f"- Gateway image SHA256: `{manifest['firmware_images_sha256']['gateway']}`",
        f"- Algorithm spec SHA256: `{manifest['algorithm_spec_v1_sha256']}`",
        f"- Serial ports: Sensor `{manifest['serial_ports']['sensor']}`, Gateway `{manifest['serial_ports']['gateway']}`",
        f"- E220 config read/verification: `{manifest['e220_configuration']}`; both boards reported E220_READY.",
        f"- Pre-fix commit: `{manifest['pre_fix']['git_commit']}`; Sensor/Gateway image SHA256: `{manifest['pre_fix']['firmware_images_sha256']}`.",
        "",
        "## Historical 13-Frame Pattern",
        "",
        "PR #3 的 141 个 Sensor DATA TX 中，Gateway 在注入前记录 128 帧（100 个 RX、28 个计划 DROP）；另有 13 帧无 RX/DROP 记录。历史日志中 13/13 都紧随前一帧的 `DROP,DATA`，Sensor 未收到 ACK。旧日志没有 AUX edge、UART buffer 或每字节 parser 时间戳，因此它本身不能证明这些帧是 RF 丢失。",
        "",
        "## Diagnostic Runner Incidents",
        "",
        "A/B/C 诊断开始前遇到的 Gateway RESET 控制路径竞争、100 Hz FreeRTOS 下 1 ms delay 被量化为 0 tick，以及 smoke runner 把 boot `E220_READY` 当作 STATUS 响应，均已修复；这些尝试未产生 A/B/C 无线数据。另有一次 v2 smoke 已启动 DATA run，但 END 同批次中的 `UART_DIAG` 被读入内存后，host runner 又等待第二条 `UART_DIAG` 并超时；该次没有保存 raw/run manifest，按未验证 runner 中断处理并重跑。上述均不按 missing frame 计数，见 `metrics/runner_incidents.json`。",
        "",
        "## Test A/B/C",
        "",
        "| Test | Frames | Physical DATA missing | DATA drops | ACK timeouts | Parser errors | Status |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for key in ("A", "B", "C"):
        result = test_lookup.get(key, {})
        lines.append(f"| {key} | {result.get('frames_requested', 0)} | {result.get('uncontrolled_physical_data_missing', 'n/a')} | "
                     f"{result.get('observed_data_drops', 'n/a')} | {result.get('sensor_ack_timeouts', 'n/a')} | "
                     f"{result.get('parser_error_total', 'n/a')} | {result.get('status', 'not run')} |")
    lines.extend(["", "## Conditional Missing Probabilities", ""])
    for key in ("A", "B", "C"):
        result = test_lookup.get(key, {})
        lines.append(f"### Test {key}")
        lines.append("")
        for metric, label in (("p_missing_given_previous_ack_success", "previous ACK success"),
                              ("p_missing_given_previous_ack_suppressed", "previous ACK suppressed"),
                              ("p_missing_given_previous_data_drop", "previous DATA drop")):
            value = result.get(metric, {})
            lines.append(f"- P(missing | {label}): {value.get('missing', 0)}/{value.get('eligible', 0)} = {value.get('probability')}")
        if result.get("missing_frame_windows"):
            lines.append(f"- Missing windows: `{json.dumps(result['missing_frame_windows'], ensure_ascii=False)}`")
        else:
            lines.append("- No uncontrolled missing DATA frame was observed.")
        lines.append(f"- Gateway AUX edges/low intervals: `{json.dumps(result.get('gateway_aux', {}), ensure_ascii=False)}`")
        lines.append(f"- Sensor AUX low interval summary: `{json.dumps(result.get('sensor_aux', {}), ensure_ascii=False)}`")
        lines.append(f"- Sensor UART-TX-done to AUX-high readiness (ms): mean={result.get('tx_uart_done_to_aux_ready_ms_mean')}, "
                     f"median={result.get('tx_uart_done_to_aux_ready_ms_median')}, max={result.get('tx_uart_done_to_aux_ready_ms_max')}")
        lines.append("")
    lines.extend(["## Timing Sweep", ""])
    if sweep:
        lines.append("| Test | ACK timeout ms | RX poll timeout ms | Sent | Received | Missing | Missing after previous no-ACK/drop | Status |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
        for row in sweep:
            lines.append(f"| {row['test']} | {row['ack_timeout_ms']} | {row['rx_poll_timeout_ms']} | "
                         f"{row['sensor_data_tx_completed']} | {row['gateway_crc_valid_data_frames']} | "
                         f"{row['uncontrolled_physical_data_missing']} | "
                         f"{len(row['missing_after_previous_ack_suppressed']) + len(row['missing_after_previous_data_drop'])} | {row['status']} |")
    else:
        lines.append("未运行：Test B/C 未出现需要复现的 uncontrolled missing DATA。")
    lines.extend(["", "## Hardware Validation v2 Smoke Gate", ""])
    smoke = manifest.get("hardware_validation_v2_smoke") or {}
    smoke_gate = manifest.get("hardware_validation_v2_gate", "NOT_RUN")
    if smoke_gate == "PASS":
        lines.append(f"PASS：{smoke.get('completed', 0)}/{smoke.get('planned', 12)} 组 smoke run 已完成，且没有失败 run。Stage 1 未自动启动，需等待用户确认。")
    elif smoke_gate == "FAIL":
        detail = manifest.get("hardware_validation_v2_smoke_error", "one or more smoke runs failed validation")
        lines.append(f"FAIL：v2 smoke 未通过；诊断 runner 信息：`{detail}`。不得进入 Stage 1。")
    else:
        lines.append("未运行：A/B/C 诊断未通过，因此 v2 smoke 被门禁阻止。不得进入 Stage 1。")
    lines.extend(["", "## Root-Cause Assessment", ""])
    missing = sum(int(test_lookup.get(key, {}).get("uncontrolled_physical_data_missing", 0)) for key in ("A", "B", "C"))
    partial_timeouts = sum(int(test_lookup.get(key, {}).get("gateway_uart_diagnostics", {}).get("partial_header_timeouts", 0)) +
                           int(test_lookup.get(key, {}).get("gateway_uart_diagnostics", {}).get("partial_body_timeouts", 0))
                           for key in ("A", "B", "C"))
    if missing == 0:
        lines.append("修复后的持续流 parser 在 A/B/C 未观察到 uncontrolled missing DATA。原 13 个历史缺失全部跟随 no-ACK/DROP；这一模式与旧 parser 的跨调用 partial-header/body 状态丢失风险一致，但修复后的复测不能单独证明每个历史缺失帧的唯一根因。")
        if partial_timeouts:
            lines.append(f"诊断期间 Gateway 记录 partial-frame timeout {partial_timeouts} 次；parser state 保留并继续接收。")
        else:
            lines.append("本轮没有出现 partial-frame timeout；UART buffer、AUX edge 和帧时序记录保存在 raw 日志，可用于排除当前链路中的同类复现。")
        lines.append("因此当前结论为：parser 的确定性工程缺陷已修复；历史 13 帧的唯一根因仍需由原固件复现或本轮具体 partial-frame 恢复证据确认。")
    else:
        lines.append(f"修复后仍观察到 {missing} 个 uncontrolled missing DATA，根因尚未定位；不得进入正式矩阵。检查 raw 中 UART_BUFFER、AUX、D_RX_* 和 timing sweep 记录。")
    if sweep:
        lines.append("Timing sweep 仅改变诊断 ACK timeout 与 Gateway RX poll timeout，没有修改研究配置或插入额外 delay。")
    lines.extend(["", "## Files", "", "- Raw UART/AUX logs: `results/e220_receive_diagnostic/raw/`",
                  "- Machine metrics: `results/e220_receive_diagnostic/metrics/`",
                  "- Timing matrix: `results/e220_receive_diagnostic/metrics/timing_sweep.json`",
                  ""])
    (OUT / "diagnostic_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-flash", action="store_true", help="use the currently flashed diagnostic images")
    parser.add_argument("--skip-timing-sweep", action="store_true")
    parser.add_argument("--sweep-frames", type=int, default=20)
    args = parser.parse_args()
    previous_manifest_path = OUT / "diagnostic_manifest.json"
    try:
        previous_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous_manifest = {}
    for directory in (OUT / "raw", OUT / "metrics", OUT / "plots"):
        directory.mkdir(parents=True, exist_ok=True)
    for old in ((OUT / "diagnostic_manifest.json"), (OUT / "metrics/summary.json"),
                *(OUT / "raw").glob("build_*.log"), *(OUT / "raw").glob("flash_*.log"),
                *(OUT / "raw").glob("size_*.log"), (OUT / "firmware_size.json")):
        _preserve_existing(old)

    frozen = subprocess.run([str(ROOT / ".venv/bin/python"), "-c",
                             "from eventguard.hardware_validation import frozen_guard; frozen_guard()"],
                            cwd=ROOT, capture_output=True, text=True)
    if frozen.returncode:
        raise RuntimeError(f"Frozen algorithm guard failed before diagnostic: {frozen.stderr}")
    mapping, readers, provenance = _prepare_boards(args.skip_flash, previous_manifest)
    resume_allowed = all(previous_manifest.get(key) == provenance.get(key) for key in (
        "firmware_images_sha256", "sensor_firmware_source_sha256", "gateway_firmware_source_sha256",
        "e220_driver_source_sha256", "stream_parser_source_sha256", "config_sha256",
        "algorithm_spec_v1_sha256", "serial_ports", "sensor_mac", "gateway_mac"))
    if args.skip_flash and previous_manifest.get("firmware_images_sha256") != provenance["firmware_images_sha256"]:
        raise RuntimeError("--skip-flash firmware hashes do not match the previous diagnostic manifest")
    previous = json.loads((ROOT / "results/hardware_validation_v1/hardware_manifest.json").read_text(encoding="utf-8"))
    old_commit = previous["git_commit"]
    provenance["pre_fix"] = {
        "git_commit": old_commit,
        "sensor_firmware_source_sha256": previous["sensor_firmware_source_sha256"],
        "gateway_firmware_source_sha256": previous["gateway_firmware_source_sha256"],
        "e220_driver_source_sha256": sha_at_commit(old_commit, "firmware/common/e220.c"),
        "firmware_images_sha256": previous["firmware_images_sha256"],
    }
    manifest = {**provenance, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "purpose": "engineering_only_e220_receive_diagnostic",
                "tests": {"A": "normal RX+ACK", "B": "ACK suppressed for seq % 5 == 0",
                          "C": "20% deterministic RANDOM_COPY DATA drop, seed 31"},
                "test_frame_count": FRAMES, "frame_period_ms": PERIOD_MS,
                "default_ack_timeout_ms": DEFAULT_ACK_TIMEOUT_MS,
                "default_gateway_rx_poll_timeout_ms": DEFAULT_POLL_TIMEOUT_MS,
                "source_commit_state": "committed source required for reproducible firmware provenance"}
    if resume_allowed:
        manifest["resumed_test_execution_commit"] = previous_manifest.get("git_commit")
    atomic_json(OUT / "diagnostic_manifest.json", manifest)
    try:
        tests = []
        for test, rate in (("A", 0.0), ("B", 0.0), ("C", 0.20)):
            result = _completed_test(test, FRAMES, rate, DEFAULT_ACK_TIMEOUT_MS,
                                     DEFAULT_POLL_TIMEOUT_MS, "", resume_allowed,
                                     provenance["firmware_images_sha256"])
            if result:
                print(f"RESUME TEST {test}: validated raw hashes and firmware SHA256", flush=True)
            else:
                result = _run_test(readers["sensor"], readers["gateway"], test, FRAMES, rate,
                                   DEFAULT_ACK_TIMEOUT_MS, DEFAULT_POLL_TIMEOUT_MS, "",
                                   provenance["firmware_images_sha256"])
            tests.append(result)
            print(f"TEST {test}: {result['status']} missing={result['uncontrolled_physical_data_missing']} "
                  f"drops={result['observed_data_drops']} ack_timeout={result['sensor_ack_timeouts']}", flush=True)
        should_sweep = (not args.skip_timing_sweep and
                        any(item["uncontrolled_physical_data_missing"] for item in tests if item["test"] in ("B", "C")))
        sweep: list[dict] = []
        if should_sweep:
            for test, rate in (("B", 0.0), ("C", 0.20)):
                for ack_timeout in SWEEP_ACK_TIMEOUTS:
                    for poll_timeout in SWEEP_POLL_TIMEOUTS:
                        suffix = f"_sweep{args.sweep_frames}"
                        result = _completed_test(test, args.sweep_frames, rate, ack_timeout,
                                                 poll_timeout, suffix, resume_allowed,
                                                 provenance["firmware_images_sha256"])
                        if result is None:
                            result = _run_test(readers["sensor"], readers["gateway"], test,
                                               args.sweep_frames, rate, ack_timeout, poll_timeout,
                                               suffix=suffix,
                                               firmware_images_sha256=provenance["firmware_images_sha256"])
                        sweep.append(result)
                        print(f"SWEEP {test} ack={ack_timeout} poll={poll_timeout}: "
                              f"missing={result['uncontrolled_physical_data_missing']}", flush=True)
        smoke_result = None
        smoke_analysis = None
        smoke_error = None
        smoke_gate = "NOT_RUN_DIAGNOSTIC_FAIL"
        if all(item["status"] == "pass" for item in tests) and all(item["status"] == "pass" for item in sweep):
            try:
                from eventguard.hardware_validation import run as run_hardware_validation
                from eventguard.hardware_analysis import analyze
                v2_dir = ROOT / "results/hardware_validation_v2"
                smoke_result = run_hardware_validation("smoke", skip_flash=True, output_dir=v2_dir)
                smoke_analysis = analyze("smoke", output_dir=v2_dir)
                v2_manifest_path = v2_dir / "hardware_manifest.json"
                v2_manifest = json.loads(v2_manifest_path.read_text(encoding="utf-8"))
                v2_manifest["receive_path_diagnostic"] = {
                    "fix_commit": provenance["git_commit"],
                    "diagnostic_manifest": "../e220_receive_diagnostic/diagnostic_manifest.json",
                    "diagnostic_report": "../e220_receive_diagnostic/diagnostic_report.md",
                    "firmware_images_sha256": provenance["firmware_images_sha256"],
                    "pre_fix_firmware": provenance["pre_fix"],
                    "root_cause": ("The legacy parser had a confirmed partial-frame loss defect: parser state was local to one receive call and a short body read discarded accumulated bytes. The historical 13 missing frames all followed a no-ACK/DATA-drop path, consistent with that failure mode; original per-byte/AUX logs were not captured, so attribution of each historical frame is not conclusive."),
                    "diagnostic_evidence": {item["test"]: {
                        "status": item["status"],
                        "uncontrolled_physical_data_missing": item["uncontrolled_physical_data_missing"],
                        "parser_error_total": item["parser_error_total"],
                        "partial_header_timeouts": item["gateway_uart_diagnostics"].get("partial_header_timeouts", 0),
                        "partial_body_timeouts": item["gateway_uart_diagnostics"].get("partial_body_timeouts", 0),
                    } for item in tests},
                    "timing_sweep_conditions": len(sweep),
                }
                atomic_json(v2_manifest_path, v2_manifest)
                report_path = v2_dir / "hardware_report.md"
                with report_path.open("a", encoding="utf-8") as report:
                    report.write("\n## E220 Receive-Path Fix\n\n")
                    report.write(f"Diagnostic gate: {'PASS' if smoke_analysis['complete'] else 'FAIL'} ({smoke_analysis['completed']}/12 smoke runs). ")
                    report.write(v2_manifest["receive_path_diagnostic"]["root_cause"] + "\n")
                smoke_gate = "PASS" if smoke_analysis["complete"] else "FAIL"
                manifest["hardware_validation_v2_smoke"] = smoke_result
                manifest["hardware_validation_v2_gate"] = smoke_gate
            except Exception as exc:
                smoke_error = f"{type(exc).__name__}: {exc}"
                smoke_gate = "FAIL"
                manifest["hardware_validation_v2_gate"] = "FAIL"
                manifest["hardware_validation_v2_smoke_error"] = smoke_error
                print(f"HARDWARE VALIDATION V2 SMOKE FAILED: {smoke_error}", flush=True)
        atomic_json(OUT / "metrics" / "summary.json", {"tests": tests, "timing_sweep": sweep})
        timing_sweep_status = ("completed" if sweep else
                               "disabled_by_cli" if args.skip_timing_sweep else
                               "not_triggered_no_uncontrolled_missing_in_B_or_C")
        atomic_json(OUT / "metrics" / "timing_sweep.json", {
            "status": timing_sweep_status,
            "run_count": len(sweep),
            "reason": (None if sweep else
                       "--skip-timing-sweep was set" if args.skip_timing_sweep else
                       "Tests B and C observed zero uncontrolled physical DATA missing."),
            "conditions": sweep,
        })
        atomic_json(OUT / "metrics" / "runner_incidents.json", {
            "classification": "runner/control-path issues; not E220 DATA loss observations",
            "incidents": [
                {"commit": "0b5b50d4c5b65fe937602c94ffeb350d9987eff1",
                 "observation": "Gateway RESET did not respond while its high-priority receive task continuously reacquired the parser diagnostics mutex.",
                 "fix": "Pause the receive task around parser-state reset and diagnostics snapshot.",
                 "experiment_started": False},
                {"commit": "686f2bd924e7ee3be630707bf7b8287aae3a6f42",
                 "observation": "Gateway RX task hit Task WDT while paused because pdMS_TO_TICKS(1) was zero at CONFIG_FREERTOS_HZ=100.",
                 "fix": "Use one actual FreeRTOS tick while the receive task is paused.",
                 "experiment_started": False},
                {"observation": "v2 smoke status handshake stopped on a boot E220_READY line before seeing the explicit STATUS role response.",
                 "fix": "Require the numeric role/port STATUS response and E220_READY.",
                 "experiment_started": False},
                {"observation": "A v2 smoke DATA run reached Sensor END; its UART_DIAG had already been drained with END, then the host runner waited a second time and timed out before persisting raw/run files.",
                 "fix": "Use the UART_DIAG line already captured in the END drain instead of waiting for it twice; rerun the unverified smoke condition.",
                 "experiment_started": True, "raw_run_saved": False, "repeat_required": True},
            ],
        })
        manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["tests_status"] = {item["test"]: item["status"] for item in tests}
        manifest["timing_sweep_run_count"] = len(sweep)
        manifest["timing_sweep_status"] = timing_sweep_status
        manifest["hardware_validation_v2_gate"] = smoke_gate
        manifest["diagnostic_results_are_paper_treatments"] = False
        atomic_json(OUT / "diagnostic_manifest.json", manifest)
        _write_report(manifest, tests, sweep)
        diagnostics_pass = all(item["status"] == "pass" for item in tests) and all(item["status"] == "pass" for item in sweep)
        return 0 if diagnostics_pass and smoke_gate == "PASS" else 1
    finally:
        for reader in readers.values():
            reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
