# Stage 1 Confirmatory Protocol

This 160-run confirmatory experiment uses the device-recovered, smoke-validated firmware pair identified in `stage1_manifest.json`. The runner does not build or flash firmware. The frozen EventGuard-v1 algorithm, default config, trace generator, loss-calendar implementation, and seeds are hash-gated before execution.

Strategies are shuffled within each model/rate/seed condition with execution-order seed `4170411`. The EventGuard host-reference DATA-copy budget for all 40 conditions is saved in `budget_table.json` before any run starts; both blind budget baselines receive exactly that count and may use only sample index, fixed total budget, deterministic allocation, and seed.

Every run resets both boards and verifies READY/ARMED/TRACE_READY before START. Any uncontrolled physical DATA/ACK anomaly, CRC/parser/reset error, sample divergence, calendar mismatch, or budget mismatch is retained as a failed attempt and stops the matrix. A run is never automatically retried after START is attempted. Results are host simulation references plus physical ESP32-S3/E220 application-layer fault-injection observations; UART-time is only a proxy, not measured RF airtime.
