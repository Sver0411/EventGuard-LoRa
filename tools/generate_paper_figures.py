"""Regenerate manuscript Figures 1–6 from frozen, archived EventGuard-v1 data.

Usage: .venv/bin/python tools/generate_paper_figures.py

All numerical checks finish before any figure is written. This script never runs a
simulation or accesses a device. Outputs are confined to docs/figures/.
"""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures"
PAPER = ROOT / "docs" / "paper_v1_submission_draft.md"
SPEC = ROOT / "docs" / "algorithm_spec_v1.md"
HOST = ROOT / "results" / "pre_hardware_v1" / "runs.csv"
SENS_RUNS = ROOT / "results" / "sensitivity" / "runs.csv"
SENS_SUMMARY = ROOT / "results" / "sensitivity" / "summary.csv"
HW_SUMMARY = ROOT / "results" / "final_hardware_v1" / "summary.csv"
HW_SELECTED = ROOT / "results" / "final_hardware_v1" / "selected_runs.json"
HW_AUDIT = ROOT / "results" / "final_hardware_v1" / "audit_runs.csv"
HW_PARETO = ROOT / "results" / "final_hardware_v1" / "pareto_points.csv"

STRATEGY_NAMES = {
    "EVENTGUARD": "EventGuard",
    "IMPORTANCE_ONLY": "Importance Only",
    "LINK_ONLY": "Link Only",
    "FIXED_2": "Fixed-2",
    "UNIFORM_BUDGET": "Uniform Budget",
    "RANDOM_BUDGET": "Random Budget",
}
STYLES = {
    "EVENTGUARD": dict(color="0.05", marker="o", linestyle="-"),
    "IMPORTANCE_ONLY": dict(color="0.40", marker="s", linestyle="--"),
    "LINK_ONLY": dict(color="0.18", marker="^", linestyle=":"),
    "FIXED_2": dict(color="0.68", marker="D", linestyle="-."),
    "UNIFORM_BUDGET": dict(color="0.43", marker="s", linestyle="--"),
    "RANDOM_BUDGET": dict(color="0.65", marker="^", linestyle=":"),
}
HOST_RATES = (0.0, 0.05, 0.10, 0.20, 0.30)
PAIRED_SEEDS = tuple(range(31, 131))
HARDWARE_SEEDS = tuple(range(31, 37))
HARDWARE_STRATEGIES = (
    "EVENTGUARD", "IMPORTANCE_ONLY", "UNIFORM_BUDGET", "RANDOM_BUDGET"
)
HARDWARE_CONDITIONS = (
    ("RANDOM_COPY", 0.20), ("RANDOM_COPY", 0.30),
    ("BURST_SAMPLE", 0.20), ("BURST_SAMPLE", 0.30),
)
T_99_975 = 1.9842169515086827  # two-sided 95% Student-t critical value, df=99


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def rows(path: Path) -> list[dict[str, str]]:
    check(path.is_file(), f"Missing frozen source: {path.relative_to(ROOT)}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(row: dict[str, str], field: str) -> float:
    result = float(row[field])
    check(math.isfinite(result), f"Nonfinite {field}: {row}")
    return result


def mean(values: list[float]) -> float:
    check(bool(values), "Empty numerical group")
    result = statistics.mean(values)
    check(math.isfinite(result), "Nonfinite mean")
    return result


def close(actual: float, expected: float, label: str, tolerance: float = 1e-10) -> None:
    check(math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance),
          f"{label}: observed {actual}, expected {expected}")


def validate_policy() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    for importance, counts in (
        ("NORMAL", (1, 2, 1)),
        ("IMPORTANT", (2, 3, 3)),
        ("CRITICAL", (3, 3, 3)),
    ):
        pattern = rf"\| {importance} \| {counts[0]} \| {counts[1]} \| {counts[2]} \|"
        check(bool(re.search(pattern, spec)), f"Frozen policy matrix mismatch: {importance}")
    check("max_redundancy=3" in spec, "Frozen maximum redundancy not found")


def validate_host() -> dict[tuple[str, float, int], dict[str, str]]:
    dataset = rows(HOST)
    check(len(dataset) == 8000, f"Frozen host run count is {len(dataset)}, not 8000")
    index: dict[tuple[str, float, int], dict[str, str]] = {}
    for row in dataset:
        if row["loss_model"] != "RANDOM_COPY":
            continue
        key = (row["strategy"], number(row, "loss_rate"), int(row["seed"]))
        check(key not in index, f"Duplicate host row: {key}")
        index[key] = row
    for rate in HOST_RATES:
        for seed in PAIRED_SEEDS:
            matched = [index.get((strategy, rate, seed)) for strategy in
                       ("EVENTGUARD", "UNIFORM_BUDGET", "RANDOM_BUDGET")]
            check(all(row is not None and row["loss_model"] == "RANDOM_COPY"
                      for row in matched), f"Missing RANDOM_COPY budget trio: {rate}, {seed}")
            check(len({row["trace_sha256"] for row in matched}) == 1,
                  f"Trace mismatch: {rate}, {seed}")
            check(len({row["channel_calendar"] for row in matched}) == 1,
                  f"Calendar mismatch: {rate}, {seed}")
            check(len({number(row, "physical_data_transmissions") for row in matched}) == 1,
                  f"DATA-copy budget mismatch: {rate}, {seed}")
            for strategy in ("FIXED_2", "LINK_ONLY", "IMPORTANCE_ONLY"):
                row = index.get((strategy, rate, seed))
                check(row is not None and row["loss_model"] == "RANDOM_COPY",
                      f"Missing ablation: {strategy}, {rate}, {seed}")
                check(row["trace_sha256"] == matched[0]["trace_sha256"],
                      f"Ablation trace mismatch: {strategy}, {rate}, {seed}")
    # These reported paper values are checks, never substituted for source data.
    paper_checks = {
        (0.20, "FIXED_2"): (0.9600, 108.00),
        (0.20, "LINK_ONLY"): (0.942, 107.02),
        (0.20, "IMPORTANCE_ONLY"): (0.9892, 112.00),
        (0.20, "EVENTGUARD"): (0.9892, 140.68),
        (0.30, "FIXED_2"): (0.9185, 108.00),
        (0.30, "LINK_ONLY"): (0.938, 129.48),
        (0.30, "IMPORTANCE_ONLY"): (0.9723, 112.00),
        (0.30, "EVENTGUARD"): (0.9723, 142.65),
    }
    for (rate, strategy), (delivery, copies) in paper_checks.items():
        group = [index[(strategy, rate, seed)] for seed in PAIRED_SEEDS]
        observed_delivery = mean([number(row, "critical_event_delivery_ratio") for row in group])
        observed_copies = mean([number(row, "physical_data_transmissions") for row in group])
        close(observed_delivery, delivery, f"Paper ablation delivery {rate}/{strategy}", 0.0005)
        close(observed_copies, copies, f"Paper ablation cost {rate}/{strategy}", 0.005)
    for rate in HOST_RATES:
        eg = mean([number(index[("EVENTGUARD", rate, seed)],
                          "critical_event_delivery_ratio") for seed in PAIRED_SEEDS])
        io = mean([number(index[("IMPORTANCE_ONLY", rate, seed)],
                          "critical_event_delivery_ratio") for seed in PAIRED_SEEDS])
        close(eg, io, f"Host EG/IO critical-delivery equality at {rate}")
    for rate in (0.20, 0.30):
        eg = mean([number(index[("EVENTGUARD", rate, seed)],
                          "physical_data_transmissions") for seed in PAIRED_SEEDS])
        io = mean([number(index[("IMPORTANCE_ONLY", rate, seed)],
                          "physical_data_transmissions") for seed in PAIRED_SEEDS])
        check(eg > io, f"Host EG cost not above IO at {rate}")
    return index


def table4_values() -> dict[tuple[str, float, str], tuple[str, str, str, str]]:
    paper = PAPER.read_text(encoding="utf-8")
    result = {}
    for line in paper.splitlines():
        match = re.fullmatch(
            r"\| (RC|BS) (20|30)% \| (EVENTGUARD|IMPORTANCE_ONLY|UNIFORM_BUDGET|RANDOM_BUDGET)"
            r" \| ([0-9.]+) \| ([0-9.]+) \| ([0-9,.]+) \| ([0-9.]+%) \|",
            line,
        )
        if match:
            model = "RANDOM_COPY" if match[1] == "RC" else "BURST_SAMPLE"
            result[(model, int(match[2]) / 100, match[3])] = match.group(4, 5, 6, 7)
    check(len(result) == 16, f"Expected 16 Table 4 paper rows, found {len(result)}")
    return result


def on_frontier(points: list[tuple[str, float, float]], chosen: str) -> bool:
    target = next((cost, delivery) for strategy, cost, delivery in points
                  if strategy == chosen)
    return not any(
        strategy != chosen and cost <= target[0] + 1e-12
        and delivery >= target[1] - 1e-12
        and (cost < target[0] - 1e-12 or delivery > target[1] + 1e-12)
        for strategy, cost, delivery in points
    )


def validate_hardware() -> dict[tuple[str, float, str], dict[str, str]]:
    selected = json.loads(HW_SELECTED.read_text(encoding="utf-8"))
    check(selected["dataset_status"] == "PASS" and selected["usable_runs"] == 96,
          "Final selected hardware set is not 96 PASS")
    check(tuple(selected["usable_paired_seeds"]) == HARDWARE_SEEDS,
          "Final paired seed set changed")
    check(len(selected["runs"]) == 96
          and all(run["audit_status"] == "PASS" for run in selected["runs"]),
          "Final selected run records are incomplete")
    audit = rows(HW_AUDIT)
    check(len(audit) == 96 and all(row["audit_status"] == "PASS" for row in audit),
          "Offline audit no longer 96/96 PASS")
    dataset = rows(HW_SUMMARY)
    check(len(dataset) == 16, f"Hardware summary has {len(dataset)}, not 16 rows")
    index = {}
    table = table4_values()
    for row in dataset:
        key = (row["loss_model"], number(row, "loss_rate"), row["strategy"])
        check(key not in index and key in table, f"Unexpected hardware summary row {key}")
        check(int(row["n_seeds"]) == 6 and row["seeds"] == "31;32;33;34;35;36",
              f"Hardware seeds changed: {key}")
        delivery = number(row, "critical_event_delivery_ratio_mean")
        copies = number(row, "physical_data_transmissions_mean")
        total_bytes = number(row, "total_bytes_transmitted_mean")
        share = number(row, "critical_traffic_share_mean")
        displayed = (f"{delivery:.4f}", f"{copies:.2f}",
                     f"{total_bytes:,.1f}", f"{share * 100:.1f}%")
        check(displayed == table[key],
              f"Table 4 mismatch for {key}: summary {displayed}, paper {table[key]}")
        index[key] = row
    for model, rate in HARDWARE_CONDITIONS:
        eg = index[(model, rate, "EVENTGUARD")]
        io = index[(model, rate, "IMPORTANCE_ONLY")]
        close(number(eg, "critical_event_delivery_ratio_mean"),
              number(io, "critical_event_delivery_ratio_mean"),
              f"Hardware EG/IO delivery equality {model}/{rate}")
        check(number(eg, "physical_data_transmissions_mean")
              > number(io, "physical_data_transmissions_mean"),
              f"Hardware EG cost is not above IO: {model}/{rate}")
        for budget in ("UNIFORM_BUDGET", "RANDOM_BUDGET"):
            close(number(eg, "physical_data_transmissions_mean"),
                  number(index[(model, rate, budget)], "physical_data_transmissions_mean"),
                  f"Hardware mean exact budget {model}/{rate}/{budget}")
    for cost_field, cost_type in (
        ("physical_data_transmissions_mean", "data_copies"),
        ("total_bytes_transmitted_mean", "total_bytes"),
    ):
        io_count = eg_count = 0
        for model, rate in HARDWARE_CONDITIONS:
            points = [(strategy, number(index[(model, rate, strategy)], cost_field),
                       number(index[(model, rate, strategy)],
                              "critical_event_delivery_ratio_mean"))
                      for strategy in HARDWARE_STRATEGIES]
            io_count += on_frontier(points, "IMPORTANCE_ONLY")
            eg_count += on_frontier(points, "EVENTGUARD")
        check(io_count == 4 and eg_count == 0,
              f"Pareto count changed for {cost_type}: IO={io_count}, EG={eg_count}")
    archived = rows(HW_PARETO)
    check(len(archived) == 32, "Archived Pareto file no longer has 32 points")
    for row in archived:
        key = (row["loss_model"], number(row, "loss_rate"), row["strategy"])
        field = ("physical_data_transmissions_mean" if row["cost_type"] == "data_copies"
                 else "total_bytes_transmitted_mean")
        close(number(row, "mean_cost"), number(index[key], field),
              f"Archived Pareto cost {key}/{field}")
        close(number(row, "mean_critical_delivery"),
              number(index[key], "critical_event_delivery_ratio_mean"),
              f"Archived Pareto delivery {key}")
    return index


def validate_sensitivity() -> dict[tuple[str, int, str, float], dict[str, str]]:
    source = rows(SENS_RUNS)
    summary = rows(SENS_SUMMARY)
    check(len(source) == 11000, f"Sensitivity run count changed: {len(source)}")
    check(not any(row["axis"] == "max_redundancy" and float(row["value"]) == 4
                  for row in source), "Unsupported cap=4 data appeared")
    check({int(float(row["value"])) for row in source if row["axis"] == "link_window"}
          == {8, 12, 16, 24}, "Link-window axis changed")
    check({int(float(row["value"])) for row in source if row["axis"] == "max_redundancy"}
          == {2, 3}, "Copy-cap axis changed")
    grouped = defaultdict(list)
    for row in source:
        if row["axis"] in ("link_window", "max_redundancy"):
            grouped[(row["axis"], int(float(row["value"])), row["loss_model"],
                     number(row, "loss_rate"))].append(row)
    index = {}
    for row in summary:
        if row["axis"] not in ("link_window", "max_redundancy"):
            continue
        if row["axis"] == "max_redundancy" and float(row["value"]) == 4:
            check(row["supported"] == "False" and int(row["n_seeds"]) == 0
                  and not row["critical_delivery_mean"],
                  "Unsupported cap=4 appears to contain a result")
            continue
        key = (row["axis"], int(float(row["value"])), row["loss_model"],
               number(row, "loss_rate"))
        check(key not in index, f"Duplicate sensitivity summary {key}")
        check(row["supported"] == "True" and int(row["n_seeds"]) == 100,
              f"Sensitivity unsupported/incomplete {key}")
        group = grouped[key]
        check(len(group) == 100 and {int(item["seed"]) for item in group}
              == set(PAIRED_SEEDS), f"Missing sensitivity seed {key}")
        for raw_field, summary_field in (
            ("critical_delivery", "critical_delivery_mean"),
            ("data_copies", "data_copies_mean"),
        ):
            close(mean([number(item, raw_field) for item in group]),
                  number(row, summary_field), f"Sensitivity summary mismatch {key}/{raw_field}")
        index[key] = row
    for rate in (0.20, 0.30):
        for window in (8, 12, 16, 24):
            check(("link_window", window, "RANDOM_COPY", rate) in index,
                  f"Missing Figure 6 window cell {window}/{rate}")
    for model, rate in HARDWARE_CONDITIONS:
        for cap in (2, 3):
            check(("max_redundancy", cap, model, rate) in index,
                  f"Missing Figure 6 cap cell {cap}/{model}/{rate}")
    return index


def configure_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8.2,
        "axes.titlesize": 9.0,
        "axes.labelsize": 8.5,
        "xtick.labelsize": 7.7,
        "ytick.labelsize": 7.7,
        "legend.fontsize": 7.4,
        "lines.linewidth": 1.35,
        "lines.markersize": 4.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
        "savefig.facecolor": "white",
    })


def save(fig: plt.Figure, stem: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for suffix in (".pdf", ".svg", ".png"):
        fig.savefig(OUT / (stem + suffix), dpi=300, bbox_inches="tight",
                    pad_inches=0.07)
    svg = OUT / (stem + ".svg")
    svg.write_text(
        "\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n",
        encoding="utf-8",
    )
    plt.close(fig)


def clean_axes(ax: plt.Axes, grid: bool = True) -> None:
    if grid:
        ax.grid(axis="y", color="0.88", linewidth=0.55, zorder=0)
    ax.tick_params(length=3, width=0.7, color="0.35")
    for spine in ("bottom", "left"):
        ax.spines[spine].set_linewidth(0.7)
        ax.spines[spine].set_color("0.35")


def host_values(index: dict, strategy: str, rate: float, field: str) -> list[float]:
    return [number(index[(strategy, rate, seed)], field) for seed in PAIRED_SEEDS]


def host_ci(values: list[float]) -> float:
    check(len(values) == 100, "Host CI requires exactly 100 seeds")
    return T_99_975 * statistics.stdev(values) / math.sqrt(100)


def figure1() -> None:
    """Diagram from frozen specification and the documented device/erasure path."""
    fig, ax = plt.subplots(figsize=(7.2, 4.05))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    width, height = 0.136, 0.115
    six_x = (0.03, 0.192, 0.354, 0.516, 0.678, 0.84)

    def box(x: float, y: float, w: float, h: float, label: str, kind: str) -> None:
        face, edge, dash = {
            "logical": ("white", "0.20", "solid"),
            "device": ("0.88", "0.20", "solid"),
            "injection": ("0.97", "0.20", (0, (4, 2))),
        }[kind]
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.008",
            facecolor=face, edgecolor=edge, linewidth=0.95, linestyle=dash,
        ))
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
                fontsize=7.1, linespacing=1.1)

    def arrow(start: tuple[float, float], end: tuple[float, float],
              style: str = "-") -> None:
        ax.add_patch(FancyArrowPatch(
            start, end, arrowstyle="-|>", mutation_scale=8.2, linewidth=0.9,
            linestyle=style, color="0.25", shrinkA=1.5, shrinkB=1.5,
        ))

    ax.text(0.03, 0.925, "LOGICAL DECISION", fontsize=7.4, weight="bold", color="0.30")
    control_x = (0.03, 0.285, 0.54, 0.795)
    control = (
        ("Seed-specific\ntrace", "logical"),
        ("Importance\nclassifier", "logical"),
        ("Redundancy\npolicy", "logical"),
        ("Sensor\nESP32-S3", "device"),
    )
    for x, (label, kind) in zip(control_x, control):
        box(x, 0.777, 0.16, 0.102, label, kind)
    for i in range(3):
        arrow((control_x[i] + 0.165, 0.828), (control_x[i + 1] - 0.006, 0.828))

    ax.text(0.03, 0.695, "DATA: DEVICE PATH \u2190", fontsize=7.4,
            weight="bold", color="0.30")
    data = (
        ("Accepted\nlogical sample", "logical"),
        ("DATA drop\nsoftware", "injection"),
        ("CRC / receive /\ndeduplication", "device"),
        ("Gateway\nESP32-S3", "device"),
        ("E220 DATA\ntransmission", "device"),
        ("Sensor\nDATA TX", "device"),
    )
    for x, (label, kind) in zip(six_x, data):
        box(x, 0.535, width, height, label, kind)
    for i in range(5, 0, -1):
        arrow((six_x[i] - 0.006, 0.592), (six_x[i - 1] + width + 0.006, 0.592))
    arrow((0.875, 0.773), (0.875, 0.657))

    ax.text(0.20, 0.405, "Software-injected erasure after frame reception (DATA and ACK)",
            fontsize=7.7, ha="left", color="0.20")
    ax.text(0.03, 0.355, "ACK RETURN: DEVICE PATH \u2192", fontsize=7.4,
            weight="bold", color="0.30")
    ack = (
        ("ACK\ngeneration", "logical"),
        ("E220 ACK\ntransmission", "device"),
        ("Sensor\nACK RX", "device"),
        ("ACK drop\nsoftware", "injection"),
        ("First-copy\naccepted ACK", "logical"),
        ("Link-state\nestimator", "logical"),
    )
    for x, (label, kind) in zip(six_x, ack):
        box(x, 0.20, width, height, label, kind)
    for i in range(5):
        arrow((six_x[i] + width + 0.006, 0.257),
              (six_x[i + 1] - 0.006, 0.257))
    arrow((0.098, 0.529), (0.098, 0.322))

    # The control feedback is routed outside the three paths.
    ax.plot([0.914, 0.986, 0.986, 0.622, 0.622],
            [0.320, 0.320, 0.965, 0.965, 0.892],
            color="0.35", linewidth=0.8, linestyle=(0, (3, 2)))
    arrow((0.622, 0.892), (0.622, 0.884), style=(0, (3, 2)))
    ax.text(0.75, 0.973, "link-state feedback", fontsize=7.0,
            ha="center", va="bottom", color="0.35")

    box(0.05, 0.055, 0.05, 0.035, "", "logical")
    ax.text(0.108, 0.073, "logical", fontsize=7.2, va="center")
    box(0.30, 0.055, 0.05, 0.035, "", "device")
    ax.text(0.358, 0.073, "device path", fontsize=7.2, va="center")
    box(0.60, 0.055, 0.05, 0.035, "", "injection")
    ax.text(0.658, 0.073, "software erasure", fontsize=7.2, va="center")
    save(fig, "fig1_system_architecture")


def figure2() -> None:
    """Render the frozen nine-cell matrix; values are checked against the spec."""
    fig, ax = plt.subplots(figsize=(5.7, 2.5))
    ax.set_xlim(-1.25, 3.08)
    ax.set_ylim(-0.67, 3.33)
    ax.axis("off")
    columns = ("GOOD", "DEGRADED", "BAD")
    matrix = (
        ("NORMAL", (1, 2, 1)),
        ("IMPORTANT", (2, 3, 3)),
        ("CRITICAL", (3, 3, 3)),
    )
    for col, label in enumerate(columns):
        ax.text(col + 0.5, 3.10, label, ha="center", va="center",
                fontsize=8.6, weight="bold")
    for row_index, (name, values) in enumerate(matrix):
        y = 2 - row_index
        ax.text(-0.10, y + 0.5, name, ha="right", va="center",
                fontsize=8.7, weight="bold" if name == "CRITICAL" else "normal")
        for col, count in enumerate(values):
            fill = {1: "0.97", 2: "0.83", 3: "0.69"}[count]
            ax.add_patch(Rectangle((col, y), 1, 1, facecolor=fill,
                                   edgecolor="0.55", linewidth=0.7))
            ax.text(col + 0.5, y + 0.55, str(count), ha="center", va="center",
                    fontsize=15, weight="bold")
            ax.text(col + 0.5, y + 0.24, "DATA copies", ha="center", va="center",
                    fontsize=6.8)
    ax.add_patch(Rectangle((0, 0), 3, 1, fill=False, edgecolor="0.05",
                           linewidth=1.65))
    ax.text(1.5, -0.37,
            "CRITICAL has no remaining link-adaptive action at the three-copy cap.",
            ha="center", va="center", fontsize=7.7)
    save(fig, "fig2_policy_matrix")


def figure3(host: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.1), constrained_layout=True)
    x = [rate * 100 for rate in HOST_RATES]
    for strategy in ("EVENTGUARD", "UNIFORM_BUDGET", "RANDOM_BUDGET"):
        values = [host_values(host, strategy, rate, "critical_event_delivery_ratio")
                  for rate in HOST_RATES]
        y = [mean(group) for group in values]
        ci = [host_ci(group) for group in values]
        axes[0].errorbar(x, y, yerr=ci, capsize=2.1, elinewidth=0.8,
                         label=STRATEGY_NAMES[strategy], zorder=3, **STYLES[strategy])
    axes[0].set_title("A  Critical-event delivery")
    axes[0].set_xlabel("Configured RANDOM_COPY loss (%)")
    axes[0].set_ylabel("Critical events delivered / critical events")
    axes[0].set_xticks(x)
    axes[0].set_ylim(0.87, 1.012)
    axes[0].legend(loc="lower left", frameon=False, handlelength=2.3)
    clean_axes(axes[0])

    groups = [host_values(host, "EVENTGUARD", rate,
                          "physical_data_transmissions") for rate in HOST_RATES]
    budgets = [mean(group) for group in groups]
    ci = [host_ci(group) for group in groups]
    axes[1].errorbar(x, budgets, yerr=ci, capsize=2.1, elinewidth=0.8,
                     color="0.15", marker="o", linestyle="-", label="All three strategies")
    axes[1].set_title("B  Matched DATA-copy budget")
    axes[1].set_xlabel("Configured RANDOM_COPY loss (%)")
    axes[1].set_ylabel("Physical DATA copies per run")
    axes[1].set_xticks(x)
    axes[1].set_ylim(0, 165)
    axes[1].text(0.03, 0.91, "EG = Uniform = Random\nwithin each paired seed",
                 transform=axes[1].transAxes, fontsize=7.2, va="top")
    clean_axes(axes[1])
    save(fig, "fig3_exact_budget_host")


def figure4(host: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.1), constrained_layout=True)
    x = [rate * 100 for rate in HOST_RATES]
    strategies = ("FIXED_2", "LINK_ONLY", "IMPORTANCE_ONLY", "EVENTGUARD")
    for strategy in strategies:
        delivery = [mean(host_values(host, strategy, rate,
                                     "critical_event_delivery_ratio"))
                    for rate in HOST_RATES]
        copies = [mean(host_values(host, strategy, rate,
                                   "physical_data_transmissions"))
                  for rate in HOST_RATES]
        for ax, y in zip(axes, (delivery, copies)):
            ax.plot(x, y, label=STRATEGY_NAMES[strategy], zorder=3,
                    **STYLES[strategy])
    axes[0].text(0.035, 0.12, "EventGuard and Importance Only\ncoincide at every rate",
                 transform=axes[0].transAxes, fontsize=7.0, va="bottom")
    axes[0].set_title("A  Critical-event delivery")
    axes[0].set_ylabel("Critical events delivered / critical events")
    axes[0].set_ylim(0.86, 1.012)
    axes[1].set_title("B  Communication cost")
    axes[1].set_ylabel("Physical DATA copies per run")
    axes[1].set_ylim(0, 165)
    for ax in axes:
        ax.set_xlabel("Configured RANDOM_COPY loss (%)")
        ax.set_xticks(x)
        clean_axes(ax)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, -0.11),
               ncol=4, frameon=False, handlelength=2.3)
    save(fig, "fig4_ablation_delivery_cost")


def figure5(hardware: dict) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.75), constrained_layout=True)
    markers = {
        "EVENTGUARD": dict(marker="o", s=100, facecolors="none",
                           edgecolors="0.05", linewidths=1.3, zorder=2),
        "IMPORTANCE_ONLY": dict(marker="s", s=43, facecolors="0.28",
                                edgecolors="0.28", linewidths=0.7, zorder=4),
        "UNIFORM_BUDGET": dict(marker="D", s=36, facecolors="0.72",
                               edgecolors="0.28", linewidths=0.7, zorder=3),
        "RANDOM_BUDGET": dict(marker="x", s=35, color="0.05",
                              linewidths=1.2, zorder=5),
    }
    for ax, (model, rate) in zip(axes.flat, HARDWARE_CONDITIONS):
        positions = defaultdict(list)
        for strategy in HARDWARE_STRATEGIES:
            row = hardware[(model, rate, strategy)]
            x = number(row, "physical_data_transmissions_mean")
            y = number(row, "critical_event_delivery_ratio_mean")
            positions[(round(x, 8), round(y, 8))].append(strategy)
            ax.scatter([x], [y], label=STRATEGY_NAMES[strategy],
                       **markers[strategy])
        for (x, y), coincident in positions.items():
            if len(coincident) > 1:
                labels = {"EVENTGUARD": "EG", "UNIFORM_BUDGET": "UB",
                          "RANDOM_BUDGET": "RB", "IMPORTANCE_ONLY": "IO"}
                ax.annotate(" = ".join(labels[item] for item in coincident),
                            (x, y), xytext=(-3, -12), textcoords="offset points",
                            fontsize=6.6, ha="right", color="0.3")
        io = hardware[(model, rate, "IMPORTANCE_ONLY")]
        eg = hardware[(model, rate, "EVENTGUARD")]
        y = number(io, "critical_event_delivery_ratio_mean")
        ax.plot([number(io, "physical_data_transmissions_mean"),
                 number(eg, "physical_data_transmissions_mean")],
                [y, y], color="0.68", linewidth=0.75, linestyle="--", zorder=1)
        ax.set_title(f"{model.replace('_', ' ').title()}  {rate:.0%}")
        ax.set_xlabel("Mean DATA copies / run")
        ax.set_ylabel("Mean critical-event delivery")
        ax.set_xlim(103, 155)
        ax.set_ylim(0.64, 1.025)
        ax.set_yticks((0.7, 0.8, 0.9, 1.0))
        clean_axes(ax)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, -0.065),
               ncol=4, frameon=False)
    save(fig, "fig5_hardware_pareto")


def figure6(sensitivity: dict) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.25), constrained_layout=True)
    windows = (8, 12, 16, 24)
    for rate, color, marker, style in (
        (0.20, "0.10", "o", "-"),
        (0.30, "0.52", "s", "--"),
    ):
        group = [sensitivity[("link_window", window, "RANDOM_COPY", rate)]
                 for window in windows]
        label = f"Random Copy {rate:.0%}"
        axes[0, 0].plot(windows, [number(row, "critical_delivery_mean")
                                  for row in group], color=color, marker=marker,
                        linestyle=style, label=label)
        axes[1, 0].plot(windows, [number(row, "data_copies_mean")
                                  for row in group], color=color, marker=marker,
                        linestyle=style, label=label)

    cap_styles = (
        ("RANDOM_COPY", 0.20, "0.10", "o"),
        ("RANDOM_COPY", 0.30, "0.45", "s"),
        ("BURST_SAMPLE", 0.20, "0.10", "^"),
        ("BURST_SAMPLE", 0.30, "0.45", "D"),
    )
    for model, rate, color, marker in cap_styles:
        for cap in (2, 3):
            row = sensitivity[("max_redundancy", cap, model, rate)]
            label = f"{model.replace('_', ' ').title()} {rate:.0%}" if cap == 2 else None
            axes[0, 1].scatter([cap], [number(row, "critical_delivery_mean")],
                               color=color, marker=marker, s=31, label=label, zorder=3)
            axes[1, 1].scatter([cap], [number(row, "data_copies_mean")],
                               color=color, marker=marker, s=31, zorder=3)

    axes[0, 0].set_title("A  Link-history window")
    axes[0, 1].set_title("B  Maximum copies")
    axes[0, 0].set_ylabel("Mean critical-event delivery")
    axes[0, 1].set_ylabel("Mean critical-event delivery")
    axes[1, 0].set_ylabel("Mean DATA copies / run")
    axes[1, 1].set_ylabel("Mean DATA copies / run")
    axes[1, 0].set_xlabel("First-copy history length")
    axes[1, 1].set_xlabel("Maximum copies / sample")
    for ax in axes[:, 0]:
        ax.set_xticks(windows)
    for ax in axes[:, 1]:
        ax.set_xticks((2, 3))
        ax.set_xlim(1.75, 3.25)
    axes[0, 0].set_ylim(0.88, 1.01)
    axes[0, 1].set_ylim(0.64, 1.01)
    axes[1, 0].set_ylim(95, 155)
    axes[1, 1].set_ylim(85, 155)
    for ax in axes.flat:
        clean_axes(ax)
    handles, labels = axes[0, 1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, -0.07),
               ncol=4, frameon=False, fontsize=6.8)
    save(fig, "fig6_sensitivity")


def main() -> None:
    validate_policy()
    host = validate_host()
    hardware = validate_hardware()
    sensitivity = validate_sensitivity()
    print("NUMERICAL_CONSISTENCY_PASS: policy, exact budgets, Table 4, "
          "96-run audit, Pareto counts, and sensitivity axes")
    configure_style()
    figure1()
    figure2()
    figure3(host)
    figure4(host)
    figure5(hardware)
    figure6(sensitivity)
    outputs = list(OUT.glob("fig[1-6]_*.pdf"))
    check(len(outputs) == 6, f"Expected six figure PDFs, found {len(outputs)}")
    print("FIGURES_GENERATED: 6/6 (PDF, SVG, PNG at 300 dpi)")


if __name__ == "__main__":
    main()
