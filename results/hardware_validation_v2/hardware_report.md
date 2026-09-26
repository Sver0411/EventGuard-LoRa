# Frozen-v1 hardware validation

Stage: smoke. Completed 12/12 planned runs; 0 failed after completion of radio transmission; 0 not attempted.

**Evidence:** real ESP32-S3 + E220 DATA/ACK frames with deterministic application-layer injection.
The previous host and pilot datasets are kept separate.

## Hardware and E220

See `hardware_manifest.json` for USB ports, chip MACs, radio profile, firmware hashes, and execution order.
Every run has raw serial logs and a run manifest. Incomplete or failed runs are listed in `summary.json`.

## Reproducibility and simulation agreement

Run-level metric disagreements: 0/12 attempted runs. Per-sample divergence is preserved in each run manifest. See `simulation_hardware_diff.csv`.

## Paired research comparisons

The statistical unit is one paired seed/run, never an individual packet. `metrics/paired_tests.csv` gives mean/median/sample SD/95% mean CI, exact signed-rank p-values, rank-biserial effect sizes, and Holm-adjusted p-values. Formal tests are emitted only for complete ten-seed conditions in the main stage, never from smoke or a selected subset.

No formal paired test is available at this stage.

## Cost and Pareto

UART communication time is an estimate: (DATA bytes + ACK bytes) × 10 bits/byte ÷ 9600 bit/s. It is **not measured RF PHY airtime**. No Joule estimate is made without current sensing.
The plots show only strategies actually completed under each matched condition.

Smoke-only descriptive frontier: two seeds and RANDOM_COPY at 0%/20%; this is not a Pareto or treatment-effect conclusion.

| Condition | EventGuard on byte-cost frontier | Importance Only on byte-cost frontier |
|---|---|---|
| RANDOM_COPY 0% | NO | NO |
| RANDOM_COPY 20% | NO | YES |

## Hardware, E220, importance, and burst interpretation

Both boards completed 12 passing logged runs with verified firmware hashes, serial roles, and E220_READY preflight checks. Every completed run has 54 SAMPLE and EVT records. The runner rejects CRC, UART, sequence, budget, calendar, and Python/C sample differences. Injected DATA/ACK loss is separated from unexpected physical-link anomalies.
The recorded host metric disagreement count is 0; the row-level details and earliest divergent sample are in `simulation_hardware_diff.csv`.

## Paper impact and limitations

This v2 stage is a smoke gate, not a confirmatory treatment study. It covers two seeds (31 and 32), RANDOM_COPY at 0% and 20%, and FIXED_2 / IMPORTANCE_ONLY / EVENTGUARD only. Passing establishes the receive, logging, reset-isolation, and host/firmware parity path for these conditions; it does not establish a general reliability/cost advantage.

## Required research judgments

- **Hardware:** both ESP32-S3 boards completed all 12 smoke runs; every run has a complete raw log and END counters.
- **E220:** bidirectional DATA/ACK completed. Across the 12 runs, uncontrolled DATA missing, uncontrolled ACK missing, and CRC errors were all zero.
- **Reproducibility:** 12/12 run metrics match the frozen host reference; per-sample differences are zero.
- **Event Importance:** firmware/Python sample behavior matched in these smoke conditions; this is not a new classifier-performance claim.
- **EventGuard vs Importance Only:** descriptive smoke comparison only (two paired seeds per condition); no formal inference.
- **Equal Budget:** UNIFORM_BUDGET and RANDOM_BUDGET were not part of this smoke; no equal-budget conclusion.
- **Burst Loss:** BURST_SAMPLE was not part of this smoke; no burst-loss conclusion.
- **Link Adaptation:** remains undetermined by this gate. The smoke does not justify a general link-adaptation benefit claim.
- **Cost:** DATA copies, bytes, and UART-time proxy are recorded; RF PHY airtime and energy were not directly measured.
- **Paper impact:** confirms the engineering path and host parity for the tested smoke conditions only; it neither confirms nor refutes the treatment conclusions.
- **Main-stage readiness:** smoke gate PASS. Stage 1 has not started and remains pending explicit user confirmation.

## E220 Receive-Path Fix

Diagnostic gate: PASS (12/12 smoke runs). The legacy parser had a confirmed partial-frame loss defect: parser state was local to one receive call and a short body read discarded accumulated bytes. The historical 13 missing frames all followed a no-ACK/DATA-drop path, consistent with that failure mode; original per-byte/AUX logs were not captured, so attribution of each historical frame is not conclusive.
