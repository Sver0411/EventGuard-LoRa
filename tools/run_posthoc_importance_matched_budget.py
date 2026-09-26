#!/usr/bin/env python3
"""POST-HOC HOST-ONLY DIAGNOSTIC; NOT PART OF FROZEN V1.

Replay a new importance-aware, link-blind exact-budget allocation against the
unchanged v1 trace/classifier/calendar. Never writes to frozen result folders.
"""
from __future__ import annotations

import csv
import hashlib
import inspect
import json
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eventguard.faults import LossPlan  # noqa: E402
from eventguard.importance import ImportanceClassifier, ImportanceConfig  # noqa: E402
from eventguard.model import GroundTruth, LossModel, RunConfig, Strategy  # noqa: E402
from eventguard.protocol import ACK_FRAME_SIZE, DATA_FRAME_SIZE  # noqa: E402
from eventguard.simulator import run_reference  # noqa: E402
from eventguard.trace import generate_trace, trace_fingerprint  # noqa: E402

SOURCE = ROOT / "results/pre_hardware_v1/runs.csv"
FROZEN_MANIFEST = ROOT / "results/pre_hardware_v1/experiment_manifest.json"
PROTOCOL = ROOT / "docs/posthoc_importance_matched_budget_protocol.md"
OUT = ROOT / "results/posthoc_importance_matched_budget"
RATES = (0.0, 0.05, 0.10, 0.20, 0.30)
SEEDS = tuple(range(31, 131))
ALGORITHM_VERSION = "IMPORTANCE_MATCHED_BUDGET_V1"
STRATEGY = "IMPORTANCE_MATCHED_BUDGET"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordering_key(seed: int, index: int, predicted_class: str) -> tuple[bytes, int]:
    payload = (f"{ALGORITHM_VERSION}|seed={seed}|sample_index={index}|"
               f"class={predicted_class}").encode("utf-8")
    return hashlib.sha256(payload).digest(), index


def allocate_importance_matched_budget(predicted_classes: Sequence[str], budget: int,
                                       seed: int) -> list[int]:
    """Pure, link/ACK/calendar/truth-blind allocation fixed by the protocol."""
    classes = tuple(predicted_classes)
    if not classes or any(label not in ("NORMAL", "IMPORTANT", "CRITICAL") for label in classes):
        raise ValueError("predicted_classes must be a nonempty sequence of valid classes")
    copies = [{"NORMAL": 1, "IMPORTANT": 2, "CRITICAL": 3}[label] for label in classes]
    extra = int(budget) - sum(copies)
    if extra < 0 or extra > sum(3 - count for count in copies):
        raise ValueError("EventGuard reference budget outside importance allocation headroom")
    for label, rounds in (("IMPORTANT", 1), ("NORMAL", 2)):
        order = sorted((i for i, kind in enumerate(classes) if kind == label),
                       key=lambda i: ordering_key(seed, i, label))
        for _ in range(rounds):
            for index in order:
                if extra == 0:
                    break
                copies[index] += 1
                extra -= 1
    assert extra == 0 and sum(copies) == budget and max(copies) <= 3
    return copies


def classify_trace(samples: list, frozen_params: dict) -> list[str]:
    classifier = ImportanceClassifier(ImportanceConfig(
        frozen_params["important_threshold"], frozen_params["critical_threshold"],
        frozen_params["importance_scales"], frozen_params["importance_baseline_alpha"]))
    return [classifier.classify(sample)[0].name for sample in samples]


def calendar_sha256(plan: LossPlan, length: int) -> str:
    bits = bytes(int(plan.drops(kind, index, copy))
                 for kind in ("DATA", "ACK")
                 for index in range(length) for copy in range(3))
    return hashlib.sha256(bits).hexdigest()


def evaluate_allocation(samples: list, predicted_classes: Sequence[str], copies: Sequence[int],
                        plan: LossPlan) -> dict:
    """Score static allocations with frozen canonical opportunities; no feedback."""
    if len(samples) != len(predicted_classes) or len(samples) != len(copies):
        raise ValueError("trace, prediction and allocation lengths differ")
    totals = {label.value: 0 for label in GroundTruth}
    delivered = {label.value: 0 for label in GroundTruth}
    truth_copy_counts = {label.value: 0 for label in GroundTruth}
    predicted_copy_counts = {label: 0 for label in ("NORMAL", "IMPORTANT", "CRITICAL")}
    ack_frames = accepted_ack = data_drops = ack_drops = 0
    for index, (sample, label, count) in enumerate(zip(samples, predicted_classes, copies)):
        if not 1 <= count <= 3:
            raise ValueError("copy count outside frozen cap")
        truth = sample.truth.value
        totals[truth] += 1
        truth_copy_counts[truth] += count
        predicted_copy_counts[label] += count
        got_data = False
        for copy in range(count):
            if plan.drops("DATA", index, copy):
                data_drops += 1
                continue
            got_data = True
            ack_frames += 1
            if plan.drops("ACK", index, copy):
                ack_drops += 1
            else:
                accepted_ack += 1
        if got_data:
            delivered[truth] += 1
    data_copies = sum(copies)
    data_bytes = data_copies * DATA_FRAME_SIZE
    ack_bytes = ack_frames * ACK_FRAME_SIZE
    total_bytes = data_bytes + ack_bytes
    return {
        "critical_event_delivery_ratio": delivered["CRITICAL"] / totals["CRITICAL"],
        "important_event_delivery_ratio": delivered["IMPORTANT"] / totals["IMPORTANT"],
        "overall_delivery_ratio": sum(delivered.values()) / len(samples),
        "normal_delivery_ratio": delivered["NORMAL"] / totals["NORMAL"],
        "physical_data_transmissions": data_copies,
        "ack_count": ack_frames,
        "accepted_ack": accepted_ack,
        "data_injected_drops": data_drops,
        "ack_injected_drops": ack_drops,
        "data_bytes_transmitted": data_bytes,
        "ack_bytes_transmitted": ack_bytes,
        "total_bytes_transmitted": total_bytes,
        "estimated_communication_time_ms": total_bytes * 10 / 9600 * 1000,
        "critical_traffic_share": truth_copy_counts["CRITICAL"] / data_copies,
        "important_traffic_share": truth_copy_counts["IMPORTANT"] / data_copies,
        "normal_traffic_share": truth_copy_counts["NORMAL"] / data_copies,
        "predicted_normal_copies": predicted_copy_counts["NORMAL"],
        "predicted_important_copies": predicted_copy_counts["IMPORTANT"],
        "predicted_critical_copies": predicted_copy_counts["CRITICAL"],
        "truth_normal_copies": truth_copy_counts["NORMAL"],
        "truth_important_copies": truth_copy_counts["IMPORTANT"],
        "truth_critical_copies": truth_copy_counts["CRITICAL"],
    }


def source_rows() -> dict[tuple[float, int, str], dict]:
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        all_rows = list(csv.DictReader(handle))
    if len(all_rows) != 8000:
        raise AssertionError("frozen host matrix must contain 8,000 rows")
    index = {}
    for row in all_rows:
        if row["loss_model"] != "RANDOM_COPY" or row["strategy"] not in ("EVENTGUARD", "IMPORTANCE_ONLY"):
            continue
        key = (float(row["loss_rate"]), int(row["seed"]), row["strategy"])
        if key in index:
            raise AssertionError(f"duplicate frozen row: {key}")
        index[key] = row
    if len(index) != 1000:
        raise AssertionError("expected 500 frozen EventGuard and 500 Importance Only rows")
    return index


def validate_frozen_importance_only(samples: list, labels: list[str], source: dict,
                                    rate: float, seed: int, params: dict, plan: LossPlan) -> None:
    """Consistency replay only; it is never counted or saved as a new run."""
    base = allocate_importance_matched_budget(labels, int(source["physical_data_transmissions"]), seed)
    config = RunConfig(Strategy.IMPORTANCE_ONLY, rate, LossModel.RANDOM_COPY, seed,
                       burst_length=params["burst_length"], max_redundancy=params["max_redundancy"],
                       important_threshold=params["important_threshold"],
                       critical_threshold=params["critical_threshold"],
                       importance_scales=params["importance_scales"],
                       importance_baseline_alpha=params["importance_baseline_alpha"],
                       link_window=params["link_window"],
                       link_degraded_threshold=params["link_degraded_threshold"],
                       link_bad_threshold=params["link_bad_threshold"],
                       link_bad_fail_streak=params["link_bad_fail_streak"],
                       ack_timeout_ms=params["ack_timeout_ms"])
    frozen_replay = run_reference(samples, config)
    if [event["importance"] for event in frozen_replay["events"]] != labels:
        raise AssertionError(f"frozen predicted class sequence mismatch: {rate}/{seed}")
    if [event["redundancy_selected"] for event in frozen_replay["events"]] != base:
        raise AssertionError(f"Importance Only allocation mismatch: {rate}/{seed}")
    scored = evaluate_allocation(samples, labels, base, plan)
    for key in ("critical_event_delivery_ratio", "important_event_delivery_ratio",
                "overall_delivery_ratio", "physical_data_transmissions", "ack_count",
                "accepted_ack", "data_bytes_transmitted", "ack_bytes_transmitted",
                "total_bytes_transmitted", "critical_traffic_share"):
        expected = float(source[key])
        if abs(float(scored[key]) - expected) > 1e-12 or abs(float(frozen_replay["metrics"][key]) - expected) > 1e-12:
            raise AssertionError(f"frozen Importance Only accounting mismatch: {rate}/{seed}/{key}")


def run() -> None:
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite diagnostic results: {OUT}")
    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    params = frozen["parameters"]
    if params["max_redundancy"] != 3 or frozen["baseline_runs_sha256"] != sha256_file(SOURCE):
        raise AssertionError("frozen v1 source or copy cap changed")
    # The historical pre-hardware manifest also fingerprints firmware/main/main.c,
    # which legitimately changed during later receive-path engineering. This
    # host-only diagnostic checks only its frozen Python dependencies and
    # shared classifier/strategy/fault C sources; it never reads firmware/main.
    for relpath, expected in frozen["algorithm_sha256"].items():
        if not (relpath.startswith("eventguard/") or relpath.startswith("firmware/common/")):
            continue
        if sha256_file(ROOT / relpath) != expected:
            raise AssertionError(f"frozen v1 source changed: {relpath}")
    if tuple(frozen["seed_range"]["evaluation"]) != (31, 130):
        raise AssertionError("frozen seed range changed")
    if tuple(frozen["loss_rates"]) != RATES:
        raise AssertionError("frozen loss rates changed")
    if tuple(inspect.signature(allocate_importance_matched_budget).parameters) != (
            "predicted_classes", "budget", "seed"):
        raise AssertionError("allocator gained forbidden inputs")
    source = source_rows()
    git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    timestamp = datetime.now(timezone.utc).isoformat()
    OUT.mkdir(parents=True)
    shutil.copyfile(PROTOCOL, OUT / "protocol_snapshot.md")
    provenance = {
        "timestamp_utc": timestamp,
        "git_commit_sha": git_commit,
        "script_sha256": sha256_file(Path(__file__)),
        "frozen_v1_reference_commit": frozen["git_commit"],
        "frozen_v1_reference_tag": "v1.0.0",
        "source_runs_csv_sha256": sha256_file(SOURCE),
        "protocol_sha256": sha256_file(PROTOCOL),
        "protocol_snapshot_sha256": sha256_file(OUT / "protocol_snapshot.md"),
        "seed_range": [31, 130], "loss_model": "RANDOM_COPY",
        "loss_rates": list(RATES), "baseline_algorithm_version": ALGORITHM_VERSION,
        "classification_source": "unchanged frozen v1 ImportanceClassifier",
        "allocation_order": "ascending SHA256 digest, sample index tie-break; see protocol",
        "run_type": "POST_HOC_HOST_ONLY_OFFLINE_COUNTERFACTUAL",
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    rows = []
    try:
        for rate in RATES:
            for seed in SEEDS:
                eg = source[(rate, seed, "EVENTGUARD")]
                io = source[(rate, seed, "IMPORTANCE_ONLY")]
                samples = generate_trace(seed, 6)
                trace_sha = trace_fingerprint(samples)
                calendar_id = f"RANDOM_COPY:{rate}:{seed}:{len(samples)}"
                if not (trace_sha == eg["trace_sha256"] == io["trace_sha256"]):
                    raise AssertionError(f"trace mismatch: {rate}/{seed}")
                if not (calendar_id == eg["channel_calendar"] == io["channel_calendar"]):
                    raise AssertionError(f"calendar mismatch: {rate}/{seed}")
                plan = LossPlan(seed, rate, LossModel.RANDOM_COPY, len(samples), params["burst_length"])
                labels = classify_trace(samples, params)
                validate_frozen_importance_only(samples, labels, io, rate, seed, params, plan)
                budget = int(eg["physical_data_transmissions"])
                allocation = allocate_importance_matched_budget(labels, budget, seed)
                scored = evaluate_allocation(samples, labels, allocation, plan)
                if scored["physical_data_transmissions"] != budget:
                    raise AssertionError(f"DATA-copy budget mismatch: {rate}/{seed}")
                rows.append({
                    "strategy": STRATEGY, "seed": seed, "loss_model": "RANDOM_COPY",
                    "loss_rate": rate, "trace_sha256": trace_sha,
                    "channel_calendar": calendar_id,
                    "channel_calendar_sha256": calendar_sha256(plan, len(samples)),
                    "eventguard_reference_budget": budget,
                    "imb_actual_data_copies": scored["physical_data_transmissions"],
                    "allocation_order_id": ALGORITHM_VERSION + "_SHA256_ASCENDING",
                    "allocation_sha256": hashlib.sha256(bytes(allocation)).hexdigest(),
                    **scored,
                })
        if len(rows) != 500:
            raise AssertionError(f"expected 500 new runs, found {len(rows)}")
        with (OUT / "runs.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        manifest = {
            "status": "complete", "run_type": provenance["run_type"],
            "protocol_sha256": provenance["protocol_sha256"],
            "source_runs_csv_sha256": provenance["source_runs_csv_sha256"],
            "new_strategy": STRATEGY, "new_host_runs": len(rows),
            "rates": list(RATES), "seeds": [31, 130],
            "primary_rates": [0.20, 0.30],
            "primary_endpoints": ["important_event_delivery_ratio", "overall_delivery_ratio"],
            "primary_contrasts": 4,
            "exact_data_budget_checks": 500,
            "trace_checks": 500, "calendar_checks": 500,
            "frozen_importance_only_replay_checks": 500,
            "runs_csv_sha256": sha256_file(OUT / "runs.csv"),
        }
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        failure = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                   "error_type": type(exc).__name__, "error": str(exc),
                   "traceback": traceback.format_exc(), "completed_in_memory": len(rows)}
        (OUT / "diagnostic_attempt_failed.json").write_text(
            json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        raise
    print("POSTHOC_IMB_RUNS: 500/500; exact budgets/traces/calendars: 500/500 PASS")


if __name__ == "__main__":
    run()
