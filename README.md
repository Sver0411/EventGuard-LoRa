# EventGuard-LoRa

EventGuard-LoRa is a research prototype for testing whether event-aware redundancy can improve delivery of important sensor events over a constrained Sub-GHz link at a comparable communication cost.

## Research Question

At equal or similar communication cost, does adapting packet redundancy to event importance and measured link state deliver more critical sensor events than no protection or fixed redundancy?

## Motivation

Periodic sensor traffic often treats every sample equally. EventGuard assigns each sample a transparent three-level importance score and spends a bounded number of packet copies on important events. This repository makes the comparison reproducible and keeps application-layer fault injection distinct from errors caused by the physical radio channel.

## System Architecture

The Sensor Node replays a deterministic trace (or optionally samples real sensors), classifies importance, selects bounded redundancy, and sends compact CRC-protected binary frames through an E220 UART. The Gateway validates frames, deduplicates logical samples, records physical copies, and returns ACK frames. A host runner configures both ESP32-S3 boards, captures machine-readable serial logs, and computes metrics.

```text
Python reference trace ──serial control──> Sensor ESP32-S3 ──E220──> Gateway ESP32-S3
        │                                      ▲                   │
        └── shared run manifest                └────── ACK ────────┘
                       host parser / metrics / plots / report
```

## EventGuard Method

Importance is computed from normalized multivariate changes, change rate, simultaneous sensor changes, and persistence. NORMAL, IMPORTANT, and CRITICAL map to 1, 2, and 3 copies on a good link. A fixed-window ACK estimator raises redundancy under degraded link conditions, subject to the configured maximum. Thresholds and radio timing are in `configs/default.json`.

## Baselines

- `NO_PROTECTION`: one DATA copy.
- `FIXED_REDUNDANCY`: the same configured copy count for every sample (default 2).
- `EVENTGUARD`: importance- and link-aware copies, capped at 3 by default.

## Experimental Methodology

The host reference experiment uses identical trace rows and deterministic loss plans keyed by seed, frame kind, logical sequence, and copy index. RANDOM loss uses independent keyed decisions. BURST loss places deterministic contiguous erasure runs of configured length in a fixed three-copy opportunity calendar. DATA and ACK loss are modeled independently. The physical runner sends actual E220 frames and applies the configured deterministic application-layer drop after reception; injected loss must not be described as measured RF loss.

`python tools/run_all_experiments.py --simulate` runs the complete software reference matrix without hardware. The default mode discovers both boards, builds and flashes role-specific ESP-IDF firmware, then runs the matrix over the real E220 link. Use `--dry-run` to inspect serial discovery without changing firmware.

## Hardware

- Two ESP32-S3 boards connected over USB at the same time.
- Sensor: E220-400T22D, SHT30, BH1750, capacitive soil sensor, optional OLED.
- Gateway: ESP32-S3 and E220-400T22D.
- ESP-IDF v5.4.4; native IDF UART driver; no Arduino framework.

Verified E220 profile: UART1 at 9600 baud; TX/RX/AUX GPIO 17/16/15; M0/M1 GPIO 13/14; address `0x0000`; REG0 `0x62`; channel register `0x17` (decimal 23). Firmware reads these settings on boot and fails radio readiness if they differ; it does not write module configuration. The two detected board MACs were Sensor `80:65:99:a7:a8:a4` and Gateway `c0:4e:30:31:42:9c`.

The Sensor trace replay is the benchmark default. `REAL_SENSOR_MODE` is a separate demonstration mode; sensor drivers are isolated and are not used to generate benchmark results.

## Automation Pipeline

`tools/run_all_experiments.py` identifies the two serial roles from `STATUS` responses or existing `TX`/`RX` identity logs, records chip MACs, builds and flashes the two firmware roles, streams the same trace to each run, captures raw serial logs, and invokes analysis. Role assignment is based on observed firmware output; the host never asks the user to swap ports.

Results are written under `results/raw/`, `results/runs/`, `results/metrics/`, `results/plots/`, along with `summary.csv`, `summary.json`, and `report.md`. The analysis script also writes `docs/paper_outline.md` from the measured results.

## Results

### Completed E220 experiment

The current summary contains 300 runs over the actual E220 link: 3 strategies × 5 configured loss rates (0%, 5%, 10%, 20%, 30%) × 2 loss models (random and burst length 3) × 10 seeds. Each run sent an 18-sample deterministic synthetic trace (8 NORMAL, 6 IMPORTANT, 4 CRITICAL). The same two boards and firmware image hashes were used throughout.

| Strategy | Critical delivery mean | Overall delivery mean | Mean bytes/run | Mean latency (ms) |
|---|---:|---:|---:|---:|
| No protection | 0.800 | 0.837 | 664 | 415 |
| Fixed redundancy (2 copies) | 0.927 | 0.929 | 1,340 | 492 |
| EventGuard (up to 3 copies) | 0.968 | 0.945 | 1,579 | 544 |

EventGuard's mean critical-event delivery was highest, with about 17.8% more transmitted bytes per run than fixed redundancy. At matched configured random-loss rates, EventGuard delivered 0.975 versus 0.875 critical events at 20% loss, and 0.950 versus 0.750 at 30%. In burst loss, it tied fixed redundancy at 20% (0.900 each) and was 0.050 higher at 30% (0.900 versus 0.850). This is a reliability/traffic tradeoff, not evidence of a general equal-cost advantage.

The report includes paired tests across ten seeds per condition. The tests cover twenty comparisons and do not adjust for multiple comparisons, so their p-values are exploratory. The automated “similar cost” table may match runs with different configured loss rates; it is a traffic-budget comparison across conditions and should not be read as a same-channel-quality test.

The radios carried real DATA and ACK frames, but configured drops were injected by software at the application layer after reception. The injected loss rates are not measured RF packet-error rates. The experiment used one pair of devices and a short synthetic trace; it did not evaluate controlled RF fading/interference, range, power consumption, or airtime.

| Artifact | Description |
|---|---|
| [`results/report.md`](results/report.md) | Full experiment report, per-condition means, byte/delivery comparisons, and paired tests |
| [`results/summary.csv`](results/summary.csv) | 30 strategy/condition aggregates with standard deviations and confidence intervals |
| [`results/summary.json`](results/summary.json) | All 300 run records, provenance, and comparison data |
| [`results/firmware_size.json`](results/firmware_size.json) | ESP-IDF size reports for both flashed firmware images |
| [`docs/paper_outline.md`](docs/paper_outline.md) | Results-aware research paper outline and limitations |

Plots: [critical delivery](results/plots/critical_delivery_vs_loss.png), [overall delivery](results/plots/overall_delivery_vs_loss.png), [critical delivery vs bytes](results/plots/critical_delivery_vs_overhead.png), [latency](results/plots/latency_vs_strategy.png).

ESP-IDF reports list application binaries of 277,376 bytes (Sensor) and 248,016 bytes (Gateway), each within a 1 MiB app partition. Both builds use 16,383 of 16,384 bytes of available IRAM; the remaining 1 byte is a tight constraint for future firmware changes. The host test suite passed 14/14 tests.

## Limitations

The fault injector models application-visible DATA and ACK erasures; it does not reproduce RF fading, interference, regulatory airtime constraints, or E220 internal retries. The current measurements use synthetic traces, one pair of physical devices, and ten seeds per condition. EventGuard sends more bytes than the two-copy baseline, and observed gains depend on the loss model. E220 RF parameters and board wiring must remain fixed across runs. RSSI is not inferred when unavailable from the selected E220 mode.

## Reproduction

```sh
python -m pip install -r requirements.txt
python tools/run_all_experiments.py --simulate
# On a machine with ESP-IDF v5.4.4 and both boards connected:
python tools/run_all_experiments.py
```

The host tests run with `python -m unittest discover -s tests -v`.
