# Offline audit: FINAL_BALANCED_HARDWARE_SET_V1

Generated at 2026-09-26T15:19:22.324636+00:00. No hardware was contacted and no run was repeated.

- Analysis Git commit: `80d3d1f26739e7d85a7e1c1f1399f1092c4a6e10`
- Finalizer script SHA256: `703696c6e4ea58c7f58de1a177cc7620c7d527a1559b9ddedbad8ca6ee0a43ca`
- Expected records: 96
- Raw logs and manifests available: 96
- Passed current offline checker: 96 / 96
- Usable paired seeds: 31, 32, 33, 34, 35, 36
- Usable balanced runs: 96 / 96
- Physical DATA missing in selected logs: 0
- Physical ACK missing in selected logs: 0
- Saved importance/copy/link policy divergences: 0
- Sample delivery outcomes different from host reference: 0

The checker revalidated manifest/raw SHA256, run identity and execution order, frozen firmware hashes, trace and loss-calendar hashes, reset/configuration/trace-ready acknowledgements, 54 EVT/SAMPLE records, per-copy DATA/ACK fault-calendar outcomes, importance/copy/link replay, Sensor and Gateway END counters, UART_DIAG frame/byte/error counters, exact budget totals, and stored metrics against a fresh parse of each raw log.

The Gateway END receive counter is compared with post-injection `physical_data_received`. Pre-injection physical frame count is separately checked as post-injection RX plus planned DATA drops.

No independent USB serial reader byte-count capture was persisted in the legacy raw JSON schema; therefore its historical byte-read telemetry cannot be reconstructed. Firmware-owned UART_DIAG byte/frame counters and serial event lines were cross-checked instead.

## Selection and exclusions

This is a post-hoc balanced analysis of the interrupted 160-run Stage 1 experiment; n=6 was not a preregistered sample size. Seeds 31–36 are the earliest six contiguous seeds with complete coverage in execution order. Selection used completion and consistency, not comparative outcomes. Each seed has its own deterministic 54-sample trace realization, shared by all strategies within that seed.

Seed 37 is not part of the paired primary set. Its partial extension records are retained in the original study directory. Run 103, `UNIFORM_BUDGET / RANDOM_COPY / 30% / seed37`, is retained as an incomplete engineering anomaly after a low-frequency ACK receive-path stall; this report does not claim that stall was resolved.
