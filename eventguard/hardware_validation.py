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
from .host import (ROOT, SerialLogReader, _config_command, _parse_metrics, _run_config,
                   _truth_code, _wait_for_ack, build_firmware, discover_boards,
                   flash_firmware, load_config, probe_mac)
from .model import LossModel, Strategy
from .simulator import run_reference
from .strategy import LinkQualityEstimator
from .trace import generate_trace, trace_fingerprint

OUT = ROOT / "results/hardware_validation_v1"
FROZEN_COMMIT = "ccf9ca5da651e2bb93229896c7d0b7a7b7b74bab"
SMOKE = ("FIXED_2", "IMPORTANCE_ONLY", "EVENTGUARD")
MAIN = ("FIXED_2", "IMPORTANCE_ONLY", "EVENTGUARD", "UNIFORM_BUDGET", "RANDOM_BUDGET")
SEEDS = tuple(range(31, 41))
ORDER_SEED = 4170411


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    mismatches = [name for name, digest in expected.items() if sha(ROOT / name) != digest]
    if mismatches:
        raise RuntimeError(f"Frozen policy, trace, or loss code changed: {mismatches}")
    return {"git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
            "algorithm_version": frozen["algorithm_version"],
            "algorithm_spec_v1_sha256": sha(ROOT / "docs/algorithm_spec_v1.md"),
            "config_sha256": sha(ROOT / "configs/default.json"),
            "frozen_core_sha256": {name: sha(ROOT / name) for name in expected},
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
        others = [s for s in strategies if s != "EVENTGUARD"]
        rng.shuffle(others)
        for strategy in ("EVENTGUARD", *others):
            result.append((model, rate, seed, strategy, len(result) + 1))
    return result


def calendar_hash(config, count: int) -> str:
    loss = LossPlan(config.seed, config.loss_rate, config.loss_model, count, config.burst_length)
    packed = bytes(loss._data) + bytes(loss._ack)
    return hashlib.sha256(packed).hexdigest()


def _lines(reader: SerialLogReader, token: str, timeout: float = 8.0):
    return _wait_for_ack(reader, token, timeout)


def _status(sensor: SerialLogReader, gateway: SerialLogReader) -> dict:
    result = {}
    for name, reader, role in (("sensor", sensor, "SENSOR"), ("gateway", gateway, "GATEWAY")):
        reader.drain()
        reader.write("STATUS")
        lines = _lines(reader, "E220_READY", 12)
        values = [line for _, line in lines]
        if not any(line.startswith(f"ROLE,{role}") for line in values):
            raise RuntimeError(f"{name} role not confirmed: {values}")
        result[name] = values
    return result


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
            "reset_contract": "Sensor RESET clears sequence/classifier/link/counters; Gateway RESET clears duplicate table/sequence/counters/armed state"}


def _parse_samples(samples, sensor_lines, gateway_lines, config, reference) -> tuple[list[dict], list[dict], list[str]]:
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
    issues, differences, records = [], [], []
    estimator = LinkQualityEstimator(config.link_window, config.link_degraded_threshold,
                                     config.link_bad_threshold, config.link_bad_fail_streak)
    loss = LossPlan(config.seed, config.loss_rate, config.loss_model, len(samples), config.burst_length)
    for sample, expected in zip(samples, reference["events"]):
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
            tx = {int(p[3]): p for p in by_kind["TX"][sid]}
            rx = {int(p[3]): p for p in by_kind["RX"][sid]}
            drops = {int(p[4]): p for p in by_kind["DROP_DATA"][sid]}
            acks = {int(p[3]): p for p in by_kind["ACK"][sid]}
            ack_tx = {int(p[3]): p for p in by_kind["ACK_TX"][sid]}
            if len(tx) != copies or set(tx) != set(range(copies)):
                issues.append(f"sample {sid}: DATA copy logs {sorted(tx)} != {list(range(copies))}")
            if any(int(p[2]) != sid for p in tx.values()):
                issues.append(f"sample {sid}: sequence did not reset to sample index")
            if before != estimator.state.name:
                issues.append(f"sample {sid}: link before {before} != replay {estimator.state.name}")
            for copy in range(copies):
                data_drop = loss.drops("DATA", sid, copy)
                expected_rx = copy in tx and not data_drop
                if (copy in drops) != (copy in tx and data_drop):
                    issues.append(f"sample {sid} copy {copy}: injected DATA calendar/log mismatch")
                if (copy in rx) != expected_rx:
                    issues.append(f"sample {sid} copy {copy}: uncontrolled physical DATA anomaly")
                if (copy in ack_tx) != (copy in rx):
                    issues.append(f"sample {sid} copy {copy}: ACK TX mismatch")
                if copy in ack_tx and copy not in acks:
                    issues.append(f"sample {sid} copy {copy}: physical ACK missing")
                if copy in acks and (acks[copy][4] == "DROP") != loss.drops("ACK", sid, copy):
                    issues.append(f"sample {sid} copy {copy}: injected ACK calendar/log mismatch")
                estimator.observe_copy(copy in acks and acks[copy][4] == "OK", copy == 0)
            actual_delivered = bool(by_kind["DELIVER"][sid])
            if importance != expected["importance"] or copies != expected["copies_transmitted"] or \
                    before != expected["link_state"] or actual_delivered != expected["delivered"]:
                differences.append({"sample_id": sid, "copy_index": 0, "importance_hardware": importance,
                                    "importance_simulation": expected["importance"], "copies_hardware": copies,
                                    "copies_simulation": expected["copies_transmitted"],
                                    "link_hardware": before, "link_simulation": expected["link_state"],
                                    "delivered_hardware": actual_delivered, "delivered_simulation": expected["delivered"]})
            records.append({"sample_id": sid, "importance": importance, "selected_copies": copies,
                            "first_copy_sent": 0 in tx, "first_copy_data_delivered": 0 in rx,
                            "first_copy_ack_physical_received": 0 in acks,
                            "first_copy_ack_accepted": 0 in acks and acks[0][4] == "OK",
                            "link_state_before": before, "link_state_after": estimator.state.name,
                            "delivered": actual_delivered, "physical_data_copies": len(tx),
                            "physical_ack_frames": len(ack_tx)})
        except (IndexError, ValueError, KeyError) as exc:
            issues.append(f"sample {sid}: parser failure {exc}")
    if len(records) != len(samples):
        issues.append(f"sample record count {len(records)} != {len(samples)}")
    for role, lines in (("sensor", sensor), ("gateway", gateway)):
        for line in lines:
            if line.startswith("HOST_SERIAL_ERROR") or line.startswith("ERR,UART") or line.startswith("ERR,CRC") or \
                    line.startswith("ERR,PACKET") or line.startswith("ERR,ACK_MISMATCH"):
                issues.append(f"{role}: {line}")
    return records, differences, issues


def _run_one(sensor, gateway, config, samples, mapping, provenance, order: int, stage: str, budget: int | None):
    if budget is not None:
        config = replace(config, data_copy_budget=budget)
    run_id = f"{config.strategy.value.lower()}_{config.loss_model.value.lower()}_{int(config.loss_rate*100):02d}_seed{config.seed}"
    raw_path = OUT / "raw" / stage / f"{run_id}.json"
    manifest_path = OUT / "runs" / stage / f"{run_id}.json"
    trace_hash = trace_fingerprint(samples)
    calendar = calendar_hash(config, len(samples))
    fw_hashes = provenance["firmware_images_sha256"]
    expected_identity = {"run_id": run_id, "stage": stage, "trace_sha256": trace_hash,
                         "loss_calendar_sha256": calendar, "firmware_images_sha256": fw_hashes,
                         "data_copy_budget": budget, "execution_order": order}
    if raw_path.exists() and manifest_path.exists():
        try:
            saved = json.loads(manifest_path.read_text())
            raw = json.loads(raw_path.read_text())
            if (all(saved.get(key) == value for key, value in expected_identity.items()) and
                    saved.get("status") == "complete" and raw.get("sensor") and raw.get("gateway") and
                    sha(raw_path) == saved.get("raw_sha256") and
                    saved.get("sensor_mac") == mapping["chip_macs"]["sensor"] and
                    saved.get("gateway_mac") == mapping["chip_macs"]["gateway"]):
                return saved
        except (OSError, ValueError, KeyError, TypeError):
            pass
    # Preserve every failed or incomplete attempt before the same condition is rerun.
    if raw_path.exists() or manifest_path.exists():
        archive = OUT / "raw" / "attempts" / stage / run_id
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
    start_utc = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    sensor.write("START")
    sensor_lines, gateway_lines = [], list(gateway_prelude)
    deadline = time.monotonic() + max(90, len(samples) * 3 * (config.ack_timeout_ms / 1000 + 1))
    while time.monotonic() < deadline:
        sb, gb = sensor.drain(), gateway.drain()
        sensor_lines.extend(sb); gateway_lines.extend(gb)
        if any(line.startswith("END,") for _, line in sb):
            break
        time.sleep(.01)
    else:
        raise TimeoutError(f"{run_id}: firmware END timeout")
    sensor_lines.extend(sensor.drain())
    gateway.write("ENDRUN")
    gateway_lines.extend(_lines(gateway, "END,", 6))
    end = time.monotonic()
    raw = {"run_id": run_id, "stage": stage, "sensor": [{"host_monotonic": t, "line": line} for t, line in sensor_lines],
           "gateway": [{"host_monotonic": t, "line": line} for t, line in gateway_lines],
           "reset": reset, "configured_sensor": configured_sensor, "configured_gateway": configured_gateway,
           "trace_ready": trace_ready}
    atomic_json(raw_path, raw)
    reference = run_reference(samples, config, load_config()["uart_baud"])
    events, event_diffs, issues = _parse_samples(samples, sensor_lines, gateway_lines, config, reference)
    metrics = _parse_metrics(samples, events, sensor_lines, gateway_lines, start, end, config, trace_hash)
    sensor_end = [line for _, line in sensor_lines if line.startswith("END,")]
    gateway_end = [line for _, line in gateway_lines if line.startswith("END,")]
    if len(sensor_end) != 1 or len(gateway_end) != 1:
        issues.append(f"missing or duplicate END lines sensor={sensor_end}, gateway={gateway_end}")
    if metrics["physical_data_transmissions"] != sum(e["physical_data_copies"] for e in events):
        issues.append("physical DATA count does not match per-sample logs")
    if metrics["logical_packets"] != len(events):
        issues.append("incomplete per-sample log")
    if metrics["crc_errors"] or metrics["invalid_packets"] or metrics["out_of_order_packets"]:
        issues.append("CRC, invalid packet, or out-of-order anomaly")
    for field in ("physical_data_transmissions", "duplicate_packets", "physical_ack_received",
                  "accepted_ack", "data_injected_drops", "ack_injected_drops", "total_bytes_transmitted"):
        if metrics[field] != reference["metrics"][field]:
            issues.append(f"simulation/firmware {field} differs: hardware={metrics[field]}, "
                          f"host={reference['metrics'][field]}")
    if sensor_end and gateway_end:
        sensor_totals = [int(x) for x in sensor_end[0].split(",")[1:]]
        gateway_totals = [int(x) for x in gateway_end[0].split(",")[1:]]
        if sensor_totals[:3] != [len(samples), metrics["physical_data_transmissions"], metrics["accepted_ack"]] or sensor_totals[3] != 0:
            issues.append(f"sensor END counters disagree: {sensor_totals}")
        if gateway_totals[:7] != [len(samples), metrics["physical_data_received"] - metrics["data_injected_drops"] - metrics["crc_errors"], metrics["delivered_packets"],
                                  metrics["duplicate_packets"], metrics["ack_count"], metrics["crc_errors"],
                                  metrics["data_injected_drops"]]:
            issues.append(f"gateway END counters disagree: {gateway_totals}")
    if event_diffs:
        issues.append(f"simulation/firmware divergence at {len(event_diffs)} sample(s)")
    uart_ms = (metrics["data_bytes_transmitted"] + metrics["ack_bytes_transmitted"]) * 10 * 1000 / load_config()["uart_baud"]
    metrics.update({"physical_ack_frames": metrics["ack_count"],
                    "ack_timeout": sum(line.startswith("TIMEOUT,") for _, line in sensor_lines),
                    "estimated_communication_time_ms": uart_ms,
                    "estimated_data_uart_time_ms": metrics["data_bytes_transmitted"] * 10 * 1000 / load_config()["uart_baud"],
                    "estimated_ack_uart_time_ms": metrics["ack_bytes_transmitted"] * 10 * 1000 / load_config()["uart_baud"],
                    "good_count": sum(e["link_state_before"] == "GOOD" for e in events),
                    "degraded_count": sum(e["link_state_before"] == "DEGRADED" for e in events),
                    "bad_count": sum(e["link_state_before"] == "BAD" for e in events),
                    "link_state_transitions": sum(a["link_state_before"] != b["link_state_before"] for a, b in zip(events, events[1:]))})
    record = {**expected_identity, **provenance, "source": "REAL_E220_WITH_APPLICATION_LAYER_INJECTION",
              "sensor_mac": mapping["chip_macs"]["sensor"], "gateway_mac": mapping["chip_macs"]["gateway"],
              "serial_ports": {"sensor": mapping["sensor_port"], "gateway": mapping["gateway_port"]},
              "strategy": config.strategy.value, "loss_model": config.loss_model.value,
              "loss_rate": config.loss_rate, "seed": config.seed, "start_time_utc": start_utc,
              "duration_s": end-start, "sample_count": len(samples), "metrics": metrics,
              "sample_events": events, "sample_differences": event_diffs, "issues": issues,
              "reference_metrics": reference["metrics"], "raw_sha256": sha(raw_path),
              "status": "complete" if not issues else "failed"}
    atomic_json(manifest_path, record)
    return record


def run(stage: str, skip_flash: bool = False) -> dict:
    provenance = frozen_guard()
    cfg = load_config()
    output = OUT
    for name in ("raw", "runs", "metrics", "plots"):
        (output / name).mkdir(parents=True, exist_ok=True)
    mapping, readers = discover_boards(3)
    for reader in readers.values(): reader.close()
    mapping["chip_macs"] = {role: probe_mac(mapping[f"{role}_port"]) for role in ("sensor", "gateway")}
    if skip_flash:
        images = {role: ROOT / "build" / role / "eventguard.bin" for role in ("sensor", "gateway")}
        if any(not path.exists() for path in images.values()):
            raise RuntimeError("--skip-flash requires built images")
    else:
        images = build_firmware(cfg, output)
        flash_firmware(images, mapping, cfg, output)
    provenance["firmware_images_sha256"] = {role: sha(path) for role, path in images.items()}
    provenance["e220_configuration"] = cfg["e220"]
    provenance["compatibility_fixes"] = [
        "host build/flash logs redirected to hardware_validation_v1; no firmware source changes",
        "SAMPLE serial parser corrected from fields 2/4/5 to 3/5/6 after the first smoke attempt; affected condition rerun",
    ]
    study = {**provenance, "hardware": mapping, "execution_order_seed": ORDER_SEED,
             "trace_version": "trace-v1-10s-9phases-6samples-per-phase",
             "airtime_proxy": "10 bits per UART byte at 9600 baud; includes start/stop bits, excludes E220 RF PHY airtime",
             "energy": "not estimated; no current sensor", "stages": {}}
    manifest_path = output / "hardware_manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous.get("firmware_images_sha256") != provenance["firmware_images_sha256"]:
            raise RuntimeError("Firmware image hashes differ from existing hardware study; preserve separate study/version")
        study["stages"] = previous.get("stages", {})
    order = plan(stage)
    study["stages"][stage] = {"planned_runs": len(order), "execution_order": [
        {"model": m, "rate": r, "seed": s, "strategy": st, "order": i} for m, r, s, st, i in order]}
    atomic_json(manifest_path, study)
    sensor = SerialLogReader(mapping["sensor_port"])
    gateway = SerialLogReader(mapping["gateway_port"])
    complete = 0
    budgets: dict[tuple, int] = {}
    try:
        _status(sensor, gateway)
        for model, rate, seed, strategy, index in order:
            samples = generate_trace(seed, 6)
            config = _run_config(cfg, strategy, rate, model, seed)
            key = (model, rate, seed)
            budget = budgets.get(key) if strategy in ("UNIFORM_BUDGET", "RANDOM_BUDGET") else None
            if strategy in ("UNIFORM_BUDGET", "RANDOM_BUDGET") and budget is None:
                raise RuntimeError(f"Missing matched EventGuard hardware budget for {key}")
            result = _run_one(sensor, gateway, config, samples, mapping, provenance, index, stage, budget)
            if strategy == "EVENTGUARD": budgets[key] = result["metrics"]["physical_data_transmissions"]
            if strategy in ("UNIFORM_BUDGET", "RANDOM_BUDGET") and result["metrics"]["physical_data_transmissions"] != budget:
                result["issues"].append("exact hardware DATA-copy budget mismatch")
                result["status"] = "failed"
                atomic_json(OUT / "runs" / stage / f"{result['run_id']}.json", result)
            if result["status"] != "complete":
                raise RuntimeError(f"{result['run_id']} failed: {result['issues'][:8]}")
            complete += 1
            print(f"{stage.upper()} {complete}/{len(order)} {result['run_id']} critical={result['metrics']['critical_event_delivery_ratio']:.4f}", flush=True)
    finally:
        sensor.close(); gateway.close()
    return {"stage": stage, "completed": complete, "planned": len(order)}
