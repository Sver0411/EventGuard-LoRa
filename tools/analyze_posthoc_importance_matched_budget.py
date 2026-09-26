#!/usr/bin/env python3
"""Analyze only the separate post-hoc host-only IMB diagnostic."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eventguard.research_analysis import describe, holm_adjust, wilcoxon_exact  # noqa: E402
from tools.run_posthoc_importance_matched_budget import OUT, RATES, SEEDS, SOURCE, STRATEGY  # noqa: E402

METRICS = (
    "critical_event_delivery_ratio", "important_event_delivery_ratio",
    "overall_delivery_ratio", "physical_data_transmissions", "ack_count",
    "data_bytes_transmitted", "ack_bytes_transmitted", "total_bytes_transmitted",
    "critical_traffic_share", "important_traffic_share",
    "estimated_communication_time_ms",
)
PRIMARY = ("important_event_delivery_ratio", "overall_delivery_ratio")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def load_inputs() -> tuple[dict, dict, dict]:
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    provenance = json.loads((OUT / "provenance.json").read_text(encoding="utf-8"))
    if manifest["status"] != "complete" or manifest["new_host_runs"] != 500:
        raise AssertionError("diagnostic run manifest is incomplete")
    if sha256_file(OUT / "runs.csv") != manifest["runs_csv_sha256"]:
        raise AssertionError("IMB runs changed after completion")
    if sha256_file(SOURCE) != manifest["source_runs_csv_sha256"]:
        raise AssertionError("frozen host source CSV changed")
    if sha256_file(OUT / "protocol_snapshot.md") != manifest["protocol_sha256"]:
        raise AssertionError("protocol snapshot changed")
    imb = {}
    for row in read_csv(OUT / "runs.csv"):
        key = (float(row["loss_rate"]), int(row["seed"]))
        if row["strategy"] != STRATEGY or row["loss_model"] != "RANDOM_COPY" or key in imb:
            raise AssertionError(f"unexpected IMB row {key}")
        imb[key] = row
    if set(imb) != {(rate, seed) for rate in RATES for seed in SEEDS}:
        raise AssertionError("IMB matrix lacks 500 unique rate/seed pairs")
    frozen = {}
    for row in read_csv(SOURCE):
        if row["loss_model"] != "RANDOM_COPY" or row["strategy"] not in ("EVENTGUARD", "IMPORTANCE_ONLY"):
            continue
        key = (float(row["loss_rate"]), int(row["seed"]), row["strategy"])
        if key in frozen:
            raise AssertionError(f"duplicate frozen row {key}")
        frozen[key] = row
    for (rate, seed), row in imb.items():
        eg = frozen[(rate, seed, "EVENTGUARD")]
        io = frozen[(rate, seed, "IMPORTANCE_ONLY")]
        if not (row["trace_sha256"] == eg["trace_sha256"] == io["trace_sha256"]):
            raise AssertionError(f"trace mismatch {rate}/{seed}")
        if not (row["channel_calendar"] == eg["channel_calendar"] == io["channel_calendar"]):
            raise AssertionError(f"calendar mismatch {rate}/{seed}")
        if int(row["physical_data_transmissions"]) != int(eg["physical_data_transmissions"]):
            raise AssertionError(f"DATA-copy budget mismatch {rate}/{seed}")
        if int(row["eventguard_reference_budget"]) != int(eg["physical_data_transmissions"]):
            raise AssertionError(f"reference budget mismatch {rate}/{seed}")
    return imb, frozen, provenance


def descriptive_rows(imb: dict, frozen: dict) -> list[dict]:
    output = []
    for rate in RATES:
        for strategy in ("EVENTGUARD", STRATEGY, "IMPORTANCE_ONLY"):
            group = [imb[(rate, seed)] if strategy == STRATEGY else frozen[(rate, seed, strategy)]
                     for seed in SEEDS]
            for metric in METRICS:
                if metric == "important_traffic_share" and strategy != STRATEGY:
                    continue  # Not retained by frozen host CSV; do not reconstruct it.
                if metric == "estimated_communication_time_ms" and strategy != STRATEGY:
                    values = [float(row["total_bytes_transmitted"]) * 10 / 9600 * 1000 for row in group]
                else:
                    values = [float(row[metric]) for row in group]
                output.append({"loss_model": "RANDOM_COPY", "loss_rate": rate,
                               "strategy": strategy, "metric": metric, **describe(values)})
    return output


def primary_rows(imb: dict, frozen: dict) -> list[dict]:
    output = []
    for rate in (0.20, 0.30):
        for metric in PRIMARY:
            eg = [float(frozen[(rate, seed, "EVENTGUARD")][metric]) for seed in SEEDS]
            baseline = [float(imb[(rate, seed)][metric]) for seed in SEEDS]
            diffs = [a - b for a, b in zip(eg, baseline)]
            stats = describe(diffs)
            test = wilcoxon_exact(diffs)
            output.append({
                "loss_model": "RANDOM_COPY", "loss_rate": rate,
                "comparison": "EVENTGUARD - IMPORTANCE_MATCHED_BUDGET", "metric": metric,
                "n_seed_pairs": 100, "mean_eventguard": statistics.mean(eg),
                "mean_imb": statistics.mean(baseline),
                "mean_paired_difference": stats["mean"],
                "median_paired_difference": stats["median"],
                "std_paired_difference": stats["std"],
                "ci95_low": stats["ci95_low"], "ci95_high": stats["ci95_high"],
                "wins": sum(d > 1e-12 for d in diffs),
                "ties": sum(abs(d) <= 1e-12 for d in diffs),
                "losses": sum(d < -1e-12 for d in diffs),
                "n_nonzero": test["n_nonzero"],
                "w_positive": test["w_positive"],
                "w_negative": test["w_negative"],
                "p_two_sided": test["p_two_sided"],
                "rank_biserial": test["rank_biserial"],
            })
    adjusted = holm_adjust([row["p_two_sided"] for row in output])
    for row, value in zip(output, adjusted):
        row["holm_p_posthoc_4_tests"] = value
        if row["wins"] + row["ties"] + row["losses"] != 100:
            raise AssertionError("paired classification count mismatch")
    return output


def make_plots(imb: dict, frozen: dict, primary: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plots = OUT / "plots"
    plots.mkdir(exist_ok=True)
    x = [rate * 100 for rate in RATES]
    styles = {
        "EVENTGUARD": dict(color="0.08", marker="o", linestyle="-", label="EventGuard"),
        STRATEGY: dict(color="0.34", marker="s", linestyle="--", label="Importance Matched Budget"),
        "IMPORTANCE_ONLY": dict(color="0.73", marker="^", linestyle=":",
                                label="Importance Only (cost-unmatched)"),
    }
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.6), constrained_layout=True)
    for ax, metric, title in zip(axes, PRIMARY, ("IMPORTANT delivery", "Overall delivery")):
        for strategy, style in styles.items():
            values = [statistics.mean(
                float((imb[(rate, seed)] if strategy == STRATEGY
                       else frozen[(rate, seed, strategy)])[metric]) for seed in SEEDS)
                      for rate in RATES]
            ax.plot(x, values, linewidth=1.7, markersize=5, **style)
        ax.set_title(title)
        ax.set_xlabel("Configured RANDOM_COPY loss (%)")
        ax.set_ylabel("Delivered fraction")
        ax.set_xticks(x)
        ax.set_ylim(0.75, 1.02)
        ax.grid(axis="y", color="0.88")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, -0.07), ncol=3, frameon=False)
    for suffix in ("png", "pdf"):
        fig.savefig(plots / f"delivery_by_rate.{suffix}", dpi=220, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.4, 4.5), constrained_layout=True)
    labels = []
    for i, row in enumerate(primary):
        rate = float(row["loss_rate"])
        metric = row["metric"]
        diffs = [float(frozen[(rate, seed, "EVENTGUARD")][metric]) -
                 float(imb[(rate, seed)][metric]) for seed in SEEDS]
        ax.scatter([i + 1] * len(diffs), diffs, color="0.65", s=13, alpha=.55, zorder=2)
        ax.errorbar(i + 1, row["mean_paired_difference"],
                    yerr=[[row["mean_paired_difference"] - row["ci95_low"]],
                          [row["ci95_high"] - row["mean_paired_difference"]]],
                    fmt="D", color="0.08", capsize=4, markersize=6, zorder=3)
        labels.append(f"{int(rate * 100)}%\n{'IMPORTANT' if metric.startswith('important') else 'Overall'}")
    ax.axhline(0, color="0.35", linewidth=1)
    ax.set_xticks(range(1, 5), labels)
    ax.set_ylabel("Paired delivery difference (EventGuard − IMB)")
    ax.set_title("Post-hoc diagnostic: 100 paired seeds per contrast")
    ax.grid(axis="y", color="0.9")
    for suffix in ("png", "pdf"):
        fig.savefig(plots / f"primary_paired_differences.{suffix}", dpi=220, bbox_inches="tight")
    plt.close(fig)


def report(imb: dict, frozen: dict, summary: list[dict], primary: list[dict], provenance: dict) -> str:
    def mean(rate: float, strategy: str, metric: str) -> float:
        return next(float(row["mean"]) for row in summary
                    if float(row["loss_rate"]) == rate and row["strategy"] == strategy
                    and row["metric"] == metric)
    lines = [
        "# Post-hoc importance-matched-budget host diagnostic", "",
        "## 1. Question", "",
        "At an exact DATA-copy budget, does EventGuard's link-state-aware copy placement outperform the specified importance-aware, link-blind counterfactual on IMPORTANT or overall delivery?", "",
        "## 2. Why the existing comparison is confounded", "",
        "EventGuard uses more DATA copies than Importance Only while also using link state. Their IMPORTANT/overall difference cannot be assigned specifically to placement without matching that budget.", "",
        "## 3. Post-hoc status", "",
        "This protocol was fixed after inspection of frozen v1 results and before producing the new IMB outcomes. It is not preregistered, not part of the original 8,000-run matrix, and not part of v1.0.0. These results are exploratory.", "",
        "## 4. Baseline definition", "",
        "IMB starts with predicted NORMAL/IMPORTANT/CRITICAL copies of 1/2/3, spends EventGuard's frozen realized extra DATA-copy budget on IMPORTANT first, then NORMAL in two headroom rounds, using only seeded SHA-256 order within a class. The allocator takes predicted classes, budget, and seed only. It does not read link state, ACK history, ground truth, sensor severity, calendar outcomes, or future delivery. It is an offline counterfactual rather than a deployable advance budget predictor.", "",
        "## 5. Frozen inputs reused", "",
        f"The experiment uses `RANDOM_COPY`, rates 0/5/10/20/30%, seeds 31–130, the unchanged 54-sample trace generator, classifier, canonical DATA/ACK calendar, and the 500 preserved EventGuard reference rows in `results/pre_hardware_v1/runs.csv` (SHA-256 `{provenance['source_runs_csv_sha256']}`). Exactly 500 new IMB runs were generated.", "",
        "## 6. Exact-budget validation", "",
        "All 500 pairs passed exact DATA-copy budget, trace hash, and canonical calendar identifier checks. All 500 frozen Importance Only consistency replays matched its saved delivery/cost fields; these are checks, not additional study runs. Matching DATA copies does not imply equal ACK frames or total bytes.", "",
        "## 7. Primary diagnostic results", "",
        "Each row is 100 paired seeds. Differences are EventGuard minus IMB in delivery-ratio units. CI is the repository's two-sided 95% paired-mean interval. Exact conditional signed-rank p-values and Holm values form a separate four-test post-hoc family and are exploratory descriptors.", "",
        "| Rate | KPI | EG mean | IMB mean | Paired mean [95% CI] | Median | SD | W/T/L | n nonzero | p | Holm p | Rank-biserial |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary:
        lines.append(
            f"| {float(row['loss_rate']):.0%} | {'IMPORTANT' if row['metric'].startswith('important') else 'Overall'} "
            f"| {row['mean_eventguard']:.4f} | {row['mean_imb']:.4f} "
            f"| {row['mean_paired_difference']:+.5f} [{row['ci95_low']:+.5f}, {row['ci95_high']:+.5f}] "
            f"| {row['median_paired_difference']:+.4f} | {row['std_paired_difference']:.4f} "
            f"| {row['wins']}/{row['ties']}/{row['losses']} | {row['n_nonzero']} "
            f"| {row['p_two_sided']:.5g} | {row['holm_p_posthoc_4_tests']:.5g} "
            f"| {row['rank_biserial']:+.3f} |")
    lines.extend(["", "## 8. Secondary and descriptive results", "",
                  "The 0/5/10% conditions and CRITICAL/cost endpoints are descriptive only; full mean, median, SD and 95% CI rows are in `summary.csv`.", "",
                  "| Rate | EG − IMB CRITICAL delivery | EG / IMB DATA copies | EG − IMB ACK frames | EG − IMB total bytes |",
                  "|---|---:|---:|---:|---:|"])
    for rate in RATES:
        lines.append(f"| {rate:.0%} | {mean(rate, 'EVENTGUARD', 'critical_event_delivery_ratio') - mean(rate, STRATEGY, 'critical_event_delivery_ratio'):+.4f} "
                     f"| {mean(rate, 'EVENTGUARD', 'physical_data_transmissions'):.2f} / {mean(rate, STRATEGY, 'physical_data_transmissions'):.2f} "
                     f"| {mean(rate, 'EVENTGUARD', 'ack_count') - mean(rate, STRATEGY, 'ack_count'):+.2f} "
                     f"| {mean(rate, 'EVENTGUARD', 'total_bytes_transmitted') - mean(rate, STRATEGY, 'total_bytes_transmitted'):+.1f} |")
    lines.extend(["", "The IMB run records also include retrospective ground-truth CRITICAL/IMPORTANT traffic shares and predicted-class copy totals. The frozen v1 CSV does not retain IMPORTANT traffic share, so it is not reconstructed for EventGuard or Importance Only.", "",
                  "![Delivery by loss rate](plots/delivery_by_rate.png)", "",
                  "![Seed-level paired primary differences](plots/primary_paired_differences.png)", "",
                  "## 9. Interpretation", ""])
    for rate in (0.20, 0.30):
        important = next(row for row in primary if float(row["loss_rate"]) == rate and row["metric"] == "important_event_delivery_ratio")
        overall = next(row for row in primary if float(row["loss_rate"]) == rate and row["metric"] == "overall_delivery_ratio")
        lines.append(f"- RC{int(rate*100)}: EventGuard versus IMB IMPORTANT paired mean {important['mean_paired_difference']:+.4f} "
                     f"({important['wins']}/{important['ties']}/{important['losses']} W/T/L); overall {overall['mean_paired_difference']:+.4f} "
                     f"({overall['wins']}/{overall['ties']}/{overall['losses']} W/T/L).")
    directions = [math.copysign(1, row["mean_paired_difference"]) if abs(row["mean_paired_difference"]) > 1e-12 else 0
                  for row in primary]
    if all(direction == 0 for direction in directions):
        interpretation = "The earlier EventGuard-versus-Importance-Only secondary gains are largely consistent with additional budget; this diagnostic does not show better link-aware placement than IMB."
    elif all(direction <= 0 for direction in directions):
        interpretation = "EventGuard did not outperform this simpler importance-aware matched-budget allocator on any primary mean contrast; the earlier secondary gains cannot be attributed to superior link-aware placement here."
    elif all(direction > 0 for direction in directions):
        interpretation = "EventGuard has higher primary mean delivery than this specific matched-budget, link-blind allocator in all four contrasts; examine effect size, paired consistency and cost before attributing practical value."
    else:
        interpretation = "The four primary contrasts have mixed directions; the relative contribution of extra budget and this link-aware placement is condition- and KPI-dependent."
    lines.extend(["", interpretation, "",
                  "The previous EventGuard-versus-Importance-Only secondary gains are largely reproduced by IMB at the same EventGuard DATA-copy budget. The following mean uplifts over the cost-unmatched Importance Only reference show how much of each earlier gain this specified link-blind allocator retains:", "",
                  "| Rate | KPI | EG − IO | IMB − IO | EG − IMB |",
                  "|---|---|---:|---:|---:|"])
    for rate in (0.20, 0.30):
        for metric, label in (("important_event_delivery_ratio", "IMPORTANT"),
                              ("overall_delivery_ratio", "Overall")):
            eg_io = mean(rate, "EVENTGUARD", metric) - mean(rate, "IMPORTANCE_ONLY", metric)
            imb_io = mean(rate, STRATEGY, metric) - mean(rate, "IMPORTANCE_ONLY", metric)
            lines.append(f"| {rate:.0%} | {label} | {eg_io:+.4f} | {imb_io:+.4f} | {eg_io - imb_io:+.4f} |")
    lines.extend(["", "**Answers to the diagnostic questions.** IMB's mean IMPORTANT delivery exceeds EventGuard's at both 20% and 30%. EventGuard's mean overall delivery is slightly higher than IMB's at 20% and slightly lower at 30%. Across all four comparisons, the IMB-minus-Importance-Only uplift is close to the prior EventGuard-minus-Importance-Only uplift. Thus extra DATA-copy budget spent with importance information is a plausible main explanation; these host results do not show a consistent incremental advantage for EventGuard's link-aware placement over this specific IMB rule. This is a descriptive mechanism comparison, not a causal decomposition of every possible allocator.", "",
                  "At 20% IMPORTANT delivery, the paired-mean t interval narrowly lies below zero while the exact signed-rank p-value is 0.0918 and Holm-adjusted p-value is 0.3672; 90 of 100 pairs tie. These exploratory summaries use different assumptions and do not support a confirmatory claim.", "",
                  "## 10. What this does NOT establish", "",
                  "This host-only result does not validate E220 RF channel prediction, fading response, interference tolerance, range, or universal IoT reliability. It isolates placement relative to one fixed link-blind allocator, not every possible importance-aware allocator. P-values are exploratory, not confirmatory proof.", "",
                  "## 11. Hardware follow-up decision left open", "",
                  "No hardware experiment is performed or authorized by this report. Review the host diagnostic before deciding whether a new, separately scoped hardware comparison is warranted.", ""])
    return "\n".join(lines)


def analyze() -> None:
    imb, frozen, provenance = load_inputs()
    summary = descriptive_rows(imb, frozen)
    primary = primary_rows(imb, frozen)
    write_csv(OUT / "summary.csv", summary)
    write_csv(OUT / "paired_tests.csv", primary)
    make_plots(imb, frozen, primary)
    (OUT / "report.md").write_text(report(imb, frozen, summary, primary, provenance), encoding="utf-8")
    provenance["analysis_generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    provenance["analysis_git_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    provenance["analysis_script_sha256"] = sha256_file(Path(__file__))
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print("POSTHOC_IMB_ANALYSIS_PASS: 4 primary contrasts, separate Holm family")


if __name__ == "__main__":
    analyze()
