# E220 Receive-Path Diagnostic Report

> 这些是 E220/UART/parser 工程诊断数据，不是 EventGuard treatment 或论文结果。

## Provenance

- Git commit: `b9992de1108208393b48b32314568cc97b8688e2` (`codex/e220-receive-diagnostic-v1`)
- Firmware image manifest commit: `b7ff9f374dd76b1be39bb04a84e1ad24ff0ea213`; hash source: `previous diagnostic manifest; no flash operation since that attestation`.
- Sensor image SHA256: `b3cecd078351cc6fe659b0087db05a67a4ca35ab610125a0f0381887281221b4`
- Gateway image SHA256: `6f69dffed65e2636b371401db45a483f951189e029b538c2f587d331428936ac`
- Algorithm spec SHA256: `ab07fd9cac09acb53a6335e398c2e9f9d32f916bf5da34696ca69d349ce0d522`
- Serial ports: Sensor `/dev/cu.usbmodem101`, Gateway `/dev/cu.usbmodem1101`
- E220 config read/verification: `{'uart_num': 1, 'tx_gpio': 17, 'rx_gpio': 16, 'aux_gpio': 15, 'm0_gpio': 13, 'm1_gpio': 14, 'baud': 9600, 'address': 0, 'reg0': 98, 'channel': 23}`; both boards reported E220_READY.
- Pre-fix commit: `13860fb6ef83de552a5c4a2f946a80b15c698770`; Sensor/Gateway image SHA256: `{'sensor': 'ed005d22ed2c5f44c33d9fa52fc5077dd2b68ce0f8030f991540e4ba51f6115d', 'gateway': '83492c7972d8f8fb2d5b2dff20cdfc3872db919dfb433031c23f0805be4f25dd'}`.

## Historical 13-Frame Pattern

PR #3 的 141 个 Sensor DATA TX 中，Gateway 在注入前记录 128 帧（100 个 RX、28 个计划 DROP）；另有 13 帧无 RX/DROP 记录。历史日志中 13/13 都紧随前一帧的 `DROP,DATA`，Sensor 未收到 ACK。旧日志没有 AUX edge、UART buffer 或每字节 parser 时间戳，因此它本身不能证明这些帧是 RF 丢失。

## Diagnostic Runner Incidents

A/B/C 诊断开始前遇到的 Gateway RESET 控制路径竞争、100 Hz FreeRTOS 下 1 ms delay 被量化为 0 tick，以及 smoke runner 把 boot `E220_READY` 当作 STATUS 响应，均已修复；这些尝试未产生 A/B/C 无线数据。另有一次 v2 smoke 已启动 DATA run，但 END 同批次中的 `UART_DIAG` 被读入内存后，host runner 又等待第二条 `UART_DIAG` 并超时；该次没有保存 raw/run manifest，按未验证 runner 中断处理并重跑。上述均不按 missing frame 计数，见 `metrics/runner_incidents.json`。

## Test A/B/C

| Test | Frames | Physical DATA missing | DATA drops | ACK timeouts | Parser errors | Status |
|---|---:|---:|---:|---:|---:|---|
| A | 500 | 0 | 0 | 0 | 0 | pass |
| B | 500 | 0 | 0 | 100 | 0 | pass |
| C | 500 | 0 | 97 | 97 | 0 | pass |

## Conditional Missing Probabilities

### Test A

- P(missing | previous ACK success): 0/499 = 0.0
- P(missing | previous ACK suppressed): 0/0 = None
- P(missing | previous DATA drop): 0/0 = None
- No uncontrolled missing DATA frame was observed.
- Gateway AUX edges/low intervals: `{"edge_count": 2000, "low_edges": 1000, "high_edges": 1000, "completed_low_intervals": 1000, "aux_low_duration_us_mean": 22610.858, "aux_low_duration_us_median": 23889.0, "aux_low_duration_us_max": 30012}`
- Sensor AUX low interval summary: `{"edge_count": 2000, "low_edges": 1000, "high_edges": 1000, "completed_low_intervals": 1000, "aux_low_duration_us_mean": 22621.041, "aux_low_duration_us_median": 22885.5, "aux_low_duration_us_max": 32373}`
- Sensor UART-TX-done to AUX-high readiness (ms): mean=3.152876, median=3.147, max=6.259

### Test B

- P(missing | previous ACK success): 0/399 = 0.0
- P(missing | previous ACK suppressed): 0/100 = 0.0
- P(missing | previous DATA drop): 0/0 = None
- No uncontrolled missing DATA frame was observed.
- Gateway AUX edges/low intervals: `{"edge_count": 1800, "low_edges": 900, "high_edges": 900, "completed_low_intervals": 900, "aux_low_duration_us_mean": 23341.41888888889, "aux_low_duration_us_median": 29068.5, "aux_low_duration_us_max": 30022}`
- Sensor AUX low interval summary: `{"edge_count": 1800, "low_edges": 900, "high_edges": 900, "completed_low_intervals": 900, "aux_low_duration_us_mean": 23355.074444444443, "aux_low_duration_us_median": 29223.5, "aux_low_duration_us_max": 29238}`
- Sensor UART-TX-done to AUX-high readiness (ms): mean=3.14657, median=3.147, max=3.162

### Test C

- P(missing | previous ACK success): 0/403 = 0.0
- P(missing | previous ACK suppressed): 0/0 = None
- P(missing | previous DATA drop): 0/96 = 0.0
- No uncontrolled missing DATA frame was observed.
- Gateway AUX edges/low intervals: `{"edge_count": 1806, "low_edges": 903, "high_edges": 903, "completed_low_intervals": 903, "aux_low_duration_us_mean": 23316.334440753046, "aux_low_duration_us_median": 29103, "aux_low_duration_us_max": 29995}`
- Sensor AUX low interval summary: `{"edge_count": 1806, "low_edges": 903, "high_edges": 903, "completed_low_intervals": 903, "aux_low_duration_us_mean": 23322.76079734219, "aux_low_duration_us_median": 29223, "aux_low_duration_us_max": 29240}`
- Sensor UART-TX-done to AUX-high readiness (ms): mean=3.146406, median=3.147, max=3.159

## Timing Sweep

未运行：Test B/C 未出现需要复现的 uncontrolled missing DATA。

## Hardware Validation v2 Smoke Gate

PASS：12/12 组 smoke run 已完成，且没有失败 run。Stage 1 未自动启动，需等待用户确认。

## Root-Cause Assessment

修复后的持续流 parser 在 A/B/C 未观察到 uncontrolled missing DATA。原 13 个历史缺失全部跟随 no-ACK/DROP；这一模式与旧 parser 的跨调用 partial-header/body 状态丢失风险一致，但修复后的复测不能单独证明每个历史缺失帧的唯一根因。
本轮没有出现 partial-frame timeout；UART buffer、AUX edge 和帧时序记录保存在 raw 日志，可用于排除当前链路中的同类复现。
因此当前结论为：parser 的确定性工程缺陷已修复；历史 13 帧的唯一根因仍需由原固件复现或本轮具体 partial-frame 恢复证据确认。

## Files

- Raw UART/AUX logs: `results/e220_receive_diagnostic/raw/`
- Machine metrics: `results/e220_receive_diagnostic/metrics/`
- Timing matrix: `results/e220_receive_diagnostic/metrics/timing_sweep.json`
