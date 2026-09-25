# Pre-hardware validation (host simulation)

Generated: 2026-09-25T18:19:32.393105+00:00

Evaluation matrix: 8000 runs; seeds 31–130. Calibration: seeds 1–30 only. No physical E220 measurements are mixed into these figures.

The legacy 300-run E220 results remain in `results/` as pilot v0. No hardware run was performed for this validation.

## Importance classifier

The calibrated thresholds were locked before evaluation. Ground truth uses phase semantics plus independent physical danger criteria (soil < 35%, or temperature > 30°C with humidity > 64%). A recovery is IMPORTANT unless values still cross these danger criteria. The score's IMPORTANT threshold is 0.85; the directional hazard-level CRITICAL threshold is 3.0. Sensor scales are temperature 0.5°C, humidity 5 points, light 100 lux, soil 8 points; baseline alpha is 0.06. These settings were selected on calibration seeds 1–30 and not changed after evaluation. The synthetic normal phases have only small jitter, so this confusion matrix does not establish field classification accuracy.

| Ground truth | Pred NORMAL | Pred IMPORTANT | Pred CRITICAL |
|---|---:|---:|---:|
| NORMAL | 1200 | 0 | 0 |
| IMPORTANT | 0 | 2600 | 300 |
| CRITICAL | 0 | 0 | 1300 |

Accuracy 0.9444; macro precision 0.9375; macro recall 0.9655; macro F1 0.9473.
CRITICAL precision 0.8125, recall 1.0000, F1 0.8966. NORMAL → CRITICAL false rate 0.0000.

## Method

Timestamps increase by exactly 10 s. Link state is estimated from first-copy outcomes to avoid strategy-dependent observation bias. All physical copy outcomes are still counted. RANDOM_COPY uses a canonical three-copy slot calendar. BURST_SAMPLE marks contiguous logical samples and drops every DATA or ACK copy opportunity in each marked sample. DATA and ACK calendars are independent. First-copy calendars and trace are identical for all strategies at a fixed model/rate/seed. UNIFORM_BUDGET uses round-robin; RANDOM_BUDGET uses a seeded hash ordering. Neither reads the event label, classifier score or truth. Both receive the exact EventGuard DATA-copy budget from the matching run.

## Strategy results by matched channel condition

Each row averages 100 evaluation seeds. Drop percentages are measured over attempted physical copies; the first-copy drop rate is shared exactly within each condition.

| Model | Loss | Strategy | DATA copies | Critical delivery | Effective DATA drop | Effective ACK drop | First DATA drop | Critical / 1000 bytes |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| BURST_SAMPLE | 0% | EVENTGUARD | 112.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.976 |
| BURST_SAMPLE | 0% | FIXED_1 | 54.00 | 1.000 | 0.000 | 0.000 | 0.000 | 6.173 |
| BURST_SAMPLE | 0% | FIXED_2 | 108.00 | 1.000 | 0.000 | 0.000 | 0.000 | 3.086 |
| BURST_SAMPLE | 0% | FIXED_3 | 162.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.058 |
| BURST_SAMPLE | 0% | IMPORTANCE_ONLY | 112.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.976 |
| BURST_SAMPLE | 0% | LINK_ONLY | 54.00 | 1.000 | 0.000 | 0.000 | 0.000 | 6.173 |
| BURST_SAMPLE | 0% | RANDOM_BUDGET | 112.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.976 |
| BURST_SAMPLE | 0% | UNIFORM_BUDGET | 112.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.976 |
| BURST_SAMPLE | 5% | EVENTGUARD | 122.45 | 0.948 | 0.056 | 0.054 | 0.056 | 2.632 |
| BURST_SAMPLE | 5% | FIXED_1 | 54.00 | 0.948 | 0.056 | 0.055 | 0.056 | 5.965 |
| BURST_SAMPLE | 5% | FIXED_2 | 108.00 | 0.948 | 0.056 | 0.055 | 0.056 | 2.983 |
| BURST_SAMPLE | 5% | FIXED_3 | 162.00 | 0.948 | 0.056 | 0.055 | 0.056 | 1.988 |
| BURST_SAMPLE | 5% | IMPORTANCE_ONLY | 112.00 | 0.948 | 0.056 | 0.055 | 0.056 | 2.875 |
| BURST_SAMPLE | 5% | LINK_ONLY | 73.05 | 0.948 | 0.055 | 0.050 | 0.056 | 4.428 |
| BURST_SAMPLE | 5% | RANDOM_BUDGET | 122.45 | 0.948 | 0.054 | 0.056 | 0.056 | 2.631 |
| BURST_SAMPLE | 5% | UNIFORM_BUDGET | 122.45 | 0.948 | 0.056 | 0.055 | 0.056 | 2.633 |
| BURST_SAMPLE | 10% | EVENTGUARD | 125.68 | 0.911 | 0.095 | 0.090 | 0.093 | 2.496 |
| BURST_SAMPLE | 10% | FIXED_1 | 54.00 | 0.911 | 0.093 | 0.090 | 0.093 | 5.801 |
| BURST_SAMPLE | 10% | FIXED_2 | 108.00 | 0.911 | 0.093 | 0.090 | 0.093 | 2.901 |
| BURST_SAMPLE | 10% | FIXED_3 | 162.00 | 0.911 | 0.093 | 0.090 | 0.093 | 1.934 |
| BURST_SAMPLE | 10% | IMPORTANCE_ONLY | 112.00 | 0.911 | 0.092 | 0.089 | 0.093 | 2.795 |
| BURST_SAMPLE | 10% | LINK_ONLY | 79.07 | 0.911 | 0.101 | 0.094 | 0.093 | 3.997 |
| BURST_SAMPLE | 10% | RANDOM_BUDGET | 125.68 | 0.911 | 0.092 | 0.091 | 0.093 | 2.493 |
| BURST_SAMPLE | 10% | UNIFORM_BUDGET | 125.68 | 0.911 | 0.093 | 0.090 | 0.093 | 2.495 |
| BURST_SAMPLE | 20% | EVENTGUARD | 137.36 | 0.792 | 0.206 | 0.214 | 0.204 | 2.064 |
| BURST_SAMPLE | 20% | FIXED_1 | 54.00 | 0.792 | 0.204 | 0.210 | 0.204 | 5.247 |
| BURST_SAMPLE | 20% | FIXED_2 | 108.00 | 0.792 | 0.204 | 0.210 | 0.204 | 2.624 |
| BURST_SAMPLE | 20% | FIXED_3 | 162.00 | 0.792 | 0.204 | 0.210 | 0.204 | 1.749 |
| BURST_SAMPLE | 20% | IMPORTANCE_ONLY | 112.00 | 0.792 | 0.205 | 0.211 | 0.204 | 2.528 |
| BURST_SAMPLE | 20% | LINK_ONLY | 110.83 | 0.792 | 0.212 | 0.220 | 0.204 | 2.573 |
| BURST_SAMPLE | 20% | RANDOM_BUDGET | 137.36 | 0.792 | 0.203 | 0.211 | 0.204 | 2.063 |
| BURST_SAMPLE | 20% | UNIFORM_BUDGET | 137.36 | 0.792 | 0.204 | 0.210 | 0.204 | 2.064 |
| BURST_SAMPLE | 30% | EVENTGUARD | 140.29 | 0.698 | 0.301 | 0.314 | 0.296 | 1.846 |
| BURST_SAMPLE | 30% | FIXED_1 | 54.00 | 0.698 | 0.296 | 0.311 | 0.296 | 4.784 |
| BURST_SAMPLE | 30% | FIXED_2 | 108.00 | 0.698 | 0.296 | 0.311 | 0.296 | 2.392 |
| BURST_SAMPLE | 30% | FIXED_3 | 162.00 | 0.698 | 0.296 | 0.311 | 0.296 | 1.595 |
| BURST_SAMPLE | 30% | IMPORTANCE_ONLY | 112.00 | 0.698 | 0.298 | 0.311 | 0.296 | 2.303 |
| BURST_SAMPLE | 30% | LINK_ONLY | 130.70 | 0.698 | 0.305 | 0.317 | 0.296 | 1.992 |
| BURST_SAMPLE | 30% | RANDOM_BUDGET | 140.29 | 0.698 | 0.295 | 0.311 | 0.296 | 1.843 |
| BURST_SAMPLE | 30% | UNIFORM_BUDGET | 140.29 | 0.698 | 0.298 | 0.310 | 0.296 | 1.845 |
| RANDOM_COPY | 0% | EVENTGUARD | 112.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.976 |
| RANDOM_COPY | 0% | FIXED_1 | 54.00 | 1.000 | 0.000 | 0.000 | 0.000 | 6.173 |
| RANDOM_COPY | 0% | FIXED_2 | 108.00 | 1.000 | 0.000 | 0.000 | 0.000 | 3.086 |
| RANDOM_COPY | 0% | FIXED_3 | 162.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.058 |
| RANDOM_COPY | 0% | IMPORTANCE_ONLY | 112.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.976 |
| RANDOM_COPY | 0% | LINK_ONLY | 54.00 | 1.000 | 0.000 | 0.000 | 0.000 | 6.173 |
| RANDOM_COPY | 0% | RANDOM_BUDGET | 112.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.976 |
| RANDOM_COPY | 0% | UNIFORM_BUDGET | 112.00 | 1.000 | 0.000 | 0.000 | 0.000 | 2.976 |
| RANDOM_COPY | 5% | EVENTGUARD | 116.45 | 1.000 | 0.050 | 0.051 | 0.051 | 2.916 |
| RANDOM_COPY | 5% | FIXED_1 | 54.00 | 0.957 | 0.051 | 0.050 | 0.051 | 6.009 |
| RANDOM_COPY | 5% | FIXED_2 | 108.00 | 1.000 | 0.052 | 0.051 | 0.051 | 3.141 |
| RANDOM_COPY | 5% | FIXED_3 | 162.00 | 1.000 | 0.051 | 0.050 | 0.051 | 2.093 |
| RANDOM_COPY | 5% | IMPORTANCE_ONLY | 112.00 | 1.000 | 0.051 | 0.051 | 0.051 | 3.028 |
| RANDOM_COPY | 5% | LINK_ONLY | 60.16 | 0.960 | 0.049 | 0.051 | 0.051 | 5.470 |
| RANDOM_COPY | 5% | RANDOM_BUDGET | 116.45 | 1.000 | 0.051 | 0.051 | 0.051 | 2.917 |
| RANDOM_COPY | 5% | UNIFORM_BUDGET | 116.45 | 1.000 | 0.051 | 0.051 | 0.051 | 2.917 |
| RANDOM_COPY | 10% | EVENTGUARD | 127.83 | 0.998 | 0.100 | 0.101 | 0.102 | 2.703 |
| RANDOM_COPY | 10% | FIXED_1 | 54.00 | 0.899 | 0.102 | 0.105 | 0.102 | 5.744 |
| RANDOM_COPY | 10% | FIXED_2 | 108.00 | 0.991 | 0.100 | 0.101 | 0.102 | 3.163 |
| RANDOM_COPY | 10% | FIXED_3 | 162.00 | 0.998 | 0.102 | 0.100 | 0.102 | 2.127 |
| RANDOM_COPY | 10% | IMPORTANCE_ONLY | 112.00 | 0.998 | 0.099 | 0.102 | 0.102 | 3.073 |
| RANDOM_COPY | 10% | LINK_ONLY | 77.97 | 0.942 | 0.099 | 0.102 | 0.102 | 4.265 |
| RANDOM_COPY | 10% | RANDOM_BUDGET | 127.83 | 0.993 | 0.101 | 0.100 | 0.102 | 2.689 |
| RANDOM_COPY | 10% | UNIFORM_BUDGET | 127.83 | 0.991 | 0.102 | 0.100 | 0.102 | 2.683 |
| RANDOM_COPY | 20% | EVENTGUARD | 140.68 | 0.989 | 0.202 | 0.203 | 0.204 | 2.517 |
| RANDOM_COPY | 20% | FIXED_1 | 54.00 | 0.792 | 0.204 | 0.203 | 0.204 | 5.233 |
| RANDOM_COPY | 20% | FIXED_2 | 108.00 | 0.960 | 0.199 | 0.201 | 0.204 | 3.173 |
| RANDOM_COPY | 20% | FIXED_3 | 162.00 | 0.989 | 0.201 | 0.201 | 0.204 | 2.182 |
| RANDOM_COPY | 20% | IMPORTANCE_ONLY | 112.00 | 0.989 | 0.200 | 0.202 | 0.204 | 3.155 |
| RANDOM_COPY | 20% | LINK_ONLY | 107.02 | 0.942 | 0.198 | 0.201 | 0.204 | 3.181 |
| RANDOM_COPY | 20% | RANDOM_BUDGET | 140.68 | 0.977 | 0.201 | 0.201 | 0.204 | 2.486 |
| RANDOM_COPY | 20% | UNIFORM_BUDGET | 140.68 | 0.975 | 0.202 | 0.201 | 0.204 | 2.480 |
| RANDOM_COPY | 30% | EVENTGUARD | 142.65 | 0.972 | 0.300 | 0.302 | 0.299 | 2.526 |
| RANDOM_COPY | 30% | FIXED_1 | 54.00 | 0.704 | 0.299 | 0.304 | 0.299 | 4.818 |
| RANDOM_COPY | 30% | FIXED_2 | 108.00 | 0.918 | 0.296 | 0.304 | 0.299 | 3.145 |
| RANDOM_COPY | 30% | FIXED_3 | 162.00 | 0.972 | 0.300 | 0.302 | 0.299 | 2.223 |
| RANDOM_COPY | 30% | IMPORTANCE_ONLY | 112.00 | 0.972 | 0.298 | 0.304 | 0.299 | 3.213 |
| RANDOM_COPY | 30% | LINK_ONLY | 129.48 | 0.938 | 0.299 | 0.303 | 0.299 | 2.702 |
| RANDOM_COPY | 30% | RANDOM_BUDGET | 142.65 | 0.949 | 0.298 | 0.302 | 0.299 | 2.463 |
| RANDOM_COPY | 30% | UNIFORM_BUDGET | 142.65 | 0.944 | 0.299 | 0.304 | 0.299 | 2.450 |

## Same-condition paired comparisons

Differences are EventGuard minus baseline under the same model, rate, seed, trace, channel calendar and simulated hardware. Only budget baselines have exact DATA-copy equality; fixed and ablation rows are diagnostic comparisons, not equal-cost claims.

| Model | Loss | Baseline | Mean critical delivery gain | Mean DATA-copy difference | Seeds won / tied / lost |
|---|---:|---|---:|---:|---:|
| RANDOM_COPY | 0% | FIXED_1 | +0.0000 | +58.00 | 0/100/0 |
| RANDOM_COPY | 0% | FIXED_2 | +0.0000 | +4.00 | 0/100/0 |
| RANDOM_COPY | 0% | FIXED_3 | +0.0000 | -50.00 | 0/100/0 |
| RANDOM_COPY | 0% | IMPORTANCE_ONLY | +0.0000 | +0.00 | 0/100/0 |
| RANDOM_COPY | 0% | LINK_ONLY | +0.0000 | +58.00 | 0/100/0 |
| RANDOM_COPY | 0% | UNIFORM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| RANDOM_COPY | 0% | RANDOM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| RANDOM_COPY | 5% | FIXED_1 | +0.0431 | +62.45 | 47/53/0 |
| RANDOM_COPY | 5% | FIXED_2 | +0.0000 | +8.45 | 0/100/0 |
| RANDOM_COPY | 5% | FIXED_3 | +0.0000 | -45.55 | 0/100/0 |
| RANDOM_COPY | 5% | IMPORTANCE_ONLY | +0.0000 | +4.45 | 0/100/0 |
| RANDOM_COPY | 5% | LINK_ONLY | +0.0400 | +56.29 | 45/55/0 |
| RANDOM_COPY | 5% | UNIFORM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| RANDOM_COPY | 5% | RANDOM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| RANDOM_COPY | 10% | FIXED_1 | +0.0992 | +73.83 | 76/24/0 |
| RANDOM_COPY | 10% | FIXED_2 | +0.0077 | +19.83 | 10/90/0 |
| RANDOM_COPY | 10% | FIXED_3 | +0.0000 | -34.17 | 0/100/0 |
| RANDOM_COPY | 10% | IMPORTANCE_ONLY | +0.0000 | +15.83 | 0/100/0 |
| RANDOM_COPY | 10% | LINK_ONLY | +0.0569 | +49.86 | 56/44/0 |
| RANDOM_COPY | 10% | UNIFORM_BUDGET | +0.0077 | +0.00 | 10/90/0 |
| RANDOM_COPY | 10% | RANDOM_BUDGET | +0.0054 | +0.00 | 7/93/0 |
| RANDOM_COPY | 20% | FIXED_1 | +0.1977 | +86.68 | 93/7/0 |
| RANDOM_COPY | 20% | FIXED_2 | +0.0292 | +32.68 | 29/71/0 |
| RANDOM_COPY | 20% | FIXED_3 | +0.0000 | -21.32 | 0/100/0 |
| RANDOM_COPY | 20% | IMPORTANCE_ONLY | +0.0000 | +28.68 | 0/100/0 |
| RANDOM_COPY | 20% | LINK_ONLY | +0.0477 | +33.66 | 49/51/0 |
| RANDOM_COPY | 20% | UNIFORM_BUDGET | +0.0146 | +0.00 | 18/82/0 |
| RANDOM_COPY | 20% | RANDOM_BUDGET | +0.0123 | +0.00 | 14/86/0 |
| RANDOM_COPY | 30% | FIXED_1 | +0.2685 | +88.65 | 98/2/0 |
| RANDOM_COPY | 30% | FIXED_2 | +0.0538 | +34.65 | 49/51/0 |
| RANDOM_COPY | 30% | FIXED_3 | +0.0000 | -19.35 | 0/100/0 |
| RANDOM_COPY | 30% | IMPORTANCE_ONLY | +0.0000 | +30.65 | 0/100/0 |
| RANDOM_COPY | 30% | LINK_ONLY | +0.0338 | +13.17 | 33/67/0 |
| RANDOM_COPY | 30% | UNIFORM_BUDGET | +0.0285 | +0.00 | 31/69/0 |
| RANDOM_COPY | 30% | RANDOM_BUDGET | +0.0231 | +0.00 | 24/76/0 |
| BURST_SAMPLE | 0% | FIXED_1 | +0.0000 | +58.00 | 0/100/0 |
| BURST_SAMPLE | 0% | FIXED_2 | +0.0000 | +4.00 | 0/100/0 |
| BURST_SAMPLE | 0% | FIXED_3 | +0.0000 | -50.00 | 0/100/0 |
| BURST_SAMPLE | 0% | IMPORTANCE_ONLY | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 0% | LINK_ONLY | +0.0000 | +58.00 | 0/100/0 |
| BURST_SAMPLE | 0% | UNIFORM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 0% | RANDOM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 5% | FIXED_1 | +0.0000 | +68.45 | 0/100/0 |
| BURST_SAMPLE | 5% | FIXED_2 | +0.0000 | +14.45 | 0/100/0 |
| BURST_SAMPLE | 5% | FIXED_3 | +0.0000 | -39.55 | 0/100/0 |
| BURST_SAMPLE | 5% | IMPORTANCE_ONLY | +0.0000 | +10.45 | 0/100/0 |
| BURST_SAMPLE | 5% | LINK_ONLY | +0.0000 | +49.40 | 0/100/0 |
| BURST_SAMPLE | 5% | UNIFORM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 5% | RANDOM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 10% | FIXED_1 | +0.0000 | +71.68 | 0/100/0 |
| BURST_SAMPLE | 10% | FIXED_2 | +0.0000 | +17.68 | 0/100/0 |
| BURST_SAMPLE | 10% | FIXED_3 | +0.0000 | -36.32 | 0/100/0 |
| BURST_SAMPLE | 10% | IMPORTANCE_ONLY | +0.0000 | +13.68 | 0/100/0 |
| BURST_SAMPLE | 10% | LINK_ONLY | +0.0000 | +46.61 | 0/100/0 |
| BURST_SAMPLE | 10% | UNIFORM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 10% | RANDOM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 20% | FIXED_1 | +0.0000 | +83.36 | 0/100/0 |
| BURST_SAMPLE | 20% | FIXED_2 | +0.0000 | +29.36 | 0/100/0 |
| BURST_SAMPLE | 20% | FIXED_3 | +0.0000 | -24.64 | 0/100/0 |
| BURST_SAMPLE | 20% | IMPORTANCE_ONLY | +0.0000 | +25.36 | 0/100/0 |
| BURST_SAMPLE | 20% | LINK_ONLY | +0.0000 | +26.53 | 0/100/0 |
| BURST_SAMPLE | 20% | UNIFORM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 20% | RANDOM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 30% | FIXED_1 | +0.0000 | +86.29 | 0/100/0 |
| BURST_SAMPLE | 30% | FIXED_2 | +0.0000 | +32.29 | 0/100/0 |
| BURST_SAMPLE | 30% | FIXED_3 | +0.0000 | -21.71 | 0/100/0 |
| BURST_SAMPLE | 30% | IMPORTANCE_ONLY | +0.0000 | +28.29 | 0/100/0 |
| BURST_SAMPLE | 30% | LINK_ONLY | +0.0000 | +9.59 | 0/100/0 |
| BURST_SAMPLE | 30% | UNIFORM_BUDGET | +0.0000 | +0.00 | 0/100/0 |
| BURST_SAMPLE | 30% | RANDOM_BUDGET | +0.0000 | +0.00 | 0/100/0 |

## Fairness audit

Effective drop-rate gap alarms (> 5 percentage points within a matched run): 374. These reflect strategy-specific selection of copy opportunities; the first-copy calendars are identical. Inspect `pre_hardware_v1/fairness_alarms.json` for individual runs.

Maximum condition-level mean EventGuard−FIXED_1 effective-loss gap: DATA 0.0048, ACK 0.0048. First-copy DATA drops and accepted-ACK outcomes are exactly equal across strategies.

## Gate decision

Hardware gate: CLOSED. IMPORTANCE_ONLY equals EventGuard's CRITICAL delivery in every matched run while never using more DATA copies. The link component shows no incremental CRITICAL-delivery benefit here. Do not rerun the full hardware matrix before redesigning or justifying that component.

Positive mean exact-budget comparisons: 6 of 20. At RANDOM_COPY 30%, the gains are small and most seeds tie. BURST_SAMPLE gives no DATA-delivery benefit from redundant copies.

Exact-budget conditions where EventGuard loses:

- None by the stated mean/win criterion.

Plots: [critical delivery vs exact DATA budget](pre_hardware_v1/plots/critical_delivery_vs_exact_budget.png), [matched channel delivery](pre_hardware_v1/plots/matched_channel_critical_delivery.png), [ablation](pre_hardware_v1/plots/ablation_critical_delivery.png).

Most informative paper comparisons are EventGuard against both exact-budget baselines within RANDOM_COPY conditions, plus the importance-only and link-only ablations. BURST_SAMPLE isolates common-mode outages: all copies in a bad sample are erased, so extra redundancy cannot recover DATA in that sample.

If the policy is redesigned and the gate later opens, use RANDOM_COPY and BURST_SAMPLE at 0/5/10/20/30% with EventGuard, the two budget baselines, and both ablations over at least 30 paired seeds. Measure actual radio-channel drift and verify matched DATA budgets per run; this host result alone cannot prove on-air benefit.
