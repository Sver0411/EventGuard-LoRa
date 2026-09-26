# Stage 1 engineering bug log

## Run 93: incomplete USB console capture

- **Run:** `random_budget_burst_sample_30_seed36` (`RANDOM_BUDGET`, `BURST_SAMPLE`, 30%, seed 36).
- **Observed:** the firmware completed all 54 samples. Sensor END reported 145 DATA transmissions and 79 accepted ACKs. Gateway E220 diagnostics reported 145 completed DATA frames; Gateway END accounted for 103 post-injection receives plus 42 planned DATA drops and 103 ACK transmissions. Sensor E220 diagnostics reported 103 completed ACK frames. Both diagnostics reported zero CRC/parser errors.
- **Capture defect:** the raw USB console stream contained only 115 Sensor `TX` lines, 43 each of `EVT`/`SAMPLE`, and 115 Gateway frame-complete lines. These are inconsistent with the firmware-owned END and E220 counters, so the run is invalid for per-sample analysis. It is not classified as physical packet loss.
- **Fix:** replaced pyserial `readline()` with 4096-byte reads and a persistent newline framer. Added per-run byte/line capture counters and post-run checks against Sensor END, Gateway END, and E220 UART_DIAG counters. Added a pseudo-terminal stress check: 86,000 bytes / 2,000 lines were captured exactly in 21 reads with no reader errors or pending bytes.
- **Retry:** user explicitly directed that run 93 be rerun and Stage 1 continue. The failed raw and manifest are archived in `stage1/retry_history/`; the retry uses the same trace, seed, loss calendar, strategy, reference budget, and frozen firmware pair.
- **Firmware impact:** none. No firmware source, image, algorithm, threshold, trace, seed, or fault calendar changed; smoke remains valid for the exact same firmware hashes.

## Run 93 retry attempt 02: ACK counter semantics false positive

- **Run:** `random_budget_burst_sample_30_seed36`, same frozen condition and firmware as attempt 01.
- **Observed:** the chunked serial reader captured the complete run: 145 Sensor `TX` lines, 54 `EVT` and 54 `SAMPLE` lines, 145 Gateway frame-complete lines, and all 103 received ACK frames. Firmware END reported 79 accepted ACKs; the other 24 ACKs were intentionally marked `DROP` by the frozen ACK fault calendar. UART diagnostics reported 103 Sensor ACK frames and 145 Gateway DATA frames, with zero CRC/parser errors.
- **Host checker defect:** a duplicate, later definition of `_validate_console_capture()` shadowed the corrected checker. It compared all 103 `ACK,...` physical receptions with the END counter for 79 accepted ACKs. Thus a complete and internally consistent console capture was incorrectly failed.
- **Fix:** removed the shadowing duplicate. The remaining checker compares only `ACK,...,OK` lines with accepted ACKs, while retaining separate received/drop/timeout accounting. The regression fixture includes both accepted and injected-drop ACKs.
- **Firmware impact:** none. The fixed smoke-validated firmware pair remains unchanged; no new smoke run is required.
- **Disposition:** attempt 02 is retained in `stage1/retry_history/` with raw log and manifests, then the exact run 93 condition is retried under explicit user authorization.

## Stage 1 order 103: Sensor ACK receive path stalled after a physical DATA miss

- **Run:** `uniform_budget_random_copy_30_seed37` (`UNIFORM_BUDGET`, `RANDOM_COPY`, 30%, seed 37).
- **Observed:** 104 Sensor DATA transmissions were captured. Gateway completed 103 CRC-valid frames (27 planned DATA drops and 76 post-injection receives); sample 35/copy 1 is absent from Gateway pre-injection logs. The available evidence cannot distinguish RF/E220 loss from a Gateway-side UART/parser loss. Sample 35/copy 0 had a planned drop and correctly timed out after about 999 ms. For copy 1, Sensor logged UART TX completion, AUX ready, DATA TX, and ACK-wait start, but no Gateway RX and no Sensor ACK/TIMEOUT outcome followed for 18 seconds. Sensor still answered the host STATUS probe. The run emitted 35 completed SAMPLE lines and no END.
- **Classification:** firmware run-task stall in the ACK receive phase, not the Gateway pre-injection frame absence alone and not the whole-run watchdog. The failure is localized to `sensor_run_task()` calling `eg_e220_receive()`, but the raw log has no END/UART_DIAG, so the specific blocked driver/parser state is unconfirmed.
- **Capture integrity:** Sensor and Gateway USB readers reported zero reader errors and zero pending bytes. The raw log remains canonical and was not retried.
- **Disposition:** Stage 1 stopped at order 103 with 102 completed runs retained. The new physical-noise allowance does not permit continuing through a firmware stall. No 400-run matrix was started.
- **Firmware impact:** none in this attempt. Images remain Sensor `b3cecd078351cc6fe659b0087db05a67a4ca35ab610125a0f0381887281221b4`, Gateway `6f69dffed65e2636b371401db45a483f951189e029b538c2f587d331428936ac`. A future firmware fix requires a new 12/12 smoke gate before another Stage 1 start.
