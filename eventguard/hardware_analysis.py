"""Summaries of completed, paired E220 runs; never imputes missing hardware data."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from .hardware_validation import OUT, plan
from .research_analysis import _frontier, describe, holm_adjust, wilcoxon_exact

METRICS = ("critical_event_delivery_ratio", "important_event_delivery_ratio", "overall_delivery_ratio",
           "physical_data_transmissions", "physical_ack_frames", "data_bytes_transmitted",
           "ack_bytes_transmitted", "total_bytes_transmitted", "redundant_copies",
           "physical_ack_received", "accepted_ack", "ack_injected_drops", "ack_timeout",
           "estimated_data_uart_time_ms", "estimated_ack_uart_time_ms",
           "estimated_communication_time_ms", "good_count", "degraded_count", "bad_count",
           "link_state_transitions", "crc_errors")
COMPARISONS = ("IMPORTANCE_ONLY", "UNIFORM_BUDGET", "RANDOM_BUDGET")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def load_stage(stage: str) -> tuple[list[dict], list[str]]:
    rows, missing = [], []
    for model, rate, seed, strategy, order in plan(stage):
        run_id = f"{strategy.lower()}_{model.lower()}_{int(rate*100):02d}_seed{seed}"
        path = OUT / "runs" / stage / f"{run_id}.json"
        if not path.exists():
            missing.append(run_id)
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        if row["status"] == "complete": rows.append(row)
        else: missing.append(run_id)
    return rows, missing


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
            if not pairs:
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


def _diff(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        hardware, host = row["metrics"], row["reference_metrics"]
        result.append({"run_id": row["run_id"], "loss_model": row["loss_model"],
                       "loss_rate": row["loss_rate"], "seed": row["seed"], "strategy": row["strategy"],
                       "critical_delivery_difference": hardware["critical_event_delivery_ratio"]-host["critical_event_delivery_ratio"],
                       "overall_delivery_difference": hardware["overall_delivery_ratio"]-host["overall_delivery_ratio"],
                       "data_copy_difference": hardware["physical_data_transmissions"]-host["physical_data_transmissions"],
                       "accepted_ack_difference": hardware["accepted_ack"]-host["accepted_ack"],
                       "link_state_difference_count": sum(e["link_state_before"] != next(
                           (x["link_simulation"] for x in row["sample_differences"] if x["sample_id"] == e["sample_id"]),
                           e["link_state_before"]) for e in row["sample_events"]),
                       "first_divergent_sample": row["sample_differences"][0]["sample_id"] if row["sample_differences"] else "",
                       "first_issue": row["issues"][0] if row["issues"] else ""})
    return result


def _plot(summary: list[dict], stage: str) -> dict:
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
        path = OUT / "plots" / filename
        fig.savefig(path, dpi=170, bbox_inches="tight"); plt.close(fig)
        paths[metric] = filename
    return paths


def analyze(stage: str = "stage1") -> dict:
    rows, missing = load_stage(stage)
    summary = _summary(rows)
    tests = _paired(rows)
    differences = _diff(rows)
    _write_csv(OUT / "summary.csv", summary)
    _write_csv(OUT / "metrics/paired_tests.csv", tests)
    _write_csv(OUT / "simulation_hardware_diff.csv", differences)
    _write_csv(OUT / "metrics/run_metrics.csv", [{"run_id": r["run_id"], "stage": r["stage"],
                                                   "loss_model": r["loss_model"], "loss_rate": r["loss_rate"],
                                                   "seed": r["seed"], "strategy": r["strategy"], **r["metrics"]}
                                                  for r in rows])
    plots = _plot(summary, stage) if summary else {}
    complete = len(rows) == len(plan(stage))
    frontier_status = defaultdict(lambda: {"EVENTGUARD": False, "IMPORTANCE_ONLY": False})
    for model, rate in sorted({(r["loss_model"], r["loss_rate"]) for r in summary}):
        condition = [r for r in summary if r["loss_model"] == model and r["loss_rate"] == rate]
        points = [{"strategy": r["strategy"], "cost": r["total_bytes_transmitted_mean"],
                   "delivery": r["critical_event_delivery_ratio_mean"]} for r in condition]
        front, _ = _frontier(points, "cost", "delivery")
        for strategy in ("EVENTGUARD", "IMPORTANCE_ONLY"):
            frontier_status[(model, rate)][strategy] = any(p["strategy"] == strategy for p in front)
    payload = {"stage": stage, "completed_runs": len(rows), "planned_runs": len(plan(stage)),
               "complete": complete, "missing_or_failed": missing, "summary": summary,
               "paired_tests": tests, "simulation_hardware_diff": differences, "plots": plots,
               "pareto_frontier_by_condition": {f"{model} {rate:.0%}": status
                                                for (model, rate), status in frontier_status.items()}}
    (OUT / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    disagreements = [r for r in differences if any(r[key] for key in (
        "critical_delivery_difference", "overall_delivery_difference", "data_copy_difference",
        "accepted_ack_difference", "link_state_difference_count"))]
    lines = ["# Frozen-v1 hardware validation", "", f"Stage: {stage}. Completed {len(rows)}/{len(plan(stage))} planned runs.",
             "", "**Evidence:** real ESP32-S3 + E220 DATA/ACK frames with deterministic application-layer injection.",
             "The previous host and pilot datasets are kept separate.", "",
             "## Hardware and E220", "",
             "See `hardware_manifest.json` for USB ports, chip MACs, radio profile, firmware hashes, and execution order.",
             "Every run has raw serial logs and a run manifest. Incomplete or failed runs are listed in `summary.json`.", "",
             "## Reproducibility and simulation agreement", "",
             f"Run-level metric disagreements: {len(disagreements)}/{len(rows)}. Per-sample divergence is preserved in each run manifest. "
             "See `simulation_hardware_diff.csv`.", "",
             "## Paired research comparisons", "",
             "The statistical unit is one paired seed/run, never an individual packet. "
             "`metrics/paired_tests.csv` gives mean/median/sample SD/95% mean CI, exact signed-rank p-values, "
             "rank-biserial effect sizes, and Holm-adjusted p-values.", ""]
    for test in tests:
        lines.append(f"- {test['loss_model']} {test['loss_rate']:.0%}, {test['comparison']}: "
                     f"n={test['n_seed_pairs']}, mean critical-delivery Δ={test['mean']:.4f}, "
                     f"DATA-copy Δ={test['mean_data_copy_difference']:.2f}, "
                     f"byte Δ={test['mean_byte_difference']:.2f}, "
                     f"estimated communication-time Δ={test['mean_airtime_proxy_difference_ms']:.1f} ms, "
                     f"p={test['p_two_sided']:.3g}, effect={test['rank_biserial']:.3f}.")
    lines += ["", "## Cost and Pareto", "",
              "UART communication time is an estimate: (DATA bytes + ACK bytes) × 10 bits/byte ÷ 9600 bit/s. "
              "It is **not measured RF PHY airtime**. No Joule estimate is made without current sensing.",
              "The plots show only strategies actually completed under each matched condition.", "",
              "| Condition | EventGuard on byte-cost frontier | Importance Only on byte-cost frontier |",
              "|---|---|---|"]
    for (model, rate), status in frontier_status.items():
        lines.append(f"| {model} {rate:.0%} | {'YES' if status['EVENTGUARD'] else 'NO'} | "
                     f"{'YES' if status['IMPORTANCE_ONLY'] else 'NO'} |")
    lines += ["", "## Hardware, E220, importance, and burst interpretation", ""]
    if rows:
        lines.append(f"Both boards completed {len(rows)} logged runs with verified firmware hashes, serial roles, "
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
                     "Resume the missing runs using the recorded firmware hashes and execution order.")
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
    (OUT / "hardware_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "paper_update.md").write_text("# Hardware validation paper update\n\n" +
        ("Hardware stage incomplete; do not promote results to a paper claim.\n" if not complete else
         f"The {stage} E220 validation completed {len(rows)} paired-condition runs. "
         "Use `hardware_report.md`, `summary.csv`, `metrics/paired_tests.csv`, and "
         "`simulation_hardware_diff.csv` for verified results. RF airtime and energy were not directly measured.\n"),
        encoding="utf-8")
    return {"completed": len(rows), "planned": len(plan(stage)), "complete": complete,
            "metric_disagreements": len(disagreements), "paired_tests": len(tests)}
