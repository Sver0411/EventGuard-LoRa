<h1 align="center">EventGuard-LoRa</h1>

<p align="center"><strong>Critical-Event Delivery Under Bounded Communication Budgets</strong></p>

<p align="center">Event Importance · Link-Aware Redundancy · Equal-Budget Evaluation</p>

---

<p align="center">
  <a href="#project-overview">Overview</a> ·
  <a href="#research-question-and-method">Method</a> ·
  <a href="#implementation-and-hardware-status">Status</a> ·
  <a href="#relationship-to-smart-agriculture-edge-ai">Related System</a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

---

<a id="project-overview"></a>

EventGuard-LoRa studies critical-event delivery versus communication cost on constrained Sub-GHz links. Its frozen v1 policy combines event importance with link-aware redundancy and is evaluated in host simulation and on two ESP32-S3 boards with E220-400T22D radios. The v1 hardware evidence is a post-hoc balanced set of 96 completed runs; the earlier 300-run v0 pilot is a separate archive.

## Research question and method

At an exact DATA-copy budget, with the same trace and channel calendar within each paired seed, does importance- and link-aware redundancy deliver more critical events than budget-matched policies?

Event importance combines normalized change, rate, baseline deviation, co-change, persistence, and a directional hazard rule. NORMAL, IMPORTANT, and CRITICAL map to 1, 2, and 3 copies on a good link. Link state uses a sliding window of **first-copy accepted ACK outcomes** and consecutive first-copy failures to avoid strategy-dependent observation bias. Copies are bounded, and physical attempts/ACK outcomes are recorded separately. See the [frozen algorithm](docs/algorithm_spec_v1.md) and [configuration](configs/default.json).

```text
Python reference trace ──serial control──> Sensor ESP32-S3 ──E220──> Gateway ESP32-S3
        │                                      ▲                   │
        └── shared run manifest                └────── ACK ────────┘
                       host parser / metrics / plots / report
```

The Sensor classifies and transmits compact CRC-protected frames; the Gateway validates, deduplicates logical samples, records copies, and ACKs. Benchmark traces use a strict 10 s interval. Optional `REAL_SENSOR_MODE` is a separate demonstration, not the source of benchmark results.

## Baselines and fair comparison

| Strategy | Purpose |
| --- | --- |
| `NO_PROTECTION` / `FIXED_1` | One DATA copy |
| `FIXED_REDUNDANCY`, `FIXED_2`, `FIXED_3` | Uniform copies; configured redundancy defaults to two |
| `IMPORTANCE_ONLY` | Importance-driven 1/2/3 copies without link state |
| `LINK_ONLY` | Link-driven 1/2/3 copies without importance |
| `EVENTGUARD` | Importance and link state; default cap three |
| `UNIFORM_BUDGET`, `RANDOM_BUDGET` | Match EventGuard's exact DATA-copy total using round-robin or seeded hash allocation, without event labels or sensor values |

Pairing fixes model, rate, seed, trace, and loss calendar. DATA and ACK calendars are independent. `RANDOM_COPY` erases keyed copy opportunities; `BURST_SAMPLE` erases all copies of contiguous samples. `BURST_COPY` is an additional copy-level burst model. On hardware, configured losses are software drops **after reception**, not measured RF packet errors.

## Key findings: frozen v1

### Final balanced hardware analysis

The set contains four strategies × two models (`RANDOM_COPY`, `BURST_SAMPLE`) × two loss rates (20%, 30%) × six seeds (31–36): **96 real-device runs**. Each paired seed uses one shared 54-sample trace. This is the earliest contiguous complete subset of an interrupted 160-run Stage 1 campaign, selected by completion and execution order, not outcomes; six seeds were not preregistered.

| Comparison | Main finding |
| --- | --- |
| `IMPORTANCE_ONLY` vs `EVENTGUARD` | Identical critical delivery in every selected pair; EventGuard used 21–33 more DATA copies/run on average |
| Exact-budget blind baselines | EventGuard assigned a larger copy share to critical samples; delivery gains were small and condition-specific |
| `RANDOM_COPY` at 30% | EventGuard mean critical-delivery gain +0.0256 versus blind baselines: two higher seeds, four ties |
| `BURST_SAMPLE` at 20% / 30% | All strategies tied; redundancy could not recover erased samples |

`IMPORTANCE_ONLY` is on the observed critical-delivery/cost Pareto frontier in all four conditions; EventGuard is on none. These results do not establish a general benefit from the added link-state mechanism. With six paired seeds, comparisons are directional; descriptive Wilcoxon and exploratory Holm-adjusted p-values do not support confirmatory significance claims.

![Critical-event delivery versus DATA copies by condition](results/final_hardware_v1/plots/pareto_critical_vs_copies.png)

The offline audit passed 96/96 raw logs/manifests, with no uncontrolled physical DATA/ACK misses or host/firmware delivery differences in the selected set. The later `UNIFORM_BUDGET / RANDOM_COPY / 30% / seed37` run103 stalled after a DATA frame was absent from Gateway pre-injection logs. Its cause remains unconfirmed; incomplete seed-37 attempts are excluded and their raw evidence is preserved.

See the [hardware report](results/final_hardware_v1/final_hardware_report.md), [audit](results/final_hardware_v1/audit_report.md), [paired tests](results/final_hardware_v1/paired_tests.csv), [rendered byte-cost Pareto plot](results/final_hardware_v1/plots/pareto_critical_vs_bytes.png), [paper draft](docs/paper_draft_final.md), and [research summary](docs/final_research_summary.md).

### Host simulation

The locked pre-hardware matrix contains 8,000 runs (eight strategies, five rates, two models, 100 evaluation seeds). Seeds 1–30 are reserved for calibration and 31–130 for evaluation. C/Python parity checks cover policy and loss/budget decisions.

CRITICAL classifier precision/recall/F1 are 0.8125 / 1.0000 / 0.8966, with NORMAL→CRITICAL false rate 0 on the simple synthetic trace. `IMPORTANCE_ONLY` matches EventGuard's critical delivery in every paired host run with no more DATA copies; under `BURST_SAMPLE`, copies do not improve delivery. Expanded analysis finds EventGuard on **0 of 10** within-condition critical-delivery/byte Pareto frontiers.

Reports and retained figures: [validation](results/pre_hardware_validation.md), [per-run data](results/pre_hardware_v1/runs.csv), [matched-budget plot](results/pre_hardware_v1/plots/critical_delivery_vs_exact_budget.png), [ablation](results/ablation/report.md), [paired tests](results/ablation/paired_tests.csv), [Pareto frontiers](results/plots/pareto/pareto_frontier.png), [sensitivity](results/sensitivity/report.md), [failure cases](results/failure_analysis.md), and [research status](results/research_status_report.md). Sensitivity varies critical threshold 2.0–4.0, window 8/12/16/24, and cap 2/3; cap four is unsupported by v1's calendar.

## Implementation and hardware status

Two ESP32-S3/E220-400T22D devices run ESP-IDF v5.4.4 with native UART drivers. The Sensor configuration includes SHT30, BH1750, capacitive soil sensor, and optional OLED; the Gateway uses ESP32-S3/E220. The recorded radio profile uses UART1 9600 baud, TX/RX/AUX GPIO 17/16/15, M0/M1 GPIO 13/14, address `0x0000`, REG0 `0x62`, and channel `0x17`. Firmware checks settings at boot without rewriting them.

Real UART and radio frames were exercised. `estimated_communication_time_ms` is a UART-time proxy, `(DATA bytes + ACK bytes) × 10 / 9600` seconds, not RF PHY airtime. Energy in joules was not measured. The unresolved run103 receive-path stall remains a hardware limitation.

## Quick start and reproduction

Host-only analysis requires Python dependencies and no device:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/run_all_experiments.py --simulate
python tools/run_research_analysis.py
```

The second command adds 11,000 host-only sensitivity runs. To regenerate final hardware summaries and plots from preserved logs without contacting hardware:

```sh
python tools/finalize_hardware_dataset.py
```

Optional checks: `python -m unittest discover -s tests -v`. Physical reruns use `tools/run_all_experiments.py` and require both boards; preserved [run evidence](results/hardware_validation_v2_policy_v2/) and the hardware report document the campaign.

## Relationship to Smart-Agriculture-Edge-AI

[Smart-Agriculture-Edge-AI](https://github.com/Sver0411/Smart-Agriculture-Edge-AI) draws on EventGuard-LoRa's communication-policy and evaluation methods. The complete frozen redundancy policy is not directly integrated; this remains an independent link-policy study.

## Limitations and future work

One device pair, short synthetic traces, software-injected erasures, and six hardware seeds per condition limit generalization. Controlled RF fading/interference, range, physical channel error rate, energy, regulatory airtime, and field classification remain untested. Real RF experiments are the next priority; a fourth copy would require a new policy/calendar, and the v0 pilot cannot be pooled with v1.

## Pilot archive: v0 E220

<details>
<summary>Expand the separate 300-run pilot and its retained figures</summary>

The original pilot used three strategies, five configured loss rates, two loss models, ten seeds, and an old 18-sample trace with duplicate timestamps (8 NORMAL, 6 IMPORTANT, 4 CRITICAL). It does not validate frozen v1.

| Strategy | Critical delivery mean | Overall delivery mean | Mean bytes/run | Mean latency (ms) |
|---|---:|---:|---:|---:|
| No protection | 0.800 | 0.837 | 664 | 415 |
| Fixed redundancy (2 copies) | 0.927 | 0.929 | 1,340 | 492 |
| EventGuard (up to 3 copies) | 0.968 | 0.945 | 1,579 | 544 |

EventGuard's mean critical delivery was highest at about 17.8% more bytes than fixed redundancy. Its historical “similar cost” comparison can mix loss rates and is not equal-budget evidence. Application-layer injected loss is separate from RF error rate. Pilot firmware used 16,383/16,384 available IRAM bytes, leaving a tight constraint.

Original records: [report](results/report.md), [CSV](results/summary.csv), [JSON](results/summary.json), [firmware sizes](results/firmware_size.json), and [paper outline](docs/paper_outline.md). Rendered figures remain linked: [critical delivery](results/plots/critical_delivery_vs_loss.png), [overall delivery](results/plots/overall_delivery_vs_loss.png), [delivery vs bytes](results/plots/critical_delivery_vs_overhead.png), and [latency](results/plots/latency_vs_strategy.png).

</details>

## Usage and Rights

This repository is publicly available for research review, portfolio
demonstration, reproducibility inspection, and academic citation.

It is **not released under an open-source license**.

Copyright © 2026 Sver0411. All rights reserved.

Reuse, modification, redistribution, republication, or commercial use of
substantial project code, firmware, datasets, figures, or documentation requires
prior permission from the author.

See [NOTICE.md](NOTICE.md) for details.

## Citation

If you reference EventGuard-LoRa in academic or technical work, please use the
repository citation metadata in [`CITATION.cff`](CITATION.cff).

The frozen research release is [`v1.0.0`](https://github.com/Sver0411/EventGuard-LoRa/releases/tag/v1.0.0).
