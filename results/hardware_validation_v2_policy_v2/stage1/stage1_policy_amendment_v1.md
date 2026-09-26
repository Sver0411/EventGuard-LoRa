# Stage 1 physical-noise policy amendment v1

**Effective before any Stage 1 restart.** The initial Stage 1 attempt remains preserved as a failed attempt and is not retroactively reclassified.

## Physical DATA and ACK loss

An isolated uncontrolled physical loss is an observed link outcome, not application-layer injected loss. Record it in `physical_anomalies` and the per-run metrics; do not count it as a planned DATA/ACK injection and do not fail the run solely for one such observation.

Definitions:

- `uncontrolled_physical_data_missing`: a Sensor `TX` copy has neither a Gateway post-injection `RX` record nor a planned `DROP,DATA` record.
- `uncontrolled_physical_ack_missing`: the Gateway logged `ACK_TX` but the Sensor logged neither a physical ACK nor its corresponding ACK outcome; a completed run must contain the Sensor timeout record.

Continue after a completed run only when all are true:

- At most one uncontrolled DATA loss and at most one uncontrolled ACK loss occurred in that run.
- No two physical-loss observations occurred within the same logical sample or on adjacent copy opportunities.
- The same `(loss model, rate, seed, sample, copy, kind)` anomaly has not repeated in another strategy run.
- Cumulative uncontrolled loss remains at or below 1% of observed DATA transmissions and, separately, 1% of physical ACK transmissions.

Otherwise preserve the attempt and stop for diagnosis. Physical anomalies remain separate from injected loss calendars and are included in the final counts and rates.

## Immediate stop conditions

- Firmware `END` timeout, missing/duplicate END, unexpected reset, or a stalled ACK wait that exceeds the configured ACK timeout plus the runner's diagnostic grace period.
- UART/serial or parser error, sequence corruption, or a CRC storm (at least 3 CRC failures in one run or more than 1% of observed frame opportunities).
- Importance-label mismatch, copy-selection mismatch when replayed using the observed link outcomes, unexplained link-state transition mismatch, loss-calendar mismatch on a frame observed before injection, or exact DATA-copy budget mismatch.
- Physical-loss burst, repeated same-opportunity anomaly, or cumulative physical-loss rate above the thresholds above.

## Watchdog

The runner deadline is computed from the frozen firmware call structure: each selected DATA copy can spend up to three 1,000 ms E220 send waits (AUX before TX, UART drain, AUX after TX) plus the configured ACK wait, with 0.5 seconds per sample and a 30-second fixed margin. For the diagnostic copy budget of 144 and 54 samples, the resulting deadline is 633 seconds. An ACK wait with no ACK/TIMEOUT result after `ack_timeout_ms + 12 seconds` is independently classified as a stalled firmware receive path; the runner sends one read-only `STATUS` probe and preserves the evidence.

The prior Stage 1 protocol, failed attempt, smoke data, and firmware binaries remain unchanged. This amendment governs only a new Stage 1 restart dataset.
