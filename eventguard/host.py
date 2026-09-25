from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .analysis import analyze_runs
from .model import GroundTruth, LossModel, RunConfig, Strategy
from .protocol import ACK_FRAME_SIZE, DATA_FRAME_SIZE
from .simulator import run_reference
from .trace import generate_trace, trace_fingerprint


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def load_config() -> dict:
    return json.loads((ROOT / "configs/default.json").read_text(encoding="utf-8"))


def _run_config(cfg: dict, strategy, loss_rate, model, seed) -> RunConfig:
    scales = cfg["importance_scales"]
    thresholds = cfg["importance_thresholds"]
    return RunConfig(Strategy(strategy), float(loss_rate), LossModel(model), int(seed),
                     cfg["burst_length"], cfg["fixed_redundancy"], cfg["max_redundancy"],
                     important_threshold=thresholds["important"], critical_threshold=thresholds["critical"],
                     importance_scales=scales, importance_baseline_alpha=cfg["importance_baseline_alpha"],
                     link_window=cfg["link_window"], link_degraded_threshold=cfg["link_degraded_threshold"],
                     link_bad_threshold=cfg["link_bad_threshold"], link_bad_fail_streak=cfg["link_bad_fail_streak"],
                     ack_timeout_ms=cfg["ack_timeout_ms"])


def _config_command(config: RunConfig) -> str:
    scales = config.importance_scales
    values = ["CONFIG", config.strategy.value, f"{config.loss_rate:.6f}", config.loss_model.value,
              str(config.seed), str(config.burst_length), str(config.fixed_redundancy), str(config.max_redundancy),
              str(config.link_window), f"{config.link_degraded_threshold:.6f}", f"{config.link_bad_threshold:.6f}",
              str(config.link_bad_fail_streak), f"{config.important_threshold:.6f}", f"{config.critical_threshold:.6f}",
              f"{scales['temperature']:.6f}", f"{scales['humidity']:.6f}", f"{scales['light']:.6f}",
              f"{scales['soil_moisture']:.6f}", f"{config.importance_baseline_alpha:.6f}", str(config.ack_timeout_ms)]
    return ",".join(values)


def _run_id(config: RunConfig) -> str:
    burst = f"_burst{config.burst_length}" if config.loss_model == LossModel.BURST else ""
    return f"{config.strategy.value.lower()}_{config.loss_model.value.lower()}{burst}_loss{int(config.loss_rate * 100):02d}_seed{config.seed}"


def _ensure_dirs() -> None:
    for name in ("raw", "runs", "metrics", "plots"):
        (RESULTS / name).mkdir(parents=True, exist_ok=True)


def run_simulation(strategies=None, loss_rates=None, models=None, seeds=None, samples_per_phase=None, burst_length=None) -> list[dict]:
    cfg = load_config()
    if burst_length is not None: cfg["burst_length"] = burst_length
    strategies = strategies or cfg["strategies"]
    loss_rates = cfg["loss_rates"] if loss_rates is None else loss_rates
    models = models or cfg["loss_models"]
    seeds = cfg["seeds"] if seeds is None else seeds
    samples_per_phase = cfg["trace_samples_per_phase"] if samples_per_phase is None else int(samples_per_phase)
    cfg["trace_samples_per_phase"] = samples_per_phase
    _ensure_dirs()
    all_runs = []
    trace_cache = {}
    for seed in seeds:
        trace_cache[seed] = generate_trace(seed, samples_per_phase)
    total = len(strategies) * len(loss_rates) * len(models) * len(seeds)
    completed = 0
    for strategy in strategies:
        for loss_rate in loss_rates:
            for model in models:
                for seed in seeds:
                    run_start_time = datetime.now(timezone.utc).isoformat()
                    run_clock = time.monotonic()
                    config = _run_config(cfg, strategy, loss_rate, model, seed)
                    samples = trace_cache[seed]
                    outcome = run_reference(samples, config, cfg["uart_baud"])
                    run_id = _run_id(config)
                    row = {"run_id": run_id, "strategy": config.strategy.value, "loss_rate": config.loss_rate,
                           "loss_model": config.loss_model.value, "seed": config.seed,
                           "burst_length": config.burst_length,
                           "trace_sha256": trace_fingerprint(samples), "source": "HOST_SIMULATION",
                           "radio": "SIMULATED", "sample_count": len(samples), "start_time": run_start_time,
                           "duration_s": time.monotonic() - run_clock,
                           "firmware_commit": _git_commit(), **outcome["metrics"]}
                    all_runs.append(row)
                    manifest = {"run_id": run_id, "strategy": config.strategy.value, "loss_rate": config.loss_rate,
                                "loss_model": config.loss_model.value, "seed": config.seed,
                                "trace_sha256": row["trace_sha256"], "trace": [s.as_dict() for s in samples],
                                "config": cfg, "provenance": {"source": "HOST_SIMULATION", "physical_radio": False,
                                                               "application_layer_injection": True,
                                                               "note": "This run does not use an E220 radio."},
                                "metrics": outcome["metrics"], "events": outcome["events"]}
                    (RESULTS / "runs" / f"{run_id}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                    raw_path = RESULTS / "raw" / f"{run_id}.jsonl"
                    with raw_path.open("w", encoding="utf-8") as stream:
                        for event in outcome["events"]:
                            stream.write(json.dumps(event, sort_keys=True) + "\n")
                    completed += 1
                    if completed % 20 == 0 or completed == total:
                        print(f"SIMULATION {completed}/{total}")
    full_matrix = (set(strategies) == set(cfg["strategies"]) and set(loss_rates) == set(cfg["loss_rates"]) and
                   set(models) == set(cfg["loss_models"]) and set(seeds) == set(cfg["seeds"]))
    if full_matrix and len(all_runs) == total:
        analyze_runs(all_runs, RESULTS, ROOT / "docs/paper_outline.md",
                     {"source": "HOST_SIMULATION", "physical_radio": False,
                      "application_layer_injection": "deterministic DATA and ACK loss calendars",
                      "synthetic_trace": True, "run_count": len(all_runs)})
    else:
        print(f"PARTIAL MATRIX {len(all_runs)}/{total}; raw runs are saved, summary analysis is deferred")
    return all_runs


def _git_commit() -> str:
    try:
        value = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        return value
    except Exception:
        return "uncommitted"


def _firmware_images_sha256() -> dict[str, str]:
    hashes = {}
    for role in ("sensor", "gateway"):
        image = ROOT / "build" / role / "eventguard.bin"
        if not image.exists():
            return {}
        hashes[role] = hashlib.sha256(image.read_bytes()).hexdigest()
    return hashes


def _load_serial():
    try:
        import serial
        from serial.tools import list_ports
        return serial, list_ports
    except ImportError as exc:
        raise RuntimeError("pyserial is required. Install requirements.txt with the project Python.") from exc


class SerialLogReader:
    def __init__(self, port, baud: int = 115200):
        serial, _ = _load_serial()
        self.port = port
        self.serial = serial.Serial(port, baudrate=baud, timeout=0.05, write_timeout=2)
        self.lines: list[tuple[float, str]] = []
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._read, name=f"serial-{Path(port).name}", daemon=True)
        self.thread.start()

    def _read(self):
        while not self.stop_event.is_set():
            try:
                raw = self.serial.readline()
                if raw:
                    line = raw.decode("utf-8", errors="replace").strip()
                    with self.lock:
                        self.lines.append((time.monotonic(), line))
            except Exception as exc:
                with self.lock:
                    self.lines.append((time.monotonic(), f"HOST_SERIAL_ERROR,{exc!r}"))
                return

    def write(self, line: str):
        self.serial.write((line.rstrip("\r\n") + "\n").encode())
        self.serial.flush()

    def drain(self) -> list[tuple[float, str]]:
        with self.lock:
            lines, self.lines = self.lines, []
        return lines

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1)
        self.serial.close()


def discover_boards(discovery_seconds: float = 12.0) -> tuple[dict, dict[str, SerialLogReader]]:
    serial, list_ports = _load_serial()
    candidates = []
    for item in list_ports.comports():
        device = item.device
        if not ("usbmodem" in device or "usbserial" in device or "wchusbserial" in device
                or device.startswith("/dev/ttyACM") or device.startswith("/dev/ttyUSB")):
            continue
        candidates.append({"device": device, "description": item.description, "hwid": item.hwid})
    if len(candidates) < 2:
        raise RuntimeError(f"Found only {len(candidates)} USB serial candidates; two ESP32-S3 boards are required.")
    readers = {}
    for item in candidates:
        try:
            readers[item["device"]] = SerialLogReader(item["device"])
        except Exception as exc:
            item["open_error"] = str(exc)
    for reader in readers.values():
        reader.write("STATUS")
    time.sleep(discovery_seconds)
    role_candidates = defaultdict(list)
    discovery_lines = {}
    for port, reader in readers.items():
        lines = reader.drain()
        normalized = [line.upper() for _, line in lines]
        discovery_lines[port] = [line for _, line in lines]
        is_sensor = any("ROLE,SENSOR" in line or "S_STATS" in line or re.search(r"\bTX\s*:\s*DATA", line) or line.startswith("TX,DATA") for line in normalized)
        is_gateway = any("ROLE,GATEWAY" in line or "G_STATS" in line or re.search(r"\bRX\s*:\s*DATA", line) or line.startswith("RX,DATA") for line in normalized)
        if is_sensor and not is_gateway:
            role_candidates["sensor"].append(port)
        elif is_gateway and not is_sensor:
            role_candidates["gateway"].append(port)
    # If exactly one device has an identity line in this observation window, the other
    # ESP32-S3 port is its counterpart; this avoids depending on their log intervals.
    identified = role_candidates["sensor"] + role_candidates["gateway"]
    if len(candidates) == 2 and len(identified) == 1 and len(readers) == 2:
        unobserved = next((item["device"] for item in candidates if item["device"] != identified[0]), None)
        if unobserved and unobserved not in identified:
            missing_role = "gateway" if role_candidates["sensor"] else "sensor"
            role_candidates[missing_role].append(unobserved)
    if len(role_candidates["sensor"]) != 1 or len(role_candidates["gateway"]) != 1:
        for reader in readers.values(): reader.close()
        # A firmware image that exited before the terminal connected cannot answer
        # STATUS. Recover only when both attached chips match MAC identities from a
        # prior successful role discovery; never infer roles from port ordering.
        previous_path = RESULTS / "hardware_discovery.json"
        previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
        known_macs = previous.get("chip_macs", {})
        if len(candidates) == 2 and known_macs.get("sensor") and known_macs.get("gateway"):
            observed_macs = {item["device"]: probe_mac(item["device"]) for item in candidates}
            sensor_ports = [port for port, mac in observed_macs.items() if mac.lower() == known_macs["sensor"].lower()]
            gateway_ports = [port for port, mac in observed_macs.items() if mac.lower() == known_macs["gateway"].lower()]
            if len(sensor_ports) == 1 and len(gateway_ports) == 1 and sensor_ports[0] != gateway_ports[0]:
                mapping = {"sensor_port": sensor_ports[0], "gateway_port": gateway_ports[0],
                           "candidates": candidates, "discovery_lines": discovery_lines,
                           "chip_macs": {"sensor": known_macs["sensor"], "gateway": known_macs["gateway"]},
                           "discovery_method": "verified_previous_chip_macs",
                           "identified_at": datetime.now(timezone.utc).isoformat()}
                return mapping, {}
        raise RuntimeError("Automatic role identification failed. Observed USB ports and logs: " + json.dumps(discovery_lines))
    mapping = {"sensor_port": role_candidates["sensor"][0], "gateway_port": role_candidates["gateway"][0],
               "candidates": candidates, "discovery_lines": discovery_lines,
               "identified_at": datetime.now(timezone.utc).isoformat()}
    return mapping, readers


def probe_mac(port: str) -> str:
    idf_venv = Path(os.environ.get("IDF_PYTHON_ENV_PATH", Path.home() / ".espressif/python_env/idf5.4_py3.13_env"))
    py = idf_venv / "bin/python"
    if not py.exists():
        py = Path(sys.executable)
    result = subprocess.run([str(py), "-m", "esptool", "--port", port, "read_mac"], text=True, capture_output=True, timeout=15)
    match = re.search(r"MAC:\s*([0-9a-f:]{17})", result.stdout + result.stderr, re.I)
    if result.returncode or not match:
        raise RuntimeError(f"Could not read ESP32 MAC on {port}: {result.stdout}{result.stderr}")
    return match.group(1).lower()


def _idf_environment() -> tuple[str, dict[str, str]]:
    idf_path = Path(os.environ.get("IDF_PATH", Path.home() / "esp/esp-idf"))
    tools_path = Path(os.environ.get("IDF_TOOLS_PATH", Path.home() / ".espressif"))
    configured_python = os.environ.get("IDF_PYTHON_ENV_PATH")
    if configured_python:
        python_path = Path(configured_python)
    else:
        envs = sorted((tools_path / "python_env").glob("idf5.4_py*_env"), reverse=True)
        python_path = next((path for path in envs if (path / "bin/python").exists()), tools_path / "python_env/idf5.4_py3.13_env")
    py = python_path / "bin/python"
    if not idf_path.exists() or not py.exists():
        raise RuntimeError("ESP-IDF v5.4.4 and its Python environment are required for hardware runs.")
    initial = dict(os.environ)
    initial["PATH"] = str(python_path / "bin") + os.pathsep + initial.get("PATH", "")
    initial.update({"IDF_PATH": str(idf_path), "IDF_TOOLS_PATH": str(tools_path),
                    "IDF_PYTHON_ENV_PATH": str(python_path)})
    command = f"source '{idf_path / 'export.sh'}' >/dev/null 2>&1 && /usr/bin/env -0"
    exported = subprocess.run(["/bin/bash", "-c", command], env=initial, capture_output=True, check=True).stdout
    env = dict(os.environ)
    for entry in exported.split(b"\0"):
        if entry and b"=" in entry:
            key, value = entry.split(b"=", 1)
            env[key.decode()] = value.decode(errors="replace")
    env["IDF_PATH"] = str(idf_path)
    env["IDF_TOOLS_PATH"] = str(tools_path)
    env["IDF_PYTHON_ENV_PATH"] = str(python_path)
    return str(py), env


def _set_flash_size_choice(sdkconfig: Path, flash_size_mb: int) -> None:
    """Update only ESP-IDF's persisted flash-size choice, preserving other user settings."""
    if not sdkconfig.exists():
        return
    sizes = (1, 2, 4, 8, 16, 32, 64, 128)
    if flash_size_mb not in sizes:
        raise ValueError(f"Unsupported ESP-IDF flash size: {flash_size_mb} MB")
    lines = sdkconfig.read_text(encoding="utf-8", errors="replace").splitlines()
    found = False
    found_selected = False
    for index, line in enumerate(lines):
        match = re.match(r"(?:# )?CONFIG_ESPTOOLPY_FLASHSIZE_(\d+)MB(?:=y| is not set)$", line)
        if match:
            found = True
            selected = int(match.group(1)) == flash_size_mb
            found_selected |= selected
            lines[index] = (f"CONFIG_ESPTOOLPY_FLASHSIZE_{flash_size_mb}MB=y" if selected
                            else f"# CONFIG_ESPTOOLPY_FLASHSIZE_{match.group(1)}MB is not set")
        elif line.startswith("CONFIG_ESPTOOLPY_FLASHSIZE="):
            lines[index] = f'CONFIG_ESPTOOLPY_FLASHSIZE="{flash_size_mb}MB"'
    if not found:
        lines.extend(f"CONFIG_ESPTOOLPY_FLASHSIZE_{size}MB=y" if size == flash_size_mb
                     else f"# CONFIG_ESPTOOLPY_FLASHSIZE_{size}MB is not set" for size in sizes)
        found_selected = True
    if not found_selected:
        lines.append(f"CONFIG_ESPTOOLPY_FLASHSIZE_{flash_size_mb}MB=y")
        lines = [line for line in lines if line != f'# CONFIG_ESPTOOLPY_FLASHSIZE_{flash_size_mb}MB is not set']
        lines.append(f'CONFIG_ESPTOOLPY_FLASHSIZE="{flash_size_mb}MB"')
    sdkconfig.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _apply_sdkconfig_defaults(sdkconfig: Path, defaults_text: str) -> None:
    """Refresh project-owned sdkconfig keys while retaining unrelated IDF choices."""
    if not sdkconfig.exists():
        return
    lines = sdkconfig.read_text(encoding="utf-8", errors="replace").splitlines()
    for entry in defaults_text.splitlines():
        if not entry.startswith("CONFIG_") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        if key.startswith("CONFIG_ESPTOOLPY_FLASHSIZE"):
            continue  # The flash-size choice and its derived string are handled separately.
        replacement = f"# {key} is not set" if value == "n" else entry
        pattern = re.compile(rf"(?:# )?{re.escape(key)}(?:=.*| is not set)$")
        matches = [index for index, line in enumerate(lines) if pattern.fullmatch(line)]
        if matches:
            lines[matches[0]] = replacement
            for index in reversed(matches[1:]):
                del lines[index]
        else:
            lines.append(replacement)
    sdkconfig.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_firmware(config: dict | None = None) -> dict[str, Path]:
    config = config or load_config()
    py, env = _idf_environment()
    idf = Path(env["IDF_PATH"]) / "tools/idf.py"
    project = ROOT / "firmware"
    outputs = {}
    for role in ("sensor", "gateway"):
        build_dir = ROOT / "build" / role
        sdkconfig = ROOT / "build" / f"sdkconfig.{role}"
        e220 = config["e220"]
        sensor_pins = config["sensor_pins"]
        defaults = ROOT / "build" / f"sdkconfig.defaults.{role}"
        role_line = ("CONFIG_EG_ROLE_SENSOR=y\nCONFIG_EG_ROLE_GATEWAY=n" if role == "sensor"
                     else "CONFIG_EG_ROLE_SENSOR=n\nCONFIG_EG_ROLE_GATEWAY=y")
        generated_defaults = ["CONFIG_IDF_TARGET=\"esp32s3\"", role_line, "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y",
            f"CONFIG_ESPTOOLPY_FLASHSIZE_{int(config.get('flash_size_mb', 16))}MB=y",
            f"CONFIG_EG_NODE_ID={config['node_id']}", f"CONFIG_EG_GATEWAY_ID={config['gateway_id']}",
            f"CONFIG_EG_E220_UART_NUM={e220['uart_num']}", f"CONFIG_EG_E220_TX_GPIO={e220['tx_gpio']}",
            f"CONFIG_EG_E220_RX_GPIO={e220['rx_gpio']}", f"CONFIG_EG_E220_AUX_GPIO={e220['aux_gpio']}",
            f"CONFIG_EG_E220_M0_GPIO={e220['m0_gpio']}", f"CONFIG_EG_E220_M1_GPIO={e220['m1_gpio']}",
            f"CONFIG_EG_E220_BAUD={e220['baud']}", f"CONFIG_EG_ACK_TIMEOUT_MS={config['ack_timeout_ms']}",
            f"CONFIG_EG_E220_ADDRESS={e220['address']}", f"CONFIG_EG_E220_REG0={e220['reg0']}",
            f"CONFIG_EG_E220_CHANNEL={e220['channel']}",
            f"CONFIG_EG_I2C_SDA_GPIO={sensor_pins['i2c_sda_gpio']}", f"CONFIG_EG_I2C_SCL_GPIO={sensor_pins['i2c_scl_gpio']}",
            f"CONFIG_EG_SOIL_ADC_GPIO={sensor_pins['soil_adc_gpio']}",
            f"CONFIG_EG_MAX_REDUNDANCY={config['max_redundancy']}", f"CONFIG_EG_FIXED_REDUNDANCY={config['fixed_redundancy']}"]
        defaults_text = "\n".join(generated_defaults) + "\n"
        if not defaults.exists() or defaults.read_text(encoding="utf-8") != defaults_text:
            defaults.write_text(defaults_text, encoding="utf-8")
        _apply_sdkconfig_defaults(sdkconfig, defaults_text)
        _set_flash_size_choice(sdkconfig, int(config.get("flash_size_mb", 16)))
        base = [py, str(idf), "-C", str(project), "-B", str(build_dir), "-D", f"SDKCONFIG={sdkconfig}",
                "-D", f"SDKCONFIG_DEFAULTS={defaults}"]
        target_marker = 'CONFIG_IDF_TARGET="esp32s3"'
        if not sdkconfig.exists() or target_marker not in sdkconfig.read_text(encoding="utf-8", errors="ignore"):
            target = subprocess.run(base + ["set-target", "esp32s3"], cwd=ROOT, env=env, text=True, capture_output=True)
            if target.returncode:
                raise RuntimeError(f"Could not configure ESP32-S3 build for {role}: {target.stdout}{target.stderr}")
        cmd = base + ["build"]
        print("BUILD", role)
        result = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)
        (RESULTS / "raw" / f"build_{role}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"ESP-IDF build failed for {role}; see results/raw/build_{role}.log\n" + result.stderr[-3000:])
        outputs[role] = build_dir / "eventguard.bin"
    size_summary = {}
    for role in ("sensor", "gateway"):
        build_dir = ROOT / "build" / role
        sdkconfig = ROOT / "build" / f"sdkconfig.{role}"
        defaults = ROOT / "build" / f"sdkconfig.defaults.{role}"
        cmd = [py, str(idf), "-C", str(project), "-B", str(build_dir), "-D", f"SDKCONFIG={sdkconfig}",
               "-D", f"SDKCONFIG_DEFAULTS={defaults}", "size"]
        result = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)
        size_text = result.stdout + result.stderr
        (RESULTS / "raw" / f"size_{role}.log").write_text(size_text, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"ESP-IDF size report failed for {role}: {size_text[-3000:]}")
        size_summary[role] = {"application_binary_bytes": outputs[role].stat().st_size, "idf_size_report": size_text}
    (RESULTS / "firmware_size.json").write_text(json.dumps(size_summary, indent=2), encoding="utf-8")
    return outputs


def flash_firmware(outputs: dict[str, Path], mapping: dict, config: dict | None = None) -> None:
    config = config or load_config()
    py, env = _idf_environment()
    idf = Path(env["IDF_PATH"]) / "tools/idf.py"
    project = ROOT / "firmware"
    for role, port_key in (("sensor", "sensor_port"), ("gateway", "gateway_port")):
        build_dir = ROOT / "build" / role
        sdkconfig = ROOT / "build" / f"sdkconfig.{role}"
        defaults = ROOT / "build" / f"sdkconfig.defaults.{role}"
        cmd = [py, str(idf), "-C", str(project), "-B", str(build_dir), "-D", f"SDKCONFIG={sdkconfig}",
               "-D", f"SDKCONFIG_DEFAULTS={defaults}", "-p", mapping[port_key], "flash"]
        print("FLASH", role, mapping[port_key])
        result = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
        (RESULTS / "raw" / f"flash_{role}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Firmware flash failed for {role}; see results/raw/flash_{role}.log\n" + result.stderr[-3000:])


def _wait_for(reader: SerialLogReader, token: str, timeout: float = 8.0) -> list[tuple[float, str]]:
    end = time.monotonic() + timeout
    found = []
    while time.monotonic() < end:
        batch = reader.drain()
        found.extend(batch)
        if any(token in line for _, line in batch):
            return found
        time.sleep(.03)
    raise TimeoutError(f"Timed out waiting for {token}; observed {found[-20:]}")


def _wait_for_ack(reader: SerialLogReader, token: str, timeout: float = 8.0) -> list[tuple[float, str]]:
    end = time.monotonic() + timeout
    found = []
    while time.monotonic() < end:
        batch = reader.drain()
        found.extend(batch)
        if any(line.startswith("ERR,") for _, line in batch):
            errors = [line for _, line in batch if line.startswith("ERR,")]
            raise RuntimeError(f"Firmware rejected command while waiting for {token}: {errors}")
        if any(token in line for _, line in batch):
            return found
        time.sleep(.02)
    raise TimeoutError(f"Timed out waiting for {token}; observed {found[-20:]}")


def _wait_for_radio_ready(reader: SerialLogReader, initial: list[tuple[float, str]], timeout: float = 8.0):
    result = list(initial)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        lines = [line for _, line in result]
        if any("E220_ERROR" in line for line in lines):
            raise RuntimeError(f"E220 initialization failed: {[line for line in lines if 'E220_ERROR' in line]}")
        if any("E220_READY" in line for line in lines):
            return result
        result.extend(reader.drain())
        time.sleep(.02)
    raise TimeoutError(f"Timed out waiting for E220_READY; observed {[line for _, line in result[-20:]]}")


def _truth_code(value: GroundTruth) -> int:
    return {GroundTruth.NORMAL: 0, GroundTruth.IMPORTANT: 1, GroundTruth.CRITICAL: 2}[value]


def _parse_metrics(samples, event_rows, sensor_lines, gateway_lines, started: float, ended: float,
                   config: RunConfig, trace_hash: str) -> dict:
    trace_by_id = {s.sample_id: s for s in samples}
    delivered = set()
    duplicate_packets = 0
    physical_data_tx = physical_data_rx = ack_count = ack_rx = 0
    crc_errors = invalid_packets = data_drops = ack_drops = sequence_gaps = out_of_order = 0
    tx_times = {}
    delivery_times = {}
    for now, line in sensor_lines:
        parts = line.split(",")
        if parts[0] == "TX_BEGIN" and len(parts) >= 4:
            try:
                tx_times.setdefault(int(parts[1]), now)
            except ValueError: pass
        elif parts[0] == "TX" and len(parts) >= 5:
            try:
                sample_id, copy_index = int(parts[1]), int(parts[3])
                physical_data_tx += 1
                tx_times.setdefault(sample_id, now)
            except ValueError: pass
        elif parts[0] == "ACK" and len(parts) >= 5:
            ack_rx += 1
            ack_drops += parts[4] == "DROP"
        elif parts[0] == "ERR" and len(parts) > 1:
            if parts[1] == "CRC": crc_errors += 1
            elif parts[1] == "PACKET": invalid_packets += 1
    for now, line in gateway_lines:
        parts = line.split(",")
        if parts[0] == "RX" and len(parts) >= 5:
            physical_data_rx += 1
            if parts[4] == "DUP": duplicate_packets += 1
        elif parts[0] == "DELIVER" and len(parts) >= 3:
            try:
                sample_id = int(parts[1]); delivered.add(sample_id); delivery_times.setdefault(sample_id, now)
            except ValueError: pass
        elif parts[0] == "ACK_TX": ack_count += 1
        elif parts[0] == "DROP" and len(parts) > 1 and parts[1] == "DATA":
            data_drops += 1
            physical_data_rx += 1
        elif parts[0] == "GAP" and len(parts) >= 5:
            try: sequence_gaps += int(parts[4])
            except ValueError: pass
        elif parts[0] == "ORDER": out_of_order += 1
        elif parts[0] == "ERR" and len(parts) > 1:
            if parts[1] == "CRC": crc_errors += 1; physical_data_rx += 1
            elif parts[1] == "PACKET": invalid_packets += 1
    totals = {label: sum(sample.truth.value == label for sample in samples) for label in ("NORMAL", "IMPORTANT", "CRITICAL")}
    delivered_by_label = {label: sum(s.sample_id in delivered and s.truth.value == label for s in samples) for label in totals}
    ratio = {label: delivered_by_label[label] / totals[label] if totals[label] else 0.0 for label in totals}
    latencies = [max(0.0, (delivery_times[sid] - tx_times[sid]) * 1000) for sid in delivered if sid in delivery_times and sid in tx_times]
    latencies.sort()
    def pct(q):
        if not latencies: return 0.0
        return latencies[min(len(latencies) - 1, round((len(latencies) - 1) * q))]
    data_bytes, ack_bytes = physical_data_tx * DATA_FRAME_SIZE, ack_count * ACK_FRAME_SIZE
    critical_delivered = delivered_by_label["CRITICAL"]
    metrics = {
        "logical_packets": len(samples), "delivered_packets": len(delivered),
        "lost_logical_packets": len(samples) - len(delivered), "sequence_gaps": sequence_gaps,
        "out_of_order_packets": out_of_order,
        "overall_delivery_ratio": len(delivered) / len(samples), "critical_event_delivery_ratio": ratio["CRITICAL"],
        "important_event_delivery_ratio": ratio["IMPORTANT"], "normal_delivery_ratio": ratio["NORMAL"],
        "critical_event_miss_rate": 1 - ratio["CRITICAL"], "physical_data_transmissions": physical_data_tx,
        "physical_data_received": physical_data_rx, "total_bytes_transmitted": data_bytes + ack_bytes,
        "data_bytes_transmitted": data_bytes, "ack_bytes_transmitted": ack_bytes,
        "redundancy_overhead": physical_data_tx / len(samples) - 1,
        "ack_count": ack_count, "ack_received": ack_rx,
        "mean_delivery_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "p50_delivery_latency_ms": pct(.50), "p95_delivery_latency_ms": pct(.95), "p99_delivery_latency_ms": pct(.99),
        "retransmissions": max(0, physical_data_tx - len(samples)), "duplicate_packets": duplicate_packets,
        "crc_errors": crc_errors, "invalid_packets": invalid_packets, "data_injected_drops": data_drops,
        "ack_injected_drops": ack_drops,
        "communication_cost_per_delivered_critical_event": (data_bytes + ack_bytes) / critical_delivered if critical_delivered else None,
        "truth_counts": totals, "truth_delivered": delivered_by_label,
    }
    return metrics


def run_hardware(strategies=None, loss_rates=None, models=None, seeds=None, samples_per_phase=None,
                 skip_build=False, dry_run=False, burst_length=None) -> list[dict]:
    cfg = load_config()
    if burst_length is not None: cfg["burst_length"] = burst_length
    if samples_per_phase is not None: cfg["trace_samples_per_phase"] = int(samples_per_phase)
    _ensure_dirs()
    mapping, readers = discover_boards()
    for reader in readers.values(): reader.close()
    try:
        if "chip_macs" not in mapping:
            mapping["chip_macs"] = {"sensor": probe_mac(mapping["sensor_port"]), "gateway": probe_mac(mapping["gateway_port"])}
        (RESULTS / "hardware_discovery.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
        print("IDENTIFIED", mapping["sensor_port"], "Sensor", mapping["chip_macs"]["sensor"])
        print("IDENTIFIED", mapping["gateway_port"], "Gateway", mapping["chip_macs"]["gateway"])
    finally:
        pass
    if dry_run:
        return []
    if not skip_build:
        outputs = build_firmware(cfg)
        flash_firmware(outputs, mapping, cfg)
    sensor = SerialLogReader(mapping["sensor_port"])
    gateway = SerialLogReader(mapping["gateway_port"])
    all_runs = []
    try:
        sensor.write("STATUS"); gateway.write("STATUS")
        s_boot = _wait_for(sensor, "ROLE,SENSOR", timeout=12)
        g_boot = _wait_for(gateway, "ROLE,GATEWAY", timeout=12)
        s_boot = _wait_for_radio_ready(sensor, s_boot)
        g_boot = _wait_for_radio_ready(gateway, g_boot)
        boot_logs = {"sensor": [x[1] for x in s_boot], "gateway": [x[1] for x in g_boot]}
        (RESULTS / "raw" / "firmware_boot.json").write_text(json.dumps(boot_logs, indent=2), encoding="utf-8")
        strategies = strategies or cfg["strategies"]
        loss_rates = cfg["loss_rates"] if loss_rates is None else loss_rates
        models = models or cfg["loss_models"]
        seeds = cfg["seeds"] if seeds is None else seeds
        samples_per_phase = cfg["trace_samples_per_phase"] if samples_per_phase is None else int(samples_per_phase)
        cfg["trace_samples_per_phase"] = samples_per_phase
        firmware_hashes = _firmware_images_sha256()
        total = len(strategies) * len(loss_rates) * len(models) * len(seeds)
        complete = 0
        for strategy in strategies:
            for loss_rate in loss_rates:
                for model in models:
                    for seed in seeds:
                        run_cfg = _run_config(cfg, strategy, loss_rate, model, seed)
                        samples = generate_trace(seed, samples_per_phase)
                        trace_hash = trace_fingerprint(samples)
                        run_id = _run_id(run_cfg) + "_e220"
                        manifest_path = RESULTS / "runs" / f"{run_id}.json"
                        raw_path = RESULTS / "raw" / f"{run_id}.json"
                        if manifest_path.exists() and raw_path.exists() and firmware_hashes:
                            try:
                                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
                                previous_run = previous["run"]
                                matches = (previous_run.get("run_id") == run_id and
                                    previous_run.get("strategy") == run_cfg.strategy.value and
                                    previous_run.get("loss_rate") == run_cfg.loss_rate and
                                    previous_run.get("loss_model") == run_cfg.loss_model.value and
                                    previous_run.get("seed") == run_cfg.seed and
                                    previous_run.get("burst_length") == run_cfg.burst_length and
                                    previous_run.get("trace_sha256") == trace_hash and
                                    previous_run.get("sample_count") == len(samples) and
                                    previous_run.get("firmware_images_sha256") == firmware_hashes and
                                    previous_run.get("sensor_mac") == mapping["chip_macs"]["sensor"] and
                                    previous_run.get("gateway_mac") == mapping["chip_macs"]["gateway"] and
                                    previous_run.get("source") == "REAL_E220_WITH_APPLICATION_LAYER_INJECTION")
                                if matches:
                                    all_runs.append(previous_run)
                                    complete += 1
                                    print(f"RESUME {complete}/{total} {run_id} critical={previous_run['critical_event_delivery_ratio']:.3f}")
                                    continue
                            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                                pass
                        sensor.drain(); gateway.drain()
                        sensor.write(_config_command(run_cfg))
                        gateway.write(_config_command(run_cfg))
                        _wait_for_ack(sensor, "CONFIGURED", timeout=8)
                        _wait_for_ack(gateway, "CONFIGURED", timeout=8)
                        gateway.write(f"TRACE_COUNT,{len(samples)}")
                        _wait_for_ack(gateway, "TRACE_COUNT", timeout=5)
                        gateway.write("ARM")
                        gateway_log_lines = _wait_for_ack(gateway, "ARMED", timeout=5)
                        sensor.write(f"TRACE_BEGIN,{len(samples)}")
                        _wait_for_ack(sensor, "TRACE_BEGIN", timeout=5)
                        for sample in samples:
                            sensor.write(f"S,{sample.sample_id},{sample.timestamp_ms},{sample.temperature:.2f},{sample.humidity:.2f},{sample.light:.1f},{sample.soil_moisture:.1f},{_truth_code(sample.truth)}")
                            time.sleep(.003)
                        sensor.write("TRACE_END")
                        _wait_for(sensor, "TRACE_READY", timeout=10)
                        start_time = datetime.now(timezone.utc).isoformat()
                        start = time.monotonic()
                        sensor.write("START")
                        sensor_end_lines = []
                        per_copy_timeout = cfg["ack_timeout_ms"] / 1000 + 1.0
                        timeout = max(60, len(samples) * cfg["max_redundancy"] * per_copy_timeout + 5)
                        deadline = time.monotonic() + timeout
                        while time.monotonic() < deadline:
                            sensor_batch, gateway_batch = sensor.drain(), gateway.drain()
                            sensor_end_lines.extend(sensor_batch)
                            gateway_log_lines.extend(gateway_batch)
                            if any("END," in line for _, line in sensor_batch):
                                break
                            time.sleep(.01)
                        else:
                            raise TimeoutError(f"Run timed out: {_run_id(run_cfg)}")
                        sensor_end_lines.extend(sensor.drain())
                        end = time.monotonic()
                        gateway.write("ENDRUN")
                        gateway_log_lines.extend(_wait_for(gateway, "END,", timeout=5))
                        sensor_lines = sensor_end_lines
                        gateway_lines = gateway_log_lines
                        run_id = _run_id(run_cfg) + "_e220"
                        raw_doc = {"run_id": run_id, "sensor_port": mapping["sensor_port"], "gateway_port": mapping["gateway_port"],
                                   "sensor": [{"host_monotonic": t, "line": line} for t, line in sensor_lines],
                                   "gateway": [{"host_monotonic": t, "line": line} for t, line in gateway_lines]}
                        (RESULTS / "raw" / f"{run_id}.json").write_text(json.dumps(raw_doc, indent=2), encoding="utf-8")
                        metrics = _parse_metrics(samples, [], sensor_lines, gateway_lines, start, end, run_cfg, trace_hash)
                        run = {"run_id": run_id, "strategy": run_cfg.strategy.value, "loss_rate": run_cfg.loss_rate,
                               "loss_model": run_cfg.loss_model.value, "seed": run_cfg.seed, "trace_sha256": trace_hash,
                               "burst_length": run_cfg.burst_length,
                               "source": "REAL_E220_WITH_APPLICATION_LAYER_INJECTION", "radio": "E220-400T22D",
                               "sample_count": len(samples), "start_time": start_time, "duration_s": end - start,
                               "firmware_commit": _git_commit(), "sensor_mac": mapping["chip_macs"]["sensor"],
                               "gateway_mac": mapping["chip_macs"]["gateway"],
                               "firmware_images_sha256": firmware_hashes, **metrics}
                        all_runs.append(run)
                        manifest = {"run": run, "trace": [s.as_dict() for s in samples], "config": cfg,
                                    "provenance": {"source": run["source"], "physical_radio": True,
                                                   "application_layer_injection": True, "synthetic_trace": True,
                                                   "hardware": mapping}}
                        (RESULTS / "runs" / f"{run_id}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                        complete += 1
                        print(f"HARDWARE {complete}/{total} {run_id} critical={metrics['critical_event_delivery_ratio']:.3f} bytes={metrics['total_bytes_transmitted']}")
        full_matrix = (set(strategies) == set(cfg["strategies"]) and set(loss_rates) == set(cfg["loss_rates"]) and
                       set(models) == set(cfg["loss_models"]) and set(seeds) == set(cfg["seeds"]))
        if full_matrix and len(all_runs) == total:
            analyze_runs(all_runs, RESULTS, ROOT / "docs/paper_outline.md",
                         {"source": "REAL_E220_WITH_APPLICATION_LAYER_INJECTION", "physical_radio": True,
                          "application_layer_injection": "deterministic DATA and ACK drops after radio reception",
                          "synthetic_trace": True, "run_count": len(all_runs),
                          "e220_profile": cfg["e220"], "hardware": mapping})
        else:
            print(f"PARTIAL MATRIX {len(all_runs)}/{total}; raw runs are saved, summary analysis is deferred")
    finally:
        sensor.close(); gateway.close()
    return all_runs
