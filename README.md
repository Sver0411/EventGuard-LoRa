# EventGuard-LoRa

EventGuard-LoRa is a research prototype for measuring critical-event delivery against communication cost on a constrained Sub-GHz link. **The current frozen-v1 conclusions are host-simulation results only.** The 300 E220 runs below are an older, separate pilot and do not validate v1.

## Research Question

At an exact DATA-copy budget under the same trace and channel calendar, does adapting redundancy to event importance and link state deliver more critical events than budget-matched policies?

## Motivation

Periodic sensor traffic often treats every sample equally. EventGuard assigns each sample a transparent three-level importance score and spends a bounded number of packet copies on important events. This repository makes the comparison reproducible and keeps application-layer fault injection distinct from errors caused by the physical radio channel.

## System Model

The Sensor Node replays a deterministic trace (or optionally samples real sensors), classifies importance, selects bounded redundancy, and sends compact CRC-protected binary frames through an E220 UART. The Gateway validates frames, deduplicates logical samples, records physical copies, and returns ACK frames. A host runner configures both ESP32-S3 boards, captures machine-readable serial logs, and computes metrics.

```text
Python reference trace ──serial control──> Sensor ESP32-S3 ──E220──> Gateway ESP32-S3
        │                                      ▲                   │
        └── shared run manifest                └────── ACK ────────┘
                       host parser / metrics / plots / report
```

## Method

Importance uses normalized change, rate, baseline deviation, co-change, persistence, and a directional hazard-level rule. The [frozen algorithm specification](docs/algorithm_spec_v1.md) records exact equations, thresholds, window behavior, and source hashes. The benchmark trace has a strict 10 s interval. NORMAL, IMPORTANT, and CRITICAL map to 1, 2, and 3 copies on a good link. Link state uses a sliding window of **first-copy accepted ACK outcomes** and consecutive first-copy failures; this avoids strategy-dependent observation bias. All physical copy attempts, ACK successes, and failures are counted separately. Thresholds and radio timing are in `configs/default.json`.

## Baselines

- `NO_PROTECTION`: one DATA copy.
- `FIXED_REDUNDANCY`: the same configured copy count for every sample (default 2).
- `EVENTGUARD`: importance- and link-aware copies, capped at 3 by default.
- `FIXED_1`, `FIXED_2`, `FIXED_3`: one, two, or three copies for every sample. `FIXED_1` equals `NO_PROTECTION`.
- `IMPORTANCE_ONLY`: 1/2/3 copies from predicted importance, without link state.
- `LINK_ONLY`: 1/2/3 copies from link state, without importance.
- `UNIFORM_BUDGET`, `RANDOM_BUDGET`: exactly match EventGuard’s total DATA-copy count for each model/rate/seed/trace. Extra copies use round-robin or seeded hash order and do not read event labels or sensor values.

## Experimental Protocol

The host reference experiment uses identical trace rows and deterministic loss plans keyed by seed, frame kind, logical sequence, and copy index. `RANDOM_COPY` uses independent keyed decisions. `BURST_COPY` erases contiguous canonical copy opportunities. `BURST_SAMPLE` erases every copy opportunity in contiguous logical samples; this is the main burst model. DATA and ACK calendars are independent. Fairness audits report effective DATA/ACK drops and first-copy drops. The physical runner sends actual E220 frames and applies deterministic application-layer drops after reception; injected loss is not measured RF loss.

`python tools/run_all_experiments.py --simulate` runs the isolated 8,000-run pre-hardware evaluation (8 strategies × 5 rates × 2 models × 100 evaluation seeds) and writes `results/pre_hardware_v1/` plus `results/pre_hardware_validation.md`. Seeds 1–30 are reserved for calibration; seeds 31–130 are used only for evaluation. `python tools/run_research_analysis.py` verifies the frozen code hashes, analyzes the locked 8,000 runs, and adds 11,000 host-only sensitivity runs. All experiments save a JSON manifest. The new hardware matrix is gated by the host diagnosis and has **not** been run.

The frozen-v1 hardware validation now has a strict smoke gate in `tools/run_hardware_validation.py`. It records reset acknowledgments, per-sample classification/copy/link outcomes, separate injected and uncontrolled loss, firmware hashes, and exact execution order under `results/hardware_validation_v1/`. `tools/analyze_hardware_validation.py` summarizes only completed runs while retaining failed runs and raw logs in the failure audit. The 160-run main stage is not started unless all 12 smoke runs pass.

## Hardware

- Two ESP32-S3 boards connected over USB at the same time.
- Sensor: E220-400T22D, SHT30, BH1750, capacitive soil sensor, optional OLED.
- Gateway: ESP32-S3 and E220-400T22D.
- ESP-IDF v5.4.4; native IDF UART driver; no Arduino framework.

Verified E220 profile: UART1 at 9600 baud; TX/RX/AUX GPIO 17/16/15; M0/M1 GPIO 13/14; address `0x0000`; REG0 `0x62`; channel register `0x17` (decimal 23). Firmware reads these settings on boot and fails radio readiness if they differ; it does not write module configuration. The two detected board MACs were Sensor `80:65:99:a7:a8:a4` and Gateway `c0:4e:30:31:42:9c`.

The Sensor trace replay is the benchmark default. `REAL_SENSOR_MODE` is a separate demonstration mode; sensor drivers are isolated and are not used to generate benchmark results.

## Automation Pipeline

`tools/run_all_experiments.py` identifies the two serial roles from `STATUS` responses or existing `TX`/`RX` identity logs, records chip MACs, builds and flashes the two firmware roles, streams the same trace to each run, captures raw serial logs, and invokes analysis. Role assignment is based on observed firmware output; the host never asks the user to swap ports.

The original physical pilot outputs remain under `results/raw/`, `results/runs/`, `results/metrics/`, `results/plots/`, `summary.csv`, `summary.json`, and `report.md`. New host outputs are isolated in `results/pre_hardware_v1/`.

## Simulation Results

### Pre-hardware validation v1

The corrected host matrix completed 8,000 runs on evaluation seeds 31–130. The host test suite includes compiled C/Python parity for importance labels, copy selection, link transitions, loss decisions, and budget allocation. The Sensor and Gateway firmware compiled with ESP-IDF during PR #1; no boards were flashed or exercised in this round.

CRITICAL classifier precision is 0.8125, recall 1.0000, and F1 0.8966; NORMAL→CRITICAL false rate is 0. The synthetic trace remains simple, so these are not field-accuracy claims. EventGuard has small exact-budget gains at some RANDOM_COPY loss rates, but `IMPORTANCE_ONLY` matches its CRITICAL delivery in **every** paired run while using no more DATA copies. Under `BURST_SAMPLE`, redundant copies do not improve DATA delivery. **The full hardware rerun gate is closed** pending a clearer benefit from the combined policy.

Read the [diagnostic report](results/pre_hardware_validation.md), [per-run CSV](results/pre_hardware_v1/runs.csv), and [matched-budget plot](results/pre_hardware_v1/plots/critical_delivery_vs_exact_budget.png).

The expanded analysis adds [ablation statistics](results/ablation/report.md), [20 predeclared paired tests](results/ablation/paired_tests.csv), [Pareto frontiers](results/plots/pareto/pareto_frontier.png), [parameter sensitivity](results/sensitivity/report.md), and [failure cases](results/failure_analysis.md). It reports mean, median, sample standard deviation, and 95% mean CI for delivery and cost. EventGuard lies on **0 of 10** within-condition critical-delivery/byte Pareto frontiers: IMPORTANCE_ONLY or another lower-cost baseline matches its critical delivery in each tested condition. The [research status report](results/research_status_report.md) and [paper draft](docs/paper_draft.md) preserve this negative finding. The sensitivity grid tests CRITICAL threshold 2.0–4.0, link window 8/12/16/24, and copy cap 2/3. Cap 4 is explicitly unsupported by frozen v1's three-slot loss calendar and is not represented as a result.

### Frozen-v1 hardware smoke status

Two ESP32-S3 and E220-400T22D nodes were detected and flashed with frozen firmware. Three RANDOM_COPY 0%, seed-31 smoke runs passed complete per-sample Python/C and DATA/ACK accounting checks. The next EventGuard run at 20% injection failed the smoke gate: 141 DATA sends were logged, while 13 copies had no gateway receive or injection-drop record. The precise cause within the E220/receiver path is not established. The remaining eight smoke runs and the 160/400-run main matrices were **not** started. The failed result is preserved in the [hardware report](results/hardware_validation_v1/hardware_report.md), [raw logs](results/hardware_validation_v1/raw/smoke/eventguard_random_copy_20_seed31.json), and [per-copy anomaly list](results/hardware_validation_v1/metrics/physical_anomalies.csv). No hardware claim about equal-budget benefit or link adaptation is supported yet.

The hardware cost field `estimated_communication_time_ms` is a **UART-time proxy**: `(DATA bytes + ACK bytes) × 10 / 9600` seconds. It is not measured E220 RF PHY airtime. No communication energy in Joules is reported because current was not measured.

## Pilot Archive (v0 E220; separate from frozen-v1 simulation)

The original, unmodified pilot summary contains 300 runs over the actual E220 link: 3 strategies × 5 configured loss rates (0%, 5%, 10%, 20%, 30%) × 2 loss models (random and burst length 3) × 10 seeds. Each run sent the **old** 18-sample trace, which contained duplicate timestamps (8 NORMAL, 6 IMPORTANT, 4 CRITICAL). The same two boards and firmware image hashes were used throughout. These results are archival and cannot validate the corrected design.

| Strategy | Critical delivery mean | Overall delivery mean | Mean bytes/run | Mean latency (ms) |
|---|---:|---:|---:|---:|
| No protection | 0.800 | 0.837 | 664 | 415 |
| Fixed redundancy (2 copies) | 0.927 | 0.929 | 1,340 | 492 |
| EventGuard (up to 3 copies) | 0.968 | 0.945 | 1,579 | 544 |

EventGuard's mean critical-event delivery was highest, with about 17.8% more transmitted bytes per run than fixed redundancy. At matched configured random-loss rates, EventGuard delivered 0.975 versus 0.875 critical events at 20% loss, and 0.950 versus 0.750 at 30%. In burst loss, it tied fixed redundancy at 20% (0.900 each) and was 0.050 higher at 30% (0.900 versus 0.850). This is a reliability/traffic tradeoff, not evidence of a general equal-cost advantage.

The pilot report includes paired tests across ten seeds per condition without multiplicity correction. Its historical “similar cost” table can compare different configured loss rates and is **not** evidence of equal-cost performance. New analyses use only matched model/rate/seed/trace/calendar and exact DATA-copy budgets.

The radios carried real DATA and ACK frames, but configured drops were injected by software at the application layer after reception. The injected loss rates are not measured RF packet-error rates. The experiment used one pair of devices and a short synthetic trace; it did not evaluate controlled RF fading/interference, range, power consumption, or airtime.

| Artifact | Description |
|---|---|
| [`results/report.md`](results/report.md) | Full experiment report, per-condition means, byte/delivery comparisons, and paired tests |
| [`results/summary.csv`](results/summary.csv) | 30 strategy/condition aggregates with standard deviations and confidence intervals |
| [`results/summary.json`](results/summary.json) | All 300 run records, provenance, and comparison data |
| [`results/firmware_size.json`](results/firmware_size.json) | ESP-IDF size reports for both flashed firmware images |
| [`docs/paper_outline.md`](docs/paper_outline.md) | Results-aware research paper outline and limitations |

Plots: [critical delivery](results/plots/critical_delivery_vs_loss.png), [overall delivery](results/plots/overall_delivery_vs_loss.png), [critical delivery vs bytes](results/plots/critical_delivery_vs_overhead.png), [latency](results/plots/latency_vs_strategy.png).

ESP-IDF reports list application binaries of 277,376 bytes (Sensor) and 248,016 bytes (Gateway), each within a 1 MiB app partition. Both builds use 16,383 of 16,384 bytes of available IRAM; the remaining 1 byte is a tight constraint for future firmware changes. The pilot host test suite passed 14/14 tests at the time; the current suite has expanded.

## Limitations

The frozen-v1 evaluation uses a short synthetic trace, 100 evaluation seeds, and deterministic application-layer DATA/ACK erasures. The limited hardware smoke data do not validate the main result and do not measure RF fading, interference, E220 internal behavior, energy, regulatory airtime, or field classification quality. The older E220 pilot used one device pair, ten seeds per condition, and a different trace. Its results cannot be pooled with v1. A fourth copy would require a new policy and canonical loss calendar. Current confirmatory v1 conclusions remain host-simulation-only.

## Reproduction

```sh
python -m pip install -r requirements.txt
python tools/run_all_experiments.py --simulate
python tools/run_research_analysis.py
```

The host tests run with `python -m unittest discover -s tests -v`. The full hardware matrix remains gated; this reproduction procedure uses no device.
