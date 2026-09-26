# Event-Importance-Aware Redundancy Allocation for Critical IoT Events: An ESP32-S3/E220 Evaluation

*Working paper draft based on the post-hoc balanced analysis of 96 completed Stage 1 hardware runs. The original Stage 1 target was 160 runs; a later run103 stall interrupted expansion. The experiment uses real ESP32-S3 and E220-400T22D devices, but configured DATA/ACK losses are injected in software after frame reception; they are not measured RF packet-error rates.*

## Abstract

Low-power sensor links must balance delivery of high-value observations against communication cost. Using two ESP32-S3 nodes and E220-400T22D radios, we evaluate a frozen event-importance-aware copy policy on six deterministic 54-sample trace realizations, one per paired seed (31–36) and shared across strategies within each seed. The interrupted Stage 1 experiment yielded a post-hoc balanced set of 96 completed runs: four strategies, RANDOM_COPY and BURST_SAMPLE loss models, 20% and 30% configured application-layer loss, and six paired seeds (31–36). The selected seeds are the earliest contiguous seeds completed across the full condition matrix, chosen by execution order and completeness rather than outcome; the six-seed sample size was not preregistered. Seed-dependent sensor noise makes trace hashes differ across seeds. Offline re-parsing confirmed firmware counters, UART diagnostics, trace and loss-calendar identities, importance/copy/link decisions, and exact DATA-copy budgets for all 96 runs; no sample-level host/firmware outcome difference or uncontrolled physical DATA/ACK loss was present in this set. EventGuard and IMPORTANCE_ONLY had identical critical-event delivery in every paired seed and condition, while EventGuard used 21–33 more DATA copies per run on average. At equal DATA-copy budgets, EventGuard assigned a larger share of transmissions to ground-truth critical samples than event-blind baselines, but critical-delivery gains were limited: the largest mean paired gain was 0.0256 under RANDOM_COPY at 30%, with two gains and four ties out of six pairs. Under BURST_SAMPLE, all strategies had identical mean critical delivery at each rate. EventGuard appeared on none of the four within-condition delivery/cost Pareto frontiers; IMPORTANCE_ONLY appeared on all four. The results support event-importance-aware allocation as a way to target traffic, but do not show a critical-delivery benefit from the evaluated link-adaptation component or establish performance under uncontrolled RF conditions.

## Introduction

Periodic sensing streams combine routine measurements with events that may matter disproportionately to an application. Uniform redundancy can spend a limited transmission budget on routine samples, while event-aware redundancy can direct more copies toward observations predicted to be important. This paper asks:

1. Under a fixed DATA-copy budget, does event-aware allocation improve critical-event delivery relative to event-blind allocation?
2. Does link-state adaptation improve critical delivery beyond importance-only allocation?
3. How does the result change when loss is independent per copy versus common-mode across all copies of a sample?

The project preserves negative outcomes. The v1 policy is frozen, and this final hardware analysis does not retune thresholds, seeds, traces, or loss calendars. The study is a real device execution of an E220 UART DATA/ACK path with deterministic software loss injection; it is not a measurement of a LoRaWAN MAC or an uncontrolled radio channel.

## Related Work

The [LoRaWAN link-layer specification](https://resources.lora-alliance.org/document/ts001-1-0-4-lorawan-l2-1-0-4-specification) defines confirmed-message acknowledgment behavior. Prior work on [confirmed traffic in LoRaWAN](https://ieeexplore.ieee.org/abstract/document/8407095) examines ACK behavior at scale, while [replication in LoRa networks](https://arxiv.org/abs/2001.08168) and [data-importance-aware retransmission](https://arxiv.org/abs/1812.02030) motivate allocating reliability effort according to message value. EventGuard uses a compact application protocol over E220 modules and deterministic post-reception erasures, so its assumptions and results are not directly comparable to LoRaWAN MAC experiments. A fuller literature review and a systematic comparison of channel and protocol assumptions are needed before submission.

## Method

The Sensor replays a deterministic, seed-specific 54-sample multivariate trace realization at a 10-second interval. The importance classifier combines normalized change, rate, baseline deviation, multi-channel co-change, persistence, and a directional hazard rule. Exact equations, thresholds, windows, and core source hashes are recorded in [algorithm_spec_v1.md](algorithm_spec_v1.md). EventGuard selects bounded redundancy using event importance and a link estimator based on first-copy accepted-ACK outcomes. NORMAL, IMPORTANT, and CRITICAL map to 1, 2, and 3 copies on a GOOD link; the policy's maximum redundancy is three. The evaluated IMPORTANCE_ONLY ablation uses the same classifier without link-state adaptation.

The Gateway verifies and deduplicates DATA frames and sends an ACK for each DATA frame accepted after application-layer fault injection. `UNIFORM_BUDGET` and `RANDOM_BUDGET` receive the same frozen EventGuard DATA-copy budget for the matching trace, seed, model, and rate. Their allocation procedures do not read ground-truth labels, predicted importance, or sensor values. Ground-truth critical-copy share is calculated after each run for analysis only.

`RANDOM_COPY` uses deterministic, independent copy-level DATA and ACK erasure calendars. `BURST_SAMPLE` erases all canonical copy opportunities for selected contiguous logical samples. The loss calendars are deterministic and identical across strategies within each condition and seed. The firmware and host reference share each seed-specific trace realization, importance policy, copy policy, link thresholds, and loss implementation.

## Experimental Setup

Two ESP32-S3 boards were connected by E220-400T22D radios. The Sensor radio profile was UART1 at 9600 baud, E220 address 0, and channel register 23; both boards passed E220-ready checks before runs. Each run reset Sensor and Gateway state, configured the strategy and deterministic fault calendar, replayed 54 samples, and saved machine-readable serial records, firmware END counters, UART diagnostics, and a run manifest.

`FINAL_BALANCED_HARDWARE_SET_V1` is a post-hoc balanced primary analysis of the interrupted 160-run Stage 1 experiment: four strategies (`EVENTGUARD`, `IMPORTANCE_ONLY`, `UNIFORM_BUDGET`, `RANDOM_BUDGET`) × two loss models × two rates (20%, 30%) × seeds 31–36 = 96 runs. Seeds 31–36 are the first six contiguous evaluation seeds completed in the interleaved Stage 1 order, with all four strategies present in all four conditions. The balanced set is selected by execution order, completeness, and auditability, not by results; `n=6` was not preregistered. Partial seed-37 expansion records are excluded from paired inference.

The analysis unit is one seed/run pair (`n=6` per condition). We report mean, median, sample standard deviation, and 95% t confidence intervals. Paired comparisons use the exact two-sided Wilcoxon signed-rank test with rank-biserial effect size. P-values are unadjusted descriptive statistics and are not used for confirmatory significance claims. Exploratory Holm-adjusted p-values are also reported for the family of 12 critical-delivery comparisons. Given six pairs, interpretation emphasizes effect direction, magnitude, and paired consistency. The 95% intervals are t-based intervals over six seed-level observations and may cross the natural [0, 1] range for delivery ratios.

The communication cost is measured as physical DATA copies and transmitted DATA+ACK bytes. The time value is an estimated UART serialization proxy, `(DATA bytes + ACK bytes) × 10 bits / 9600 bit/s`; it is not measured RF airtime. No energy in Joules is estimated because current was not measured.

## Results

### Audit and host/firmware agreement

All 96 selected raw logs passed a fresh offline audit. Recomputed Sensor/Gateway END counts agreed with the raw event lines; UART_DIAG frame, byte, parser, and CRC counters were internally consistent. All 54 EVT and SAMPLE records were present per run. The frozen importance/copy/link replay had zero divergence; deterministic DATA/ACK loss-calendar decisions and exact budget totals matched. There were zero uncontrolled physical DATA losses, zero uncontrolled physical ACK losses, and zero per-sample delivery outcome differences relative to the host reference. Across conditions, host and hardware strategy rankings and paired contrast directions matched.

The agreement applies to the deterministic trace and software injection path. It does not establish agreement under uncontrolled RF fading, interference, changing range, or a different E220 installation.

### Critical delivery and communication cost

| Loss condition | Strategy | Critical delivery | Mean DATA copies | Mean total bytes | DATA share for ground-truth CRITICAL samples |
|---|---|---:|---:|---:|---:|
| RANDOM_COPY 20% | EVENTGUARD | 0.9744 | 142.50 | 5,154.5 | 27.4% |
| RANDOM_COPY 20% | IMPORTANCE_ONLY | 0.9744 | 112.00 | 4,056.0 | 34.8% |
| RANDOM_COPY 20% | UNIFORM_BUDGET | 0.9744 | 142.50 | 5,165.3 | 23.0% |
| RANDOM_COPY 20% | RANDOM_BUDGET | 0.9615 | 142.50 | 5,167.5 | 24.1% |
| RANDOM_COPY 30% | EVENTGUARD | 0.9487 | 145.00 | 5,033.2 | 26.9% |
| RANDOM_COPY 30% | IMPORTANCE_ONLY | 0.9487 | 112.00 | 3,913.0 | 34.8% |
| RANDOM_COPY 30% | UNIFORM_BUDGET | 0.9231 | 145.00 | 5,057.0 | 24.0% |
| RANDOM_COPY 30% | RANDOM_BUDGET | 0.9231 | 145.00 | 5,063.5 | 24.0% |
| BURST_SAMPLE 20% | EVENTGUARD | 0.7821 | 133.17 | 4,857.7 | 29.3% |
| BURST_SAMPLE 20% | IMPORTANCE_ONLY | 0.7821 | 112.00 | 4,084.2 | 34.8% |
| BURST_SAMPLE 20% | UNIFORM_BUDGET | 0.7821 | 133.17 | 4,862.0 | 21.1% |
| BURST_SAMPLE 20% | RANDOM_BUDGET | 0.7821 | 133.17 | 4,851.2 | 23.9% |
| BURST_SAMPLE 30% | EVENTGUARD | 0.7179 | 139.17 | 4,894.5 | 28.1% |
| BURST_SAMPLE 30% | IMPORTANCE_ONLY | 0.7179 | 112.00 | 3,943.3 | 34.8% |
| BURST_SAMPLE 30% | UNIFORM_BUDGET | 0.7179 | 139.17 | 4,920.5 | 22.7% |
| BURST_SAMPLE 30% | RANDOM_BUDGET | 0.7179 | 139.17 | 4,894.5 | 23.9% |

EventGuard and Importance Only have identical critical-event delivery for every paired seed in all four conditions. EventGuard uses 30.50, 33.00, 21.17, and 27.17 more DATA copies per run, respectively, and 1,098.5, 1,120.2, 773.5, and 951.2 more total bytes. Importance Only is therefore on the observed critical-delivery/cost frontier in all four conditions, while EventGuard is on none. The link-adaptation component did not add observed critical-delivery benefit over the importance-only ablation in these conditions.

At equal DATA-copy budgets, EventGuard allocates a larger share of copies to ground-truth critical samples than Uniform Budget and Random Budget in every condition. This confirms the intended allocation behavior, but does not imply a large or consistent end-to-end delivery gain. Against Uniform Budget, EventGuard ties in three conditions and is higher in two of six seeds (four ties) under RANDOM_COPY 30%, with mean paired difference +0.0256 and exact p=0.50. Against Random Budget, its mean paired difference is +0.0128 at RANDOM_COPY 20% (one higher, five ties) and +0.0256 at RANDOM_COPY 30% (two higher, four ties); all six pairs tie in both burst conditions. There are no losses in these listed critical-delivery comparisons, but six pairs are insufficient for a broad superiority claim.

### Burst-sample loss

All four strategies have the same mean critical delivery under BURST_SAMPLE: 0.7821 at 20% and 0.7179 at 30%. EventGuard is tied with each baseline in all six seeds. This matches the model's common-mode structure: if all copy opportunities of a logical sample are erased together, adding copies of that same sample does not recover it. This is a result about the tested deterministic burst model, not all bursty RF channels.

### Paired inference and Pareto frontier

The [paired tests](../results/final_hardware_v1/paired_tests.csv) include mean/median paired differences, standard deviation, 95% confidence interval, higher/equal/lower counts, exact raw p-value, Holm-adjusted p-value for the 12 critical-delivery comparisons, and rank-biserial effect for delivery, cost, UART-time proxy, and critical-copy share. For example, the all-positive EventGuard-minus-Importance-Only DATA-copy differences have exact p=0.03125 with rank-biserial effect 1.0 in each condition. This small-n result describes six paired runs and is not used as a broad population claim. Equal-delivery dominance by Importance Only is the central cost finding.

![Critical delivery versus DATA copies](../results/final_hardware_v1/plots/pareto_critical_vs_copies.png)

![Critical delivery versus transmitted bytes](../results/final_hardware_v1/plots/pareto_critical_vs_bytes.png)

Importance Only is on the empirical frontier in all four conditions under both copy and byte cost. EventGuard is on neither frontier. Frontier membership uses condition means and is descriptive, not a statistical confidence region.

## Discussion

The hardware executions preserve the host simulation's main conclusions for this deterministic setup: importance-aware redundancy can direct more DATA traffic to critical samples than event-blind equal-budget allocation; the benefit in critical-event delivery is modest and condition-dependent; and the link-adaptation ablation does not improve critical delivery beyond Importance Only. Importance Only has the same critical delivery with substantially fewer copies and bytes in all tested conditions. A defensible project contribution is therefore **event-importance-aware redundancy allocation for critical IoT events**, with link adaptation reported as an evaluated component that did not provide consistent additional benefit under these conditions.

The equal-budget result should be presented narrowly. EventGuard exceeds both blind budget baselines in mean critical delivery under RANDOM_COPY 30%, but only two of six paired runs differ in that direction; it ties the remaining four. Results under RANDOM_COPY 20% and BURST_SAMPLE do not establish a consistent delivery advantage. EventGuard's higher critical-copy share versus blind baselines is evidence of targeted allocation, while the unchanged or slightly improved delivery means show that targeted allocation does not always translate into additional delivery on a finite trace and fixed calendar.

## Limitations

This post-hoc balanced analysis has six seed-level experimental units per condition, one pair of ESP32-S3/E220 devices, six seed-specific 54-sample trace realizations from one synthetic trace generator, and deterministic application-layer loss injection. The configured loss rates are not physical RF packet-error measurements. There is no controlled range, fading, interference, antenna, regulatory airtime, or current measurement. The UART-time proxy is not RF PHY airtime, and there is no energy result. The classifier has not been evaluated against field-labeled events.

Seeds 31–36 form the complete balanced primary dataset because they are the first contiguous seeds completed across the full Stage 1 matrix. Later expansion data at seed 37 are not mixed into the paired analysis. In the extension, run 103 (`UNIFORM_BUDGET / RANDOM_COPY / 30% / seed37`) stalled while waiting for an ACK after a DATA frame was absent from Gateway pre-injection logs. Its incomplete raw log and diagnosis are preserved; the low-level cause remains unconfirmed and is not claimed to be resolved.

## Conclusion

The 96-run real-device dataset passes offline integrity and host/firmware consistency checks. Within the tested deterministic conditions, Importance Only matches EventGuard's critical delivery with lower communication cost, while EventGuard allocates a larger share of its exact DATA-copy budget to critical samples than event-blind baselines. Equal-budget critical-delivery gains are limited to a small directional advantage in some RANDOM_COPY conditions, and all strategies tie in the BURST_SAMPLE conditions. The current evidence supports presenting event importance as the principal mechanism and link adaptation as an ablation without consistent additional critical-delivery benefit. Broader claims require more seed-level replications and physical channel experiments that measure RF behavior directly.
