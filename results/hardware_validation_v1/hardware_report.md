# Frozen-v1 hardware validation

Stage: smoke. Completed 3/12 planned runs; 1 failed after completion of radio transmission; 8 not attempted.

**Evidence:** real ESP32-S3 + E220 DATA/ACK frames with deterministic application-layer injection.
The previous host and pilot datasets are kept separate.

## Hardware and E220

See `hardware_manifest.json` for USB ports, chip MACs, radio profile, firmware hashes, and execution order.
Every run has raw serial logs and a run manifest. Incomplete or failed runs are listed in `summary.json`.

## Reproducibility and simulation agreement

Run-level metric disagreements: 1/4 attempted runs. Per-sample divergence is preserved in each run manifest. See `simulation_hardware_diff.csv`.

## Paired research comparisons

The statistical unit is one paired seed/run, never an individual packet. `metrics/paired_tests.csv` gives mean/median/sample SD/95% mean CI, exact signed-rank p-values, rank-biserial effect sizes, and Holm-adjusted p-values. Formal tests are emitted only for complete ten-seed conditions in the main stage, never from smoke or a selected subset.

No formal paired test is available at this stage.

## Cost and Pareto

UART communication time is an estimate: (DATA bytes + ACK bytes) × 10 bits/byte ÷ 9600 bit/s. It is **not measured RF PHY airtime**. No Joule estimate is made without current sensing.
The plots show only strategies actually completed under each matched condition.

| Condition | EventGuard on byte-cost frontier | Importance Only on byte-cost frontier |
|---|---|---|
| RANDOM_COPY 0% | NO | NO |

## Hardware, E220, importance, and burst interpretation

Both boards completed 3 passing logged runs with verified firmware hashes, serial roles, and E220_READY preflight checks. Every completed run has 54 SAMPLE and EVT records. The runner rejects CRC, UART, sequence, budget, calendar, and Python/C sample differences. Injected DATA/ACK loss is separated from unexpected physical-link anomalies.
The recorded host metric disagreement count is 1; the row-level details and earliest divergent sample are in `simulation_hardware_diff.csv`.

## Paper impact and limitations

This stage is incomplete. No final hardware claim or paper readiness judgment is made. The 160-run main stage is gated until every smoke run passes. Preserve the failed condition and investigate the uncontrolled physical-link anomalies without adjusting seeds or thresholds.
- Failed eventguard_random_copy_20_seed31: sample 6 copy 0: uncontrolled physical DATA anomaly; sample 7 copy 2: uncontrolled physical DATA missing before planned injection; sample 9 copy 0: uncontrolled physical DATA missing before planned injection; sample 10 copy 2: uncontrolled physical DATA anomaly; sample 22 copy 2: uncontrolled physical DATA anomaly; sample 23 copy 2: uncontrolled physical DATA anomaly; sample 28 copy 1: uncontrolled physical DATA missing before planned injection; sample 35 copy 1: uncontrolled physical DATA missing before planned injection; sample 36 copy 2: uncontrolled physical DATA anomaly; sample 44 copy 0: uncontrolled physical DATA anomaly; sample 45 copy 2: uncontrolled physical DATA missing before planned injection; sample 47 copy 2: uncontrolled physical DATA anomaly; sample 50 copy 1: uncontrolled physical DATA anomaly; simulation/firmware divergence at 13 sample(s).

## Required research judgments

- Hardware runs completed: 3 passing of 12 planned; 1 completed with failed validation.
- Failed runs: 1. All failed raw logs and manifests are retained.
- Simulation/hardware agreement: 3 passing runs agree; 1 attempted run disagrees. The failed run is not silently discarded.
- EventGuard vs Importance Only: one 0% paired seed ties in delivery and DATA copies; higher-loss paired hardware evidence is unavailable.
- EventGuard vs Uniform Budget: not evaluated; equal-budget hardware conclusion unavailable.
- EventGuard vs Random Budget: not evaluated; equal-budget hardware conclusion unavailable.
- EventGuard Pareto status: incomplete smoke-only comparison; no main-matrix frontier claim.
- Link adaptation useful: **UNKNOWN**. The failed 20% run shows extra link-state changes caused by uncontrolled receive-path losses, but no matched ablation.
- Main contribution after hardware validation: unconfirmed; smoke establishes baseline 0% Python/C parity and reveals a physical receive-path discrepancy under injected loss.
- Largest remaining limitation: source of the uncontrolled DATA disappearance is not localized to RF, E220 buffering, or gateway UART reception.
- Is the project ready for paper writing: **NO** as a confirmatory hardware paper; the negative smoke result is reportable as a feasibility finding.
- Recommended next action: instrument the receive path and E220 AUX/UART timing under a separately labeled engineering diagnostic, then repeat the fixed smoke plan from the start with recorded firmware hashes. Do not start the main matrix before it passes.

## Failed-run observation before application injection

eventguard_random_copy_20_seed31: 141 DATA frames were logged as sent; the gateway logged 128 valid frames before injection (100 accepted RX plus 28 planned drops), leaving **13 sent copies without a gateway log**. No CRC or UART error line was observed. This identifies the receiver path but does not prove an RF-air cause.
The run had 100 ACK transmissions, 100 physically received ACKs, 72 accepted ACKs, 28 injected ACK drops, and 41 ACK timeouts. Overall delivery was 0.9074 versus host 0.9444; critical delivery happened to be 1.0000 in both. The failed run used 141 DATA copies, 4966 total bytes, and a 5172.9 ms UART-time proxy. These are descriptive failed-run values, not a paired treatment estimate.

## Hardware-report checklist

- **Hardware:** two ESP32-S3 devices remained online for four attempted runs; the remaining eight smoke runs were not attempted.
- **E220:** bidirectional DATA/ACK worked in three 0% runs. The 20% run exposed 13 uncontrolled DATA disappearances before application injection; root cause unknown.
- **Reproducibility:** all three passing runs matched host metrics and per-sample behavior. The failed run diverged beginning at sample 6, copy 0.
- **Event Importance:** firmware importance labels matched Python for all 54 samples in each attempted run, including the failed run.
- **Equal Budget:** neither blind budget strategy reached its smoke or main comparison; no conclusion.
- **Ablation / Link Adaptation:** the sole 0% EventGuard–Importance Only pair tied. High-loss treatment effect and cost increment remain unknown.
- **Burst Loss:** BURST_SAMPLE hardware runs were not attempted; the host finding is unvalidated.
- **Cost:** DATA copies, ACK frames, bytes, and a clearly labeled UART-time proxy are recorded per run; no measured RF airtime or energy is reported.
- **Paper Impact:** the hardware data support only 0% parity and identify an unmodeled receive-path anomaly. They neither confirm nor overturn the treatment comparison.
