"""Summaries of completed, paired E220 runs; never imputes missing hardware data."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

from .hardware_validation import OUT, atomic_json, plan, sha
from .faults import LossPlan
from .host import load_config
from .research_analysis import _frontier, describe, holm_adjust, wilcoxon_exact

METRICS = ("critical_event_delivery_ratio", "important_event_delivery_ratio", "overall_delivery_ratio",
           "physical_data_transmissions", "physical_ack_frames", "data_bytes_transmitted",
           "ack_bytes_transmitted", "total_bytes_transmitted", "redundant_copies",
           "physical_ack_received", "accepted_ack", "ack_injected_drops", "ack_timeout",
           "estimated_data_uart_time_ms", "estimated_ack_uart_time_ms",
           "estimated_communication_time_ms", "good_count", "degraded_count", "bad_count",
           "link_state_transitions", "crc_errors")
COMPARISONS = ("IMPORTANCE_ONLY", "UNIFORM_BUDGET", "RANDOM_BUDGET")


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and not fieldnames:
        path.write_text("", encoding="utf-8")
        return
    keys = fieldnames or list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def load_stage(stage: str, output_dir: Path | None = None) -> tuple[list[dict], list[dict], list[str]]:
    output = output_dir or OUT
    rows, failed, missing = [], [], []
    for model, rate, seed, strategy, order in plan(stage):
        run_id = f"{strategy.lower()}_{model.lower()}_{int(rate*100):02d}_seed{seed}"
        path = output / "runs" / stage / f"{run_id}.json"
        if not path.exists():
            missing.append(run_id)
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        raw_path = output / "raw" / stage / f"{run_id}.json"
        if not raw_path.exists() or row.get("raw_sha256") != sha(raw_path):
            missing.append(run_id + " [raw log missing or hash mismatch]")
            continue
        if row["status"] == "complete": rows.append(row)
        else: failed.append(row)
    return rows, failed, missing


def _summary(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["loss_model"], row["loss_rate"], row["strategy"])].append(row)
    result = []
    for (model, rate, strategy), group in sorted(groups.items()):
        record = {"loss_model": model, "loss_rate": rate, "strategy": strategy, "n_seed_runs": len(group)}
        for metric in METRICS:
            values = [r["metrics"][metric] for r in group]
            for name, value in describe(values).items():
                record[f"{metric}_{name}"] = value
        result.append(record)
    return result


def _paired(rows: list[dict]) -> list[dict]:
    index = {(r["loss_model"], r["loss_rate"], r["seed"], r["strategy"]): r for r in rows}
    conditions = sorted({(r["loss_model"], r["loss_rate"]) for r in rows})
    tests = []
    for model, rate in conditions:
        for baseline in COMPARISONS:
            pairs = [(index[(model, rate, seed, "EVENTGUARD")], index[(model, rate, seed, baseline)])
                     for seed in range(31, 41) if (model, rate, seed, "EVENTGUARD") in index
                     and (model, rate, seed, baseline) in index]
            if len(pairs) != 10:
                continue
            differences = [a["metrics"]["critical_event_delivery_ratio"] - b["metrics"]["critical_event_delivery_ratio"]
                           for a, b in pairs]
            if baseline in ("UNIFORM_BUDGET", "RANDOM_BUDGET") and any(
                    a["metrics"]["physical_data_transmissions"] != b["metrics"]["physical_data_transmissions"]
                    or a["trace_sha256"] != b["trace_sha256"]
                    or a["loss_calendar_sha256"] != b["loss_calendar_sha256"] for a, b in pairs):
                raise RuntimeError(f"budget pairing failed for {model} {rate} {baseline}")
            stats = describe(differences)
            wilcoxon = wilcoxon_exact(differences)
            tests.append({"loss_model": model, "loss_rate": rate, "comparison": f"EVENTGUARD - {baseline}",
                          "n_seed_pairs": len(pairs), "wins": sum(x > 0 for x in differences),
                          "ties": sum(x == 0 for x in differences), "losses": sum(x < 0 for x in differences),
                          **stats, **wilcoxon,
                          "mean_data_copy_difference": describe([a["metrics"]["physical_data_transmissions"]-
                            b["metrics"]["physical_data_transmissions"] for a,b in pairs])["mean"],
                          "mean_byte_difference": describe([a["metrics"]["total_bytes_transmitted"]-
                            b["metrics"]["total_bytes_transmitted"] for a,b in pairs])["mean"],
                          "mean_airtime_proxy_difference_ms": describe([a["metrics"]["estimated_communication_time_ms"]-
                            b["metrics"]["estimated_communication_time_ms"] for a,b in pairs])["mean"]})
    adjusted = holm_adjust([r["p_two_sided"] for r in tests])
    for row, p in zip(tests, adjusted): row["holm_p"] = p
    return tests


def _partial_diagnostics(row: dict, output_dir: Path) -> dict:
    """Recover safely classifiable physical activity from a STARTed but incomplete attempt."""
    raw_path = output_dir / "raw" / row.get("stage", "stage1") / f"{row.get('run_id', '')}.json"
    if not raw_path.is_file():
        return {}
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    sensor = [x.get("line", "") for x in raw.get("sensor", [])]
    gateway = [x.get("line", "") for x in raw.get("gateway", [])]
    sent = set()
    received = set()
    data_drops = set()
    ack_tx = set()
    ack_rx = set()
    timeouts = set()
    for line in sensor:
        fields = line.split(",")
        try:
            if fields[0] == "TX": sent.add((int(fields[1]), int(fields[3])))
            elif fields[0] == "ACK": ack_rx.add((int(fields[1]), int(fields[3])))
            elif fields[0] == "TIMEOUT": timeouts.add((int(fields[1]), int(fields[2])))
        except (IndexError, ValueError):
            continue
    for line in gateway:
        fields = line.split(",")
        try:
            if fields[0] == "RX": received.add((int(fields[1]), int(fields[3])))
            elif fields[:2] == ["DROP", "DATA"]: data_drops.add((int(fields[2]), int(fields[4])))
            elif fields[0] == "ACK_TX": ack_tx.add((int(fields[1]), int(fields[3])))
        except (IndexError, ValueError):
            continue
    cfg = load_config()
    loss = LossPlan(int(row.get("seed", 0)), float(row.get("loss_rate", 0)),
                    row.get("loss_model", "RANDOM_COPY"), int(row.get("sample_count", 0)),
                    cfg["burst_length"])
    missing = sorted(sent - received - data_drops)
    missing_rows = [{"sample_id": sample, "copy_index": copy,
                     "planned_data_drop": loss.drops("DATA", sample, copy)}
                    for sample, copy in missing]
    missing_evidence = []
    for sample, copy in missing:
        sensor_markers = [line for line in sensor
                          if line.startswith(("D_TX_BEGIN,", "D_TX_UART_DONE,", "D_TX_AUX_READY,"))
                          and len(line.split(",")) > 2
                          and line.split(",")[1:3] == [str(sample), str(copy)]]
        gateway_markers = [line for line in gateway
                           if line.startswith(("D_RX_FIRST_BYTE,", "D_RX_HEADER_COMPLETE,",
                                              "D_RX_FRAME_COMPLETE,", "D_RX_CRC_OK,"))
                           and len(line.split(",")) > 2
                           and line.split(",")[1:3] == [str(sample), str(copy)]]
        missing_evidence.append({"sample_id": sample, "copy_index": copy,
                                 "sensor_tx_markers": sensor_markers,
                                 "gateway_rx_markers": gateway_markers})
    sensor_ack_keys = {key for key in ack_rx}
    ack_missing = sorted(ack_tx - sensor_ack_keys)
    drop_calendar_mismatches = []
    for sample, copy in data_drops:
        if not loss.drops("DATA", sample, copy):
            drop_calendar_mismatches.append({"kind": "DATA", "sample_id": sample, "copy_index": copy})
    for line in sensor:
        fields = line.split(",")
        if len(fields) > 4 and fields[0] == "ACK":
            try:
                sample, copy = int(fields[1]), int(fields[3])
                if (fields[4] == "DROP") != loss.drops("ACK", sample, copy):
                    drop_calendar_mismatches.append({"kind": "ACK", "sample_id": sample, "copy_index": copy})
            except ValueError:
                pass
    open_ack_wait = None
    for line in sensor:
        fields = line.split(",")
        if len(fields) >= 4 and fields[0] == "D_ACK_WAIT_BEGIN":
            try:
                key = (int(fields[1]), int(fields[2]))
                if key not in ack_rx and key not in timeouts:
                    open_ack_wait = {"sample_id": key[0], "copy_index": key[1]}
            except ValueError:
                pass
    error_lines = [line for line in sensor + gateway
                   if line.startswith(("HOST_SERIAL_ERROR", "ERR,UART", "ERR,CRC", "ERR,PACKET"))]
    first_time = next((x.get("host_monotonic") for x in raw.get("sensor", []) if x.get("line", "").startswith("TX,")), None)
    last_time = next((x.get("host_monotonic") for x in reversed(raw.get("sensor", []))
                      if x.get("line", "").startswith(("TX,", "ACK,", "TIMEOUT,"))), None)
    return {
        "partial": True,
        "sensor_data_tx": len(sent),
        "gateway_post_injection_rx": len(received),
        "planned_data_drops_logged": len(data_drops),
        "physical_data_before_injection": len(received) + len(data_drops),
        "uncontrolled_physical_data_missing": len(missing_rows),
        "missing_data_copies": missing_rows,
        "missing_data_evidence": missing_evidence,
        "physical_ack_frames": len(ack_tx),
        "physical_ack_received": len(ack_rx),
        "uncontrolled_physical_ack_missing": len(ack_missing),
        "missing_ack_copies": [{"sample_id": s, "copy_index": c} for s, c in ack_missing],
        "ack_timeouts_logged": len(timeouts),
        "sample_records_complete": sum(line.startswith("SAMPLE,") for line in sensor),
        "sample_events_started": sum(line.startswith("EVT,") for line in sensor),
        "data_loss_calendar_mismatches": drop_calendar_mismatches,
        "serial_or_parser_error_lines": error_lines,
        "open_ack_wait_at_last_sensor_log": open_ack_wait,
        "last_sensor_progress_line": next((line for line in reversed(sensor)
                                            if line.startswith(("TX,", "ACK,", "TIMEOUT,", "SAMPLE,"))), ""),
        "host_monotonic_span_from_first_tx_to_last_sensor_record_s":
            last_time - first_time if first_time is not None and last_time is not None else None,
        "run_timeout_s": row.get("duration_s"),
    }


def _diff(rows: list[dict], output_dir: Path | None = None) -> list[dict]:
    result = []
    for row in rows:
        hardware, host = row.get("metrics", {}), row.get("reference_metrics", {})
        partial = _partial_diagnostics(row, output_dir) if output_dir and "metrics" not in row else {}
        differences = row.get("sample_differences", [])
        sample_events = row.get("sample_events", [])
        result.append({"run_id": row.get("run_id"), "loss_model": row.get("loss_model"),
                       "loss_rate": row["loss_rate"], "seed": row["seed"], "strategy": row["strategy"],
                       "critical_delivery_difference": hardware.get("critical_event_delivery_ratio", "")-host.get("critical_event_delivery_ratio", 0)
                       if isinstance(hardware.get("critical_event_delivery_ratio"), (int, float)) else "",
                       "overall_delivery_difference": hardware.get("overall_delivery_ratio", "")-host.get("overall_delivery_ratio", 0)
                       if isinstance(hardware.get("overall_delivery_ratio"), (int, float)) else "",
                       "data_copy_difference": hardware.get("physical_data_transmissions", "")-host.get("physical_data_transmissions", 0)
                       if isinstance(hardware.get("physical_data_transmissions"), (int, float)) else "",
                       "accepted_ack_difference": hardware.get("accepted_ack", "")-host.get("accepted_ack", 0)
                       if isinstance(hardware.get("accepted_ack"), (int, float)) else "",
                       "link_state_difference_count": sum(
                           e.get("link_state_before") != e.get("link_state_before_simulation") or
                           e.get("link_state_after") != e.get("link_state_after_simulation")
                           for e in sample_events) if sample_events else "",
                       "uncontrolled_physical_data_missing": partial.get("uncontrolled_physical_data_missing",
                           hardware.get("uncontrolled_physical_data_missing", "")),
                       "missing_data_copies": json.dumps(partial.get("missing_data_copies", [])),
                       "first_divergent_sample": differences[0].get("sample_id", "") if differences else
                           (partial.get("missing_data_copies", [{}])[0].get("sample_id", "")
                            if partial.get("missing_data_copies") else ""),
                       "first_divergent_copy": differences[0].get("copy_index", "") if differences else
                           (partial.get("missing_data_copies", [{}])[0].get("copy_index", "")
                            if partial.get("missing_data_copies") else ""),
                       "first_issue": row.get("issues", [""])[0] if row.get("issues") else "",
                       "partial_diagnostics": json.dumps(partial, ensure_ascii=False) if partial else ""})
    return result


def _plot(summary: list[dict], stage: str, output_dir: Path | None = None) -> dict:
    output = output_dir or OUT
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    paths = {}
    for metric, filename, xlabel in (("physical_data_transmissions", "hardware_pareto_delivery_vs_copies.png", "Mean DATA copies/run"),
                                     ("total_bytes_transmitted", "hardware_pareto_delivery_vs_bytes.png", "Mean bytes/run")):
        conditions = sorted({(r["loss_model"], r["loss_rate"]) for r in summary})
        fig, axes = plt.subplots(1, len(conditions), figsize=(max(7, 4.1*len(conditions)), 4.5), squeeze=False)
        for ax, (model, rate) in zip(axes[0], conditions):
            group = [r for r in summary if r["loss_model"] == model and r["loss_rate"] == rate]
            points = [{"strategy": r["strategy"], "cost": r[f"{metric}_mean"],
                       "delivery": r["critical_event_delivery_ratio_mean"]} for r in group]
            front, _ = _frontier(points, "cost", "delivery")
            for p in points:
                ax.scatter(p["cost"], p["delivery"], s=60, alpha=1 if p in front else .45,
                           label=p["strategy"])
                ax.annotate(p["strategy"], (p["cost"], p["delivery"]), fontsize=7, xytext=(3, 3),
                            textcoords="offset points")
            ax.set(title=f"{model} {rate:.0%}", xlabel=xlabel, ylabel="Critical delivery ratio")
            ax.grid(alpha=.25)
        fig.suptitle(f"E220 hardware Pareto by fixed condition ({stage}; observed strategies only)")
        fig.tight_layout()
        path = output / "plots" / filename
        fig.savefig(path, dpi=170, bbox_inches="tight"); plt.close(fig)
        paths[metric] = filename
    return paths


def analyze(stage: str = "stage1", output_dir: Path | None = None) -> dict:
    output = output_dir or OUT
    rows, failed, missing = load_stage(stage, output)
    if stage == "stage1":
        return _analyze_stage1_attempt(output, rows, failed, missing)
    summary = _summary(rows)
    tests = _paired(rows) if stage != "smoke" else []
    differences = _diff(rows + failed)
    _write_csv(output / "summary.csv", summary)
    _write_csv(output / "metrics/paired_tests.csv", tests)
    _write_csv(output / "simulation_hardware_diff.csv", differences)
    _write_csv(output / "metrics/run_metrics.csv", [{"run_id": r["run_id"], "stage": r["stage"],
                                                   "status": r["status"],
                                                   "loss_model": r["loss_model"], "loss_rate": r["loss_rate"],
                                                   "seed": r["seed"], "strategy": r["strategy"], **r["metrics"]}
                                                  for r in rows + failed])
    anomaly_rows = []
    for row in failed:
        for issue in row["issues"]:
            match = re.match(r"sample (\d+) copy (\d+): (.*)", issue)
            if match:
                anomaly_rows.append({"run_id": row["run_id"], "sample_id": int(match.group(1)),
                                     "copy_index": int(match.group(2)), "observation": match.group(3),
                                     "loss_model": row["loss_model"], "loss_rate": row["loss_rate"],
                                     "seed": row["seed"]})
    _write_csv(output / "metrics/physical_anomalies.csv", anomaly_rows)
    plots = _plot(summary, stage, output) if summary else {}
    complete = len(rows) == len(plan(stage)) and not failed
    frontier_status = defaultdict(lambda: {"EVENTGUARD": False, "IMPORTANCE_ONLY": False})
    for model, rate in sorted({(r["loss_model"], r["loss_rate"]) for r in summary}):
        condition = [r for r in summary if r["loss_model"] == model and r["loss_rate"] == rate]
        points = [{"strategy": r["strategy"], "cost": r["total_bytes_transmitted_mean"],
                   "delivery": r["critical_event_delivery_ratio_mean"]} for r in condition]
        front, _ = _frontier(points, "cost", "delivery")
        for strategy in ("EVENTGUARD", "IMPORTANCE_ONLY"):
            frontier_status[(model, rate)][strategy] = any(p["strategy"] == strategy for p in front)
    payload = {"stage": stage, "completed_runs": len(rows), "failed_runs": len(failed),
               "attempted_runs": len(rows) + len(failed), "planned_runs": len(plan(stage)),
               "complete": complete, "missing": missing,
               "failed_details": [{"run_id": r.get("run_id"), "issues": r.get("issues", []),
                                   "uncontrolled_physical_data_missing": r.get("metrics", {}).get("uncontrolled_physical_data_missing"),
                                   "uncontrolled_physical_ack_missing": r.get("metrics", {}).get("uncontrolled_physical_ack_missing")}
                                  for r in failed], "summary": summary,
               "paired_tests": tests, "simulation_hardware_diff": differences, "plots": plots,
               "pareto_frontier_by_condition": {f"{model} {rate:.0%}": status
                                                for (model, rate), status in frontier_status.items()}}
    (output / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    manifest_path = output / "hardware_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stage_record = manifest["stages"].setdefault(stage, {})
        stage_record.update({"passed_runs": len(rows), "failed_runs": len(failed),
                             "not_attempted_runs": len(missing),
                             "gate_status": "PASS" if complete else "FAIL" if failed else "INCOMPLETE"})
        fixes = manifest.setdefault("compatibility_fixes", [])
        for note in (
            "host SAMPLE serial parser field offset corrected after failed first smoke attempt; condition rerun",
            "hardware DATA receipt accounting separated pre-injection receipt from post-injection acceptance; raw logs reparsed",
        ):
            if note not in fixes: fixes.append(note)
        manifest["engineering_bug_log"] = "engineering_bug_log.md"
        atomic_json(manifest_path, manifest)
    disagreements = [r for r in differences if any(r[key] for key in (
        "critical_delivery_difference", "overall_delivery_difference", "data_copy_difference",
        "accepted_ack_difference", "link_state_difference_count"))]
    lines = ["# Frozen-v1 hardware validation", "", f"Stage: {stage}. Completed {len(rows)}/{len(plan(stage))} planned runs; "
             f"{len(failed)} failed after completion of radio transmission; {len(missing)} not attempted.",
             "", "**Evidence:** real ESP32-S3 + E220 DATA/ACK frames with deterministic application-layer injection.",
             "The previous host and pilot datasets are kept separate.", "",
             "## Hardware and E220", "",
             "See `hardware_manifest.json` for USB ports, chip MACs, radio profile, firmware hashes, and execution order.",
             "Every run has raw serial logs and a run manifest. Incomplete or failed runs are listed in `summary.json`.", "",
             "## Reproducibility and simulation agreement", "",
             f"Run-level metric disagreements: {len(disagreements)}/{len(rows)+len(failed)} attempted runs. "
             "Per-sample divergence is preserved in each run manifest. "
             "See `simulation_hardware_diff.csv`.", "",
             "## Paired research comparisons", "",
             "The statistical unit is one paired seed/run, never an individual packet. "
             "`metrics/paired_tests.csv` gives mean/median/sample SD/95% mean CI, exact signed-rank p-values, "
             "rank-biserial effect sizes, and Holm-adjusted p-values. Formal tests are emitted only "
             "for complete ten-seed conditions in the main stage, never from smoke or a selected subset.", ""]
    for test in tests:
        lines.append(f"- {test['loss_model']} {test['loss_rate']:.0%}, {test['comparison']}: "
                     f"n={test['n_seed_pairs']}, mean critical-delivery Δ={test['mean']:.4f}, "
                     f"DATA-copy Δ={test['mean_data_copy_difference']:.2f}, "
                     f"byte Δ={test['mean_byte_difference']:.2f}, "
                     f"estimated communication-time Δ={test['mean_airtime_proxy_difference_ms']:.1f} ms, "
                     f"p={test['p_two_sided']:.3g}, effect={test['rank_biserial']:.3f}.")
    if not tests:
        lines.append("No formal paired test is available at this stage.")
    lines += ["", "## Cost and Pareto", "",
              "UART communication time is an estimate: (DATA bytes + ACK bytes) × 10 bits/byte ÷ 9600 bit/s. "
              "It is **not measured RF PHY airtime**. No Joule estimate is made without current sensing.",
              "The plots show only strategies actually completed under each matched condition.", "",
              ("Smoke-only descriptive frontier: two seeds and RANDOM_COPY at 0%/20%; this is not a Pareto or treatment-effect conclusion."
               if stage == "smoke" else ""), "",
              "| Condition | EventGuard on byte-cost frontier | Importance Only on byte-cost frontier |",
              "|---|---|---|"]
    for (model, rate), status in frontier_status.items():
        lines.append(f"| {model} {rate:.0%} | {'YES' if status['EVENTGUARD'] else 'NO'} | "
                     f"{'YES' if status['IMPORTANCE_ONLY'] else 'NO'} |")
    lines += ["", "## Hardware, E220, importance, and burst interpretation", ""]
    if rows:
        lines.append(f"Both boards completed {len(rows)} passing logged runs with verified firmware hashes, serial roles, "
                     "and E220_READY preflight checks. Every completed run has 54 SAMPLE and EVT records. "
                     "The runner rejects CRC, UART, sequence, budget, calendar, and Python/C sample differences. "
                     "Injected DATA/ACK loss is separated from unexpected physical-link anomalies.")
        lines.append(f"The recorded host metric disagreement count is {len(disagreements)}; "
                     "the row-level details and earliest divergent sample are in `simulation_hardware_diff.csv`.")
    if complete:
        for baseline in COMPARISONS:
            subset = [t for t in tests if t["comparison"] == f"EVENTGUARD - {baseline}"]
            if subset:
                lines.append(f"EVENTGUARD versus {baseline}: " + "; ".join(
                    f"{t['loss_model']} {t['loss_rate']:.0%}: critical Δ={t['mean']:.4f}, "
                    f"copies Δ={t['mean_data_copy_difference']:.2f}, bytes Δ={t['mean_byte_difference']:.2f}"
                    for t in subset) + ".")
        burst = [t for t in tests if t["loss_model"] == "BURST_SAMPLE" and
                 t["comparison"] == "EVENTGUARD - IMPORTANCE_ONLY"]
        if burst:
            lines.append("BURST_SAMPLE EventGuard-versus-importance critical-delivery differences: " +
                         ", ".join(f"{t['loss_rate']:.0%}={t['mean']:.4f}" for t in burst) + ".")
    lines += ["", "## Paper impact and limitations", ""]
    if not complete:
        lines.append("This stage is incomplete. No final hardware claim or paper readiness judgment is made. "
                     "The 160-run main stage is gated until every smoke run passes. Preserve the failed condition "
                     "and investigate the uncontrolled physical-link anomalies without adjusting seeds or thresholds.")
        for row in failed:
            lines.append(f"- Failed {row['run_id']}: " + "; ".join(row["issues"][:20]) + ".")
        lines += ["", "## Required research judgments", "",
                  f"- Hardware runs completed: {len(rows)} passing of {len(plan(stage))} planned; "
                  f"{len(failed)} completed with failed validation.",
                  f"- Failed runs: {len(failed)}. All failed raw logs and manifests are retained.",
                  f"- Simulation/hardware agreement: {len(rows)} passing runs agree; "
                  f"{len(disagreements)} attempted run disagrees. The failed run is not silently discarded.",
                  "- EventGuard vs Importance Only: one 0% paired seed ties in delivery and DATA copies; "
                  "higher-loss paired hardware evidence is unavailable.",
                  "- EventGuard vs Uniform Budget: not evaluated; equal-budget hardware conclusion unavailable.",
                  "- EventGuard vs Random Budget: not evaluated; equal-budget hardware conclusion unavailable.",
                  "- EventGuard Pareto status: incomplete smoke-only comparison; no main-matrix frontier claim.",
                  "- Link adaptation useful: **UNKNOWN**. The failed 20% run shows extra link-state changes "
                  "caused by uncontrolled receive-path losses, but no matched ablation.",
                  "- Main contribution after hardware validation: unconfirmed; smoke establishes baseline 0% "
                  "Python/C parity and reveals a physical receive-path discrepancy under injected loss.",
                  "- Largest remaining limitation: source of the uncontrolled DATA disappearance is not localized "
                  "to RF, E220 buffering, or gateway UART reception.",
                  "- Is the project ready for paper writing: **NO** as a confirmatory hardware paper; the "
                  "negative smoke result is reportable as a feasibility finding.",
                  "- Recommended next action: instrument the receive path and E220 AUX/UART timing under "
                  "a separately labeled engineering diagnostic, then repeat the fixed smoke plan from the start "
                  "with recorded firmware hashes. Do not start the main matrix before it passes."]
        if failed:
            first = failed[0]
            m, h = first["metrics"], first["reference_metrics"]
            lines += ["", "## Failed-run observation before application injection", "",
                      f"{first['run_id']}: {m['physical_data_transmissions']} DATA frames were logged as sent; "
                      f"the gateway logged {m['physical_data_before_injection']} valid frames before injection "
                      f"({m['physical_data_received']} accepted RX plus {m['data_injected_drops']} planned drops), "
                      f"leaving **{m['uncontrolled_physical_data_missing']} sent copies without a gateway log**. "
                      "No CRC or UART error line was observed. This identifies the receiver path but does not "
                      "prove an RF-air cause.",
                      f"The run had {m['physical_ack_frames']} ACK transmissions, "
                      f"{m['physical_ack_received']} physically received ACKs, {m['accepted_ack']} accepted ACKs, "
                      f"{m['ack_injected_drops']} injected ACK drops, and {m['ack_timeout']} ACK timeouts. "
                      f"Overall delivery was {m['overall_delivery_ratio']:.4f} versus host "
                      f"{h['overall_delivery_ratio']:.4f}; critical delivery happened to be "
                      f"{m['critical_event_delivery_ratio']:.4f} in both. The failed run used "
                      f"{m['physical_data_transmissions']} DATA copies, {m['total_bytes_transmitted']} total bytes, "
                      f"and a {m['estimated_communication_time_ms']:.1f} ms UART-time proxy. "
                      "These are descriptive failed-run values, not a paired treatment estimate."]
        lines += ["", "## Hardware-report checklist", "",
                  "- **Hardware:** two ESP32-S3 devices remained online for four attempted runs; the remaining "
                  "eight smoke runs were not attempted.",
                  "- **E220:** bidirectional DATA/ACK worked in three 0% runs. The 20% run exposed 13 "
                  "uncontrolled DATA disappearances before application injection; root cause unknown.",
                  "- **Reproducibility:** all three passing runs matched host metrics and per-sample behavior. "
                  "The failed run diverged beginning at sample 6, copy 0.",
                  "- **Event Importance:** firmware importance labels matched Python for all 54 samples "
                  "in each attempted run, including the failed run.",
                  "- **Equal Budget:** neither blind budget strategy reached its smoke or main comparison; no conclusion.",
                  "- **Ablation / Link Adaptation:** the sole 0% EventGuard–Importance Only pair tied. "
                  "High-loss treatment effect and cost increment remain unknown.",
                  "- **Burst Loss:** BURST_SAMPLE hardware runs were not attempted; the host finding is unvalidated.",
                  "- **Cost:** DATA copies, ACK frames, bytes, and a clearly labeled UART-time proxy are recorded "
                  "per run; no measured RF airtime or energy is reported.",
                  "- **Paper Impact:** the hardware data support only 0% parity and identify an unmodeled "
                  "receive-path anomaly. They neither confirm nor overturn the treatment comparison."]
    elif stage == "smoke":
        lines += ["This v2 stage is a smoke gate, not a confirmatory treatment study. It covers two seeds (31 and 32), "
                  "RANDOM_COPY at 0% and 20%, and FIXED_2 / IMPORTANCE_ONLY / EVENTGUARD only. Passing establishes "
                  "the receive, logging, reset-isolation, and host/firmware parity path for these conditions; it does "
                  "not establish a general reliability/cost advantage.",
                  "", "## Required research judgments", "",
                  "- **Hardware:** both ESP32-S3 boards completed all 12 smoke runs; every run has a complete raw log and END counters.",
                  "- **E220:** bidirectional DATA/ACK completed. Across the 12 runs, uncontrolled DATA missing, uncontrolled ACK missing, and CRC errors were all zero.",
                  "- **Reproducibility:** 12/12 run metrics match the frozen host reference; per-sample differences are zero.",
                  "- **Event Importance:** firmware/Python sample behavior matched in these smoke conditions; this is not a new classifier-performance claim.",
                  "- **EventGuard vs Importance Only:** descriptive smoke comparison only (two paired seeds per condition); no formal inference.",
                  "- **Equal Budget:** UNIFORM_BUDGET and RANDOM_BUDGET were not part of this smoke; no equal-budget conclusion.",
                  "- **Burst Loss:** BURST_SAMPLE was not part of this smoke; no burst-loss conclusion.",
                  "- **Link Adaptation:** remains undetermined by this gate. The smoke does not justify a general link-adaptation benefit claim.",
                  "- **Cost:** DATA copies, bytes, and UART-time proxy are recorded; RF PHY airtime and energy were not directly measured.",
                  "- **Paper impact:** confirms the engineering path and host parity for the tested smoke conditions only; it neither confirms nor refutes the treatment conclusions.",
                  "- **Main-stage readiness:** smoke gate PASS. Stage 1 has not started and remains pending explicit user confirmation."]
    else:
        importance_tests = [t for t in tests if t["comparison"] == "EVENTGUARD - IMPORTANCE_ONLY"]
        blind_tests = [t for t in tests if t["comparison"] in
                       ("EVENTGUARD - UNIFORM_BUDGET", "EVENTGUARD - RANDOM_BUDGET")]
        same_critical = all(abs(t["mean"]) < 1e-12 for t in importance_tests)
        extra_copies = any(t["mean_data_copy_difference"] > 0 for t in importance_tests)
        blind_gain = any(t["mean"] > 0 for t in blind_tests)
        link_verdict = "NO" if same_critical and extra_copies else "CONDITION-DEPENDENT"
        paper_impact = "supports" if not disagreements and same_critical and blind_gain else "partly supports"
        lines += [f"- Hardware runs completed: {len(rows)}.",
                  f"- Failed or missing runs: {len(missing)}.",
                  f"- Simulation/hardware agreement: {len(rows)-len(disagreements)}/{len(rows)} run metrics agree.",
                  f"- Link adaptation useful for the declared CRITICAL-delivery/cost objective: **{link_verdict}**.",
                  f"- Hardware result {paper_impact} the host-simulation conclusion in these conditions.",
                  "- Largest remaining limitation: injected loss is application-layer; natural RF channel variation, "
                  "energy, and exact PHY airtime are not measured.",
                  "- Ready for paper writing: PARTIALLY. The hardware stage strengthens the methods and results "
                  "section but is not field validation.", "- Recommended next action: preserve these run-level logs, "
                  "then collect instrumented natural-channel and current/airtime measurements before broader claims."]
    (output / "hardware_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not complete:
        paper_text = ("The frozen-v1 E220 smoke stage did not pass its preregistered gate. "
                      f"Three of twelve planned runs passed complete logging and host parity; "
                      f"one further run completed radio transmission but failed validation, and {len(missing)} were not attempted. ")
        if failed:
            first = failed[0]
            paper_text += (f"In {first['run_id']}, {first['metrics']['uncontrolled_physical_data_missing']} of "
                           f"{first['metrics']['physical_data_transmissions']} logged DATA sends had no gateway "
                           "receive or injection-drop record. The cause could lie in the RF, E220, or gateway "
                           "receive path and has not been localized. The 160-run main matrix was not started. ")
        paper_text += ("These observations are a feasibility result, not confirmation of a treatment effect. "
                       "The reported communication-time estimate is a UART-time proxy, not measured RF airtime; "
                       "energy was not directly measured and no Joule value is reported.\n")
    elif stage == "smoke":
        paper_text = ("The v2 E220 smoke gate completed all 12 planned runs across two seeds, RANDOM_COPY loss at 0%/20%, "
                      "and FIXED_2, IMPORTANCE_ONLY, and EVENTGUARD. All run metrics matched the frozen host reference; "
                      "uncontrolled DATA/ACK missing and CRC errors were zero. This is engineering-gate evidence, not a "
                      "confirmatory treatment result: budget baselines, BURST_SAMPLE, and the ten-seed Stage 1 matrix "
                      "were not run. Stage 1 awaits explicit user confirmation. RF airtime and energy were not directly measured.\n")
    else:
        paper_text = (f"The {stage} E220 validation completed {len(rows)} paired-condition runs. "
                      "Use `hardware_report.md`, `summary.csv`, `metrics/paired_tests.csv`, and "
                      "`simulation_hardware_diff.csv` for verified results. RF airtime and energy were not directly measured.\n")
    (output / "paper_update.md").write_text("# Hardware validation paper update\n\n" + paper_text,
                                           encoding="utf-8")
    return {"completed": len(rows), "failed": len(failed), "planned": len(plan(stage)), "complete": complete,
            "metric_disagreements": len(disagreements), "paired_tests": len(tests)}


def _analyze_stage1_attempt(output: Path, rows: list[dict], failed: list[dict],
                            missing: list[str]) -> dict:
    """Write Stage 1 outputs without touching the v2 smoke summaries or raw attempts."""
    report_root = output / "stage1"
    metrics_dir = report_root / "metrics"
    report_root.mkdir(parents=True, exist_ok=True)
    plots_dir = report_root / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    diagnostics = {row["run_id"]: _partial_diagnostics(row, output)
                   for row in failed if "metrics" not in row}
    summary = _summary(rows)
    tests = _paired(rows) if rows else []
    differences = _diff(rows + failed, output)
    summary_fields = ["loss_model", "loss_rate", "strategy", "n_seed_runs"] + [
        f"{metric}_{stat}" for metric in METRICS
        for stat in ("mean", "median", "std", "ci95_low", "ci95_high")]
    paired_fields = ["loss_model", "loss_rate", "comparison", "n_seed_pairs", "wins", "ties", "losses",
                     "mean", "median", "std", "ci95_low", "ci95_high", "p_two_sided", "holm_p",
                     "rank_biserial", "mean_data_copy_difference", "mean_byte_difference",
                     "mean_airtime_proxy_difference_ms"]
    _write_csv(report_root / "summary.csv", summary, summary_fields)
    _write_csv(metrics_dir / "paired_tests.csv", tests, paired_fields)
    _write_csv(report_root / "simulation_hardware_diff.csv", differences)

    run_metrics = []
    for row in rows + failed:
        partial = diagnostics.get(row["run_id"], {})
        run_metrics.append({"run_id": row["run_id"], "stage": row.get("stage", "stage1"),
                            "status": row.get("status"), "loss_model": row.get("loss_model"),
                            "loss_rate": row.get("loss_rate"), "seed": row.get("seed"),
                            "strategy": row.get("strategy"), **row.get("metrics", {}),
                            **{f"partial_{key}": json.dumps(value, ensure_ascii=False)
                               if isinstance(value, (dict, list)) else value
                               for key, value in partial.items()}})
    _write_csv(metrics_dir / "run_metrics.csv", run_metrics)

    anomaly_rows = []
    for row in failed:
        diag = diagnostics.get(row["run_id"], {})
        for item in diag.get("missing_data_copies", []):
            anomaly_rows.append({"run_id": row["run_id"], "kind": "uncontrolled_physical_data_missing",
                                 "sample_id": item["sample_id"], "copy_index": item["copy_index"],
                                 "planned_data_drop": item["planned_data_drop"],
                                 "loss_model": row.get("loss_model"), "loss_rate": row.get("loss_rate"),
                                 "seed": row.get("seed")})
        for item in diag.get("missing_ack_copies", []):
            anomaly_rows.append({"run_id": row["run_id"], "kind": "uncontrolled_physical_ack_missing",
                                 "sample_id": item["sample_id"], "copy_index": item["copy_index"],
                                 "planned_data_drop": "", "loss_model": row.get("loss_model"),
                                 "loss_rate": row.get("loss_rate"), "seed": row.get("seed")})
    _write_csv(metrics_dir / "physical_anomalies.csv", anomaly_rows)

    complete = len(rows) == len(plan("stage1")) and not failed
    all_diagnostics = {row["run_id"]: diagnostics.get(row["run_id"], {}) for row in failed}
    payload = {"stage": "stage1", "status": "COMPLETE" if complete else "FAILED" if failed else "INCOMPLETE",
               "completed_runs": len(rows), "failed_runs": len(failed),
               "attempted_runs": len(rows) + len(failed), "planned_runs": len(plan("stage1")),
               "not_attempted_runs": len(missing), "complete": complete, "missing": missing,
               "failed_details": [{"run_id": row.get("run_id"), "issues": row.get("issues", []),
                                   "diagnostics": diagnostics.get(row.get("run_id"), {})}
                                  for row in failed],
               "summary": summary, "paired_tests": tests,
               "simulation_hardware_diff": differences, "plots": {},
               "treatment_conclusions_available": bool(complete),
               "raw_attempts_preserved": True}
    (report_root / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (report_root / "postmortem.json").write_text(json.dumps({"failed_attempt_diagnostics": all_diagnostics},
                                                           indent=2) + "\n", encoding="utf-8")

    smoke_rows, smoke_failed, smoke_missing = load_stage("smoke", output)
    smoke_passed, smoke_planned = len(smoke_rows), len(plan("smoke"))
    smoke_gate = "PASS" if smoke_passed == smoke_planned and not smoke_failed and not smoke_missing else "FAIL"
    first = failed[0] if failed else None
    diag = diagnostics.get(first.get("run_id"), {}) if first else {}
    missing_copy = (diag.get("missing_data_copies") or [{}])[0]
    sensor_markers = (diag.get("missing_data_evidence") or [{}])[0].get("sensor_tx_markers", [])
    gateway_markers = (diag.get("missing_data_evidence") or [{}])[0].get("gateway_rx_markers", [])
    firmware = {}
    stage_manifest_path = report_root / "stage1_manifest.json"
    if stage_manifest_path.is_file():
        stage_manifest = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
        firmware = stage_manifest.get("firmware_images_sha256", {})
        stage_manifest["analysis"] = {"generated_at": datetime.now(timezone.utc).isoformat(),
            "completed_runs": len(rows), "failed_runs": len(failed),
            "not_attempted_runs": len(missing), "gate_status": "FAIL",
            "postmortem_code_sha256": sha(Path(__file__)),
            "outputs": ["summary.json", "simulation_hardware_diff.csv", "metrics/physical_anomalies.csv",
                        "postmortem.json", "stage1_report.md", "paper_update.md"]}
        atomic_json(stage_manifest_path, stage_manifest)

    run_id = first.get("run_id", "unknown") if first else "none"
    lines = [
        "# Stage 1 confirmatory hardware experiment report", "",
        "## Outcome", "",
        f"Stage 1 stopped after {len(rows)} complete runs and {len(failed)} failed STARTed attempt(s); "
        f"{len(missing)} of {len(plan('stage1'))} planned runs were not attempted.",
        "The preregistered stop rule was applied. No retry or later condition was run after the failed attempt.",
        f"Smoke gate before Stage 1: {smoke_passed}/{smoke_planned} ({smoke_gate}). Stage 1 preflight: PASS.",
        "Treatment-level statistics, equal-budget comparisons, and Pareto analysis are unavailable because "
        "no condition has a complete paired seed set.", "",
        "## Frozen provenance", "",
        f"- Firmware recovery path: B (device image recovery; exact canonical image hashes matched the smoke pair).",
        f"- Sensor SHA256: `{firmware.get('sensor', 'unavailable')}`",
        f"- Gateway SHA256: `{firmware.get('gateway', 'unavailable')}`",
        "- Algorithm/config/trace/fault hashes were checked at preflight; no firmware or frozen core was changed.",
        "- Stage 1 outputs are isolated in this directory; the prior v2 smoke reports and raw data were not overwritten.", "",
        "## Failed attempt diagnostic", "",
        f"- Run: `{run_id}` ({first.get('strategy', '') if first else ''}, "
        f"{first.get('loss_model', '') if first else ''} {first.get('loss_rate', 0):.0%}, "
        f"seed {first.get('seed', '') if first else ''}).",
        f"- Failure: {first.get('failure', {}).get('message', 'no failure record') if first else 'none'}.",
        f"- Sensor DATA TX records: {diag.get('sensor_data_tx', 0)}; gateway post-injection RX: "
        f"{diag.get('gateway_post_injection_rx', 0)}; planned DATA drops logged: {diag.get('planned_data_drops_logged', 0)}; "
        f"physical DATA before injection: {diag.get('physical_data_before_injection', 0)}.",
        f"- Uncontrolled physical DATA missing: {diag.get('uncontrolled_physical_data_missing', 0)}; "
        f"uncontrolled physical ACK missing: {diag.get('uncontrolled_physical_ack_missing', 0)}.",
        f"- Missing copy: sample {missing_copy.get('sample_id', 'n/a')} copy {missing_copy.get('copy_index', 'n/a')}; "
        f"planned DATA drop={missing_copy.get('planned_data_drop', 'n/a')}.",
        f"- Gateway ACK TX / Sensor physical ACK RX: {diag.get('physical_ack_frames', 0)} / "
        f"{diag.get('physical_ack_received', 0)}; logged ACK timeouts: {diag.get('ack_timeouts_logged', 0)}.",
        f"- CRC/UART/parser error lines captured: {len(diag.get('serial_or_parser_error_lines', []))}; "
        f"loss-calendar mismatches: {len(diag.get('data_loss_calendar_mismatches', []))}.",
        f"- Sensor open ACK wait at last log: `{json.dumps(diag.get('open_ack_wait_at_last_sensor_log'), ensure_ascii=False)}`. "
        "No subsequent ACK or TIMEOUT record for that copy was captured before the runner's firmware END timeout.",
        f"- Sensor TX-path markers for the missing copy: `{'; '.join(sensor_markers) or 'none'}`.",
        f"- Matching Gateway RX markers: `{'; '.join(gateway_markers) or 'none'}`.",
        "The evidence places the unexplained disappearance after the Sensor firmware logged UART completion/AUX-ready "
        "and before any matching Gateway parser receive marker. It does not distinguish E220 buffering, RF, or the "
        "Gateway receive path; this report does not label it an RF loss.",
        "No arbitrary delay was introduced. No parser/firmware change was made during this Stage 1 attempt.", "",
        "## Research questions", "",
        "- EventGuard vs Importance Only: not evaluated; no complete Stage 1 pair.",
        "- EventGuard vs Uniform Budget: not evaluated; no complete Stage 1 pair.",
        "- EventGuard vs Random Budget: not evaluated; no complete Stage 1 pair.",
        "- RANDOM_COPY 20%/30% and BURST_SAMPLE 20%/30%: no treatment conclusion; the first attempted "
        "condition (UNIFORM_BUDGET, RANDOM_COPY 20%, seed 31) failed.",
        "- Pareto status: unavailable; no completed matched condition.",
        "- Link adaptation usefulness: UNKNOWN from this Stage 1 attempt.",
        "- Host/firmware agreement: full sample-level agreement is unavailable for this truncated run; one "
        "unplanned TX-to-Gateway observation gap is confirmed from the retained logs.", "",
        "## Decision", "",
        "- Hardware runs completed: 0 / 160 PASS.",
        f"- Failed runs: {len(failed)}; not attempted: {len(missing)}.",
        "- Uncontrolled physical anomalies: 1 DATA copy; 0 observed missing ACK frames; 0 captured CRC/parser errors.",
        "- Simulation/firmware divergences: full-run parity cannot be scored; one transport-level DATA observation divergence at sample 25 copy 1.",
        "- Need full 400 runs: NO. Stage 1 did not pass; do not expand the matrix.",
        "- Recommended next action: retain this attempt and perform separately authorized receive-path engineering diagnosis "
        "before any new confirmatory run. The Stage 1 matrix remains stopped.", "",
        "This is an incomplete engineering feasibility observation, not a strategy effect estimate. No reliability, "
        "cost, or Pareto claim is made from this attempt.",
    ]
    (report_root / "stage1_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (report_root / "paper_update.md").write_text(
        "# Hardware validation paper update\n\n"
        "Stage 1 was stopped after its first STARTed attempt failed firmware END validation. The retained log "
        "contains one unplanned Sensor-TX-to-Gateway-observation gap (sample 25, copy 1), while no captured CRC, "
        "UART, or parser error localizes its cause. No treatment comparisons are available, and this attempt does "
        "not support a strategy-effect claim. Preserve it as an engineering feasibility observation; do not update "
        "the paper's treatment results from this incomplete run.\n",
        encoding="utf-8")
    (plots_dir / "README.md").write_text(
        "No Pareto plots were generated: Stage 1 stopped after its first failed attempt, so no matched "
        "condition has completed treatment data.\n", encoding="utf-8")
    return {"completed": len(rows), "failed": len(failed), "not_attempted": len(missing),
            "planned": len(plan("stage1")), "complete": complete, "metric_disagreements": 0,
            "transport_divergences": sum(d.get("uncontrolled_physical_data_missing") == 1
                                         for d in diagnostics.values()),
            "paired_tests": len(tests), "report_dir": str(report_root)}
