# Stage 1 Confirmatory Protocol

This 160-run confirmatory experiment uses the device-recovered, smoke-validated firmware pair identified in `stage1_manifest.json`. The runner does not build or flash firmware. The frozen EventGuard-v1 algorithm, default config, trace generator, loss-calendar implementation, and seeds are hash-gated before execution.

Strategies are shuffled within each model/rate/seed condition with execution-order seed `4170411`. The EventGuard host-reference DATA-copy budget for all 40 conditions is saved in `budget_table.json` before any run starts; both blind budget baselines receive exactly that count and may use only sample index, fixed total budget, deterministic allocation, and seed.

Every run resets both boards and verifies READY/ARMED/TRACE_READY before START. Isolated, uncontrolled DATA/ACK losses are retained as physical-link noise and are not reclassified as application-layer injection; the versioned physical-noise gate allows at most one per type per run, and stops on bursts, repeats, or cumulative rate above 1%. END timeout, firmware reset/hang, UART/parser failure, CRC storm, policy divergence, loss-calendar mismatch, or budget mismatch stops the matrix. A run is never automatically retried after START is attempted. Results are host simulation references plus physical ESP32-S3/E220 application-layer fault-injection observations plus separately counted physical anomalies; UART-time is only a proxy, not measured RF airtime.

## Console log capture integrity

Host serial capture uses `chunked-read-4096-incremental-line-framing-v1`. Incoming serial bytes are read in chunks and incrementally framed on newline boundaries. After every run, Sensor TX/ACK/SAMPLE lines, Gateway RX/DROP/ACK lines, and per-frame console diagnostics are cross-checked against the firmware END counters and E220 UART_DIAG frame counters. Any disagreement fails the run and stops the matrix.

An explicitly user-authorized retry of a failed run is archived under `stage1/retry_history/`; the original attempt remains immutable and the retry retains the same condition and firmware.
