from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from .model import Strategy

METRICS = (
    "overall_delivery_ratio", "critical_event_delivery_ratio", "important_event_delivery_ratio",
    "lost_logical_packets", "sequence_gaps", "out_of_order_packets",
    "normal_delivery_ratio", "critical_event_miss_rate", "total_bytes_transmitted",
    "redundancy_overhead", "ack_count", "mean_delivery_latency_ms", "p50_delivery_latency_ms",
    "p95_delivery_latency_ms", "p99_delivery_latency_ms", "retransmissions", "duplicate_packets",
    "crc_errors", "communication_cost_per_delivered_critical_event",
)


def _t_critical(df: int) -> float:
    # Two-sided 95% Student-t critical values; normal approximation for larger samples.
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
             8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
             15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
             21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
             27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}
    return table.get(df, 1.96)


def confidence_interval(values: list[float]) -> tuple[float, float, float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    mean = statistics.mean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    margin = _t_critical(len(values) - 1) * std / math.sqrt(len(values)) if len(values) > 1 else 0.0
    return mean, std, statistics.median(values), mean - margin, mean + margin


def _beta_cf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30: d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30: c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30: d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30: c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return bt * _beta_cf(a, b, x) / a
    return 1 - bt * _beta_cf(b, a, 1 - x) / b


def paired_t_test(left: list[float], right: list[float]) -> dict:
    if len(left) != len(right) or len(left) < 2:
        return {"n": min(len(left), len(right)), "mean_difference": None, "t": None, "p_two_sided": None}
    diffs = [a - b for a, b in zip(left, right)]
    mean = statistics.mean(diffs)
    std = statistics.stdev(diffs)
    if std == 0:
        p = 1.0 if mean == 0 else 0.0
        t = 0.0 if mean == 0 else math.inf
    else:
        t = mean / (std / math.sqrt(len(diffs)))
        x = (len(diffs) - 1) / ((len(diffs) - 1) + t * t)
        p = _regularized_beta(x, (len(diffs) - 1) / 2, 0.5)
    return {"n": len(diffs), "mean_difference": mean, "t": t, "p_two_sided": p}


def aggregate_runs(runs: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for run in runs:
        groups[(run["strategy"], run["loss_model"], run["loss_rate"])].append(run)
    result = []
    for (strategy, model, rate), rows in sorted(groups.items(), key=lambda item: (item[0][1], item[0][2], item[0][0])):
        record = {"strategy": strategy, "loss_model": model, "loss_rate": rate, "n": len(rows)}
        for metric in METRICS:
            values = [float(row[metric]) for row in rows if row.get(metric) is not None]
            mean, std, median, low, high = confidence_interval(values)
            record[f"{metric}_mean"] = mean
            record[f"{metric}_std"] = std
            record[f"{metric}_median"] = median
            record[f"{metric}_ci95_low"] = low
            record[f"{metric}_ci95_high"] = high
        result.append(record)
    return result


def comparisons(runs: list[dict], aggregate: list[dict], tolerance: float = 0.15) -> dict:
    by_condition = defaultdict(dict)
    for row in aggregate:
        by_condition[(row["loss_model"], row["loss_rate"])][row["strategy"]] = row
    raw = defaultdict(dict)
    for row in runs:
        raw[(row["loss_model"], row["loss_rate"], row["seed"])][row["strategy"]] = row
    tests = []
    for (model, rate, seed), values in sorted(raw.items()):
        eventguard = values.get(Strategy.EVENTGUARD.value)
        if not eventguard:
            continue
        for baseline in (Strategy.NO_PROTECTION.value, Strategy.FIXED_REDUNDANCY.value):
            if baseline not in values:
                continue
            tests.append({"loss_model": model, "loss_rate": rate, "seed": seed,
                          "comparison": f"EVENTGUARD - {baseline}",
                          "critical_delivery_difference": eventguard["critical_event_delivery_ratio"] - values[baseline]["critical_event_delivery_ratio"],
                          "bytes_difference": eventguard["total_bytes_transmitted"] - values[baseline]["total_bytes_transmitted"]})
    paired = []
    for (model, rate), strategies in sorted(by_condition.items()):
        for baseline in (Strategy.NO_PROTECTION.value, Strategy.FIXED_REDUNDANCY.value):
            pair_rows = [v for (m, r, _), v in raw.items() if m == model and r == rate and Strategy.EVENTGUARD.value in v and baseline in v]
            pair_rows.sort(key=lambda v: v[Strategy.EVENTGUARD.value]["seed"])
            paired.append({"loss_model": model, "loss_rate": rate, "comparison": f"EVENTGUARD vs {baseline}",
                           "critical_delivery": paired_t_test([p[Strategy.EVENTGUARD.value]["critical_event_delivery_ratio"] for p in pair_rows], [p[baseline]["critical_event_delivery_ratio"] for p in pair_rows]),
                           "total_bytes": paired_t_test([p[Strategy.EVENTGUARD.value]["total_bytes_transmitted"] for p in pair_rows], [p[baseline]["total_bytes_transmitted"] for p in pair_rows])})
    cost_match = []
    delivery_match = []
    for model in sorted({row["loss_model"] for row in aggregate}):
        eg_points = [row for row in aggregate if row["loss_model"] == model and row["strategy"] == "EVENTGUARD"]
        fixed_points = [row for row in aggregate if row["loss_model"] == model and row["strategy"] == "FIXED_REDUNDANCY"]
        for eg in eg_points:
            if not fixed_points:
                continue
            closest_cost = min(fixed_points, key=lambda row: abs(row["total_bytes_transmitted_mean"] - eg["total_bytes_transmitted_mean"]))
            cost_gap = abs(closest_cost["total_bytes_transmitted_mean"] - eg["total_bytes_transmitted_mean"]) / max(1, closest_cost["total_bytes_transmitted_mean"])
            if cost_gap <= tolerance:
                cost_match.append({"loss_model": model, "eventguard_loss_rate": eg["loss_rate"],
                                   "fixed_loss_rate": closest_cost["loss_rate"], "relative_byte_gap": cost_gap,
                                   "eventguard_bytes": eg["total_bytes_transmitted_mean"],
                                   "fixed_bytes": closest_cost["total_bytes_transmitted_mean"],
                                   "eventguard_critical_delivery": eg["critical_event_delivery_ratio_mean"],
                                   "fixed_critical_delivery": closest_cost["critical_event_delivery_ratio_mean"],
                                   "critical_delivery_difference": eg["critical_event_delivery_ratio_mean"] - closest_cost["critical_event_delivery_ratio_mean"]})
            closest_delivery = min(fixed_points, key=lambda row: abs(row["critical_event_delivery_ratio_mean"] - eg["critical_event_delivery_ratio_mean"]))
            delivery_gap = abs(eg["critical_event_delivery_ratio_mean"] - closest_delivery["critical_event_delivery_ratio_mean"])
            delivery_match.append({"loss_model": model, "eventguard_loss_rate": eg["loss_rate"],
                                   "fixed_loss_rate": closest_delivery["loss_rate"], "critical_delivery_gap": delivery_gap,
                                   "eventguard_critical_delivery": eg["critical_event_delivery_ratio_mean"],
                                   "fixed_critical_delivery": closest_delivery["critical_event_delivery_ratio_mean"],
                                   "eventguard_bytes": eg["total_bytes_transmitted_mean"],
                                   "fixed_bytes": closest_delivery["total_bytes_transmitted_mean"],
                                   "byte_difference": eg["total_bytes_transmitted_mean"] - closest_delivery["total_bytes_transmitted_mean"]})
    return {"paired_seed_differences": tests, "paired_significance": paired,
            "similar_cost_eventguard_vs_fixed": cost_match,
            "similar_delivery_eventguard_vs_fixed": delivery_match}


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _plot(all_runs: list[dict], out: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = aggregate_runs(all_runs)
    generated = []
    def subset(model=None):
        return [r for r in groups if model is None or r["loss_model"] == model]
    colors = {"NO_PROTECTION": "#64748b", "FIXED_REDUNDANCY": "#f59e0b", "EVENTGUARD": "#2563eb"}
    for filename, metric, model in (
        ("critical_delivery_vs_loss.png", "critical_event_delivery_ratio", None),
        ("critical_miss_vs_loss.png", "critical_event_miss_rate", None),
        ("overall_delivery_vs_loss.png", "overall_delivery_ratio", None),
        ("burst_loss_performance.png", "critical_event_delivery_ratio", "BURST"),
    ):
        fig, ax = plt.subplots(figsize=(8, 5))
        for strategy in colors:
            for loss_model in sorted({r["loss_model"] for r in subset(model)}):
                points = sorted((r for r in subset(model) if r["strategy"] == strategy and r["loss_model"] == loss_model), key=lambda r: r["loss_rate"])
                if not points: continue
                suffix = "" if model else f" ({loss_model.lower()})"
                ax.errorbar([r["loss_rate"] * 100 for r in points], [r[f"{metric}_mean"] for r in points],
                            yerr=[r[f"{metric}_std"] for r in points], marker="o", capsize=3,
                            color=colors[strategy], linestyle="-" if loss_model == "RANDOM" else "--",
                            label=strategy + suffix)
        ax.set(xlabel="Configured application-layer loss (%)", ylabel=metric.replace("_", " ").title(), ylim=(-0.03, 1.03))
        ax.grid(True, alpha=.25); ax.legend(fontsize=8); fig.tight_layout()
        path = out / filename; fig.savefig(path, dpi=160); plt.close(fig); generated.append(filename)

    fig, ax = plt.subplots(figsize=(8, 5))
    for strategy in colors:
        rows = [r for r in groups if r["strategy"] == strategy]
        ax.scatter([r["total_bytes_transmitted_mean"] for r in rows], [r["critical_event_delivery_ratio_mean"] for r in rows],
                   s=40, alpha=.75, color=colors[strategy], label=strategy)
    ax.set(xlabel="Mean transmitted bytes per run", ylabel="Critical event delivery rate", ylim=(-.03, 1.03))
    ax.grid(True, alpha=.25); ax.legend(); fig.tight_layout()
    path = out / "critical_delivery_vs_overhead.png"; fig.savefig(path, dpi=160); plt.close(fig); generated.append(path.name)

    fig, ax = plt.subplots(figsize=(8, 5))
    names = list(colors)
    for i, strategy in enumerate(names):
        vals = [r["total_bytes_transmitted"] for r in all_runs if r["strategy"] == strategy]
        ax.boxplot(vals, positions=[i], widths=.55, patch_artist=True,
                   boxprops={"facecolor": colors[strategy], "alpha": .65})
    ax.set_xticks(range(len(names)), names, rotation=15); ax.set_ylabel("Total transmitted bytes per run")
    ax.grid(True, axis="y", alpha=.25); fig.tight_layout()
    path = out / "bytes_vs_strategy.png"; fig.savefig(path, dpi=160); plt.close(fig); generated.append(path.name)

    fig, ax = plt.subplots(figsize=(8, 5))
    for i, strategy in enumerate(names):
        vals = [r["mean_delivery_latency_ms"] for r in all_runs if r["strategy"] == strategy and r["delivered_packets"]]
        ax.boxplot(vals or [0], positions=[i], widths=.55, patch_artist=True,
                   boxprops={"facecolor": colors[strategy], "alpha": .65})
    ax.set_xticks(range(len(names)), names, rotation=15); ax.set_ylabel("Mean logical delivery latency (ms)")
    ax.grid(True, axis="y", alpha=.25); fig.tight_layout()
    path = out / "latency_vs_strategy.png"; fig.savefig(path, dpi=160); plt.close(fig); generated.append(path.name)

    fig, ax = plt.subplots(figsize=(8, 5))
    truth_classes = (("normal_delivery_ratio", "NORMAL"), ("important_event_delivery_ratio", "IMPORTANT"), ("critical_event_delivery_ratio", "CRITICAL"))
    strategies = list(colors)
    width = .24
    for cls_idx, (metric, label) in enumerate(truth_classes):
        for strat_idx, strategy in enumerate(strategies):
            vals = [r[metric] for r in all_runs if r["strategy"] == strategy]
            ax.bar(cls_idx + (strat_idx - 1) * width, statistics.mean(vals) if vals else 0, width,
                   color=colors[strategy], label=strategy if cls_idx == 0 else None)
    ax.set_xticks(range(3), [label for _, label in truth_classes]); ax.set_ylabel("Mean delivery ratio")
    ax.set_ylim(0, 1.03); ax.legend(fontsize=8); ax.grid(True, axis="y", alpha=.25); fig.tight_layout()
    path = out / "event_class_delivery.png"; fig.savefig(path, dpi=160); plt.close(fig); generated.append(path.name)
    return generated


def analyze_runs(runs: list[dict], results_dir: str | Path, paper_path: str | Path,
                 provenance: dict | None = None) -> dict:
    result_root = Path(results_dir)
    metrics_dir = result_root / "metrics"
    plots_dir = result_root / "plots"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)
    aggregates = aggregate_runs(runs)
    compare = comparisons(runs, aggregates)
    summary = {"provenance": provenance or {}, "run_count": len(runs), "runs": runs,
               "aggregates": aggregates, "comparisons": compare}
    (result_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    _write_csv(result_root / "summary.csv", aggregates)
    _write_csv(metrics_dir / "run_metrics.csv", runs)
    plots = _plot(runs, plots_dir)
    summary["plots"] = plots
    (result_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    run_groups = {strategy: [r for r in runs if r["strategy"] == strategy]
                  for strategy in ("NO_PROTECTION", "FIXED_REDUNDANCY", "EVENTGUARD")}
    first_run = runs[0] if runs else {}
    config = first_run.get("config", {})
    radio = config.get("e220", {}) or (provenance or {}).get("e220_profile", {})
    hardware = (provenance or {}).get("hardware", {})
    chip_macs = hardware.get("chip_macs", {})
    injection_info = (provenance or {}).get("application_layer_injection")
    if injection_info is None:
        injection_info = "APPLICATION_LAYER_INJECTION" in (provenance or {}).get("source", "")
    injection_text = ("yes" if injection_info is True else
                      "no" if injection_info is False else str(injection_info))
    seeds = sorted({r.get("seed") for r in runs if r.get("seed") is not None})
    sample_counts = sorted({r.get("sample_count") for r in runs if r.get("sample_count") is not None})

    def overall_mean(strategy: str, metric: str) -> float | None:
        values = [float(row[metric]) for row in run_groups[strategy] if row.get(metric) is not None]
        return statistics.mean(values) if values else None

    def condition_mean(strategy: str, model: str, rate: float, metric: str) -> float | None:
        rows = [row for row in aggregates if row["strategy"] == strategy
                and row["loss_model"] == model and math.isclose(row["loss_rate"], rate)]
        return rows[0].get(f"{metric}_mean") if rows else None

    def fmt(value: float | None, digits: int = 3) -> str:
        return "n/a" if value is None else f"{value:.{digits}f}"

    def overall_row(strategy: str) -> tuple[str, str, str, str, str]:
        return (strategy, fmt(overall_mean(strategy, "critical_event_delivery_ratio")),
                fmt(overall_mean(strategy, "overall_delivery_ratio")),
                fmt(overall_mean(strategy, "total_bytes_transmitted"), 1),
                fmt(overall_mean(strategy, "mean_delivery_latency_ms"), 1))

    seed_text = ", ".join(str(seed) for seed in seeds) or "not recorded"
    sample_text = ", ".join(str(count) for count in sample_counts) or "not recorded"
    matrix_text = (f"{len(run_groups['NO_PROTECTION'])} no-protection, "
                   f"{len(run_groups['FIXED_REDUNDANCY'])} fixed-redundancy, and "
                   f"{len(run_groups['EVENTGUARD'])} EventGuard runs; "
                   f"{len(seeds)} seeds ({seed_text}); loss models "
                   f"{', '.join(sorted({r['loss_model'] for r in runs})) or 'not recorded'}; "
                   f"configured loss rates "
                   f"{', '.join(f'{rate:.0%}' for rate in sorted({r['loss_rate'] for r in runs})) or 'not recorded'}.")
    radio_text = (f"UART{radio['uart_num']} at {radio['baud']} baud; TX/RX/AUX="
                  f"{radio['tx_gpio']}/{radio['rx_gpio']}/{radio['aux_gpio']}; "
                  f"M0/M1={radio['m0_gpio']}/{radio['m1_gpio']}; address=0x{radio['address']:04X}; "
                  f"REG0=0x{radio['reg0']:02X}; channel register=0x{radio['channel']:02X}.") if radio else "not recorded"

    report = ["# EventGuard-LoRa Automated Experiment Report", "",
              f"- Run count: {len(runs)}",
              f"- Source: {(provenance or {}).get('source', 'not recorded')}",
              f"- Physical radio: {bool((provenance or {}).get('physical_radio', False))}; synthetic trace: {bool((provenance or {}).get('synthetic_trace', False))}; application-layer injection: {injection_text}.",
              f"- Matrix: {matrix_text}",
              f"- Synthetic samples per run: {sample_text}.",
              f"- E220 profile: {radio_text}",
              f"- Sensor MAC: {chip_macs.get('sensor', first_run.get('sensor_mac', 'not recorded'))}; gateway MAC: {chip_macs.get('gateway', first_run.get('gateway_mac', 'not recorded'))}.",
              "", "## Overall strategy means", "",
              "Each strategy has equal representation across its configured conditions. Bytes and latency are per run.",
              "", "| Strategy | Critical delivery | Overall delivery | Bytes/run | Mean latency (ms) |",
              "|---|---:|---:|---:|---:|"]
    for strategy in ("EVENTGUARD", "FIXED_REDUNDANCY", "NO_PROTECTION"):
        row = overall_row(strategy)
        report.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} |")

    eg_bytes = overall_mean("EVENTGUARD", "total_bytes_transmitted")
    fixed_bytes = overall_mean("FIXED_REDUNDANCY", "total_bytes_transmitted")
    bytes_overhead = ((eg_bytes / fixed_bytes - 1.0) if eg_bytes is not None and fixed_bytes else None)
    if (provenance or {}).get("physical_radio"):
        experiment_interpretation = ("The radios transmitted actual E220 frames, but the configured loss percentages are deterministic post-reception application-layer drops, not measured RF packet-error rates.")
    else:
        experiment_interpretation = ("This is a host reference simulation; no physical E220 frames were transmitted. Configured losses are deterministic DATA and ACK erasures.")
    report.extend(["", "## Interpretation", "",
                   f"These results describe the recorded trace, seeds, hardware, and configured application-layer loss model. {experiment_interpretation}"])
    if bytes_overhead is not None:
        report.append(f"Across all runs, EventGuard used {bytes_overhead:.1%} more transmitted bytes per run on average than fixed redundancy. A higher aggregate delivery rate therefore does not establish an equal-cost advantage.")
    for model in ("RANDOM", "BURST"):
        for rate in (0.20, 0.30):
            eg_delivery = condition_mean("EVENTGUARD", model, rate, "critical_event_delivery_ratio")
            fixed_delivery = condition_mean("FIXED_REDUNDANCY", model, rate, "critical_event_delivery_ratio")
            if eg_delivery is not None and fixed_delivery is not None:
                report.append(f"At {model.lower()} {rate:.0%} configured loss, EventGuard critical delivery was {eg_delivery:.3f} versus {fixed_delivery:.3f} for fixed redundancy ({eg_delivery - fixed_delivery:+.3f}).")
    report.extend([f"Paired p-values below are exploratory: there are {len(seeds)} seeds per condition, {len(compare['paired_significance'])} critical-delivery comparisons, and no multiplicity correction. Treat them as descriptive rather than confirmatory evidence.",
                   "", "## Strategy summaries", "",
              "| Strategy | Loss model | Loss | Critical delivery (mean ± SD) | Overall delivery | Bytes | Mean latency (ms) |",
              "|---|---:|---:|---:|---:|---:|---:|"])
    for row in aggregates:
        report.append(f"| {row['strategy']} | {row['loss_model']} | {row['loss_rate']:.0%} | {row['critical_event_delivery_ratio_mean']:.3f} ± {row['critical_event_delivery_ratio_std']:.3f} | {row['overall_delivery_ratio_mean']:.3f} | {row['total_bytes_transmitted_mean']:.1f} | {row['mean_delivery_latency_ms_mean']:.1f} |")
    report.extend(["", "## Similar-cost and similar-delivery comparisons", "",
                   "Matches are selected automatically within the same loss model across configured loss-rate points. Similar cost uses a 15% relative byte tolerance. The matched points can have different configured loss rates, so these are traffic-budget comparisons across conditions, not same-channel-condition claims.",
                   "", "### Similar transmitted bytes", "",
                   "| Loss model | EG loss | Fixed loss | Byte gap | EG critical delivery | Fixed critical delivery | Difference |",
                   "|---|---:|---:|---:|---:|---:|---:|"])
    for item in compare["similar_cost_eventguard_vs_fixed"]:
        report.append(f"| {item['loss_model']} | {item['eventguard_loss_rate']:.0%} | {item['fixed_loss_rate']:.0%} | {item['relative_byte_gap']:.1%} | {item['eventguard_critical_delivery']:.3f} | {item['fixed_critical_delivery']:.3f} | {item['critical_delivery_difference']:+.3f} |")
    report.extend(["", "### Similar critical delivery", "",
                   "| Loss model | EG loss | Fixed loss | Delivery gap | EG bytes | Fixed bytes | Byte difference |",
                   "|---|---:|---:|---:|---:|---:|---:|"])
    for item in compare["similar_delivery_eventguard_vs_fixed"]:
        report.append(f"| {item['loss_model']} | {item['eventguard_loss_rate']:.0%} | {item['fixed_loss_rate']:.0%} | {item['critical_delivery_gap']:.3f} | {item['eventguard_bytes']:.1f} | {item['fixed_bytes']:.1f} | {item['byte_difference']:+.1f} |")
    report.extend(["", "## Paired tests", "", "Paired two-sided t-tests compare matched seeds within each loss condition; p-values are descriptive and no multiplicity correction is applied.", ""])
    for item in compare["paired_significance"]:
        test = item["critical_delivery"]
        report.append(f"- {item['loss_model']} {item['loss_rate']:.0%}, {item['comparison']}: critical-delivery mean difference={test['mean_difference']!s}, n={test['n']}, p={test['p_two_sided']!s}.")
    report.extend(["", "## Generated plots", ""] + [f"- `plots/{name}`" for name in plots])
    (result_root / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    eg_mean_critical = overall_mean("EVENTGUARD", "critical_event_delivery_ratio")
    fixed_mean_critical = overall_mean("FIXED_REDUNDANCY", "critical_event_delivery_ratio")
    no_mean_critical = overall_mean("NO_PROTECTION", "critical_event_delivery_ratio")
    random_diffs = []
    burst_diffs = []
    for rate in (0.20, 0.30):
        eg_value = condition_mean("EVENTGUARD", "RANDOM", rate, "critical_event_delivery_ratio")
        fixed_value = condition_mean("FIXED_REDUNDANCY", "RANDOM", rate, "critical_event_delivery_ratio")
        if eg_value is not None and fixed_value is not None:
            random_diffs.append(f"{rate:.0%}: {eg_value:.3f} vs {fixed_value:.3f} ({eg_value - fixed_value:+.3f})")
        eg_value = condition_mean("EVENTGUARD", "BURST", rate, "critical_event_delivery_ratio")
        fixed_value = condition_mean("FIXED_REDUNDANCY", "BURST", rate, "critical_event_delivery_ratio")
        if eg_value is not None and fixed_value is not None:
            burst_diffs.append(f"{rate:.0%}: {eg_value:.3f} vs {fixed_value:.3f} ({eg_value - fixed_value:+.3f})")
    radio_claim = ("real-radio runs" if (provenance or {}).get("physical_radio")
                   else "host-simulation runs")
    paper = ["# EventGuard-LoRa Paper Outline", "",
             "## Working title", "Event-Aware Bounded Redundancy for Critical Sensor Delivery over E220 LoRa-Class Links", "",
             "## Abstract draft", f"We implemented EventGuard-LoRa on two ESP32-S3 boards connected by E220-400T22D radios and compared no protection, fixed two-copy redundancy, and event/link-aware redundancy capped at three copies. The current measured dataset contains {len(runs)} {radio_claim} with deterministic synthetic sensor traces and post-reception application-layer loss injection. Across the configured conditions, mean critical-event delivery was {fmt(eg_mean_critical)} for EventGuard, {fmt(fixed_mean_critical)} for fixed redundancy, and {fmt(no_mean_critical)} for no protection. EventGuard used {fmt(eg_bytes, 1)} bytes per run on average versus {fmt(fixed_bytes, 1)} for fixed redundancy. It improved critical delivery over fixed redundancy in some random-loss conditions, while gains were smaller or absent in burst-loss conditions. These findings are preliminary and do not demonstrate an equal-cost advantage or robustness to RF fading.", "",
             "## 1. Research question and scope", "- Study how an event-aware, bounded copy policy affects critical-event delivery and communication cost on a constrained Sub-GHz link.\n- Separate actual E220 transmission from deterministic application-layer erasures; do not label configured injected loss as measured RF loss.\n- Treat this as an engineering evaluation, not a novelty claim.", "",
             "## 2. System and method", "- Sensor and gateway use ESP32-S3 plus E220-400T22D over UART; compact CRC-protected DATA and ACK frames.\n- Event classes are NORMAL, IMPORTANT, and CRITICAL; fixed redundancy sends two copies and EventGuard adapts one to three copies using event importance and an ACK-based link estimate.\n- Include the score, thresholds, copy-selection logic, deduplication, ACK behavior, and failure cases from the source.", "",
             "## 3. Experimental setup", f"- Matrix: {len(runs)} runs total; {len(run_groups['NO_PROTECTION'])} per strategy; {len(seeds)} seeds ({seed_text}); configured loss rates 0%, 5%, 10%, 20%, and 30%; RANDOM and BURST models (burst length 3).\n- Each run uses {sample_text} synthetic sensor samples (the current trace contains 8 NORMAL, 6 IMPORTANT, and 4 CRITICAL samples).\n- E220 settings were read and checked by firmware at boot; profile: {radio_text}\n- Hardware identity: sensor {chip_macs.get('sensor', first_run.get('sensor_mac', 'not recorded'))}; gateway {chip_macs.get('gateway', first_run.get('gateway_mac', 'not recorded'))}.\n- Explain how identical trace content and matched seeds construct each strategy comparison; cite manifests and firmware image hashes.", "",
             "## 4. Results to report", f"- Overall mean critical-event delivery: EventGuard {fmt(eg_mean_critical)}, fixed redundancy {fmt(fixed_mean_critical)}, no protection {fmt(no_mean_critical)}.\n- Mean bytes per run: EventGuard {fmt(eg_bytes, 1)}, fixed redundancy {fmt(fixed_bytes, 1)}, no protection {fmt(overall_mean('NO_PROTECTION', 'total_bytes_transmitted'), 1)}.\n- RANDOM loss, EventGuard vs fixed critical delivery: {'; '.join(random_diffs) or 'not available'}.\n- BURST loss, EventGuard vs fixed critical delivery: {'; '.join(burst_diffs) or 'not available'}.\n- Include all conditions, confidence intervals, latencies, transmitted bytes, and plots from `results/report.md` and `results/summary.csv`; retain every run.", "",
             "## 5. Statistical analysis", "- Paired two-sided t-tests use ten matched seeds per configured condition. The current report runs twenty critical-delivery comparisons without multiplicity correction; p-values are exploratory, not confirmatory.\n- Report confidence intervals and per-seed values; avoid interpreting small p-values without a predeclared primary comparison and correction.", "",
             "## 6. Limitations and next experiments", "- Application-layer drops are not RF fading, interference, or E220 retry behavior; actual channel errors are not controlled or characterized.\n- One sensor/gateway pair, a short deterministic synthetic trace, and ten seeds per condition limit generalization. No energy, current, range, or regulatory airtime measurement was collected.\n- EventGuard transmits more bytes on average than fixed redundancy. The automatic similar-cost table compares points that can use different configured loss rates; it cannot establish superiority at equal channel quality.\n- Next: compare policies under fixed byte/airtime budgets (including fixed one-, two-, and three-copy baselines), lengthen traces and seed sets, characterize real RF conditions with distance/interference/attenuation measurements, and measure energy and airtime.", "",
             "## 7. Conclusion", "State only the measured tradeoff: EventGuard raises mean critical delivery in this dataset at higher byte cost, with stronger paired differences under random 20–30% application-layer loss and no consistent win under burst loss. Do not claim a general LoRa reliability improvement until controlled RF experiments are complete.", ""]
    Path(paper_path).parent.mkdir(parents=True, exist_ok=True)
    Path(paper_path).write_text("\n".join(paper), encoding="utf-8")
    return summary
