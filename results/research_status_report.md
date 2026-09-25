# EventGuard-LoRa research status: frozen algorithm v1

**Evidence tier:** host simulation only. No hardware was used in this round. The original 300-run E220 dataset is pilot v0 and is not mixed with this evaluation.

## 1. What does the algorithm currently contribute?

The v1 policy assigns 1/2/3 proactive DATA copies from predicted event importance and adds bounded copies for degraded first-copy ACK history. Against event-blind, exact-budget allocation it can improve critical delivery under independent copy loss. At RANDOM_COPY 30%, the mean critical-delivery ratios are EventGuard 0.9723, UNIFORM_BUDGET 0.9438, and RANDOM_BUDGET 0.9492; all three use the same 142.65 mean DATA copies and exactly matched copy counts in each paired run. The contribution supported here is **allocation by importance**, not a demonstrated CRITICAL-delivery gain from combining importance with link adaptation.

## 2. Which component produces the benefit?

- **Importance:** primary contributor for CRITICAL delivery. IMPORTANCE_ONLY reaches 0.9723 at RANDOM_COPY 30%, equal to EventGuard, with 112.00 mean copies rather than 142.65.
- **Link adaptation:** no incremental CRITICAL-delivery gain in any matched evaluation run; the three-copy cap is already reached for predicted CRITICAL events. It can increase IMPORTANT and overall delivery under RANDOM_COPY loss, with additional traffic.
- **Budget allocation:** EventGuard's importance-guided allocation has a small advantage over blind exact-budget allocation at some RANDOM_COPY rates. At 30%, the paired gain over UNIFORM_BUDGET is 0.0285 (95% mean CI 0.0192 to 0.0377); this does not imply the combined policy beats IMPORTANCE_ONLY.

## 3. Should EventGuard be retained?

Retain it as a **frozen research comparator**, with IMPORTANCE_ONLY as the stronger efficiency baseline for CRITICAL delivery. Do not claim the combined policy is Pareto superior or ready for a full hardware matrix. The link component may matter for an expanded objective that values IMPORTANT/overall delivery, but that objective must be declared before a new study. Preserve these unfavorable results.

## 4. What should the next hardware experiment test?

When hardware becomes available, first run a small, paired feasibility matrix with EVENTGUARD, IMPORTANCE_ONLY, UNIFORM_BUDGET, and RANDOM_BUDGET under the same trace, configured injection calendar, device pair, and measured RF conditions. Verify actual DATA-copy counts, ACK acceptance, airtime, energy, and ambient channel loss. Start with RANDOM_COPY 20%/30% and BURST_SAMPLE 30%, then decide whether a larger matrix is justified. A real RF channel cannot be assumed identical across sequential runs; interleave strategy order and record channel diagnostics. No hardware work was performed here.

## Completion and remaining limits

- Frozen baseline: 8,000 evaluation runs plus 11,000 host-only sensitivity runs.
- Statistics: per-condition mean, median, sample standard deviation, 95% mean CI; 20 predeclared exact Wilcoxon signed-rank tests with Holm correction.
- Pareto: EventGuard is on 0 of 10 mean critical-delivery/byte frontiers computed within fixed channel conditions.
- Failure audit: 0 strict critical-delivery losses; 10 conditions with a cheaper equal-or-better critical-delivery baseline.
- Maximum redundancy 4 was not simulated: v1 defines only three canonical copy opportunities. It requires a new algorithm/channel version.
- Largest limit: short synthetic 54-sample trace, deterministic application-layer erasures, one policy cap, and no new real-radio or field validation.

Artifacts: [algorithm spec](../docs/algorithm_spec_v1.md), [ablation](ablation/report.md), [sensitivity](sensitivity/report.md), [failure analysis](failure_analysis.md), [Pareto points](plots/pareto/pareto_points.csv), [paper draft](../docs/paper_draft.md).
