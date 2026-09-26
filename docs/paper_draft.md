# Title

**Event-Aware Copy Allocation on a Constrained E220-Class Link: A Frozen Host-Simulation Study**

*Working paper draft. All new results below are host simulation. The earlier 300-run E220 experiment is a separate pilot and is not pooled with this evaluation.*

## Abstract

Resource-constrained sensor links must balance delivery of critical observations against communication cost. We evaluate a frozen, bounded proactive-copy policy called EventGuard-LoRa using deterministic 54-sample multivariate traces and application-layer DATA/ACK erasure calendars. The policy classifies event importance, estimates link state from copy-0 ACK outcomes, and selects at most three DATA copies. We compare eight strategies over five loss rates, two loss models, and 100 held-out evaluation seeds (8,000 runs), followed by 11,000 parameter-sensitivity runs. Under independent copy loss at 30%, EventGuard delivers 0.9723 of critical events versus 0.9438 and 0.9492 for uniform and random policies with the exact same DATA-copy budget. However, an importance-only ablation also delivers 0.9723 while using 112.00 mean copies rather than EventGuard's 142.65. EventGuard lies on none of ten within-condition critical-delivery/byte Pareto frontiers. Under whole-sample burst erasure, extra copies do not recover affected samples. These results support importance-guided allocation over event-blind allocation in this simulator but do not establish added critical-delivery value from link adaptation or performance on a physical radio channel.

## Introduction

Low-power sensor systems often emit routine observations alongside events that may matter more to an application. A copy budget spent uniformly can waste transmissions on routine samples while leaving critical samples exposed. This study asks: **under the same trace, canonical loss calendar, and total DATA-copy budget, does event-aware allocation improve critical-event delivery, and does copy-0 link adaptation add value beyond importance alone?**

We predeclare three comparisons: importance-aware versus fixed allocation; EventGuard versus two exact-budget event-blind allocations; and EventGuard versus importance-only and link-only ablations. The study deliberately preserves unfavorable results. The original 300-run E220 pilot used an older trace and design and is excluded from the statistical evaluation here.

## Related Work

The [LoRaWAN link-layer specification](https://resources.lora-alliance.org/document/ts001-1-0-4-lorawan-l2-1-0-4-specification) defines confirmed-message acknowledgment behavior. Work on [confirmed traffic in LoRaWAN](https://ieeexplore.ieee.org/abstract/document/8407095) reports that ACK-related design choices can impair performance at scale. [Hybrid coded replication in LoRa networks](https://arxiv.org/abs/2001.08168) studies message replication as a reliability mechanism. [Data-importance-aware retransmission for wireless edge learning](https://arxiv.org/abs/1812.02030) studies assigning reliability effort according to data value in a different system setting. These sources motivate the cost–reliability question; the present E220 experiment uses a compact application protocol and deterministic post-reception erasures, **not** LoRaWAN MAC operation. A fuller literature review and direct comparison of system assumptions are needed before journal submission.

## Method

The Sensor node produces temperature, humidity, light, and soil-moisture observations at a fixed 10-second trace interval. The classifier combines normalized change, rate, baseline deviation, multichannel co-change, persistence, and directional hazard level. Its frozen thresholds and exact equations are in [algorithm_spec_v1.md](algorithm_spec_v1.md). Link quality uses a 12-observation copy-0 accepted-ACK window and consecutive copy-0 failures. This yields a strategy-independent principal observation under the same calendar.

EventGuard maps NORMAL/IMPORTANT/CRITICAL to 1/2/3 copies on a GOOD link, then increases bounded copies for DEGRADED or BAD link state. The hard maximum is three copies. The Gateway deduplicates logical samples and acknowledges accepted physical frames. The simulator records physical DATA, physical ACK reception, application-accepted ACK, injected drops, delivery, bytes, and latency separately.

Eight strategies are evaluated: FIXED_1, FIXED_2, FIXED_3, IMPORTANCE_ONLY, LINK_ONLY, EVENTGUARD, UNIFORM_BUDGET, and RANDOM_BUDGET. Each budget baseline is assigned exactly EventGuard's DATA-copy count **within each matched run**; neither reads labels, classifier output, or sensor values. RANDOM_COPY erases canonical physical-copy opportunities independently. BURST_SAMPLE erases all three canonical opportunities of consecutive logical samples and is a common-mode failure model. DATA and ACK calendars are independent and shared across strategies.

## Experimental Setup

Calibration uses seeds 1–30. The locked evaluation uses seeds 31–130, five configured loss rates (0%, 5%, 10%, 20%, 30%), two main loss models, and eight strategies: 8,000 host runs. Each run has a 54-sample synthetic trace (nine phases × six samples). The evaluation reports critical, important, and overall delivery; DATA copies and total bytes; copies per generated and per delivered critical event; and critical delivery per 1,000 bytes. Per-condition summaries include mean, median, sample standard deviation, and a two-sided 95% confidence interval for the mean across the 100 seeds.

The predeclared paired statistical family contains 20 tests: EventGuard minus IMPORTANCE_ONLY and EventGuard minus UNIFORM_BUDGET across all ten model/rate conditions. We use an exact conditional sign-randomization distribution for the Wilcoxon signed-rank statistic with tied absolute differences assigned midranks, drop zero differences, report rank-biserial effect size and the number of nonzero pairs, and apply Holm correction across all 20 p-values. Equal-budget checks additionally require identical seed, trace fingerprint, and channel-calendar identifier. All code and output hashes are recorded in the experiment manifests.

The sensitivity grid uses the same 100 evaluation seeds: CRITICAL threshold 2.0/2.5/3.0/3.5/4.0; link window 8/12/16/24; and maximum redundancy 2/3. This adds 11,000 host runs. Maximum redundancy 4 is **not simulated**: the frozen policy, firmware, and canonical calendar define only three copy opportunities. Treating a fourth copy as loss-free would invalidate the comparison. No v1 parameter is retuned from the sensitivity results.

## Results

The held-out importance confusion matrix has 1,200 NORMAL correctly classified, 2,600 IMPORTANT correctly classified with 300 predicted CRITICAL, and 1,300 CRITICAL correctly classified. CRITICAL precision is 0.8125 and recall is 1.0000; NORMAL→CRITICAL false rate is zero on this simple synthetic trace. These values are not field-accuracy estimates.

| Condition | EventGuard critical delivery | Importance-only | Uniform exact budget | Random exact budget | EventGuard mean DATA copies |
|---|---:|---:|---:|---:|---:|
| RANDOM_COPY 20% | 0.9892 | 0.9892 | 0.9746 | 0.9769 | 140.68 |
| RANDOM_COPY 30% | 0.9723 | 0.9723 | 0.9438 | 0.9492 | 142.65 |
| BURST_SAMPLE 30% | 0.6985 | 0.6985 | 0.6985 | 0.6985 | 140.29 |

At RANDOM_COPY 30%, EventGuard versus uniform exact budget has a paired mean critical-delivery difference of +0.0285 (95% mean CI +0.0192 to +0.0377): 31 wins, 69 ties, zero losses. The exact signed-rank p-value is approximately 9.31 × 10⁻¹⁰; Holm-adjusted p across all 20 tests is approximately 1.86 × 10⁻⁸. The rank-biserial effect is 1 among the 31 nonzero pairs; the 69 ties and small absolute mean gain are essential for interpretation. EventGuard versus IMPORTANCE_ONLY ties in all 100 paired runs at this condition (p=1, effect size 0), while IMPORTANCE_ONLY uses 112.00 mean DATA copies.

Within each of the ten fixed model/rate conditions, at least one baseline achieves equal or higher mean critical delivery using fewer mean DATA copies. EventGuard appears on **zero** critical-delivery/byte Pareto frontiers. The [Pareto plots](../results/plots/pareto/pareto_frontier.png) and [all point values](../results/plots/pareto/pareto_points.csv) make this trade-off explicit.

Sensitivity experiments find no change in mean critical delivery across the tested CRITICAL thresholds or link windows for the displayed high-loss conditions, although traffic and link-state transitions vary. Increasing the cap from two to three improves RANDOM_COPY 30% critical delivery from 0.9185 to 0.9723 while raising mean copies from 101.00 to 142.65. Under BURST_SAMPLE 30%, both caps deliver 0.6985, while copies rise from 99.52 to 140.29. Full seed-level and uncertainty results are in [sensitivity](../results/sensitivity/report.md).

## Discussion

Importance-aware allocation is the supported mechanism for CRITICAL delivery in this simulator. The combined EventGuard policy improves over event-blind equal-budget allocation when copy losses are independent and nontrivial, yet its link component does not improve CRITICAL delivery beyond IMPORTANCE_ONLY. The reason is structural: predicted CRITICAL events already receive the maximum of three copies on a GOOD link. Link adaptation can improve IMPORTANT and overall delivery under RANDOM_COPY loss at additional cost; this is a different objective from critical delivery per byte and needs to be declared separately.

BURST_SAMPLE is a deliberately severe common-mode model: all copies of an affected sample disappear. Equal delivery across copy counts is therefore a property of the model, not evidence that all real-world bursts make redundancy ineffective. Pareto analysis is computed **within each model/rate condition**; costs from different channel conditions are never matched to make an equal-cost claim. Failure cases, including cost dominance, are detailed in [failure_analysis.md](../results/failure_analysis.md).

## Limitations

The evaluation is host simulation using a short, seeded synthetic trace and deterministic application-layer erasures. It does not measure RF fading, interference, E220 internal behavior, energy, regulatory airtime, or field event-label quality. The classifier's high recall is based on simple synthetic danger patterns. The study uses one predefined policy cap and one class distribution; confidence intervals across seeds reflect seeded noise/calendar variability, not diversity of deployments. A physical link cannot be held exactly constant across sequential strategy runs. The old 300-run E220 dataset is a design pilot with a different trace and is not confirmatory evidence for v1.

A subsequent frozen-v1 E220 smoke attempt completed three 0%-loss runs with host/firmware agreement, then stopped at the first 20%-injection EventGuard run because 13 of 141 logged DATA sends had no gateway receive or injection-drop record. This preliminary observation is retained in the [hardware report](../results/hardware_validation_v1/hardware_report.md); it is not included in the 8,000-run host statistics or presented as confirmation of the simulated benefit. Hardware cost is currently reported as an **estimated UART communication-time proxy**, calculated as `(DATA bytes + ACK bytes) × 10 bits/byte ÷ 9600 bit/s`. It is not measured RF PHY airtime. Energy was not directly measured and no Joule estimate is reported.

## Conclusion

Frozen v1 demonstrates a reproducible host-simulation benefit of importance-guided copy allocation over event-blind allocation at equal DATA-copy budgets under independent copy loss. It does **not** demonstrate additional CRITICAL-delivery efficiency from link adaptation, and the combined policy is dominated in critical-delivery/byte trade-offs across the tested conditions. The next step is a small, instrumented and interleaved hardware feasibility study when devices are available, with IMPORTANCE_ONLY and exact-budget baselines retained. A full hardware matrix is not justified by the present critical-delivery objective alone.
