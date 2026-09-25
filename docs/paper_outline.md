# EventGuard-LoRa Paper Outline

## Working title
Event-Aware Bounded Redundancy for Critical Sensor Delivery over E220 LoRa-Class Links

## Abstract draft
We implemented EventGuard-LoRa on two ESP32-S3 boards connected by E220-400T22D radios and compared no protection, fixed two-copy redundancy, and event/link-aware redundancy capped at three copies. The current measured dataset contains 300 real-radio runs with deterministic synthetic sensor traces and post-reception application-layer loss injection. Across the configured conditions, mean critical-event delivery was 0.968 for EventGuard, 0.927 for fixed redundancy, and 0.800 for no protection. EventGuard used 1578.6 bytes per run on average versus 1339.7 for fixed redundancy. It improved critical delivery over fixed redundancy in some random-loss conditions, while gains were smaller or absent in burst-loss conditions. These findings are preliminary and do not demonstrate an equal-cost advantage or robustness to RF fading.

## 1. Research question and scope
- Study how an event-aware, bounded copy policy affects critical-event delivery and communication cost on a constrained Sub-GHz link.
- Separate actual E220 transmission from deterministic application-layer erasures; do not label configured injected loss as measured RF loss.
- Treat this as an engineering evaluation, not a novelty claim.

## 2. System and method
- Sensor and gateway use ESP32-S3 plus E220-400T22D over UART; compact CRC-protected DATA and ACK frames.
- Event classes are NORMAL, IMPORTANT, and CRITICAL; fixed redundancy sends two copies and EventGuard adapts one to three copies using event importance and an ACK-based link estimate.
- Include the score, thresholds, copy-selection logic, deduplication, ACK behavior, and failure cases from the source.

## 3. Experimental setup
- Matrix: 300 runs total; 100 per strategy; 10 seeds (11, 23, 37, 41, 53, 67, 71, 83, 97, 101); configured loss rates 0%, 5%, 10%, 20%, and 30%; RANDOM and BURST models (burst length 3).
- Each run uses 18 synthetic sensor samples (the current trace contains 8 NORMAL, 6 IMPORTANT, and 4 CRITICAL samples).
- E220 settings were read and checked by firmware at boot; profile: UART1 at 9600 baud; TX/RX/AUX=17/16/15; M0/M1=13/14; address=0x0000; REG0=0x62; channel register=0x17.
- Hardware identity: sensor 80:65:99:a7:a8:a4; gateway c0:4e:30:31:42:9c.
- Explain how identical trace content and matched seeds construct each strategy comparison; cite manifests and firmware image hashes.

## 4. Results to report
- Overall mean critical-event delivery: EventGuard 0.968, fixed redundancy 0.927, no protection 0.800.
- Mean bytes per run: EventGuard 1578.6, fixed redundancy 1339.7, no protection 663.9.
- RANDOM loss, EventGuard vs fixed critical delivery: 20%: 0.975 vs 0.875 (+0.100); 30%: 0.950 vs 0.750 (+0.200).
- BURST loss, EventGuard vs fixed critical delivery: 20%: 0.900 vs 0.900 (+0.000); 30%: 0.900 vs 0.850 (+0.050).
- Include all conditions, confidence intervals, latencies, transmitted bytes, and plots from `results/report.md` and `results/summary.csv`; retain every run.

## 5. Statistical analysis
- Paired two-sided t-tests use ten matched seeds per configured condition. The current report runs twenty critical-delivery comparisons without multiplicity correction; p-values are exploratory, not confirmatory.
- Report confidence intervals and per-seed values; avoid interpreting small p-values without a predeclared primary comparison and correction.

## 6. Limitations and next experiments
- Application-layer drops are not RF fading, interference, or E220 retry behavior; actual channel errors are not controlled or characterized.
- One sensor/gateway pair, a short deterministic synthetic trace, and ten seeds per condition limit generalization. No energy, current, range, or regulatory airtime measurement was collected.
- EventGuard transmits more bytes on average than fixed redundancy. The automatic similar-cost table compares points that can use different configured loss rates; it cannot establish superiority at equal channel quality.
- Next: compare policies under fixed byte/airtime budgets (including fixed one-, two-, and three-copy baselines), lengthen traces and seed sets, characterize real RF conditions with distance/interference/attenuation measurements, and measure energy and airtime.

## 7. Conclusion
State only the measured tradeoff: EventGuard raises mean critical delivery in this dataset at higher byte cost, with stronger paired differences under random 20–30% application-layer loss and no consistent win under burst loss. Do not claim a general LoRa reliability improvement until controlled RF experiments are complete.
