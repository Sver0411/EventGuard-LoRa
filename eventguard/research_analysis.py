"""Analysis of the frozen v1 algorithm. No policy or channel behavior is changed here."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from .analysis import confidence_interval
from .host import ROOT, _run_config, load_config
from .model import LossModel, Strategy
from .simulator import run_reference
from .trace import generate_trace, trace_fingerprint
from .validation import EVALUATION_SEEDS, MODELS, RATES, STRATEGIES

ALGORITHM_VERSION = "EventGuard-v1"
FROZEN_COMMIT = "84fe41701597b250c315002eabaa99638ef17a95"
FROZEN_FILES = {
    "eventguard/importance.py": "e8a32c16ce9f2a8c8e1d8aa9060162190ac1e567e8350b7a95b2d61bfe908d6d",
    "eventguard/strategy.py": "d46893ca68b30481115a6ea02c40e331c1e6f4b09a16d3b58d3b9949876773f3",
    "eventguard/faults.py": "db1995b06d165af614c16ec661919f017e6e788b92759b86095365294a97a660",
    "eventguard/simulator.py": "7b86c81316dad083b2615f9b0b80e1fd03a8fdd1b52e0ea39608a2b60e57b4a6",
    "eventguard/trace.py": "e05f9dbb5f9c250459cc37e89e56349030d10a774914ceb326b5c86c21009614",
    "eventguard/model.py": "bedb4d63e841489d84cf68d763837172a5fd78bf03b8b4215e4b825b5a1f9daa",
    "firmware/common/importance.c": "26342d36b2bbe9f9743ea797f073c64bbd64feeffac0d68f954d003849ce3e14",
    "firmware/common/strategy.c": "cd9a17696ee2e76ce656ae00d5fb7fe4b11b6b14a4f95064f2bd874118a21061",
    "firmware/common/faults.c": "8366d31c29a8f2f927c468ef9b43cb61c29a228e87929d0b2b3db17c96e59129",
    "firmware/main/main.c": "73167f0213fa86d3460b1354e80770159750d7974676c3c732fb712c0110460d",
}
MAIN_METRICS = (
    "critical_event_delivery_ratio", "important_event_delivery_ratio", "overall_delivery_ratio",
    "physical_data_transmissions", "total_bytes_transmitted", "data_copies_per_critical_event",
    "data_copies_per_delivered_critical", "critical_delivery_per_1000_bytes",
)
COLORS = {
    "FIXED_1": "#7f7f7f", "FIXED_2": "#b07aa1", "FIXED_3": "#e15759",
    "IMPORTANCE_ONLY": "#76b7b2", "LINK_ONLY": "#edc949", "EVENTGUARD": "#225ea8",
    "UNIFORM_BUDGET": "#f28e2b", "RANDOM_BUDGET": "#59a14f",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_frozen_algorithm() -> None:
    mismatches = [name for name, expected in FROZEN_FILES.items() if _sha256(ROOT / name) != expected]
    if mismatches:
        raise RuntimeError(f"frozen algorithm differs from {FROZEN_COMMIT}: {', '.join(mismatches)}")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _manifest(name: str, extra: dict | None = None) -> dict:
    base = {
        "experiment": name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": FROZEN_COMMIT,
        "algorithm_version": ALGORITHM_VERSION,
        "algorithm_sha256": FROZEN_FILES,
        "trace_version": "trace-v1-10s-9phases-6samples-per-phase",
        "trace_samples_per_phase": 6,
        "seed_range": {"calibration": [1, 30], "evaluation": [31, 130]},
        "loss_models": [model.value for model in MODELS],
        "loss_rates": list(RATES),
        "strategy_list": [strategy.value for strategy in STRATEGIES],
        "parameters": {
            "important_threshold": .85, "critical_threshold": 3.0,
            "importance_scales": {"temperature": .5, "humidity": 5.0, "light": 100.0, "soil_moisture": 8.0},
            "importance_baseline_alpha": .06,
            "link_window": 12, "link_degraded_threshold": .80, "link_bad_threshold": .50,
            "link_bad_fail_streak": 3, "max_redundancy": 3,
            "burst_length": 3, "ack_timeout_ms": 1000, "uart_baud": 9600,
        },
        "source": "HOST_SIMULATION_ONLY",
        "baseline_runs_sha256": _sha256(ROOT / "results/pre_hardware_v1/runs.json"),
        "analysis_script_sha256": _sha256(ROOT / "eventguard/research_analysis.py"),
    }
    if extra:
        base.update(extra)
    return base


def _save_manifest(directory: Path, name: str, extra: dict | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "experiment_manifest.json").write_text(
        json.dumps(_manifest(name, extra), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_frozen_runs() -> list[dict]:
    assert_frozen_algorithm()
    rows = json.loads((ROOT / "results/pre_hardware_v1/runs.json").read_text(encoding="utf-8"))
    if len(rows) != 8000 or {r["seed"] for r in rows} != set(EVALUATION_SEEDS):
        raise RuntimeError("the locked 8,000-run evaluation dataset is incomplete")
    expected = {(m.value, rate, seed, s.value) for m in MODELS for rate in RATES
                for seed in EVALUATION_SEEDS for s in STRATEGIES}
    actual = {(r["loss_model"], r["loss_rate"], r["seed"], r["strategy"]) for r in rows}
    if actual != expected:
        raise RuntimeError("evaluation matrix has missing or duplicate conditions")
    return rows


def _enrich(row: dict) -> dict:
    enriched = dict(row)
    critical_total = row["truth_counts"]["CRITICAL"]
    critical_delivered = row["truth_delivered"]["CRITICAL"]
    # Frozen run records include the share of physical DATA copies sent for true
    # CRITICAL samples. Use that class-specific numerator, not all DATA traffic.
    critical_copies = row["critical_traffic_share"] * row["physical_data_transmissions"]
    enriched["data_copies_per_critical_event"] = critical_copies / critical_total
    enriched["data_copies_per_delivered_critical"] = (
        critical_copies / critical_delivered if critical_delivered else None)
    return enriched


def describe(values: list[float]) -> dict:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    avg, std, median, low, high = confidence_interval(clean)
    return {"n": len(clean), "mean": avg, "median": median, "std": std,
            "ci95_low": low, "ci95_high": high}


def _rank_abs_nonzero(differences: list[float]) -> list[tuple[float, int]]:
    """Return (difference, doubled midrank) for a Wilcoxon signed-rank test."""
    nonzero = sorted((float(d) for d in differences if abs(float(d)) > 1e-12), key=abs)
    ranked = []
    index = 0
    while index < len(nonzero):
        end = index + 1
        while end < len(nonzero) and math.isclose(abs(nonzero[end]), abs(nonzero[index]), rel_tol=1e-10, abs_tol=1e-12):
            end += 1
        doubled_midrank = (index + 1) + end
        ranked.extend((d, doubled_midrank) for d in nonzero[index:end])
        index = end
    return ranked


def wilcoxon_exact(differences: list[float]) -> dict:
    """Two-sided exact conditional sign-randomization p for tied Wilcoxon midranks."""
    ranked = _rank_abs_nonzero(differences)
    if not ranked:
        return {"n_nonzero": 0, "w_positive": 0.0, "w_negative": 0.0,
                "p_two_sided": 1.0, "rank_biserial": 0.0}
    weights = [rank for _, rank in ranked]
    positive = sum(rank for diff, rank in ranked if diff > 0)
    total = sum(weights)
    counts = [0] * (total + 1)
    counts[0] = 1
    reachable = 0
    for weight in weights:
        for score in range(reachable, -1, -1):
            if counts[score]:
                counts[score + weight] += counts[score]
        reachable += weight
    denominator = 1 << len(weights)
    lower = sum(counts[:positive + 1])
    upper = sum(counts[positive:])
    p = min(1.0, 2 * min(lower, upper) / denominator)
    return {"n_nonzero": len(weights), "w_positive": positive / 2,
            "w_negative": (total - positive) / 2, "p_two_sided": p,
            "rank_biserial": (2 * positive - total) / total}


def holm_adjust(p_values: list[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [1.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, p_values[index] * (len(p_values) - rank)))
        adjusted[index] = running
    return adjusted


def _paired_index(rows: list[dict]) -> dict[tuple, dict[str, dict]]:
    index: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        key = (row["loss_model"], row["loss_rate"], row["seed"])
        index[key][row["strategy"]] = row
    return index


def _paired_results(rows: list[dict]) -> list[dict]:
    index = _paired_index(rows)
    comparisons = ("IMPORTANCE_ONLY", "UNIFORM_BUDGET")
    results = []
    for model in MODELS:
        for rate in RATES:
            for baseline in comparisons:
                pairs = [index[(model.value, rate, seed)] for seed in EVALUATION_SEEDS]
                differences = [p["EVENTGUARD"]["critical_event_delivery_ratio"] -
                               p[baseline]["critical_event_delivery_ratio"] for p in pairs]
                if baseline == "UNIFORM_BUDGET":
                    if not all(p["EVENTGUARD"]["physical_data_transmissions"] ==
                               p[baseline]["physical_data_transmissions"] and
                               p["EVENTGUARD"]["trace_sha256"] == p[baseline]["trace_sha256"] and
                               p["EVENTGUARD"]["channel_calendar"] == p[baseline]["channel_calendar"]
                               for p in pairs):
                        raise RuntimeError("equal-budget or matched-condition invariant failed")
                stats = describe(differences)
                test = wilcoxon_exact(differences)
                results.append({"loss_model": model.value, "loss_rate": rate, "comparison": f"EVENTGUARD - {baseline}",
                                "n_pairs": len(differences), "mean_difference": stats["mean"],
                                "median_difference": stats["median"], "std_difference": stats["std"],
                                "ci95_low": stats["ci95_low"], "ci95_high": stats["ci95_high"],
                                "wins": sum(d > 0 for d in differences), "ties": sum(d == 0 for d in differences),
                                "losses": sum(d < 0 for d in differences), **test})
    adjusted = holm_adjust([r["p_two_sided"] for r in results])
    for row, p in zip(results, adjusted):
        row["holm_p_20_tests"] = p
    return results


def _summary(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["loss_model"], row["loss_rate"], row["strategy"])].append(_enrich(row))
    result = []
    for (model, rate, strategy), group in sorted(groups.items()):
        record = {"loss_model": model, "loss_rate": rate, "strategy": strategy, "n_seeds": len(group)}
        for metric in MAIN_METRICS:
            stats = describe([r[metric] for r in group])
            for key in ("mean", "median", "std", "ci95_low", "ci95_high"):
                record[f"{metric}_{key}"] = stats[key]
            record[f"{metric}_n"] = stats["n"]
        result.append(record)
    return result


def _matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _group_plot(rows: list[dict], strategies: tuple[str, ...], title: str, path: Path) -> None:
    plt = _matplotlib()
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
    for ax, model in zip(axes, MODELS):
        for strategy in strategies:
            means = [mean(r["critical_event_delivery_ratio"] for r in rows
                          if r["loss_model"] == model.value and r["loss_rate"] == rate and
                          r["strategy"] == strategy) for rate in RATES]
            ax.plot([rate * 100 for rate in RATES], means, marker="o", label=strategy,
                    color=COLORS[strategy])
        ax.set(title=model.value, xlabel="Configured injected loss (%)", ylim=(.65, 1.02))
        ax.grid(alpha=.25)
    axes[0].set_ylabel("Mean critical delivery ratio")
    axes[1].legend(fontsize=8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def analyze_ablations(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    out = ROOT / "results/ablation"
    out.mkdir(parents=True, exist_ok=True)
    summary = _summary(rows)
    tests = _paired_results(rows)
    _write_csv(out / "summary.csv", summary)
    _write_csv(out / "paired_tests.csv", tests)
    groups = {
        "importance_contribution": ("FIXED_2", "IMPORTANCE_ONLY", "EVENTGUARD"),
        "link_contribution": ("IMPORTANCE_ONLY", "LINK_ONLY", "EVENTGUARD"),
        "budget_fairness": ("EVENTGUARD", "UNIFORM_BUDGET", "RANDOM_BUDGET"),
    }
    for name, strategies in groups.items():
        _group_plot(rows, strategies, name.replace("_", " ").title(), out / f"{name}.png")
    _save_manifest(out, "frozen_v1_ablation_and_paired_statistics",
                   {"run_count": len(rows), "comparisons": list(groups),
                    "statistics": "mean, median, sample std, two-sided 95% t/normal CI across 100 paired seeds",
                    "paired_test": "exact two-sided Wilcoxon signed-rank with zero differences removed; Holm correction over 20 tests"})
    lines = ["# Frozen v1 ablation study", "", "Source: 8,000 host-only evaluation runs, seeds 31–130. "
             "Each contrast keeps loss model, rate, seed, trace, and canonical channel calendar fixed. "
             "Fixed and component ablations are not equal-cost comparisons; only budget baselines are.", "",
             "## A. Importance contribution", "",
             "FIXED_2, IMPORTANCE_ONLY, and EVENTGUARD are shown in "
             "[importance_contribution.png](importance_contribution.png). Importance allocation gives "
             "CRITICAL samples three copies; it is the main source of their delivery benefit. "
             "Compare costs as well as delivery in `summary.csv`.", "",
             "## B. Link contribution", "",
             "IMPORTANCE_ONLY, LINK_ONLY, and EVENTGUARD are shown in "
             "[link_contribution.png](link_contribution.png). The frozen cap of three copies means that "
             "a correctly predicted CRITICAL sample already has the maximum under IMPORTANCE_ONLY. "
             "Link adaptation can improve IMPORTANT/overall delivery under RANDOM_COPY loss but cannot add "
             "CRITICAL copies in this design.", "",
             "## C. Exact DATA-copy budget", "",
             "EVENTGUARD, UNIFORM_BUDGET, and RANDOM_BUDGET are shown in "
             "[budget_fairness.png](budget_fairness.png). Exact copy counts were checked per matched run. "
             "The two budget baselines allocate without truth, classifier output, or sensor values.", "",
             "## Paired tests", "",
             "`paired_tests.csv` contains all 20 predeclared comparisons: 2 models × 5 rates × "
             "(EVENTGUARD−IMPORTANCE_ONLY, EVENTGUARD−UNIFORM_BUDGET). Zero differences are excluded "
             "from signed ranks; all-tie comparisons have p=1 and effect size 0. Rank-biserial correlation "
             "is the effect size. Holm-adjusted p-values cover all 20 tests. Mean-difference 95% CIs are "
             "descriptive and are not used to select conditions.", ""]
    for model in MODELS:
        for rate in RATES:
            e = next(r for r in summary if r["loss_model"] == model.value and r["loss_rate"] == rate and r["strategy"] == "EVENTGUARD")
            i = next(r for r in summary if r["loss_model"] == model.value and r["loss_rate"] == rate and r["strategy"] == "IMPORTANCE_ONLY")
            u = next(r for r in summary if r["loss_model"] == model.value and r["loss_rate"] == rate and r["strategy"] == "UNIFORM_BUDGET")
            lines.append(f"- {model.value} {rate:.0%}: critical delivery EG={e['critical_event_delivery_ratio_mean']:.4f}, "
                         f"importance={i['critical_event_delivery_ratio_mean']:.4f}, "
                         f"uniform={u['critical_event_delivery_ratio_mean']:.4f}; mean DATA copies "
                         f"EG={e['physical_data_transmissions_mean']:.2f}, "
                         f"importance={i['physical_data_transmissions_mean']:.2f}, "
                         f"uniform={u['physical_data_transmissions_mean']:.2f}.")
    (out / "report.md").write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return summary, tests


def _frontier(points: list[dict], cost_key: str = "total_bytes_transmitted_mean",
              value_key: str = "critical_event_delivery_ratio_mean") -> tuple[list[dict], list[dict]]:
    front, dominated = [], []
    for candidate in points:
        cost, delivery = candidate[cost_key], candidate[value_key]
        is_dominated = any(
            other is not candidate and other[cost_key] <= cost + 1e-10 and
            other[value_key] >= delivery - 1e-10 and
            (other[cost_key] < cost - 1e-10 or other[value_key] > delivery + 1e-10)
            for other in points)
        (dominated if is_dominated else front).append(candidate)
    return sorted(front, key=lambda p: (p[cost_key], p["strategy"])), dominated


def analyze_pareto(summary: list[dict]) -> list[dict]:
    out = ROOT / "results/plots/pareto"
    out.mkdir(parents=True, exist_ok=True)
    points = []
    for model in MODELS:
        for rate in RATES:
            condition = [r for r in summary if r["loss_model"] == model.value and r["loss_rate"] == rate]
            front, dominated = _frontier(condition)
            for row in condition:
                points.append({"loss_model": model.value, "loss_rate": rate, "strategy": row["strategy"],
                               "mean_data_copies": row["physical_data_transmissions_mean"],
                               "mean_bytes": row["total_bytes_transmitted_mean"],
                               "mean_critical_delivery": row["critical_event_delivery_ratio_mean"],
                               "pareto_efficient": row in front})
    _write_csv(out / "pareto_points.csv", points)
    plt = _matplotlib()
    from matplotlib.lines import Line2D
    for frontier_only, filename in ((False, "critical_delivery_vs_cost.png"), (True, "pareto_frontier.png")):
        fig, axes = plt.subplots(2, 5, figsize=(17, 8), sharey=True)
        for row_index, model in enumerate(MODELS):
            for col_index, rate in enumerate(RATES):
                ax = axes[row_index][col_index]
                subset = [p for p in points if p["loss_model"] == model.value and p["loss_rate"] == rate]
                front = sorted((p for p in subset if p["pareto_efficient"]), key=lambda p: p["mean_bytes"])
                if frontier_only:
                    ax.scatter([p["mean_bytes"] for p in subset if not p["pareto_efficient"]],
                               [p["mean_critical_delivery"] for p in subset if not p["pareto_efficient"]],
                               color="#cbd5e1", s=26, label="dominated" if row_index == col_index == 0 else None)
                    ax.plot([p["mean_bytes"] for p in front], [p["mean_critical_delivery"] for p in front],
                            color="#111827", linewidth=1.5, zorder=1)
                for point in subset:
                    if frontier_only and not point["pareto_efficient"]:
                        continue
                    ax.scatter(point["mean_bytes"], point["mean_critical_delivery"], s=33,
                               color=COLORS[point["strategy"]], label=point["strategy"] if row_index == col_index == 0 else None,
                               zorder=2)
                ax.set(title=f"{model.value} {rate:.0%}", ylim=(.68, 1.02))
                ax.grid(alpha=.2)
                if row_index == 1: ax.set_xlabel("Mean bytes/run")
                if col_index == 0: ax.set_ylabel("Critical delivery")
        legend_handles = [Line2D([0], [0], marker="o", color="none", markerfacecolor=color,
                                 markeredgecolor=color, markersize=7, label=strategy)
                          for strategy, color in COLORS.items()]
        if frontier_only:
            legend_handles.append(Line2D([0], [0], marker="o", color="none", markerfacecolor="#cbd5e1",
                                         markeredgecolor="#cbd5e1", markersize=7, label="dominated"))
        fig.legend(handles=legend_handles, loc="lower center", ncol=5, fontsize=8, bbox_to_anchor=(.5, -.005))
        fig.suptitle("Pareto frontier within each fixed channel condition" if frontier_only else
                     "Critical delivery versus communication cost, fixed channel conditions")
        fig.tight_layout(rect=(0, .04, 1, .96))
        fig.savefig(out / filename, dpi=170, bbox_inches="tight")
        plt.close(fig)
    _save_manifest(out, "frozen_v1_pareto",
                   {"run_count": 8000, "frontier_definition": "mean bytes minimized, mean critical delivery maximized; computed separately by model/rate"})
    return points

SENSITIVITY_GRID = {
    "critical_threshold": (2.0, 2.5, 3.0, 3.5, 4.0),
    "link_window": (8, 12, 16, 24),
    "max_redundancy": (2, 3),
}
SENSITIVITY_METRICS = ("critical_delivery", "important_delivery", "overall_delivery", "data_copies",
                       "total_bytes", "critical_per_1000_bytes", "link_transitions", "bad_link_share",
                       "predicted_critical", "normal_to_critical_rate")


def _sensitivity_run(axis: str, value: float, model: LossModel, rate: float, seed: int,
                     samples, cfg: dict) -> dict:
    config = _run_config(cfg, Strategy.EVENTGUARD.value, rate, model.value, seed)
    if axis == "critical_threshold":
        config = replace(config, critical_threshold=float(value))
    elif axis == "link_window":
        config = replace(config, link_window=int(value))
    elif axis == "max_redundancy":
        config = replace(config, max_redundancy=int(value))
    else:
        raise ValueError(axis)
    result = run_reference(samples, config, cfg["uart_baud"])
    metrics, events = result["metrics"], result["events"]
    states = [event["link_state"] for event in events]
    normal = [event for event in events if event["truth"] == "NORMAL"]
    return {
        "axis": axis, "value": value, "loss_model": model.value, "loss_rate": rate, "seed": seed,
        "trace_sha256": trace_fingerprint(samples),
        "critical_delivery": metrics["critical_event_delivery_ratio"],
        "important_delivery": metrics["important_event_delivery_ratio"],
        "overall_delivery": metrics["overall_delivery_ratio"],
        "data_copies": metrics["physical_data_transmissions"],
        "total_bytes": metrics["total_bytes_transmitted"],
        "critical_per_1000_bytes": metrics["critical_delivery_per_1000_bytes"],
        "link_transitions": sum(a != b for a, b in zip(states, states[1:])),
        "bad_link_share": sum(state == "BAD" for state in states) / len(states),
        "predicted_critical": sum(event["importance"] == "CRITICAL" for event in events),
        "normal_to_critical_rate": sum(event["importance"] == "CRITICAL" for event in normal) / len(normal),
    }


def _plot_sensitivity(summary: list[dict], out: Path) -> None:
    plt = _matplotlib()
    for axis in SENSITIVITY_GRID:
        metrics = ("critical_delivery", "data_copies", "link_transitions") if axis == "link_window" else (
            "critical_delivery", "data_copies")
        fig, axes = plt.subplots(len(metrics), 2, figsize=(12, 4 * len(metrics)), squeeze=False)
        for col, model in enumerate(MODELS):
            for row, metric in enumerate(metrics):
                ax = axes[row][col]
                for rate in RATES:
                    points = sorted((r for r in summary if r["axis"] == axis and r["loss_model"] == model.value
                                     and r["loss_rate"] == rate and r["supported"]), key=lambda r: r["value"])
                    ax.plot([p["value"] for p in points], [p[f"{metric}_mean"] for p in points],
                            marker="o", label=f"{rate:.0%}")
                ax.set(xlabel=axis.replace("_", " "), ylabel=f"Mean {metric.replace('_', ' ')}",
                       title=model.value)
                ax.grid(alpha=.25)
                if axis == "max_redundancy":
                    ax.set_xticks((2, 3, 4))
                    ax.axvline(4, color="#d62728", linestyle=":", alpha=.7)
                    ax.text(4, ax.get_ylim()[1], "4 unsupported", color="#d62728", rotation=90,
                            va="top", ha="right", fontsize=8)
        axes[0][1].legend(title="Injected loss", fontsize=8, ncol=2)
        fig.suptitle(f"Frozen v1 sensitivity: {axis.replace('_', ' ')}")
        fig.tight_layout(rect=(0, 0, 1, .97))
        fig.savefig(out / f"{axis}.png", dpi=170)
        plt.close(fig)


def analyze_sensitivity() -> tuple[list[dict], int]:
    out = ROOT / "results/sensitivity"
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    traces = {seed: generate_trace(seed, 6) for seed in EVALUATION_SEEDS}
    runs = []
    for axis, values in SENSITIVITY_GRID.items():
        for value in values:
            for model in MODELS:
                for rate in RATES:
                    for seed in EVALUATION_SEEDS:
                        runs.append(_sensitivity_run(axis, value, model, rate, seed, traces[seed], cfg))
    _write_csv(out / "runs.csv", runs)
    groups = defaultdict(list)
    for row in runs:
        groups[(row["axis"], row["value"], row["loss_model"], row["loss_rate"])].append(row)
    summary = []
    for (axis, value, model, rate), group in sorted(groups.items()):
        record = {"axis": axis, "value": value, "loss_model": model, "loss_rate": rate,
                  "supported": True, "n_seeds": len(group)}
        for metric in SENSITIVITY_METRICS:
            for key, number in describe([r[metric] for r in group]).items():
                if key != "n": record[f"{metric}_{key}"] = number
        summary.append(record)
    for model in MODELS:
        for rate in RATES:
            summary.append({"axis": "max_redundancy", "value": 4, "loss_model": model.value,
                            "loss_rate": rate, "supported": False, "n_seeds": 0,
                            "reason": "Frozen v1 supports only 3 canonical copy opportunities; a fourth would change policy and loss calendar."})
    _write_csv(out / "summary.csv", summary)
    _plot_sensitivity(summary, out)
    _save_manifest(out, "frozen_v1_parameter_sensitivity",
                   {"run_count": len(runs), "strategy_list": ["EVENTGUARD"],
                    "sensitivity_grid": {**SENSITIVITY_GRID, "max_redundancy_requested_but_unsupported": [4]},
                    "max_redundancy_4_status": "not simulated; outside frozen v1 algorithm and canonical loss calendar"})
    lines = ["# Frozen v1 sensitivity analysis", "",
             f"Host-only simulation runs: {len(runs)}. All cells use the same evaluation seeds 31–130, "
             "five loss rates, and both main loss models. No parameter was selected from these results; "
             "the primary 8,000-run evaluation remains unchanged.", "",
             "| Axis | Values | Status |", "|---|---|---|",
             "| CRITICAL threshold | 2.0, 2.5, 3.0, 3.5, 4.0 | All evaluated |",
             "| Link window | 8, 12, 16, 24 | All evaluated |",
             "| Maximum redundancy | 2, 3 | Evaluated |",
             "| Maximum redundancy | 4 | Unsupported in frozen v1; no fabricated fourth opportunity |", "",
             "`summary.csv` reports mean, median, sample standard deviation, and 95% mean CI for "
             "delivery, cost, link-state transitions, and classifier behavior. "
             "`runs.csv` preserves each seed-level observation. Plots: "
             "[threshold](critical_threshold.png), [window](link_window.png), "
             "[copy cap](max_redundancy.png).", "",
             "The unsupported cap=4 is a scientific boundary: v1 firmware, policy, and the fixed three-slot "
             "fault calendar all cap at three. Simulating a fourth slot with the current loss plan would "
             "silently assign it no erasure and produce a false benefit. Any cap=4 study must be a separately "
             "versioned algorithm and channel experiment.", ""]
    # Report selected descriptive contrasts without tuning the frozen default.
    for axis, values in SENSITIVITY_GRID.items():
        lines += [f"## {axis.replace('_', ' ').title()}", ""]
        for model in MODELS:
            for rate in (.20, .30):
                variants = [r for r in summary if r["axis"] == axis and r["loss_model"] == model.value
                            and r["loss_rate"] == rate and r["supported"]]
                detail = "; ".join(f"{r['value']}: delivery {r['critical_delivery_mean']:.4f}, "
                                   f"copies {r['data_copies_mean']:.2f}" for r in sorted(variants, key=lambda r: r["value"]))
                lines.append(f"- {model.value} {rate:.0%}: {detail}.")
        lines.append("")
    (out / "report.md").write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return summary, len(runs)


def analyze_failures(rows: list[dict], summary: list[dict], paired_tests: list[dict], pareto_points: list[dict]) -> dict:
    summary_index = {(r["loss_model"], r["loss_rate"], r["strategy"]): r for r in summary}
    paired = _paired_index(rows)
    lines = ["# Failure-case analysis (frozen v1, host simulation only)", "",
             "All ten channel conditions and all evaluation seeds 31–130 are inspected. "
             "A failure includes cost dominance even when delivery ties; such ties must not be presented "
             "as a reliability win.", "",
             "## Strict critical-delivery losses", ""]
    strict_losses = []
    for (model, rate, seed), condition in sorted(paired.items()):
        eg = condition["EVENTGUARD"]
        for baseline in STRATEGIES:
            if baseline == Strategy.EVENTGUARD:
                continue
            other = condition[baseline.value]
            if eg["critical_event_delivery_ratio"] < other["critical_event_delivery_ratio"]:
                strict_losses.append((model, rate, seed, baseline.value))
    lines.append(f"EventGuard has {len(strict_losses)} strict critical-delivery losses across all paired "
                 "baseline comparisons. The absence of strict losses is expected: correctly classified "
                 "critical samples already receive the frozen maximum of three copies. It does not "
                 "establish cost efficiency.")
    lines += ["", "## Conditions with cost dominance", "",
              "A baseline dominates EventGuard here when its mean critical delivery is at least as high "
              "and it uses fewer mean DATA copies within the same model and loss rate.", "",
              "| Model | Loss | Dominating baseline(s) | EG critical | EG copies | Lowest dominating copies |",
              "|---|---:|---|---:|---:|---:|"]
    dominance = []
    for model in MODELS:
        for rate in RATES:
            eg = summary_index[(model.value, rate, "EVENTGUARD")]
            candidates = [summary_index[(model.value, rate, s.value)] for s in STRATEGIES if s != Strategy.EVENTGUARD]
            dominated_by = [r for r in candidates if
                            r["critical_event_delivery_ratio_mean"] >= eg["critical_event_delivery_ratio_mean"] - 1e-12 and
                            r["physical_data_transmissions_mean"] < eg["physical_data_transmissions_mean"] - 1e-12]
            if dominated_by:
                least = min(dominated_by, key=lambda r: r["physical_data_transmissions_mean"])
                names = ", ".join(r["strategy"] for r in dominated_by)
                dominance.append({"model": model.value, "rate": rate, "baselines": [r["strategy"] for r in dominated_by]})
                lines.append(f"| {model.value} | {rate:.0%} | {names} | "
                             f"{eg['critical_event_delivery_ratio_mean']:.4f} | "
                             f"{eg['physical_data_transmissions_mean']:.2f} | "
                             f"{least['physical_data_transmissions_mean']:.2f} |")
    lines += ["", "## Representative failure: BURST_SAMPLE at 30%", ""]
    model, rate = LossModel.BURST_SAMPLE.value, .30
    eg = summary_index[(model, rate, "EVENTGUARD")]
    importance = summary_index[(model, rate, "IMPORTANCE_ONLY")]
    lines.append(f"EventGuard and IMPORTANCE_ONLY both deliver "
                 f"{eg['critical_event_delivery_ratio_mean']:.4f} of critical events. "
                 f"EventGuard uses {eg['physical_data_transmissions_mean']:.2f} mean DATA copies versus "
                 f"{importance['physical_data_transmissions_mean']:.2f}. "
                 "The fixed BURST_SAMPLE calendar erases every copy opportunity within an affected "
                 "sample; extra copies cannot restore it. Link adaptation adds traffic without "
                 "critical-delivery improvement.")
    lines += ["", "## Where allocation helps, and where it does not", ""]
    for rate in (.20, .30):
        eg = summary_index[(LossModel.RANDOM_COPY.value, rate, "EVENTGUARD")]
        uniform = summary_index[(LossModel.RANDOM_COPY.value, rate, "UNIFORM_BUDGET")]
        random = summary_index[(LossModel.RANDOM_COPY.value, rate, "RANDOM_BUDGET")]
        lines.append(f"- RANDOM_COPY {rate:.0%}: EventGuard critical delivery "
                     f"{eg['critical_event_delivery_ratio_mean']:.4f}; exact-budget uniform "
                     f"{uniform['critical_event_delivery_ratio_mean']:.4f}, random "
                     f"{random['critical_event_delivery_ratio_mean']:.4f}. This is evidence for "
                     "importance-guided allocation versus event-blind allocation under independent copy loss.")
    lines.append("- At low loss, delivery saturates near one; extra copies have little opportunity to help. "
                 "Under common-mode BURST_SAMPLE loss, copies within a lost sample are correlated and "
                 "cannot recover that sample.")
    lines += ["", "## Mechanism and interpretation", "",
              "The frozen rule sets CRITICAL to three copies on a GOOD link, already the maximum. "
              "DEGRADED or BAD link state therefore cannot increase its copy count. Any combined-policy "
              "benefit for CRITICAL delivery versus IMPORTANCE_ONLY is structurally blocked under this cap. "
              "Link adaptation may still improve IMPORTANT or overall delivery at additional cost. "
              "The baseline synthetic trace and application-layer erasures do not establish on-air benefit.", "",
              "See `results/ablation/paired_tests.csv` for every predeclared comparison, including "
              "effect size and multiplicity-adjusted p-value. No favorable subset was selected after testing.", ""]
    path = ROOT / "results/failure_analysis.md"
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return {"strict_delivery_losses": len(strict_losses), "dominated_conditions": dominance,
            "eventguard_pareto_frontier_count": sum(p["pareto_efficient"] and p["strategy"] == "EVENTGUARD"
                                                for p in pareto_points)}


def research_status_report(rows: list[dict], summary: list[dict], paired_tests: list[dict],
                           sensitivity_count: int, failure: dict) -> None:
    idx = {(r["loss_model"], r["loss_rate"], r["strategy"]): r for r in summary}
    e30 = idx[("RANDOM_COPY", .30, "EVENTGUARD")]
    i30 = idx[("RANDOM_COPY", .30, "IMPORTANCE_ONLY")]
    u30 = idx[("RANDOM_COPY", .30, "UNIFORM_BUDGET")]
    r30 = idx[("RANDOM_COPY", .30, "RANDOM_BUDGET")]
    w_u = next(r for r in paired_tests if r["loss_model"] == "RANDOM_COPY" and r["loss_rate"] == .30
               and r["comparison"] == "EVENTGUARD - UNIFORM_BUDGET")
    lines = ["# EventGuard-LoRa research status: frozen algorithm v1", "",
             "**Evidence tier:** host simulation only. No hardware was used in this round. The original "
             "300-run E220 dataset is pilot v0 and is not mixed with this evaluation.", "",
             "## 1. What does the algorithm currently contribute?", "",
             "The v1 policy assigns 1/2/3 proactive DATA copies from predicted event importance and "
             "adds bounded copies for degraded first-copy ACK history. Against event-blind, exact-budget "
             "allocation it can improve critical delivery under independent copy loss. At RANDOM_COPY 30%, "
             f"the mean critical-delivery ratios are EventGuard {e30['critical_event_delivery_ratio_mean']:.4f}, "
             f"UNIFORM_BUDGET {u30['critical_event_delivery_ratio_mean']:.4f}, and "
             f"RANDOM_BUDGET {r30['critical_event_delivery_ratio_mean']:.4f}; all three use the same "
             f"{e30['physical_data_transmissions_mean']:.2f} mean DATA copies and exactly matched "
             "copy counts in each paired run. The contribution supported here is **allocation by importance**, "
             "not a demonstrated CRITICAL-delivery gain from combining importance with link adaptation.", "",
             "## 2. Which component produces the benefit?", "",
             "- **Importance:** primary contributor for CRITICAL delivery. IMPORTANCE_ONLY reaches "
             f"{i30['critical_event_delivery_ratio_mean']:.4f} at RANDOM_COPY 30%, equal to EventGuard, "
             f"with {i30['physical_data_transmissions_mean']:.2f} mean copies rather than "
             f"{e30['physical_data_transmissions_mean']:.2f}.",
             "- **Link adaptation:** no incremental CRITICAL-delivery gain in any matched evaluation run; "
             "the three-copy cap is already reached for predicted CRITICAL events. It can increase "
             "IMPORTANT and overall delivery under RANDOM_COPY loss, with additional traffic.",
             "- **Budget allocation:** EventGuard's importance-guided allocation has a small advantage "
             "over blind exact-budget allocation at some RANDOM_COPY rates. "
             f"At 30%, the paired gain over UNIFORM_BUDGET is {w_u['mean_difference']:.4f} "
             f"(95% mean CI {w_u['ci95_low']:.4f} to {w_u['ci95_high']:.4f}); "
             "this does not imply the combined policy beats IMPORTANCE_ONLY.", "",
             "## 3. Should EventGuard be retained?", "",
             "Retain it as a **frozen research comparator**, with IMPORTANCE_ONLY as the stronger "
             "efficiency baseline for CRITICAL delivery. Do not claim the combined policy is Pareto "
             "superior or ready for a full hardware matrix. The link component may matter for an "
             "expanded objective that values IMPORTANT/overall delivery, but that objective must be "
             "declared before a new study. Preserve these unfavorable results.", "",
             "## 4. What should the next hardware experiment test?", "",
             "When hardware becomes available, first run a small, paired feasibility matrix with "
             "EVENTGUARD, IMPORTANCE_ONLY, UNIFORM_BUDGET, and RANDOM_BUDGET under the same trace, "
             "configured injection calendar, device pair, and measured RF conditions. Verify actual "
             "DATA-copy counts, ACK acceptance, airtime, energy, and ambient channel loss. Start with "
             "RANDOM_COPY 20%/30% and BURST_SAMPLE 30%, then decide whether a larger matrix is justified. "
             "A real RF channel cannot be assumed identical across sequential runs; interleave strategy "
             "order and record channel diagnostics. No hardware work was performed here.", "",
             "## Completion and remaining limits", "",
             f"- Frozen baseline: 8,000 evaluation runs plus {sensitivity_count:,} host-only sensitivity runs.",
             "- Statistics: per-condition mean, median, sample standard deviation, 95% mean CI; "
             "20 predeclared exact Wilcoxon signed-rank tests with Holm correction.",
             f"- Pareto: EventGuard is on {failure['eventguard_pareto_frontier_count']} of 10 "
             "mean critical-delivery/byte frontiers computed within fixed channel conditions.",
             f"- Failure audit: {failure['strict_delivery_losses']} strict critical-delivery losses; "
             f"{len(failure['dominated_conditions'])} conditions with a cheaper equal-or-better "
             "critical-delivery baseline.",
             "- Maximum redundancy 4 was not simulated: v1 defines only three canonical copy "
             "opportunities. It requires a new algorithm/channel version.",
             "- Largest limit: short synthetic 54-sample trace, deterministic application-layer erasures, "
             "one policy cap, and no new real-radio or field validation.", "",
             "Artifacts: [algorithm spec](../docs/algorithm_spec_v1.md), "
             "[ablation](ablation/report.md), [sensitivity](sensitivity/report.md), "
             "[failure analysis](failure_analysis.md), [Pareto points](plots/pareto/pareto_points.csv), "
             "[paper draft](../docs/paper_draft.md).", ""]
    (ROOT / "results/research_status_report.md").write_text("\n".join(lines), encoding="utf-8")


def run_all_analyses() -> dict:
    rows = load_frozen_runs()
    _save_manifest(ROOT / "results/pre_hardware_v1", "frozen_v1_evaluation",
                   {"run_count": len(rows), "matrix": "8 strategies × 5 rates × 2 models × 100 held-out seeds"})
    _save_manifest(ROOT / "results", "research_maturity_v1",
                   {"baseline_run_count": len(rows), "sensitivity_design": SENSITIVITY_GRID,
                    "max_redundancy_4_status": "unsupported under frozen v1"})
    summary, paired_tests = analyze_ablations(rows)
    pareto_points = analyze_pareto(summary)
    sensitivity_summary, sensitivity_count = analyze_sensitivity()
    failure = analyze_failures(rows, summary, paired_tests, pareto_points)
    research_status_report(rows, summary, paired_tests, sensitivity_count, failure)
    return {"baseline_runs": len(rows), "sensitivity_runs": sensitivity_count,
            "paired_tests": len(paired_tests), "summary_conditions": len(summary),
            "sensitivity_conditions": len(sensitivity_summary), **failure}
