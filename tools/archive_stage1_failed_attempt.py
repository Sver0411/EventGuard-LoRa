#!/usr/bin/env python3
"""Archive a completed-but-invalid Stage 1 run before an explicitly authorized retry."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counters(line: str) -> list[int]:
    return [int(value) for value in line.split(",")[1:]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--output-dir", type=Path, default=Path("results/hardware_validation_v2_policy_v2"))
    args = parser.parse_args()
    output = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    run_path = output / "runs/stage1" / f"{args.run_id}.json"
    raw_path = output / "raw/stage1" / f"{args.run_id}.json"
    stage_manifest_path = output / "stage1/stage1_manifest.json"
    study_manifest_path = output / "hardware_manifest.json"
    if args.run_id != "random_budget_burst_sample_30_seed36":
        raise SystemExit("This retry authorization is scoped to the Stage 1 run 93 capture failure only")
    if not all(path.is_file() for path in (run_path, raw_path, stage_manifest_path, study_manifest_path)):
        raise SystemExit("Cannot archive: Stage 1 run/raw/study manifests are incomplete")

    run = json.loads(run_path.read_text(encoding="utf-8"))
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    stage_manifest = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
    study = json.loads(study_manifest_path.read_text(encoding="utf-8"))
    if run.get("status") != "failed" or sha(raw_path) != run.get("raw_sha256"):
        raise SystemExit("Cannot archive: run is not the saved FAILED attempt or its raw hash is invalid")
    if stage_manifest.get("status") != "FAILED" or stage_manifest.get("failed_run_id") != args.run_id:
        raise SystemExit("Cannot archive: Stage 1 manifest does not identify this as the current failed run")
    if run.get("firmware_images_sha256") != {
            "sensor": "b3cecd078351cc6fe659b0087db05a67a4ca35ab610125a0f0381887281221b4",
            "gateway": "6f69dffed65e2636b371401db45a483f951189e029b538c2f587d331428936ac"}:
        raise SystemExit("Cannot archive: failed run firmware does not match the frozen Stage 1 pair")

    sensor_end = [row["line"] for row in raw["sensor"] if row["line"].startswith("END,")]
    gateway_end = [row["line"] for row in raw["gateway"] if row["line"].startswith("END,")]
    sensor_diag = [row["line"] for row in raw["sensor"] if row["line"].startswith("UART_DIAG,")]
    gateway_diag = [row["line"] for row in raw["gateway"] if row["line"].startswith("UART_DIAG,")]
    if len(sensor_end) != 1 or len(gateway_end) != 1 or len(sensor_diag) != 1 or len(gateway_diag) != 1:
        raise SystemExit("Cannot archive: both complete END and UART_DIAG records are required")
    st, gt = counters(sensor_end[0]), counters(gateway_end[0])

    def diag_counts(line: str) -> dict[str, int]:
        fields = line.split(",")
        return dict(zip(fields[1::2], (int(value) for value in fields[2::2])))

    sd, gd = diag_counts(sensor_diag[0]), diag_counts(gateway_diag[0])
    if st[:1] != [54] or st[1] != 145 or st[3] != 0 or gt[0] != 54:
        raise SystemExit("Cannot archive: run did not complete all 54 samples without a firmware stop")
    if sd.get("frames_completed") != 103 or gd.get("frames_completed") != 145 or \
            sd.get("crc_failures") or gd.get("crc_failures") or sd.get("parser_resyncs") or gd.get("parser_resyncs"):
        raise SystemExit("Cannot archive: internal E220 counters do not support a console-capture-only failure")

    sensor_text = [row["line"] for row in raw["sensor"]]
    gateway_text = [row["line"] for row in raw["gateway"]]
    sensor_tx = sum(line.startswith("TX,") for line in sensor_text)
    sensor_ok_ack = sum(line.startswith("ACK,") and line.endswith(",OK") for line in sensor_text)
    sensor_sample_events = sum(line.startswith("EVT,") for line in sensor_text)
    sensor_sample_summaries = sum(line.startswith("SAMPLE,") for line in sensor_text)
    gateway_frames = sum(line.startswith("D_RX_FRAME_COMPLETE,") for line in gateway_text)
    false_ack_gate = any("Sensor console ACK capture" in issue for issue in run.get("issues", []))
    if (sensor_tx != st[1] or sensor_ok_ack != st[2] or
            sensor_sample_events != 54 or sensor_sample_summaries != 54 or
            gateway_frames != gd.get("frames_completed")):
        raise SystemExit("Cannot classify this attempt as an ACK-semantics false positive: console lines are incomplete")

    history_path = output / "stage1/retry_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {"attempts": []}
    attempt_number = len(history.get("attempts", [])) + 1
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = output / "stage1/retry_history" / f"{args.run_id}_attempt_{attempt_number:02d}_{timestamp}"
    if archive.exists():
        raise SystemExit(f"Refusing to overwrite retry archive: {archive}")
    archive.mkdir(parents=True)
    shutil.copy2(raw_path, archive / "raw.json")
    shutil.copy2(run_path, archive / "run_manifest.json")
    shutil.copy2(stage_manifest_path, archive / "stage1_manifest_failed.json")
    shutil.copy2(study_manifest_path, archive / "hardware_manifest_failed.json")
    archive_manifest = {
        "run_id": args.run_id,
        "attempt": attempt_number,
        "archived_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_sha256": sha(archive / "raw.json"),
        "run_manifest_sha256": sha(archive / "run_manifest.json"),
        "firmware_images_sha256": run["firmware_images_sha256"],
        "classification": ("host validation false positive: total physical ACK receptions were compared with accepted ACKs"
                           if false_ack_gate else
                           "console log capture complete; run invalidated by a host validation check"),
        "evidence": {
            "sensor_end": sensor_end[0], "gateway_end": gateway_end[0],
            "sensor_uart_diag": sensor_diag[0], "gateway_uart_diag": gateway_diag[0],
            "sensor_console_tx_lines": sensor_tx,
            "sensor_console_ack_ok_lines": sensor_ok_ack,
            "sensor_console_ack_drop_lines": sum(line.startswith("ACK,") and line.endswith(",DROP") for line in sensor_text),
            "sensor_console_sample_event_lines": sensor_sample_events,
            "sensor_console_sample_summary_lines": sensor_sample_summaries,
            "gateway_console_rx_and_drop_lines": sum(
                row["line"].startswith(("RX,", "DROP,DATA,")) for row in raw["gateway"]),
            "gateway_console_frame_complete_lines": gateway_frames,
            "sensor_uart_completed_ack_frames": sd.get("frames_completed"),
            "sensor_firmware_accepted_ack_count": st[2],
            "sensor_console_capture": raw.get("serial_capture", {}).get("sensor", {}),
            "gateway_console_capture": raw.get("serial_capture", {}).get("gateway", {}),
        },
        "retry_authorization": "User explicitly requested: rerun run 93 and continue the Stage 1 sequence.",
    }
    (archive / "archive_manifest.json").write_text(
        json.dumps(archive_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    history["attempts"].append({"run_id": args.run_id, "attempt": attempt_number,
                                "archive": str(archive.relative_to(output)),
                                "archive_manifest_sha256": sha(archive / "archive_manifest.json")})
    history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stage_manifest["retry_history"] = "retry_history.json"
    stage_manifest["retry_attempt_count"] = len(history["attempts"])
    stage_manifest["last_retry_archive"] = str(archive.relative_to(output))
    stage_manifest_path.write_text(json.dumps(stage_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    study.setdefault("stages", {}).setdefault("stage1", {})["retry_history"] = "stage1/retry_history.json"
    study["stages"]["stage1"]["retry_attempt_count"] = len(history["attempts"])
    study_manifest_path.write_text(json.dumps(study, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    raw_path.unlink()
    run_path.unlink()
    print(json.dumps({"status": "FAILED_ATTEMPT_ARCHIVED_FOR_EXPLICIT_RETRY",
                      "run_id": args.run_id, "archive": str(archive),
                      "raw_sha256": archive_manifest["raw_sha256"],
                      "sensor_end": sensor_end[0], "gateway_end": gateway_end[0]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
