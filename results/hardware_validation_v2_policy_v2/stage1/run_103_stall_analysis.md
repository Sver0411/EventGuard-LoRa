# Stage 1 Run 103 stall analysis

## Disposition

Stage 1 is **stopped** at execution order 103. The first 102 runs are complete and retained; run 103 is a failed, incomplete attempt whose raw log and manifest remain at the canonical `raw/stage1/` and `runs/stage1/` paths. It was not automatically retried. No 400-run matrix was started.

- Run: `uniform_budget_random_copy_30_seed37`
- Strategy/model/rate/seed: `UNIFORM_BUDGET / RANDOM_COPY / 30% / 37`
- Frozen EventGuard reference DATA-copy budget: 140
- Run start: `2026-09-26T12:41:03.153642+00:00`
- Runner failure: no ACK/TIMEOUT progress after sample 35, copy 1 had waited 18 seconds, despite a configured 1000 ms ACK timeout
- Sensor and Gateway images: `b3cecd078351cc6fe659b0087db05a67a4ca35ab610125a0f0381887281221b4` and `6f69dffed65e2636b371401db45a483f951189e029b538c2f587d331428936ac`
- Raw SHA256: `964da6276f845721df7daff9d8863cb9fe7faf3c9114c5bed8f77ebd3bac05b9`
- Run manifest SHA256: `432b339088e7d0906add9974afdf5c764bbf070d5f0c1d7cf3d73d8ba703c084`

## Evidence

The Sensor console capture is internally complete through the stall: 104 DATA `TX` lines, 36 `EVT` records, 35 completed `SAMPLE` records, 104 `D_ACK_WAIT_BEGIN` records, and 103 ACK outcomes (76 ACKs plus 27 timeouts). There were zero host serial-reader errors and zero pending bytes. The Gateway logged 103 CRC-valid complete DATA frames: 27 planned `DROP,DATA` events and 76 post-injection `RX` events.

For sample 35, copy 0 followed the planned DATA-drop path and correctly produced `D_ACK_TIMEOUT`/`TIMEOUT` after approximately 999 ms. Copy 1 then reached `D_TX_UART_DONE`, `D_TX_AUX_READY`, `TX`, and `D_ACK_WAIT_BEGIN`. The Gateway has no corresponding first-byte/frame event for sample 35, copy 1, so one DATA frame is absent at the Gateway before application-layer injection. It is separate from configured loss. These logs cannot distinguish RF/E220 loss from a Gateway-side receive-path loss, so the physical mechanism is unconfirmed.

The Sensor emitted neither `D_ACK_TIMEOUT` nor `TIMEOUT` for copy 1 during the next 18 seconds, and it did not emit `SAMPLE,35` or `END`. A host `STATUS` probe after about 6 seconds received `ROLE,SENSOR` and `E220_READY`, showing that the console/control path remained responsive. The run task was stalled or otherwise ceased making progress while in the ACK receive phase.

## Root-cause assessment

The failure is localized to the Sensor firmware execution path waiting for the ACK after sample 35, copy 1: `sensor_run_task()` → `eg_e220_receive()` → the E220 UART receive path. It is **not** an END watchdog that was too short: the run stopped at 97.45 seconds, far below its calculated 617-second whole-run watchdog. It is also **not** explained by host log truncation: both USB captures have no reader errors or pending bytes, and the host observed the Sensor STATUS response.

The raw log cannot distinguish a blocked `uart_read_bytes()`/receive mutex from another stall inside the receive call. The run has no END or `UART_DIAG` record, so parser counters and internal receive state for this attempt are unavailable. Therefore the precise low-level root cause remains **unconfirmed**; it would be inaccurate to claim that the E220 RF miss itself caused the firmware hang.

## Policy and next gate

The amended Stage 1 policy does not invalidate a normally completed run solely for one isolated uncontrolled DATA/ACK absence. It records that absence separately from injected loss. This run nevertheless fails independently because the Sensor firmware did not produce the configured ACK timeout outcome or continue the trace. The Stage 1 stop gate therefore remains active.

No firmware source or binary was changed in this run. The smoke-validated hashes remain unchanged, but the newly observed receive-path stall means formal Stage 1 must not resume with this pair until the receive stall is diagnosed and resolved. Any firmware fix requires a new firmware pair and a fresh 12/12 smoke validation before Stage 1 can restart.

## Relevant files

- Raw log: `raw/stage1/uniform_budget_random_copy_30_seed37.json`
- Run manifest: `runs/stage1/uniform_budget_random_copy_30_seed37.json`
- Engineering log: `engineering_bug_log.md`
- Runner failure: Sensor ACK outcome stalled after copy 1; no retry performed
