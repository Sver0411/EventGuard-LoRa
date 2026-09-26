# Offline audit: FINAL_BALANCED_HARDWARE_SET_V1

Generated at 2026-09-26T14:48:15.923752+00:00. No hardware was contacted and no run was repeated.

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

Seeds 31–36 were selected as the earliest six contiguous evaluation seeds in the preregistered interleaved Stage 1 order. Selection did not inspect comparative outcomes. Every selected seed contains all four strategies in all four model/rate conditions.

Seed 37 is not part of the paired primary set. Its partial extension records are retained in the original study directory. Run 103, `UNIFORM_BUDGET / RANDOM_COPY / 30% / seed37`, is retained as an incomplete engineering anomaly after a low-frequency ACK receive-path stall; this report does not claim that stall was resolved.
