#!/usr/bin/env python3
"""Seed a new Stage 1 output tree while retaining the failed v2 study unchanged."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from eventguard.hardware_analysis import load_stage
from eventguard.hardware_validation import STAGE1_FIRMWARE_SHA256, sha


def main() -> int:
    source = PROJECT_ROOT / "results/hardware_validation_v2"
    target = PROJECT_ROOT / "results/hardware_validation_v2_policy_v2"
    if target.exists():
        raise SystemExit(f"Refusing to overwrite restart study directory: {target}")
    rows, failed, missing = load_stage("smoke", source)
    if len(rows) != 12 or failed or missing:
        raise SystemExit("Restart blocked: original v2 smoke raw logs/manifests are not 12/12 complete and hash-valid")
    source_study = json.loads((source / "hardware_manifest.json").read_text(encoding="utf-8"))
    if source_study.get("firmware_images_sha256") != STAGE1_FIRMWARE_SHA256:
        raise SystemExit("Restart blocked: source study firmware pair differs from recovered v2 smoke pair")
    diagnostic_root = source / "end_timeout_diagnostic_v1"
    diagnostic = json.loads((diagnostic_root / "diagnostic_manifest.json").read_text(encoding="utf-8"))
    if diagnostic.get("status") != "complete" or diagnostic.get("sample_records_completed") != 54 or \
            diagnostic.get("sensor_end_lines") != ["END,54,144,76,0"] or \
            diagnostic.get("gateway_end_lines") != ["END,54,111,54,57,111,0,33,0,0"]:
        raise SystemExit("Restart blocked: exact-condition diagnostic did not complete 54 samples with both END counters")
    if diagnostic.get("serial_errors"):
        raise SystemExit("Restart blocked: engineering diagnostic captured a serial/UART/parser error")

    target.mkdir(parents=True)
    for directory in ("raw/smoke", "runs/smoke"):
        src, dst = source / directory, target / directory
        dst.mkdir(parents=True, exist_ok=True)
        for path in src.glob("*.json"):
            shutil.copy2(path, dst / path.name)
    shutil.copy2(source / "hardware_manifest.json", target / "hardware_manifest.json")
    policy_src = source / "stage1_policy_amendment_v1.md"
    policy_dst = target / "stage1" / "stage1_policy_amendment_v1.md"
    policy_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(policy_src, policy_dst)
    restart = {"restart_study": "hardware_validation_v2_policy_v2",
        "restart_from": "results/hardware_validation_v2",
        "reason": "Preserve the first STARTed END-timeout attempt and apply the versioned physical-noise gate to a fresh Stage 1 dataset.",
        "old_failed_attempt": "uniform_budget_random_copy_20_seed31",
        "old_failed_attempt_raw_sha256": json.loads((source / "runs/stage1/uniform_budget_random_copy_20_seed31.json").read_text(encoding="utf-8"))["raw_sha256"],
        "policy_path": "stage1/stage1_policy_amendment_v1.md",
        "policy_sha256": sha(policy_dst),
        "diagnostic_manifest": "results/hardware_validation_v2/end_timeout_diagnostic_v1/diagnostic_manifest.json",
        "diagnostic_status": diagnostic["status"],
        "copied_smoke_runs": 12,
        "firmware_images_sha256": STAGE1_FIRMWARE_SHA256,
        "formal_stage1_started": False}
    (target / "restart_manifest.json").write_text(json.dumps(restart, indent=2) + "\n", encoding="utf-8")
    study_doc = (target / "hardware_manifest.json")
    study_payload = json.loads(study_doc.read_text(encoding="utf-8"))
    study_payload["restart_context"] = restart
    study_doc.write_text(json.dumps(study_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "READY_FOR_STAGE1_PREFLIGHT", "output_dir": str(target),
                      "smoke_runs_copied": 12, "physical_noise_policy_sha256": restart["policy_sha256"],
                      "firmware_images_sha256": STAGE1_FIRMWARE_SHA256}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
