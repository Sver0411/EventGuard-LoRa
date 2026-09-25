"""Locked pre-hardware study; writes only separate host-simulation artifacts."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from .host import ROOT, _run_config, load_config
from .model import GroundTruth, LossModel, Strategy
from .simulator import run_reference
from .trace import generate_trace, trace_fingerprint

STRATEGIES = (Strategy.FIXED_1, Strategy.FIXED_2, Strategy.FIXED_3,
              Strategy.IMPORTANCE_ONLY, Strategy.LINK_ONLY, Strategy.EVENTGUARD,
              Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET)
MODELS = (LossModel.RANDOM_COPY, LossModel.BURST_SAMPLE)
RATES = (0.0, .05, .10, .20, .30)
CALIBRATION_SEEDS = tuple(range(1, 31))
EVALUATION_SEEDS = tuple(range(31, 131))
OUT = ROOT / "results" / "pre_hardware_v1"


def classifier_diagnostics(seeds: tuple[int, ...], samples_per_phase: int, cfg: dict) -> dict:
    from .importance import ImportanceClassifier, ImportanceConfig
    labels = tuple(label.value for label in GroundTruth)
    matrix = {truth: {pred: 0 for pred in labels} for truth in labels}
    for seed in seeds:
        classifier = ImportanceClassifier(ImportanceConfig(
            cfg["importance_thresholds"]["important"], cfg["importance_thresholds"]["critical"],
            cfg["importance_scales"], cfg["importance_baseline_alpha"]))
        for sample in generate_trace(seed, samples_per_phase):
            prediction, _ = classifier.classify(sample)
            matrix[sample.truth.value][prediction.name] += 1
    per_class = {}
    for label in labels:
        tp = matrix[label][label]
        predicted = sum(matrix[truth][label] for truth in labels)
        actual = sum(matrix[label].values())
        precision = tp / predicted if predicted else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1}
    total = sum(sum(row.values()) for row in matrix.values())
    return {"seeds": list(seeds), "matrix": matrix, "per_class": per_class,
            "accuracy": sum(matrix[label][label] for label in labels) / total,
            "macro_precision": mean(per_class[label]["precision"] for label in labels),
            "macro_recall": mean(per_class[label]["recall"] for label in labels),
            "macro_f1": mean(per_class[label]["f1"] for label in labels),
            "false_critical_rate": matrix["NORMAL"]["CRITICAL"] / sum(matrix["NORMAL"].values())}


def _fmt(value: float) -> str:
    return f"{value:.4f}"


def _plots(rows: list[dict], output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output.mkdir(parents=True, exist_ok=True)
    colors = {"EVENTGUARD": "#225ea8", "UNIFORM_BUDGET": "#f28e2b", "RANDOM_BUDGET": "#59a14f",
              "FIXED_1": "#7f7f7f", "FIXED_2": "#b07aa1", "FIXED_3": "#e15759",
              "IMPORTANCE_ONLY": "#76b7b2", "LINK_ONLY": "#edc949"}
    random_rows = [r for r in rows if r["loss_model"] == LossModel.RANDOM_COPY.value]
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    for strategy in ("EVENTGUARD", "UNIFORM_BUDGET", "RANDOM_BUDGET"):
        subset = [r for r in random_rows if r["strategy"] == strategy]
        ax.scatter([r["physical_data_transmissions"] for r in subset],
                   [r["critical_event_delivery_ratio"] for r in subset], alpha=.28, s=20,
                   color=colors[strategy], label=strategy)
    ax.set(xlabel="Exact DATA copy budget per run", ylabel="Critical delivery ratio", ylim=(-.03, 1.03),
           title="RANDOM_COPY: matched budget, same seeds and channel calendar")
    ax.grid(alpha=.2); ax.legend(); fig.tight_layout()
    fig.savefig(output / "critical_delivery_vs_exact_budget.png", dpi=160); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.7), sharey=True)
    for ax, model in zip(axes, MODELS):
        for strategy in ("EVENTGUARD", "UNIFORM_BUDGET", "RANDOM_BUDGET"):
            points = [mean(r["critical_event_delivery_ratio"] for r in rows
                           if r["loss_model"] == model.value and r["loss_rate"] == rate and r["strategy"] == strategy)
                      for rate in RATES]
            ax.plot([rate * 100 for rate in RATES], points, marker="o", color=colors[strategy], label=strategy)
        ax.set(title=model.value, xlabel="Configured loss (%)", ylim=(-.03, 1.03))
        ax.grid(alpha=.2)
    axes[0].set_ylabel("Mean critical delivery ratio")
    axes[1].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(output / "matched_channel_critical_delivery.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for strategy in ("FIXED_1", "FIXED_2", "FIXED_3", "IMPORTANCE_ONLY", "LINK_ONLY", "EVENTGUARD"):
        points = [mean(r["critical_event_delivery_ratio"] for r in random_rows
                       if r["loss_rate"] == rate and r["strategy"] == strategy) for rate in RATES]
        ax.plot([rate * 100 for rate in RATES], points, marker="o", color=colors[strategy], label=strategy)
    ax.set(xlabel="Configured RANDOM_COPY loss (%)", ylabel="Mean critical delivery ratio",
           ylim=(-.03, 1.03), title="Ablations under identical channel conditions")
    ax.grid(alpha=.2); ax.legend(fontsize=8, ncol=2)
    fig.tight_layout(); fig.savefig(output / "ablation_critical_delivery.png", dpi=160); plt.close(fig)


def _report(rows: list[dict], diag: dict, calibration: dict, warnings: list[dict], out: Path) -> None:
    grouped = defaultdict(list)
    paired = defaultdict(dict)
    for row in rows:
        grouped[(row["loss_model"], row["loss_rate"], row["strategy"])].append(row)
        paired[(row["loss_model"], row["loss_rate"], row["seed"])][row["strategy"]] = row
    lines = ["# Pre-hardware validation (host simulation)", "",
             f"Generated: {datetime.now(timezone.utc).isoformat()}", "",
             f"Evaluation matrix: {len(rows)} runs; seeds {min(diag['seeds'])}–{max(diag['seeds'])}. "
             f"Calibration: seeds {min(CALIBRATION_SEEDS)}–{max(CALIBRATION_SEEDS)} only. "
             "No physical E220 measurements are mixed into these figures.", "",
             "The legacy 300-run E220 results remain in `results/` as pilot v0. "
             "No hardware run was performed for this validation.", "",
             "## Importance classifier", "",
             "The calibrated thresholds were locked before evaluation. Ground truth uses phase semantics plus "
             "independent physical danger criteria (soil < 35%, or temperature > 30°C with humidity > 64%). "
             "A recovery is IMPORTANT unless values still cross these danger criteria. The score's IMPORTANT "
             "threshold is 0.85; the directional hazard-level CRITICAL threshold is 3.0. Sensor scales are "
             "temperature 0.5°C, humidity 5 points, light 100 lux, soil 8 points; baseline alpha is 0.06. "
             "These settings were selected on calibration seeds 1–30 and not changed after evaluation. "
             "The synthetic normal phases have only small jitter, so this confusion matrix does not establish "
             "field classification accuracy.", "",
             "| Ground truth | Pred NORMAL | Pred IMPORTANT | Pred CRITICAL |", "|---|---:|---:|---:|"]
    for label in ("NORMAL", "IMPORTANT", "CRITICAL"):
        row = diag["matrix"][label]
        lines.append(f"| {label} | {row['NORMAL']} | {row['IMPORTANT']} | {row['CRITICAL']} |")
    lines += ["", f"Accuracy {_fmt(diag['accuracy'])}; macro precision {_fmt(diag['macro_precision'])}; "
              f"macro recall {_fmt(diag['macro_recall'])}; macro F1 {_fmt(diag['macro_f1'])}.",
              f"CRITICAL precision {_fmt(diag['per_class']['CRITICAL']['precision'])}, "
              f"recall {_fmt(diag['per_class']['CRITICAL']['recall'])}, "
              f"F1 {_fmt(diag['per_class']['CRITICAL']['f1'])}. "
              f"NORMAL → CRITICAL false rate {_fmt(diag['false_critical_rate'])}.", "",
              "## Method", "",
              "Timestamps increase by exactly 10 s. Link state is estimated from first-copy outcomes to avoid "
              "strategy-dependent observation bias. All physical copy outcomes are still counted. "
              "RANDOM_COPY uses a canonical three-copy slot calendar. BURST_SAMPLE marks contiguous logical "
              "samples and drops every DATA or ACK copy opportunity in each marked sample. "
              "DATA and ACK calendars are independent. First-copy calendars and trace are identical for all strategies "
              "at a fixed model/rate/seed. UNIFORM_BUDGET uses round-robin; RANDOM_BUDGET uses a seeded "
              "hash ordering. Neither reads the event label, classifier score or truth. "
              "Both receive the exact EventGuard DATA-copy budget from the matching run.", "",
              "## Strategy results by matched channel condition", "",
              f"Each row averages {len(diag['seeds'])} evaluation seeds. Drop percentages are measured over attempted physical copies; "
              "the first-copy drop rate is shared exactly within each condition.", "",
              "| Model | Loss | Strategy | DATA copies | Critical delivery | Effective DATA drop | Effective ACK drop | First DATA drop | Critical / 1000 bytes |", 
              "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
    for (model, rate, strategy), group in sorted(grouped.items()):
        avg = lambda key: mean(float(row[key]) for row in group)
        lines.append(f"| {model} | {rate:.0%} | {strategy} | {avg('physical_data_transmissions'):.2f} | "
                     f"{avg('critical_event_delivery_ratio'):.3f} | {avg('effective_data_drop_rate'):.3f} | "
                     f"{avg('effective_ack_drop_rate'):.3f} | {avg('first_copy_data_drop_rate'):.3f} | "
                     f"{avg('critical_delivery_per_1000_bytes'):.3f} |")
    lines += ["", "## Same-condition paired comparisons", "",
              "Differences are EventGuard minus baseline under the same model, rate, seed, trace, channel calendar "
              "and simulated hardware. Only budget baselines have exact DATA-copy equality; fixed and ablation "
              "rows are diagnostic comparisons, not equal-cost claims.", "",
              "| Model | Loss | Baseline | Mean critical delivery gain | Mean DATA-copy difference | "
              "Seeds won / tied / lost |", "|---|---:|---|---:|---:|---:|"]
    advantage_conditions = []
    losing = []
    for model in MODELS:
        for rate in RATES:
            for baseline in STRATEGIES:
                if baseline == Strategy.EVENTGUARD:
                    continue
                cases = [values for (m, r, _), values in paired.items() if m == model.value and r == rate]
                diffs = [v[Strategy.EVENTGUARD.value]["critical_event_delivery_ratio"] -
                         v[baseline.value]["critical_event_delivery_ratio"] for v in cases]
                budgets = [v[Strategy.EVENTGUARD.value]["physical_data_transmissions"] -
                           v[baseline.value]["physical_data_transmissions"] for v in cases]
                wins, ties, losses = sum(d > 0 for d in diffs), sum(d == 0 for d in diffs), sum(d < 0 for d in diffs)
                gain = mean(diffs)
                lines.append(f"| {model.value} | {rate:.0%} | {baseline.value} | {gain:+.4f} | "
                             f"{mean(budgets):+.2f} | {wins}/{ties}/{losses} |")
                if baseline in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET):
                    assert all(b == 0 for b in budgets), "exact budget violated"
                    if gain > 0 and wins > losses:
                        advantage_conditions.append((model.value, rate, baseline.value, gain, wins, losses))
                    if gain < 0 or losses > wins:
                        losing.append((model.value, rate, baseline.value, gain, wins, losses))
    lines += ["", "## Fairness audit", "",
              f"Effective drop-rate gap alarms (> 5 percentage points within a matched run): {len(warnings)}. "
              "These reflect strategy-specific selection of copy opportunities; the first-copy calendars are "
              "identical. Inspect `pre_hardware_v1/fairness_alarms.json` for individual runs.", "",
              "## Gate decision", ""]
    gap_data = []
    gap_ack = []
    for model in MODELS:
        for rate in RATES:
            values = [v for (m, r, _), v in paired.items() if m == model.value and r == rate]
            gap_data.append(abs(mean(v[Strategy.EVENTGUARD.value]["effective_data_drop_rate"] -
                                     v[Strategy.FIXED_1.value]["effective_data_drop_rate"] for v in values)))
            gap_ack.append(abs(mean(v[Strategy.EVENTGUARD.value]["effective_ack_drop_rate"] -
                                    v[Strategy.FIXED_1.value]["effective_ack_drop_rate"] for v in values)))
    lines.insert(lines.index("## Gate decision"),
                 f"Maximum condition-level mean EventGuard−FIXED_1 effective-loss gap: "
                 f"DATA {max(gap_data):.4f}, ACK {max(gap_ack):.4f}. "
                 "First-copy DATA drops and accepted-ACK outcomes are exactly equal across strategies.")
    lines.insert(lines.index("## Gate decision"), "")
    ablation_dominates = all(
        values[Strategy.EVENTGUARD.value]["critical_event_delivery_ratio"] ==
        values[Strategy.IMPORTANCE_ONLY.value]["critical_event_delivery_ratio"] and
        values[Strategy.EVENTGUARD.value]["physical_data_transmissions"] >=
        values[Strategy.IMPORTANCE_ONLY.value]["physical_data_transmissions"]
        for values in paired.values())
    if ablation_dominates:
        lines.append("Hardware gate: CLOSED. IMPORTANCE_ONLY equals EventGuard's CRITICAL delivery in every "
                     "matched run while never using more DATA copies. The link component shows no incremental "
                     "CRITICAL-delivery benefit here. Do not rerun the full hardware matrix before redesigning "
                     "or justifying that component.")
    elif advantage_conditions:
        lines.append(f"EventGuard has a positive mean exact-budget gain in {len(advantage_conditions)} "
                     "model/rate/baseline comparisons. A focused hardware matrix may be considered.")
    else:
        lines.append("Hardware gate: CLOSED. EventGuard has no stable exact-budget advantage.")
    lines += ["", f"Positive mean exact-budget comparisons: {len(advantage_conditions)} of 20. "
              "At RANDOM_COPY 30%, the gains are small and most seeds tie. BURST_SAMPLE gives no "
              "DATA-delivery benefit from redundant copies."]
    lines += ["", "Exact-budget conditions where EventGuard loses:", ""]
    if losing:
        for model, rate, baseline, gain, wins, losses in losing:
            lines.append(f"- {model} {rate:.0%} vs {baseline}: mean gain {gain:+.4f}, {wins} wins, {losses} losses.")
    else:
        lines.append("- None by the stated mean/win criterion.")
    lines += ["", "Plots: [critical delivery vs exact DATA budget](pre_hardware_v1/plots/critical_delivery_vs_exact_budget.png), "
              "[matched channel delivery](pre_hardware_v1/plots/matched_channel_critical_delivery.png), "
              "[ablation](pre_hardware_v1/plots/ablation_critical_delivery.png).", "",
              "Most informative paper comparisons are EventGuard against both exact-budget baselines "
              "within RANDOM_COPY conditions, plus the importance-only and link-only ablations. "
              "BURST_SAMPLE isolates common-mode outages: all copies in a bad sample are erased, so "
              "extra redundancy cannot recover DATA in that sample.", "",
              "If the policy is redesigned and the gate later opens, use RANDOM_COPY and BURST_SAMPLE at "
              "0/5/10/20/30% with "
              "EventGuard, the two budget baselines, and both ablations over at least 30 paired seeds. "
              "Measure actual radio-channel drift and verify matched DATA budgets per run; this host result "
              "alone cannot prove on-air benefit.", ""]
    out.write_text("\n".join(lines), encoding="utf-8")


def run_validation(seeds: tuple[int, ...] = EVALUATION_SEEDS, samples_per_phase: int = 6,
                   rates: tuple[float, ...] = RATES, models: tuple[LossModel, ...] = MODELS,
                   strategies: tuple[Strategy, ...] = STRATEGIES, output: Path = OUT) -> list[dict]:
    if Strategy.EVENTGUARD not in strategies:
        raise ValueError("validation requires EVENTGUARD as the matched reference")
    cfg = load_config()
    calibration = classifier_diagnostics(CALIBRATION_SEEDS, samples_per_phase, cfg)
    diagnostics = classifier_diagnostics(seeds, samples_per_phase, cfg)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    warnings = []
    for seed in seeds:
        samples = generate_trace(seed, samples_per_phase)
        if any(b.timestamp_ms <= a.timestamp_ms for a, b in zip(samples, samples[1:])):
            raise AssertionError("non-increasing trace timestamp")
        fingerprint = trace_fingerprint(samples)
        for model in models:
            for rate in rates:
                eventguard_config = _run_config(cfg, Strategy.EVENTGUARD.value, rate, model.value, seed)
                eg = run_reference(samples, eventguard_config, cfg["uart_baud"])
                budget = eg["metrics"]["physical_data_transmissions"]
                condition = {}
                for strategy in strategies:
                    config = replace(eventguard_config, strategy=strategy, data_copy_budget=budget)
                    outcome = eg if strategy == Strategy.EVENTGUARD else run_reference(samples, config, cfg["uart_baud"])
                    row = {"strategy": strategy.value, "loss_model": model.value, "loss_rate": rate,
                           "seed": seed, "trace_sha256": fingerprint, "hardware": "HOST_SIMULATION",
                           "channel_calendar": f"{model.value}:{rate}:{seed}:{len(samples)}",
                           "critical_delivery_gain_at_equal_budget": None,
                           **outcome["metrics"]}
                    rows.append(row)
                    condition[strategy.value] = row
                anchor = condition[Strategy.EVENTGUARD.value]
                for budget_strategy in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET):
                    if budget_strategy.value in condition:
                        condition[budget_strategy.value]["critical_delivery_gain_at_equal_budget"] = (
                            anchor["critical_event_delivery_ratio"] -
                            condition[budget_strategy.value]["critical_event_delivery_ratio"])
                for strategy, row in condition.items():
                    if row["first_copy_data_drop_rate"] != anchor["first_copy_data_drop_rate"] or \
                       row["first_copy_success_ratio"] != anchor["first_copy_success_ratio"]:
                        raise AssertionError("first-copy fairness violated")
                    if abs(row["effective_data_drop_rate"] - anchor["effective_data_drop_rate"]) > .05 or \
                       abs(row["effective_ack_drop_rate"] - anchor["effective_ack_drop_rate"]) > .05:
                        warnings.append({"model": model.value, "rate": rate, "seed": seed,
                                         "strategy": strategy, "eventguard_data": anchor["effective_data_drop_rate"],
                                         "strategy_data": row["effective_data_drop_rate"],
                                         "eventguard_ack": anchor["effective_ack_drop_rate"],
                                         "strategy_ack": row["effective_ack_drop_rate"]})
    expected = len(seeds) * len(models) * len(rates) * len(strategies)
    assert len(rows) == expected
    for strategy in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET):
        if strategy not in strategies or Strategy.EVENTGUARD not in strategies:
            continue
        for seed in seeds:
            for model in models:
                for rate in rates:
                    pair = [r for r in rows if r["seed"] == seed and r["loss_model"] == model.value and r["loss_rate"] == rate
                            and r["strategy"] in (Strategy.EVENTGUARD.value, strategy.value)]
                    assert len(pair) == 2 and pair[0]["physical_data_transmissions"] == pair[1]["physical_data_transmissions"]
    (output / "runs.json").write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    (output / "importance.json").write_text(json.dumps({"calibration": calibration, "evaluation": diagnostics}, indent=2), encoding="utf-8")
    (output / "fairness_alarms.json").write_text(json.dumps(warnings, indent=2), encoding="utf-8")
    with (output / "runs.csv").open("w", newline="", encoding="utf-8") as stream:
        scalar_keys = [key for key, value in rows[0].items() if not isinstance(value, dict)]
        writer = csv.DictWriter(stream, fieldnames=scalar_keys, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    if (output == OUT and tuple(seeds) == EVALUATION_SEEDS and tuple(models) == MODELS and tuple(rates) == RATES and
            tuple(strategies) == STRATEGIES and samples_per_phase == 6):
        _plots(rows, output / "plots")
        _report(rows, diagnostics, calibration, warnings, ROOT / "results" / "pre_hardware_validation.md")
    return rows
