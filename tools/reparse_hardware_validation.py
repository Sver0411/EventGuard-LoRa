#!/usr/bin/env python3
"""Recompute parser-derived metrics from immutable E220 raw logs after a parser fix."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eventguard.hardware_validation import OUT, atomic_json, plan, _parse_samples
from eventguard.host import _parse_metrics, _run_config, load_config
from eventguard.simulator import run_reference
from eventguard.trace import generate_trace, trace_fingerprint
from dataclasses import replace


def main(stage: str) -> None:
    cfg = load_config()
    updated = 0
    for model, rate, seed, strategy, _ in plan(stage):
        run_id = f"{strategy.lower()}_{model.lower()}_{int(rate*100):02d}_seed{seed}"
        raw_path = OUT / "raw" / stage / f"{run_id}.json"
        manifest_path = OUT / "runs" / stage / f"{run_id}.json"
        if not raw_path.exists() or not manifest_path.exists():
            continue
        raw = json.loads(raw_path.read_text())
        record = json.loads(manifest_path.read_text())
        samples = generate_trace(seed, 6)
        config = _run_config(cfg, strategy, rate, model, seed)
        if record["data_copy_budget"] is not None:
            config = replace(config, data_copy_budget=record["data_copy_budget"])
        sensor = [(v["host_monotonic"], v["line"]) for v in raw["sensor"]]
        gateway = [(v["host_monotonic"], v["line"]) for v in raw["gateway"]]
        expected = run_reference(samples, config, cfg["uart_baud"])
        events, differences, issues = _parse_samples(samples, sensor, gateway, config, expected)
        metrics = _parse_metrics(samples, events, sensor, gateway, 0, record["duration_s"],
                                 config, trace_fingerprint(samples))
        old = record["metrics"]
        for key in ("physical_data_received", "physical_data_before_injection", "uncontrolled_physical_data_missing"):
            old[key] = metrics[key]
        old["uncontrolled_physical_ack_missing"] = max(0, old["ack_count"] - old["physical_ack_received"])
        record["sample_events"] = events
        record["sample_differences"] = differences
        current_issues = list(issues)
        if differences:
            current_issues.append(f"simulation/firmware divergence at {len(differences)} sample(s)")
        if record["issues"] != current_issues:
            record.setdefault("prior_issue_versions", []).append(record["issues"])
        record["issues"] = current_issues
        record["status"] = "complete" if not record["issues"] else "failed"
        record["analysis_reparsed_at_utc"] = datetime.now(timezone.utc).isoformat()
        record["parser_correction"] = "Separate valid physical DATA frames before application injection from post-injection received frames"
        atomic_json(manifest_path, record)
        updated += 1
    print(json.dumps({"stage": stage, "reparsed": updated}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("smoke", "stage1", "full"))
    args = parser.parse_args()
    main(args.stage)
