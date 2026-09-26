"""Frozen-v1 E220 validation runner. This module changes no policy or fault logic."""
from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
import subprocess
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .faults import LossPlan
from .host import (ROOT, SERIAL_CAPTURE_VERSION, SerialLogReader, _config_command, _parse_metrics, _run_config,
                   _truth_code, _wait_for_ack, build_firmware, discover_boards,
                   flash_firmware, load_config, probe_mac)
from .model import Importance, LossModel, Strategy
from .simulator import budget_allocation, run_reference
from .strategy import LinkQualityEstimator, choose_redundancy
from .trace import generate_trace, trace_fingerprint

OUT = ROOT / "results/hardware_validation_v1"
FROZEN_STAGE1_FIRMWARE = ROOT / "artifacts/final_stage1_firmware"
FROZEN_COMMIT = "ccf9ca5da651e2bb93229896c7d0b7a7b7b74bab"
SMOKE = ("FIXED_2", "IMPORTANCE_ONLY", "EVENTGUARD")
MAIN = ("FIXED_2", "IMPORTANCE_ONLY", "EVENTGUARD", "UNIFORM_BUDGET", "RANDOM_BUDGET")
SEEDS = tuple(range(31, 41))
ORDER_SEED = 4170411
STAGE1_FIRMWARE_SHA256 = {
    "sensor": "b3cecd078351cc6fe659b0087db05a67a4ca35ab610125a0f0381887281221b4",
    "gateway": "6f69dffed65e2636b371401db45a483f951189e029b538c2f587d331428936ac",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frozen_main_is_additive_extension() -> bool:
    """Allow diagnostic-only insertions while proving frozen main.c lines remain verbatim."""
    baseline = subprocess.run(["git", "show", f"{FROZEN_COMMIT}:firmware/main/main.c"],
                              cwd=ROOT, text=True, capture_output=True)
    if baseline.returncode:
        return False
    original_lines = baseline.stdout.splitlines()
    current_lines = (ROOT / "firmware/main/main.c").read_text(encoding="utf-8").splitlines()
    cursor = 0
    for line in original_lines:
        while cursor < len(current_lines) and current_lines[cursor] != line:
            cursor += 1
        if cursor == len(current_lines):
            return False
        cursor += 1
    current_source = "\n".join(current_lines)
    return all(marker in current_source for marker in (
        "CONFIG_EG_DIAGNOSTIC_MODE", "E220_RX_DIAGNOSTIC", "eg_e220_get_diagnostics"))


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def frozen_guard() -> dict:
    if subprocess.run(["git", "merge-base", "--is-ancestor", FROZEN_COMMIT, "HEAD"], cwd=ROOT).returncode:
        raise RuntimeError("Frozen PR #2 is not an ancestor of HEAD")
    frozen = json.loads((ROOT / "results/experiment_manifest.json").read_text())
    expected = frozen["algorithm_sha256"]
    additive_main = False
    mismatches = []
    for name, digest in expected.items():
        if sha(ROOT / name) == digest:
            continue
        if name == "firmware/main/main.c" and _frozen_main_is_additive_extension():
            additive_main = True
            continue
        mismatches.append(name)
    if mismatches:
        raise RuntimeError(f"Frozen policy, trace, or loss code changed: {mismatches}")
    return {"git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
            "algorithm_version": frozen["algorithm_version"],
            "algorithm_spec_v1_sha256": sha(ROOT / "docs/algorithm_spec_v1.md"),
            "config_sha256": sha(ROOT / "configs/default.json"),
            "frozen_core_sha256": {name: sha(ROOT / name) for name in expected},
            "serial_console_capture_version": SERIAL_CAPTURE_VERSION,
            "firmware_main_additive_diagnostic_extension": additive_main,
            "sensor_firmware_source_sha256": sha(ROOT / "firmware/main/main.c"),
            "gateway_firmware_source_sha256": sha(ROOT / "firmware/main/main.c")}


def plan(stage: str) -> list[tuple[str, float, int, str, int]]:
    """Interleave strategies within each model/rate/seed condition."""
    if stage == "smoke":
        conditions = [("RANDOM_COPY", rate, seed) for seed in (31, 32) for rate in (0.0, .20)]
        strategies = SMOKE
    elif stage == "stage1":
        conditions = [(model, rate, seed) for seed in SEEDS
                      for model in ("RANDOM_COPY", "BURST_SAMPLE") for rate in (.20, .30)]
        strategies = ("EVENTGUARD", "IMPORTANCE_ONLY", "UNIFORM_BUDGET", "RANDOM_BUDGET")
    elif stage == "full":
        conditions = [(model, rate, seed) for seed in SEEDS
                      for model in ("RANDOM_COPY", "BURST_SAMPLE") for rate in (0.0, .10, .20, .30)]
        strategies = MAIN
    else:
        raise ValueError(stage)
    rng = random.Random(ORDER_SEED)
    result = []
    for model, rate, seed in conditions:
        if stage == "stage1":
            condition_order = list(strategies)
            rng.shuffle(condition_order)
        else:
            others = [s for s in strategies if s != "EVENTGUARD"]
            rng.shuffle(others)
            condition_order = ["EVENTGUARD", *others]
        for strategy in condition_order:
            result.append((model, rate, seed, strategy, len(result) + 1))
    return result


def _precompute_stage1_budgets(cfg: dict) -> tuple[dict[tuple[str, float, int], int], list[dict]]:
    """Freeze EventGuard reference budgets before any hardware run starts."""
    budgets: dict[tuple[str, float, int], int] = {}
    rows = []
    for seed in SEEDS:
        for model in ("RANDOM_COPY", "BURST_SAMPLE"):
            for rate in (.20, .30):
                samples = generate_trace(seed, 6)
                config = _run_config(cfg, "EVENTGUARD", rate, model, seed)
                reference = run_reference(samples, config, cfg["uart_baud"])
                budget = int(reference["metrics"]["physical_data_transmissions"])
                key = (model, rate, seed)
                budgets[key] = budget
                rows.append({
                    "loss_model": model,
                    "loss_rate": rate,
                    "seed": seed,
                    "trace_sha256": trace_fingerprint(samples),
                    "loss_calendar_sha256": calendar_hash(config, len(samples)),
                    "eventguard_reference_data_budget": budget,
                })
    if len(rows) != 40:
        raise RuntimeError(f"expected 40 Stage 1 reference budgets, got {len(rows)}")
    return budgets, rows


def _verify_stage1_firmware(previous_study: dict) -> tuple[dict[str, Path], dict]:
    """Require the exact v2 smoke pair recovered from the boards; never build or flash."""
    if previous_study.get("firmware_images_sha256") != STAGE1_FIRMWARE_SHA256:
        raise RuntimeError("Stage 1 firmware hash gate: v2 smoke manifest does not match the frozen pair")
    manifest_path = FROZEN_STAGE1_FIRMWARE / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Stage 1 firmware provenance manifest is missing: {manifest_path}")
    frozen = json.loads(manifest_path.read_text(encoding="utf-8"))
    if frozen.get("artifact_status") != "FROZEN_STAGE1_FIRMWARE" or not frozen.get("recovered_from_device"):
        raise RuntimeError("Stage 1 firmware manifest does not attest device recovery")
    if frozen.get("algorithm_spec_sha256") != sha(ROOT / "docs/algorithm_spec_v1.md") or \
            frozen.get("config_sha256") != sha(ROOT / "configs/default.json"):
        raise RuntimeError("Stage 1 firmware manifest algorithm/config hash mismatch")
    if frozen.get("smoke_result", {}).get("gate_status") != "PASS" or \
            frozen.get("smoke_result", {}).get("passed_runs") != 12:
        raise RuntimeError("Stage 1 firmware pair lacks the exact 12/12 smoke attestation")
    firmware_source_commit = frozen.get("firmware_build_commit_attested_by_smoke_manifest")
    if not firmware_source_commit or subprocess.run(
            ["git", "diff", "--quiet", firmware_source_commit, "--", "firmware"], cwd=ROOT).returncode:
        raise RuntimeError("Firmware source differs from the v2 smoke build revision; Stage 1 requires a new smoke")
    untracked_firmware = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "firmware"], cwd=ROOT, text=True).strip()
    if untracked_firmware:
        raise RuntimeError(f"Untracked firmware source appeared after smoke: {untracked_firmware}")
    device_map_path = ROOT / frozen.get("device_partition_map", "")
    if not device_map_path.is_file():
        raise RuntimeError("Stage 1 device partition map is missing")
    device_map = json.loads(device_map_path.read_text(encoding="utf-8"))
    paths = {}
    for role, expected in STAGE1_FIRMWARE_SHA256.items():
        image = FROZEN_STAGE1_FIRMWARE / f"{role}_eventguard.bin"
        entry = frozen.get("firmware_images", {}).get(role, {})
        board = device_map.get("devices", {}).get(role, {})
        if not image.is_file() or sha(image) != expected:
            raise RuntimeError(f"Stage 1 {role} firmware binary hash mismatch")
        if entry.get("sha256") != expected or entry.get("device_extracted_sha256") != expected or \
                not entry.get("exact_match"):
            raise RuntimeError(f"Stage 1 {role} device-recovery manifest hash mismatch")
        if board.get("extracted_app_sha256") != expected or not board.get("esp_image_checksum_valid") or \
                not board.get("embedded_esp_sha256_digest_valid"):
            raise RuntimeError(f"Stage 1 {role} partition dump/image-parser provenance is invalid")
        paths[role] = image
    return paths, {"firmware_manifest": frozen, "device_partition_map": device_map}


def calendar_hash(config, count: int) -> str:
    loss = LossPlan(config.seed, config.loss_rate, config.loss_model, count, config.burst_length)
    packed = bytes(loss._data) + bytes(loss._ack)
    return hashlib.sha256(packed).hexdigest()


def _lines(reader: SerialLogReader, token: str, timeout: float = 8.0):
    return _wait_for_ack(reader, token, timeout)


def _ensure_uart_diag(reader: SerialLogReader, captured: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Do not wait for a diagnostic line that the END drain already captured."""
    if any(line.startswith("UART_DIAG,") for _, line in captured):
        return captured
    return captured + _lines(reader, "UART_DIAG,", 3)


def _capture_delta(before: dict, after: dict) -> dict:
    counters = ("bytes_read", "lines_read", "read_calls", "reader_errors")
    return {**{name: after.get(name, 0) - before.get(name, 0) for name in counters},
            "max_read_chunk_bytes": after.get("max_read_chunk_bytes", 0),
            "pending_bytes_at_end": after.get("pending_bytes", 0)}


def _validate_console_capture(sensor_lines: list[tuple[float, str]],
                              gateway_lines: list[tuple[float, str]],
                              sensor_end: list[str], gateway_end: list[str],
                              uart_diagnostics: dict) -> list[str]:
    """Cross-check console event lines against firmware-owned END/UART counters."""
    errors = []
    sensor_text = [line for _, line in sensor_lines]
    gateway_text = [line for _, line in gateway_lines]
    if len(sensor_end) == 1:
        totals = [int(value) for value in sensor_end[0].split(",")[1:]]
        tx_lines = sum(line.startswith("TX,") for line in sensor_text)
        ack_lines = sum(line.startswith("ACK,") for line in sensor_text)
        accepted_ack_lines = sum(line.startswith("ACK,") and line.endswith(",OK") for line in sensor_text)
        timeout_lines = sum(line.startswith("TIMEOUT,") for line in sensor_text)
        evt_lines = sum(line.startswith("EVT,") for line in sensor_text)
        sample_lines = sum(line.startswith("SAMPLE,") for line in sensor_text)
        if len(totals) >= 3:
            if tx_lines != totals[1]:
                errors.append(f"Sensor console TX capture {tx_lines} != firmware END tx_count {totals[1]}")
            if accepted_ack_lines != totals[2]:
                errors.append(f"Sensor console ACK,...,OK capture {accepted_ack_lines} != firmware END accepted_ack {totals[2]}")
            if evt_lines != totals[0] or sample_lines != totals[0]:
                errors.append(f"Sensor console sample capture EVT/SAMPLE={evt_lines}/{sample_lines} != END completed {totals[0]}")
            if ack_lines + timeout_lines != tx_lines:
                errors.append(f"Sensor console ACK/TIMEOUT outcomes {ack_lines + timeout_lines} != captured TX {tx_lines}")
    if len(gateway_end) == 1:
        totals = [int(value) for value in gateway_end[0].split(",")[1:]]
        rx_lines = sum(line.startswith("RX,") for line in gateway_text)
        drop_lines = sum(line.startswith("DROP,DATA,") for line in gateway_text)
        ack_tx_lines = sum(line.startswith("ACK_TX,") for line in gateway_text)
        frame_lines = sum(line.startswith("D_RX_FRAME_COMPLETE,") for line in gateway_text)
        if len(totals) >= 7:
            for label, observed, expected in (("RX", rx_lines, totals[1]),
                                               ("DROP,DATA", drop_lines, totals[6]),
                                               ("ACK_TX", ack_tx_lines, totals[4])):
                if observed != expected:
                    errors.append(f"Gateway console {label} capture {observed} != firmware END counter {expected}")
        completed = uart_diagnostics.get("gateway", {}).get("frames_completed")
        if completed is not None and frame_lines != completed:
            errors.append(f"Gateway console D_RX_FRAME_COMPLETE capture {frame_lines} != UART_DIAG frames_completed {completed}")
    if len(sensor_end) == 1:
        completed = uart_diagnostics.get("sensor", {}).get("frames_completed")
        ack_received = sum(line.startswith("D_ACK_RECEIVED,") for line in sensor_text)
        if completed is not None and ack_received != completed:
            errors.append(f"Sensor console D_ACK_RECEIVED capture {ack_received} != UART_DIAG frames_completed {completed}")
    return errors


def _status(sensor: SerialLogReader, gateway: SerialLogReader) -> dict:
    result = {}
    for name, reader, role in (("sensor", sensor, "SENSOR"), ("gateway", gateway, "GATEWAY")):
        reader.drain()
        reader.write("STATUS")
        values = []
        role_confirmed = False
        ready = False
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            batch = reader.drain()
            values.extend(line for _, line in batch)
            if any(line.startswith(("ERR,", "E220_ERROR,")) for _, line in batch):
                raise RuntimeError(f"{name} STATUS failed: {values[-20:]}")
            role_confirmed |= any(_is_status_role_line(line, role) for _, line in batch)
            ready |= any(line == "E220_READY" for _, line in batch)
            if role_confirmed and ready:
                break
            time.sleep(.02)
        if not role_confirmed or not ready:
            raise RuntimeError(f"{name} role/readiness not confirmed: {values[-20:]}")
        result[name] = values
    return result


def _is_status_role_line(line: str, role: str) -> bool:
    fields = line.split(",")
    if len(fields) != 5 or fields[0] != "ROLE" or fields[1] != role:
        return False
    try:
        int(fields[2])
        int(fields[3])
        int(fields[4])
        return True
    except ValueError:
        return False


def test_run_state_isolation(sensor: SerialLogReader, gateway: SerialLogReader, cfg: dict) -> dict:
    """Exercise the on-device reset contract before every run."""
    sensor.drain(); gateway.drain()
    sensor.write("RESET"); gateway.write("RESET")
    sensor_reset = [line for _, line in _lines(sensor, "RESET,OK")]
    gateway_reset = [line for _, line in _lines(gateway, "RESET,OK")]
    status = _status(sensor, gateway)
    if not sensor_reset or not gateway_reset:
        raise RuntimeError("RESET was not acknowledged by both boards")
    return {"sensor_reset": sensor_reset, "gateway_reset": gateway_reset,
            "sensor_ready": status["sensor"], "gateway_ready": status["gateway"],
            "reset_contract": "Sensor RESET clears sequence/classifier/link/counters and E220 parser diagnostics; Gateway RESET clears duplicate table/sequence/counters/armed state and E220 parser diagnostics"}


def _gateway_end_counter_issues(gateway_totals: list[int], expected_samples: int,
                                metrics: dict) -> list[str]:
    expected = [expected_samples, metrics["physical_data_received"], metrics["delivered_packets"],
                metrics["duplicate_packets"], metrics["ack_count"], metrics["crc_errors"],
                metrics["data_injected_drops"]]
    issues = []
    if gateway_totals[:7] != expected:
        issues.append(f"gateway END counters disagree: {gateway_totals}; expected prefix {expected}")
    before_injection = metrics["physical_data_before_injection"]
    post_injection = metrics["physical_data_received"]
    injected_drops = metrics["data_injected_drops"]
    if before_injection != post_injection + injected_drops:
        issues.append("host DATA accounting before/after injection is inconsistent")
    return issues


def _stage1_physical_noise_gate(record: dict, previous_records: list[dict]) -> dict:
    """Allow isolated physical losses while stopping bursty or repeatable anomalies."""
    current_order = record.get("execution_order")
    current_run_id = record.get("run_id")
    earlier_records = []
    if current_order is not None:
        for prior in previous_records:
            prior_order = prior.get("execution_order")
            if (prior.get("run_id") != current_run_id and prior_order is not None
                    and int(prior_order) < int(current_order)):
                earlier_records.append(prior)
    anomalies = record.get("physical_anomalies", [])
    data = [a for a in anomalies if a.get("kind") == "uncontrolled_physical_data_missing"]
    ack = [a for a in anomalies if a.get("kind") == "uncontrolled_physical_ack_missing"]
    reasons = []
    if len(data) > 1:
        reasons.append(f"{len(data)} uncontrolled DATA losses in one run exceeds the 1/run allowance")
    if len(ack) > 1:
        reasons.append(f"{len(ack)} uncontrolled ACK losses in one run exceeds the 1/run allowance")
    by_sample: dict[int, int] = defaultdict(int)
    slots = []
    for anomaly in anomalies:
        sample_id, copy_index = anomaly.get("sample_id"), anomaly.get("copy_index")
        if sample_id is not None:
            by_sample[int(sample_id)] += 1
            slots.append(int(sample_id) * 3 + int(copy_index or 0))
    if any(count > 1 for count in by_sample.values()):
        reasons.append("multiple uncontrolled physical losses occurred within one logical sample")
    slots.sort()
    if any(right - left == 1 for left, right in zip(slots, slots[1:])):
        reasons.append("uncontrolled losses occurred on adjacent copy opportunities")

    condition_key = (record.get("loss_model"), record.get("loss_rate"), record.get("seed"))
    prior_keys = set()
    for prior in earlier_records:
        if prior.get("status") != "complete":
            continue
        prior_condition = (prior.get("loss_model"), prior.get("loss_rate"), prior.get("seed"))
        for anomaly in prior.get("physical_anomalies", []):
            prior_keys.add((*prior_condition, anomaly.get("kind"), anomaly.get("sample_id"),
                            anomaly.get("copy_index")))
    for anomaly in anomalies:
        key = (*condition_key, anomaly.get("kind"), anomaly.get("sample_id"), anomaly.get("copy_index"))
        if key in prior_keys:
            reasons.append("the same model/rate/seed/sample/copy physical anomaly repeated across strategies")
            break

    all_records = [r for r in earlier_records if r.get("status") == "complete"] + [record]
    total_data_missing = sum(sum(a.get("kind") == "uncontrolled_physical_data_missing"
                                 for a in r.get("physical_anomalies", [])) for r in all_records)
    total_data_tx = sum(r.get("metrics", {}).get("physical_data_transmissions", 0) for r in all_records)
    total_ack_missing = sum(sum(a.get("kind") == "uncontrolled_physical_ack_missing"
                                for a in r.get("physical_anomalies", [])) for r in all_records)
    total_ack_tx = sum(r.get("metrics", {}).get("physical_ack_frames", 0) for r in all_records)
    data_rate = total_data_missing / max(1, total_data_tx)
    ack_rate = total_ack_missing / max(1, total_ack_tx)
    if data_rate > 0.01:
        reasons.append(f"cumulative uncontrolled DATA loss rate {data_rate:.3%} exceeds 1%")
    if ack_rate > 0.01:
        reasons.append(f"cumulative uncontrolled ACK loss rate {ack_rate:.3%} exceeds 1%")
    return {"allowed_to_continue": not reasons, "data_losses_this_run": len(data),
            "ack_losses_this_run": len(ack), "cumulative_data_missing": total_data_missing,
            "cumulative_data_tx": total_data_tx, "cumulative_data_missing_rate": data_rate,
            "cumulative_ack_missing": total_ack_missing, "cumulative_ack_tx": total_ack_tx,
            "cumulative_ack_missing_rate": ack_rate, "stop_reasons": reasons,
            "policy_version": "stage1-physical-noise-v2"}


def _previous_stage1_records(output: Path) -> list[dict]:
    records = []
    for path in sorted((output / "runs" / "stage1").glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            raw_path = output / "raw" / "stage1" / f"{row.get('run_id', path.stem)}.json"
            if row.get("status") == "complete" and raw_path.is_file() and sha(raw_path) == row.get("raw_sha256"):
                records.append(row)
        except (OSError, ValueError, TypeError):
            continue
    return records


def _run_deadline_seconds(expected_data_copies: int, sample_count: int, ack_timeout_ms: int) -> tuple[float, dict]:
    """Conservative watchdog from the three 1 s send waits plus configured ACK wait."""
    components = {"expected_data_copies": int(expected_data_copies),
                  "per_copy_worst_case_seconds": ack_timeout_ms / 1000.0 + 3.0,
                  "per_sample_overhead_seconds": 0.5, "fixed_margin_seconds": 30.0,
                  "send_waits": "AUX before TX, UART TX drain, AUX after TX; 1000 ms each in frozen firmware"}
    seconds = max(90.0, expected_data_copies * components["per_copy_worst_case_seconds"] +
                  sample_count * components["per_sample_overhead_seconds"] + 30.0)
    return seconds, components


def _parse_samples(samples, sensor_lines, gateway_lines, config, reference) -> tuple[list[dict], list[dict], list[str], list[dict]]:
    sensor = [line for _, line in sensor_lines]
    gateway = [line for _, line in gateway_lines]
    by_kind: dict[str, dict[int, list[list[str]]]] = defaultdict(lambda: defaultdict(list))
    for line in sensor:
        parts = line.split(",")
        try:
            if parts[0] in ("EVT", "SAMPLE", "TX", "TX_BEGIN"):
                by_kind[parts[0]][int(parts[1])].append(parts)
            elif parts[0] == "ACK":
                by_kind["ACK"][int(parts[2])].append(parts)
            elif parts[0] == "TIMEOUT":
                by_kind["TIMEOUT"][int(parts[1])].append(parts)
        except (IndexError, ValueError):
            pass
    for line in gateway:
        parts = line.split(",")
        try:
            if parts[0] == "RX": by_kind["RX"][int(parts[2])].append(parts)
            elif parts[0] == "DROP" and parts[1] == "DATA": by_kind["DROP_DATA"][int(parts[3])].append(parts)
            elif parts[0] == "ACK_TX": by_kind["ACK_TX"][int(parts[2])].append(parts)
            elif parts[0] == "DELIVER": by_kind["DELIVER"][int(parts[1])].append(parts)
        except (IndexError, ValueError):
            pass
    issues, differences, records, physical_anomalies = [], [], [], []
    estimator = LinkQualityEstimator(config.link_window, config.link_degraded_threshold,
                                     config.link_bad_threshold, config.link_bad_fail_streak)
    loss = LossPlan(config.seed, config.loss_rate, config.loss_model, len(samples), config.burst_length)
    budget_mode = config.strategy in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET)
    allocation = (budget_allocation(config.strategy, len(samples), config.data_copy_budget,
                                    config.seed, config.max_redundancy) if budget_mode else None)
    for index, (sample, expected) in enumerate(zip(samples, reference["events"])):
        sid = sample.sample_id
        evts, summaries = by_kind["EVT"][sid], by_kind["SAMPLE"][sid]
        if len(evts) != 1 or len(summaries) != 1:
            issues.append(f"sample {sid}: EVT/SAMPLE log count {len(evts)}/{len(summaries)}")
            continue
        evt, summary = evts[0], summaries[0]
        try:
            importance, before, copies = summary[3], summary[5], int(summary[6])
            if evt[5] != before or int(evt[6]) != copies:
                issues.append(f"sample {sid}: EVT/SAMPLE disagreement")
            expected_importance = expected["importance"]
            if budget_mode:
                expected_copies = allocation[index]
            else:
                expected_copies = choose_redundancy(config.strategy, Importance[expected_importance],
                    estimator.state, config.fixed_redundancy, config.max_redundancy)
            policy_mismatch = importance != expected_importance or copies != expected_copies
            if before != estimator.state.name:
                issues.append(f"sample {sid}: link before {before} != replay {estimator.state.name}")
                policy_mismatch = True
            if policy_mismatch:
                differences.append({"sample_id": sid, "copy_index": "",
                    "importance_hardware": importance, "importance_simulation": expected_importance,
                    "copies_hardware": copies, "copies_simulation": expected_copies,
                    "link_hardware": before, "link_replay": estimator.state.name,
                    "link_after_hardware": "", "link_after_replay": "",
                    "delivered_hardware": bool(by_kind["DELIVER"][sid]),
                    "delivered_simulation": expected["delivered"],
                    "physical_outcome_differs": bool(by_kind["DELIVER"][sid]) != expected["delivered"]})
            tx = {int(p[3]): p for p in by_kind["TX"][sid]}
            rx = {int(p[3]): p for p in by_kind["RX"][sid]}
            drops = {int(p[4]): p for p in by_kind["DROP_DATA"][sid]}
            acks = {int(p[3]): p for p in by_kind["ACK"][sid]}
            ack_tx = {int(p[3]): p for p in by_kind["ACK_TX"][sid]}
            timeouts = {int(p[2]): p for p in by_kind["TIMEOUT"][sid] if len(p) > 2}
            copy_records = []
            if len(tx) != copies or set(tx) != set(range(copies)):
                issues.append(f"sample {sid}: DATA copy logs {sorted(tx)} != {list(range(copies))}")
            if any(int(p[2]) != sid for p in tx.values()):
                issues.append(f"sample {sid}: sequence did not reset to sample index")
            for index_set_name, index_set in (("RX", rx), ("DROP,DATA", drops),
                                              ("ACK_TX", ack_tx), ("ACK", acks), ("TIMEOUT", timeouts)):
                if any(copy >= copies for copy in index_set):
                    issues.append(f"sample {sid}: unexpected {index_set_name} copy index")
            for copy in range(copies):
                data_drop = loss.drops("DATA", sid, copy)
                rx_logged, drop_logged = copy in rx, copy in drops
                if rx_logged and drop_logged:
                    issues.append(f"sample {sid} copy {copy}: both RX and DROP,DATA logged")
                elif not rx_logged and not drop_logged and copy in tx:
                    physical_anomalies.append({"kind": "uncontrolled_physical_data_missing",
                        "sample_id": sid, "copy_index": copy, "planned_drop_opportunity": data_drop})
                elif drop_logged and not data_drop:
                    issues.append(f"sample {sid} copy {copy}: injected DATA calendar/log mismatch")
                elif rx_logged and data_drop:
                    issues.append(f"sample {sid} copy {copy}: planned DATA drop was not injected")
                if rx_logged and copy not in ack_tx:
                    issues.append(f"sample {sid} copy {copy}: Gateway RX has no ACK_TX")
                if not rx_logged and copy in ack_tx:
                    issues.append(f"sample {sid} copy {copy}: ACK_TX has no post-injection DATA RX")
                if copy in ack_tx and copy not in acks:
                    physical_anomalies.append({"kind": "uncontrolled_physical_ack_missing",
                        "sample_id": sid, "copy_index": copy,
                        "planned_drop_opportunity": loss.drops("ACK", sid, copy)})
                    if copy not in timeouts:
                        issues.append(f"sample {sid} copy {copy}: missing ACK has no ACK/TIMEOUT outcome")
                if copy in acks and copy not in ack_tx:
                    issues.append(f"sample {sid} copy {copy}: Sensor ACK has no Gateway ACK_TX")
                if copy in acks and (acks[copy][4] == "DROP") != loss.drops("ACK", sid, copy):
                    issues.append(f"sample {sid} copy {copy}: injected ACK calendar/log mismatch")
                if copy in acks and copy in timeouts:
                    issues.append(f"sample {sid} copy {copy}: ACK and TIMEOUT both logged")
                if copy not in ack_tx and copy not in acks and copy not in timeouts:
                    issues.append(f"sample {sid} copy {copy}: no ACK/TIMEOUT outcome logged")
                estimator.observe_copy(copy in acks and acks[copy][4] == "OK", copy == 0)
                copy_records.append({
                    "copy_index": copy,
                    "data_tx": copy in tx,
                    "planned_data_drop": loss.drops("DATA", sid, copy),
                    "gateway_pre_injection_receipt": copy in drops or copy in rx,
                    "post_injection_rx": copy in rx,
                    "ack_tx": copy in ack_tx,
                    "physical_ack_received": copy in acks,
                    "planned_ack_drop": loss.drops("ACK", sid, copy),
                    "accepted_ack": copy in acks and acks[copy][4] == "OK",
                    "ack_timeout": copy in timeouts,
                })
            actual_delivered = bool(by_kind["DELIVER"][sid])
            if len(by_kind["DELIVER"][sid]) > 1 or actual_delivered != bool(rx):
                issues.append(f"sample {sid}: Gateway DELIVER log inconsistent with observed DATA RX")
            records.append({"sample_id": sid, "ground_truth": sample.truth.value,
                            "predicted_importance": importance,
                            "importance_score_hardware": float(summary[4]),
                            "importance_score_simulation": expected["importance_score"],
                            "importance": importance, "selected_copies": copies,
                            "first_copy_sent": 0 in tx, "first_copy_data_delivered": 0 in rx,
                            "first_copy_ack_physical_received": 0 in acks,
                            "first_copy_ack_accepted": 0 in acks and acks[0][4] == "OK",
                            "link_state_before": before, "link_state_after": estimator.state.name,
                            "link_state_before_simulation": expected["link_state"],
                            "link_state_after_simulation": expected["next_link_state"],
                            "link_state_after_observed_replay": estimator.state.name,
                            "delivered": actual_delivered, "physical_data_copies": len(tx),
                            "delivered_simulation": expected["delivered"],
                            "physical_outcome_differs": actual_delivered != expected["delivered"],
                            "copy_records": copy_records,
                            "physical_ack_frames": len(ack_tx)})
        except (IndexError, ValueError, KeyError) as exc:
            issues.append(f"sample {sid}: parser failure {exc}")
    if len(records) != len(samples):
        issues.append(f"sample record count {len(records)} != {len(samples)}")
    for role, lines in (("sensor", sensor), ("gateway", gateway)):
        for line in lines:
            if line.startswith("HOST_SERIAL_ERROR") or line.startswith("ERR,UART") or \
                    line.startswith("ERR,PACKET") or line.startswith("ERR,ACK_MISMATCH"):
                issues.append(f"{role}: {line}")
    return records, differences, issues, physical_anomalies


def _run_one(sensor, gateway, config, samples, mapping, provenance, order: int, stage: str,
             budget: int | None, output_dir: Path | None = None):
    output = output_dir or OUT
    is_budget_strategy = config.strategy.value in ("UNIFORM_BUDGET", "RANDOM_BUDGET")
    if is_budget_strategy and budget is None:
        raise RuntimeError(f"{config.strategy.value} requires a frozen EventGuard reference budget")
    if is_budget_strategy:
        config = replace(config, data_copy_budget=budget)
    run_id = f"{config.strategy.value.lower()}_{config.loss_model.value.lower()}_{int(config.loss_rate*100):02d}_seed{config.seed}"
    raw_path = output / "raw" / stage / f"{run_id}.json"
    manifest_path = output / "runs" / stage / f"{run_id}.json"
    trace_hash = trace_fingerprint(samples)
    calendar = calendar_hash(config, len(samples))
    fw_hashes = provenance["firmware_images_sha256"]
    expected_identity = {"run_id": run_id, "stage": stage, "trace_sha256": trace_hash,
                         "loss_calendar_sha256": calendar, "firmware_images_sha256": fw_hashes,
                         "reference_eventguard_data_copy_budget": budget,
                         "configured_data_copy_budget": config.data_copy_budget,
                         "execution_order": order}
    if raw_path.exists() and manifest_path.exists():
        try:
            saved = json.loads(manifest_path.read_text())
            raw = json.loads(raw_path.read_text())
            if saved.get("status") == "failed":
                raise RuntimeError(f"{run_id} already failed validation; preserve it until a documented engineering fix and new study version")
            if (all(saved.get(key) == value for key, value in expected_identity.items()) and
                    saved.get("status") == "complete" and raw.get("sensor") and raw.get("gateway") and
                    sha(raw_path) == saved.get("raw_sha256") and
                    saved.get("sensor_mac") == mapping["chip_macs"]["sensor"] and
                    saved.get("gateway_mac") == mapping["chip_macs"]["gateway"]):
                return saved
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if stage == "stage1" and (raw_path.exists() or manifest_path.exists()):
        raise RuntimeError(f"{run_id} already has an incomplete or mismatched Stage 1 attempt; preserve it and stop")
    # Preserve every failed or incomplete attempt before the same condition is rerun.
    if raw_path.exists() or manifest_path.exists():
        archive = output / "raw" / "attempts" / stage / run_id
        archive.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for source in (raw_path, manifest_path):
            if source.exists():
                shutil.copy2(source, archive / f"{stamp}_{source.parent.name}_{source.name}")
    reset = test_run_state_isolation(sensor, gateway, load_config())
    sensor.write(_config_command(config)); gateway.write(_config_command(config))
    configured_sensor = [line for _, line in _lines(sensor, "CONFIGURED")]
    configured_gateway = [line for _, line in _lines(gateway, "CONFIGURED")]
    gateway.write(f"TRACE_COUNT,{len(samples)}")
    _lines(gateway, "TRACE_COUNT")
    gateway.write("ARM")
    gateway_prelude = _lines(gateway, "ARMED")
    sensor.write(f"TRACE_BEGIN,{len(samples)}")
    _lines(sensor, "TRACE_BEGIN")
    for sample in samples:
        sensor.write(f"S,{sample.sample_id},{sample.timestamp_ms},{sample.temperature:.2f},{sample.humidity:.2f},{sample.light:.1f},{sample.soil_moisture:.1f},{_truth_code(sample.truth)}")
        time.sleep(.003)
    sensor.write("TRACE_END")
    trace_ready = [line for _, line in _lines(sensor, "TRACE_READY", 10)]
    sensor.drain(); gateway_prelude.extend(gateway.drain())
    capture_before = {"sensor": sensor.capture_snapshot(), "gateway": gateway.capture_snapshot()}
    start_utc = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    reference_for_deadline = run_reference(samples, config, load_config()["uart_baud"])
    expected_data_copies = int(reference_for_deadline["metrics"]["physical_data_transmissions"])
    ack_wait_s = config.ack_timeout_ms / 1000.0
    run_deadline_seconds, deadline_components = _run_deadline_seconds(
        expected_data_copies, len(samples), config.ack_timeout_ms)
    sensor_lines, gateway_lines = [], list(gateway_prelude)
    start_attempted = True
    atomic_json(manifest_path, {**expected_identity, **provenance, "run_id": run_id, "stage": stage,
                                "strategy": config.strategy.value, "loss_model": config.loss_model.value,
                                "loss_rate": config.loss_rate, "seed": config.seed,
                                "sensor_mac": mapping["chip_macs"]["sensor"],
                                "gateway_mac": mapping["chip_macs"]["gateway"],
                                "serial_ports": {"sensor": mapping["sensor_port"], "gateway": mapping["gateway_port"]},
                                "start_time_utc": start_utc, "start_host_monotonic": start, "status": "started",
                                "maximum_runtime_seconds": run_deadline_seconds,
                                "deadline_calculation": deadline_components,
                                "start_command_attempted": start_attempted})
    try:
        sensor.write("START")
        deadline = time.monotonic() + run_deadline_seconds
        pending_ack: tuple[int, int, float] | None = None
        status_probe_sent = False
        progress_diagnostics = []
        while time.monotonic() < deadline:
            sb, gb = sensor.drain(), gateway.drain()
            sensor_lines.extend(sb); gateway_lines.extend(gb)
            for _, line in sb:
                fields = line.split(",")
                if fields[0] == "D_ACK_WAIT_BEGIN" and len(fields) >= 3:
                    try: pending_ack = (int(fields[1]), int(fields[2]), time.monotonic())
                    except ValueError: pass
                elif fields[0] in ("D_ACK_RECEIVED", "D_ACK_TIMEOUT"):
                    pending_ack = None
            if any(line.startswith("END,") for _, line in sb):
                break
            if pending_ack and not status_probe_sent and time.monotonic() - pending_ack[2] > ack_wait_s + 5.0:
                sample_id, copy_index, wait_started = pending_ack
                sensor.write("STATUS")
                status_probe_sent = True
                progress_diagnostics.append({"kind": "ack_outcome_overdue_status_probe",
                    "sample_id": sample_id, "copy_index": copy_index,
                    "ack_wait_elapsed_seconds": time.monotonic() - wait_started,
                    "sent_at_monotonic": time.monotonic()})
            if status_probe_sent and sb:
                responses = [line for _, line in sb if line.startswith(("ROLE,SENSOR,", "E220_READY", "E220_ERROR,"))]
                if responses and progress_diagnostics:
                    progress_diagnostics[-1]["response_lines"] = responses
            if status_probe_sent and pending_ack and time.monotonic() - pending_ack[2] > ack_wait_s + 12.0:
                raise TimeoutError(f"{run_id}: no ACK/TIMEOUT progress for sample {pending_ack[0]} "
                                   f"copy {pending_ack[1]} after configured {config.ack_timeout_ms} ms timeout")
            time.sleep(.01)
        else:
            raise TimeoutError(f"{run_id}: firmware END timeout after {run_deadline_seconds:.1f}s watchdog")
        sensor_lines.extend(sensor.drain())
        sensor_lines = _ensure_uart_diag(sensor, sensor_lines)
        gateway.write("ENDRUN")
        gateway_lines.extend(_lines(gateway, "END,", 6))
        capture_after = {"sensor": sensor.capture_snapshot(), "gateway": gateway.capture_snapshot()}
        serial_capture = {role: _capture_delta(capture_before[role], capture_after[role])
                          for role in ("sensor", "gateway")}
    except BaseException as exc:
        sensor_lines.extend(sensor.drain())
        gateway_lines.extend(gateway.drain())
        failed_end = time.monotonic()
        partial_raw = {"run_id": run_id, "stage": stage,
                       "sensor": [{"host_monotonic": t, "line": line} for t, line in sensor_lines],
                       "gateway": [{"host_monotonic": t, "line": line} for t, line in gateway_lines],
                       "reset": reset, "configured_sensor": configured_sensor,
                       "configured_gateway": configured_gateway, "trace_ready": trace_ready,
                       "start_command_attempted": start_attempted, "failure": repr(exc),
                       "start_host_monotonic": start,
                       "maximum_runtime_seconds": run_deadline_seconds,
                       "deadline_calculation": deadline_components,
                       "progress_diagnostics": locals().get("progress_diagnostics", []),
                       "serial_capture": {role: _capture_delta(capture_before[role], reader.capture_snapshot())
                                          for role, reader in (("sensor", sensor), ("gateway", gateway))}}
        atomic_json(raw_path, partial_raw)
        failure = {**expected_identity, **provenance, "source": "REAL_E220_WITH_APPLICATION_LAYER_INJECTION",
                   "sensor_mac": mapping["chip_macs"]["sensor"], "gateway_mac": mapping["chip_macs"]["gateway"],
                   "serial_ports": {"sensor": mapping["sensor_port"], "gateway": mapping["gateway_port"]},
                   "strategy": config.strategy.value, "loss_model": config.loss_model.value,
                   "loss_rate": config.loss_rate, "seed": config.seed, "start_time_utc": start_utc,
                   "duration_s": failed_end-start, "sample_count": len(samples), "status": "failed",
                   "start_host_monotonic": start,
                   "maximum_runtime_seconds": run_deadline_seconds,
                   "deadline_calculation": deadline_components,
                   "progress_diagnostics": locals().get("progress_diagnostics", []),
                   "start_command_attempted": start_attempted,
                   "failure": {"type": type(exc).__name__, "message": str(exc)},
                   "issues": [f"run terminated after START attempt: {type(exc).__name__}: {exc}"],
                   "raw_sha256": sha(raw_path)}
        atomic_json(manifest_path, failure)
        raise
    end = time.monotonic()
    raw = {"run_id": run_id, "stage": stage, "sensor": [{"host_monotonic": t, "line": line} for t, line in sensor_lines],
           "gateway": [{"host_monotonic": t, "line": line} for t, line in gateway_lines],
           "reset": reset, "configured_sensor": configured_sensor, "configured_gateway": configured_gateway,
           "trace_ready": trace_ready, "start_host_monotonic": start,
           "maximum_runtime_seconds": run_deadline_seconds,
           "deadline_calculation": deadline_components,
           "progress_diagnostics": locals().get("progress_diagnostics", []),
           "serial_capture": serial_capture}
    atomic_json(raw_path, raw)
    reference = run_reference(samples, config, load_config()["uart_baud"])
    events, event_diffs, issues, physical_anomalies = _parse_samples(samples, sensor_lines, gateway_lines,
                                                                     config, reference)
    metrics = _parse_metrics(samples, events, sensor_lines, gateway_lines, start, end, config, trace_hash)
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
        sensor_totals = [int(x) for x in sensor_end[0].split(",")[1:]]
        gateway_totals = [int(x) for x in gateway_end[0].split(",")[1:]]
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
    issues.extend(f"serial console capture mismatch: {issue}" for issue in
                  _validate_console_capture(sensor_lines, gateway_lines, sensor_end, gateway_end, uart_diagnostics))
    observed_frames = max(1, metrics["physical_data_transmissions"] + metrics["ack_count"])
    if crc_failures >= 3 or crc_failures / observed_frames > .01:
        issues.append(f"CRC storm: {crc_failures} CRC failures across {observed_frames} observed frame opportunities")
    if event_diffs:
        issues.append(f"host/firmware policy divergence at {len(event_diffs)} sample(s)")
    uart_ms = (metrics["data_bytes_transmitted"] + metrics["ack_bytes_transmitted"]) * 10 * 1000 / load_config()["uart_baud"]
    metrics.update({"physical_ack_frames": metrics["ack_count"],
                    "ack_timeout": sum(line.startswith("TIMEOUT,") for _, line in sensor_lines),
                    "uncontrolled_physical_ack_missing": max(0, metrics["ack_count"] - metrics["physical_ack_received"]),
                    "estimated_communication_time_ms": uart_ms,
                    "estimated_data_uart_time_ms": metrics["data_bytes_transmitted"] * 10 * 1000 / load_config()["uart_baud"],
                    "estimated_ack_uart_time_ms": metrics["ack_bytes_transmitted"] * 10 * 1000 / load_config()["uart_baud"],
                    "good_count": sum(e["link_state_before"] == "GOOD" for e in events),
                    "degraded_count": sum(e["link_state_before"] == "DEGRADED" for e in events),
                    "bad_count": sum(e["link_state_before"] == "BAD" for e in events),
                    "link_state_transitions": sum(a["link_state_before"] != b["link_state_before"] for a, b in zip(events, events[1:]))})
    data_missing_count = sum(a["kind"] == "uncontrolled_physical_data_missing" for a in physical_anomalies)
    ack_missing_count = sum(a["kind"] == "uncontrolled_physical_ack_missing" for a in physical_anomalies)
    metrics.update({"uncontrolled_physical_data_missing": data_missing_count,
                    "uncontrolled_physical_ack_missing": ack_missing_count,
                    "uncontrolled_physical_data_missing_rate": data_missing_count / max(1, metrics["physical_data_transmissions"]),
                    "uncontrolled_physical_ack_missing_rate": ack_missing_count / max(1, metrics["physical_ack_frames"]),
                    "crc_failure_count_including_parser": crc_failures,
                    "uart_diagnostics": uart_diagnostics})
    record = {**expected_identity, **provenance, "source": "REAL_E220_WITH_APPLICATION_LAYER_INJECTION",
              "sensor_mac": mapping["chip_macs"]["sensor"], "gateway_mac": mapping["chip_macs"]["gateway"],
              "serial_ports": {"sensor": mapping["sensor_port"], "gateway": mapping["gateway_port"]},
              "strategy": config.strategy.value, "loss_model": config.loss_model.value,
              "loss_rate": config.loss_rate, "seed": config.seed, "start_time_utc": start_utc,
              "duration_s": end-start, "sample_count": len(samples), "metrics": metrics,
              "start_host_monotonic": start,
              "sample_events": events, "sample_differences": event_diffs, "issues": issues,
              "physical_anomalies": physical_anomalies,
              "maximum_runtime_seconds": run_deadline_seconds,
              "deadline_calculation": deadline_components,
              "progress_diagnostics": locals().get("progress_diagnostics", []),
              "reference_metrics": reference["metrics"], "raw_sha256": sha(raw_path),
              "status": "complete" if not issues else "failed"}
    atomic_json(manifest_path, record)
    return record


def run(stage: str, skip_flash: bool = False, output_dir: Path | None = None) -> dict:
    if stage == "stage1" and not skip_flash:
        raise RuntimeError("Stage 1 is read-only with respect to firmware: pass --skip-flash; build/flash is forbidden")
    provenance = frozen_guard()
    provenance["hardware_runner_sha256"] = sha(Path(__file__))
    provenance["hardware_analysis_sha256"] = sha(ROOT / "eventguard/hardware_analysis.py")
    provenance["git_worktree_status"] = subprocess.check_output(
        ["git", "status", "--short"], cwd=ROOT, text=True).splitlines()
    provenance["git_worktree_clean"] = not provenance["git_worktree_status"]
    output = output_dir or (ROOT / "results/hardware_validation_v2" if stage == "stage1" else OUT)
    manifest_path = output / "hardware_manifest.json"
    previous_study = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    cfg = load_config()

    if stage != "smoke":
        smoke_state = previous_study.get("stages", {}).get("smoke", {})
        if smoke_state.get("gate_status") != "PASS" or smoke_state.get("passed_runs") != 12:
            raise RuntimeError("main hardware matrix is blocked: all 12 frozen smoke runs must pass first")

    stage_budgets: dict[tuple[str, float, int], int] = {}
    budget_rows: list[dict] = []
    recovery_provenance = {}
    if stage == "stage1":
        if previous_study.get("algorithm_spec_v1_sha256") != provenance["algorithm_spec_v1_sha256"] or \
                previous_study.get("config_sha256") != provenance["config_sha256"] or \
                previous_study.get("frozen_core_sha256") != provenance["frozen_core_sha256"]:
            raise RuntimeError("Stage 1 frozen algorithm/config/trace/fault hashes differ from the v2 smoke study")
        smoke_runs = sorted((output / "runs" / "smoke").glob("*.json"))
        if len(smoke_runs) != 12:
            raise RuntimeError(f"Stage 1 requires 12 saved smoke run manifests; found {len(smoke_runs)}")
        for path in smoke_runs:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("status") != "complete" or row.get("firmware_images_sha256") != STAGE1_FIRMWARE_SHA256:
                raise RuntimeError(f"Stage 1 smoke evidence is incomplete or uses a different firmware: {path}")
        images, recovery_provenance = _verify_stage1_firmware(previous_study)
        provenance["firmware_images_sha256"] = {role: sha(path) for role, path in images.items()}
        provenance["firmware_image_manifest_commit"] = previous_study.get("firmware_image_manifest_commit")
        provenance["firmware_hash_source"] = "active app partitions read from Sensor/Gateway; exact canonical image hashes match v2 smoke"
        provenance["firmware_artifact_directory"] = str(FROZEN_STAGE1_FIRMWARE.relative_to(ROOT))
        stage_budgets, budget_rows = _precompute_stage1_budgets(cfg)
    elif skip_flash:
        images = {role: ROOT / "build" / role / "eventguard.bin" for role in ("sensor", "gateway")}
        if any(not path.exists() for path in images.values()):
            raise RuntimeError("--skip-flash requires built images")
        expected_sources = {
            "sensor_firmware_source_sha256": sha(ROOT / "firmware/main/main.c"),
            "gateway_firmware_source_sha256": sha(ROOT / "firmware/main/main.c"),
            "config_sha256": sha(ROOT / "configs/default.json"),
        }
        source_mismatches = [key for key, value in expected_sources.items()
                             if previous_study.get(key) != value]
        actual_hashes = {role: sha(path) for role, path in images.items()}
        if source_mismatches or previous_study.get("firmware_images_sha256") != actual_hashes:
            raise RuntimeError("--skip-flash image/source hash gate failed; refusing to attest current build output")
        provenance["firmware_images_sha256"] = actual_hashes
        provenance["firmware_image_manifest_commit"] = previous_study.get("firmware_image_manifest_commit",
                                                                            previous_study.get("git_commit"))
        provenance["firmware_hash_source"] = "build image SHA256 exactly matched the existing study manifest"
    else:
        images = {}

    for name in ("raw", "runs", "metrics", "plots"):
        (output / name).mkdir(parents=True, exist_ok=True)
    if stage == "stage1":
        provenance["e220_configuration"] = cfg["e220"]
        provenance["recovery_manifest"] = "artifacts/final_stage1_firmware/manifest.json"
    else:
        # Discovery is needed before a build/flash operation, preserving legacy smoke behavior.
        discovery, open_readers = discover_boards(3)
        for reader in open_readers.values():
            reader.close()
        discovery["chip_macs"] = {role: probe_mac(discovery[f"{role}_port"]) for role in ("sensor", "gateway")}
        if skip_flash:
            expected_macs = previous_study.get("hardware", {}).get("chip_macs", {})
            if expected_macs and any(expected_macs.get(role) != discovery["chip_macs"][role]
                                      for role in ("sensor", "gateway")):
                raise RuntimeError("--skip-flash board MACs differ from the existing hardware manifest")
        else:
            images = build_firmware(cfg, output)
            flash_firmware(images, discovery, cfg, output)
            provenance["firmware_images_sha256"] = {role: sha(path) for role, path in images.items()}
            provenance["firmware_image_manifest_commit"] = provenance["git_commit"]
            provenance["firmware_hash_source"] = "images built and flashed in this hardware validation run"

    provenance["e220_configuration"] = cfg["e220"]
    provenance["compatibility_fixes"] = [
        "SAMPLE serial parser corrected from fields 2/4/5 to 3/5/6 after an initial smoke attempt; affected condition rerun",
        "E220 receive parser retains stream state and incrementally accumulates short UART reads",
        "Gateway END s_rx_count is validated against post-injection physical_data_received",
        "Stage 1 uses frozen device-recovered firmware artifacts; current build/ output is never used",
        "Stage 1 strategy order is shuffled across all four strategies using the recorded seed",
        "Stage 1 equal-DATA budgets are computed from frozen host references before the first START",
    ]
    study = {**previous_study, **provenance,
             "hardware": previous_study.get("hardware", {}), "execution_order_seed": ORDER_SEED,
             "trace_version": "trace-v1-10s-9phases-6samples-per-phase",
             "airtime_proxy": "10 bits per UART byte at 9600 baud; includes start/stop bits, excludes E220 RF PHY airtime",
             "energy": "not estimated; no current sensor", "stages": previous_study.get("stages", {})}
    if previous_study.get("firmware_images_sha256") and \
            previous_study.get("firmware_images_sha256") != provenance["firmware_images_sha256"]:
        raise RuntimeError("Firmware image hashes differ from existing hardware study; preserve separate study/version")
    order = plan(stage)
    order_records = [{"model": m, "rate": r, "seed": s, "strategy": st, "order": i}
                     for m, r, s, st, i in order]
    stage_root = output / stage
    if stage == "stage1":
        stage_root.mkdir(parents=True, exist_ok=True)
        execution_payload = {"execution_order_seed": ORDER_SEED, "execution_order": order_records}
        budget_payload = {"algorithm_version": provenance["algorithm_version"],
                          "algorithm_spec_sha256": provenance["algorithm_spec_v1_sha256"],
                          "config_sha256": provenance["config_sha256"], "trace_version": study["trace_version"],
                          "loss_calendar_implementation_sha256": provenance["frozen_core_sha256"]["eventguard/faults.py"],
                          "seed_range": [31, 40], "conditions": budget_rows}
        for path, payload in ((stage_root / "execution_order.json", execution_payload),
                              (stage_root / "budget_table.json", budget_payload)):
            if path.exists() and json.loads(path.read_text(encoding="utf-8")) != payload:
                raise RuntimeError(f"Existing Stage 1 pre-registration differs; refusing to overwrite {path}")
            if not path.exists():
                atomic_json(path, payload)
        smoke_macs = previous_study.get("hardware", {}).get("chip_macs", {})
        for model, rate, seed, strategy, index in order:
            run_id = f"{strategy.lower()}_{model.lower()}_{int(rate*100):02d}_seed{seed}"
            raw_path = output / "raw" / stage / f"{run_id}.json"
            run_path = output / "runs" / stage / f"{run_id}.json"
            if raw_path.exists() or run_path.exists():
                if not raw_path.exists() or not run_path.exists():
                    raise RuntimeError(f"Existing Stage 1 attempt {run_id} is incomplete; stopping without retry")
                saved = json.loads(run_path.read_text(encoding="utf-8"))
                samples = generate_trace(seed, 6)
                config = _run_config(cfg, strategy, rate, model, seed)
                budget = stage_budgets[(model, rate, seed)]
                is_budget_strategy = strategy in ("UNIFORM_BUDGET", "RANDOM_BUDGET")
                if is_budget_strategy:
                    config = replace(config, data_copy_budget=budget)
                expected_identity = {
                    "run_id": run_id, "stage": stage,
                    "trace_sha256": trace_fingerprint(samples),
                    "loss_calendar_sha256": calendar_hash(config, len(samples)),
                    "firmware_images_sha256": STAGE1_FIRMWARE_SHA256,
                    "reference_eventguard_data_copy_budget": budget,
                    "configured_data_copy_budget": config.data_copy_budget,
                    "execution_order": index,
                }
                if saved.get("status") != "complete" or saved.get("raw_sha256") != sha(raw_path) or \
                        any(saved.get(key) != value for key, value in expected_identity.items()) or \
                        saved.get("sensor_mac") != smoke_macs.get("sensor") or \
                        saved.get("gateway_mac") != smoke_macs.get("gateway"):
                    raise RuntimeError(f"Existing Stage 1 attempt {run_id} is failed/incomplete or has a hash mismatch")
        protocol_path = stage_root / "stage1_protocol.md"
        if not protocol_path.exists():
            protocol_path.write_text(
                "# Stage 1 Confirmatory Protocol\n\n"
                "This 160-run confirmatory experiment uses the device-recovered, smoke-validated firmware pair "
                "identified in `stage1_manifest.json`. The runner does not build or flash firmware. The frozen "
                "EventGuard-v1 algorithm, default config, trace generator, loss-calendar implementation, and seeds "
                "are hash-gated before execution.\n\n"
                "Strategies are shuffled within each model/rate/seed condition with execution-order seed "
                f"`{ORDER_SEED}`. The EventGuard host-reference DATA-copy budget for all 40 conditions is saved "
                "in `budget_table.json` before any run starts; both blind budget baselines receive exactly that "
                "count and may use only sample index, fixed total budget, deterministic allocation, and seed.\n\n"
                "Every run resets both boards and verifies READY/ARMED/TRACE_READY before START. Isolated, "
                "uncontrolled DATA/ACK losses are retained as physical-link noise and are not reclassified as "
                "application-layer injection; the versioned physical-noise gate allows at most one per type per run, "
                "and stops on bursts, repeats, or cumulative rate above 1%. END timeout, firmware reset/hang, "
                "UART/parser failure, CRC storm, policy divergence, loss-calendar mismatch, or budget mismatch "
                "stops the matrix. A run is never automatically retried "
                "after START is attempted. Results are host simulation references plus physical ESP32-S3/E220 "
                "application-layer fault-injection observations plus separately counted physical anomalies; "
                "UART-time is only a proxy, not measured RF airtime.\n",
                encoding="utf-8")
        protocol_text = protocol_path.read_text(encoding="utf-8")
        capture_marker = "## Console log capture integrity"
        if capture_marker not in protocol_text:
            protocol_path.write_text(protocol_text +
                "\n## Console log capture integrity\n\n"
                f"Host serial capture uses `{SERIAL_CAPTURE_VERSION}`. Incoming serial bytes are read in chunks "
                "and incrementally framed on newline boundaries. After every run, Sensor TX/ACK/SAMPLE lines, "
                "Gateway RX/DROP/ACK lines, and per-frame console diagnostics are cross-checked against the "
                "firmware END counters and E220 UART_DIAG frame counters. Any disagreement fails the run and "
                "stops the matrix.\n\n"
                "An explicitly user-authorized retry of a failed run is archived under `stage1/retry_history/`; "
                "the original attempt remains immutable and the retry retains the same condition and firmware.\n",
                encoding="utf-8")
        stage_manifest_path = stage_root / "stage1_manifest.json"
        noise_policy_path = stage_root / "stage1_policy_amendment_v1.md"
        if not noise_policy_path.is_file():
            raise RuntimeError(f"Stage 1 requires the versioned physical-noise policy amendment: {noise_policy_path}")
        stage_manifest = {"status": "PREFLIGHT_PENDING", "planned_runs": 160,
                          "algorithm_version": provenance["algorithm_version"],
                          "git_commit": provenance["git_commit"], "branch": provenance["branch"],
                          "git_worktree_clean": provenance["git_worktree_clean"],
                          "git_worktree_status": provenance["git_worktree_status"],
                          "hardware_runner_sha256": provenance["hardware_runner_sha256"],
                          "hardware_analysis_sha256": provenance["hardware_analysis_sha256"],
                          "serial_console_capture_version": provenance["serial_console_capture_version"],
                          "firmware_images_sha256": provenance["firmware_images_sha256"],
                          "recovery_manifest": provenance["recovery_manifest"],
                          "execution_order_seed": ORDER_SEED,
                          "algorithm_spec_sha256": provenance["algorithm_spec_v1_sha256"],
                          "config_sha256": provenance["config_sha256"],
                          "trace_sha256": provenance["frozen_core_sha256"]["eventguard/trace.py"],
                          "loss_calendar_implementation_sha256": provenance["frozen_core_sha256"]["eventguard/faults.py"],
                          "budget_table": "budget_table.json", "execution_order": "execution_order.json",
                          "physical_noise_policy_version": "stage1-physical-noise-v2",
                          "physical_noise_policy_path": noise_policy_path.name,
                          "physical_noise_policy_sha256": sha(noise_policy_path),
                          "firmware_provenance": recovery_provenance}
        if stage_manifest_path.exists():
            existing = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
            immutable_keys = ("planned_runs", "firmware_images_sha256", "execution_order_seed",
                              "algorithm_spec_sha256", "config_sha256", "trace_sha256",
                              "loss_calendar_implementation_sha256", "physical_noise_policy_version",
                              "physical_noise_policy_sha256")
            if any(existing.get(key) != stage_manifest.get(key) for key in immutable_keys):
                raise RuntimeError("Existing Stage 1 manifest does not match this frozen preflight")
            stage_manifest = {**existing, **stage_manifest}
            for stale_key in ("failed_run_id", "failure", "failed_at_utc"):
                stage_manifest.pop(stale_key, None)
        attempt_history_path = stage_root / "retry_history.json"
        if attempt_history_path.is_file():
            attempt_history = json.loads(attempt_history_path.read_text(encoding="utf-8"))
            stage_manifest["retry_history"] = "retry_history.json"
            stage_manifest["retry_attempt_count"] = len(attempt_history.get("attempts", []))
        atomic_json(stage_manifest_path, stage_manifest)

    study["hardware"] = previous_study.get("hardware", {})
    study["stages"][stage] = {"planned_runs": len(order), "execution_order": order_records}
    if stage == "stage1" and (stage_root / "retry_history.json").is_file():
        stage_history = json.loads((stage_root / "retry_history.json").read_text(encoding="utf-8"))
        study["stages"][stage]["retry_history"] = "stage1/retry_history.json"
        study["stages"][stage]["retry_attempt_count"] = len(stage_history.get("attempts", []))
    atomic_json(manifest_path, study)

    mapping, readers = discover_boards(3)
    for reader in readers.values():
        reader.close()
    mapping["chip_macs"] = {role: probe_mac(mapping[f"{role}_port"]) for role in ("sensor", "gateway")}
    if stage == "stage1":
        recovered_devices = recovery_provenance["device_partition_map"]["devices"]
        if any(mapping["chip_macs"][role].lower() != recovered_devices[role]["mac"].lower()
               for role in ("sensor", "gateway")):
            raise RuntimeError("Stage 1 board MACs do not match the devices from which firmware was recovered")
        expected_macs = previous_study.get("hardware", {}).get("chip_macs", {})
        if any(expected_macs.get(role, "").lower() != mapping["chip_macs"][role].lower()
               for role in ("sensor", "gateway")):
            raise RuntimeError("Stage 1 board MACs differ from the v2 smoke devices")
    elif skip_flash:
        expected_macs = previous_study.get("hardware", {}).get("chip_macs", {})
        if expected_macs and any(expected_macs.get(role) != mapping["chip_macs"][role]
                                 for role in ("sensor", "gateway")):
            raise RuntimeError("--skip-flash board MACs differ from the existing hardware manifest")
    study["hardware"] = mapping
    atomic_json(manifest_path, study)

    sensor = SerialLogReader(mapping["sensor_port"])
    gateway = SerialLogReader(mapping["gateway_port"])
    complete = 0
    current_run_id = None
    if stage == "stage1":
        stage_manifest["status"] = "PREFLIGHTING"
        stage_manifest["preflight_started_at_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_json(stage_root / "stage1_manifest.json", stage_manifest)
    try:
        status = _status(sensor, gateway)
        if stage == "stage1":
            stage_manifest.update({"status": "PREFLIGHT_PASS", "preflight_passed_at_utc": datetime.now(timezone.utc).isoformat(),
                                   "serial_ports": {"sensor": mapping["sensor_port"], "gateway": mapping["gateway_port"]},
                                   "sensor_mac": mapping["chip_macs"]["sensor"],
                                   "gateway_mac": mapping["chip_macs"]["gateway"],
                                   "status_sensor": status["sensor"], "status_gateway": status["gateway"]})
            atomic_json(stage_root / "stage1_manifest.json", stage_manifest)
            print("STAGE1_PREFLIGHT_PASS", flush=True)
        complete = 0
        for model, rate, seed, strategy, index in order:
            samples = generate_trace(seed, 6)
            config = _run_config(cfg, strategy, rate, model, seed)
            key = (model, rate, seed)
            if stage == "stage1":
                budget = stage_budgets[key]
            else:
                budget = None
            current_run_id = f"{strategy.lower()}_{model.lower()}_{int(rate*100):02d}_seed{seed}"
            result = _run_one(sensor, gateway, config, samples, mapping, provenance, index, stage, budget, output)
            actual_copies = result.get("metrics", {}).get("physical_data_transmissions")
            if stage == "stage1" and strategy in ("EVENTGUARD", "UNIFORM_BUDGET", "RANDOM_BUDGET") and actual_copies != budget:
                result.setdefault("issues", []).append(
                    f"DATA-copy budget mismatch: actual={actual_copies}, frozen EventGuard reference={budget}")
                result["status"] = "failed"
                atomic_json(output / "runs" / stage / f"{result['run_id']}.json", result)
            if stage == "stage1" and result.get("status") == "complete":
                noise_gate = _stage1_physical_noise_gate(result, _previous_stage1_records(output))
                result["physical_noise_gate"] = noise_gate
                if not noise_gate["allowed_to_continue"]:
                    result.setdefault("issues", []).extend(noise_gate["stop_reasons"])
                    result["status"] = "failed"
                atomic_json(output / "runs" / stage / f"{result['run_id']}.json", result)
            if result["status"] != "complete":
                raise RuntimeError(f"{result['run_id']} failed: {result.get('issues', [])[:8]}")
            complete += 1
            print(f"{stage.upper()} {complete}/{len(order)} {result['run_id']} critical={result['metrics']['critical_event_delivery_ratio']:.4f}", flush=True)
            if stage == "stage1":
                stage_manifest.update({"status": "RUNNING", "completed_runs": complete,
                                       "last_completed_run": result["run_id"],
                                       "updated_at_utc": datetime.now(timezone.utc).isoformat()})
                atomic_json(stage_root / "stage1_manifest.json", stage_manifest)
    except BaseException as exc:
        if stage == "stage1":
            stage_manifest.update({"status": "FAILED", "completed_runs": complete,
                                   "failed_run_id": current_run_id,
                                   "failure": {"type": type(exc).__name__, "message": str(exc)},
                                   "failed_at_utc": datetime.now(timezone.utc).isoformat()})
            atomic_json(stage_root / "stage1_manifest.json", stage_manifest)
            study["stages"][stage].update({"gate_status": "FAIL", "completed_runs": complete,
                                          "failed_run_id": current_run_id,
                                          "failure": {"type": type(exc).__name__, "message": str(exc)}})
            atomic_json(manifest_path, study)
        raise
    finally:
        sensor.close()
        gateway.close()

    if stage == "stage1":
        stage_manifest.update({"status": "COMPLETE", "completed_runs": complete,
                               "completed_at_utc": datetime.now(timezone.utc).isoformat()})
        atomic_json(stage_root / "stage1_manifest.json", stage_manifest)
        study["stages"][stage].update({"gate_status": "PASS", "completed_runs": complete})
        atomic_json(manifest_path, study)
    return {"stage": stage, "completed": complete, "planned": len(order)}
