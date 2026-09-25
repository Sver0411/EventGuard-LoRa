# EventGuard-LoRa Automated Experiment Report

- Run count: 300
- Source: REAL_E220_WITH_APPLICATION_LAYER_INJECTION
- Physical radio: True; synthetic trace: True; application-layer injection: deterministic DATA and ACK drops after radio reception.
- Matrix: 100 no-protection, 100 fixed-redundancy, and 100 EventGuard runs; 10 seeds (11, 23, 37, 41, 53, 67, 71, 83, 97, 101); loss models BURST, RANDOM; configured loss rates 0%, 5%, 10%, 20%, 30%.
- Synthetic samples per run: 18.
- E220 profile: UART1 at 9600 baud; TX/RX/AUX=17/16/15; M0/M1=13/14; address=0x0000; REG0=0x62; channel register=0x17.
- Sensor MAC: 80:65:99:a7:a8:a4; gateway MAC: c0:4e:30:31:42:9c.

## Overall strategy means

Each strategy has equal representation across its configured conditions. Bytes and latency are per run.

| Strategy | Critical delivery | Overall delivery | Bytes/run | Mean latency (ms) |
|---|---:|---:|---:|---:|
| EVENTGUARD | 0.968 | 0.945 | 1578.6 | 543.6 |
| FIXED_REDUNDANCY | 0.927 | 0.929 | 1339.7 | 491.7 |
| NO_PROTECTION | 0.800 | 0.837 | 663.9 | 415.3 |

## Interpretation

These results describe the recorded trace, seeds, hardware, and configured application-layer loss model. The radios transmitted actual E220 frames, but the configured loss percentages are deterministic post-reception application-layer drops, not measured RF packet-error rates.
Across all runs, EventGuard used 17.8% more transmitted bytes per run on average than fixed redundancy. A higher aggregate delivery rate therefore does not establish an equal-cost advantage.
At random 20% configured loss, EventGuard critical delivery was 0.975 versus 0.875 for fixed redundancy (+0.100).
At random 30% configured loss, EventGuard critical delivery was 0.950 versus 0.750 for fixed redundancy (+0.200).
At burst 20% configured loss, EventGuard critical delivery was 0.900 versus 0.900 for fixed redundancy (+0.000).
At burst 30% configured loss, EventGuard critical delivery was 0.900 versus 0.850 for fixed redundancy (+0.050).
Paired p-values below are exploratory: there are 10 seeds per condition, 20 critical-delivery comparisons, and no multiplicity correction. Treat them as descriptive rather than confirmatory evidence.

## Strategy summaries

| Strategy | Loss model | Loss | Critical delivery (mean ± SD) | Overall delivery | Bytes | Mean latency (ms) |
|---|---:|---:|---:|---:|---:|---:|
| EVENTGUARD | BURST | 0% | 1.000 ± 0.000 | 1.000 | 1599.0 | 415.2 |
| FIXED_REDUNDANCY | BURST | 0% | 1.000 ± 0.000 | 1.000 | 1404.0 | 415.1 |
| NO_PROTECTION | BURST | 0% | 1.000 ± 0.000 | 1.000 | 702.0 | 415.0 |
| EVENTGUARD | BURST | 5% | 0.975 ± 0.079 | 0.972 | 1583.4 | 469.7 |
| FIXED_REDUNDANCY | BURST | 5% | 0.975 ± 0.079 | 0.944 | 1378.0 | 415.1 |
| NO_PROTECTION | BURST | 5% | 0.975 ± 0.079 | 0.933 | 686.4 | 417.3 |
| EVENTGUARD | BURST | 10% | 0.975 ± 0.079 | 0.961 | 1571.7 | 514.7 |
| FIXED_REDUNDANCY | BURST | 10% | 0.950 ± 0.105 | 0.922 | 1358.5 | 436.2 |
| NO_PROTECTION | BURST | 10% | 0.925 ± 0.121 | 0.894 | 677.3 | 415.1 |
| EVENTGUARD | BURST | 20% | 0.900 ± 0.129 | 0.883 | 1601.6 | 567.7 |
| FIXED_REDUNDANCY | BURST | 20% | 0.900 ± 0.129 | 0.850 | 1310.4 | 480.6 |
| NO_PROTECTION | BURST | 20% | 0.775 ± 0.184 | 0.739 | 640.9 | 415.2 |
| EVENTGUARD | BURST | 30% | 0.900 ± 0.175 | 0.822 | 1589.9 | 677.7 |
| FIXED_REDUNDANCY | BURST | 30% | 0.850 ± 0.175 | 0.783 | 1266.2 | 522.7 |
| NO_PROTECTION | BURST | 30% | 0.775 ± 0.322 | 0.639 | 617.5 | 415.1 |
| EVENTGUARD | RANDOM | 0% | 1.000 ± 0.000 | 1.000 | 1599.0 | 415.4 |
| FIXED_REDUNDANCY | RANDOM | 0% | 1.000 ± 0.000 | 1.000 | 1404.0 | 415.3 |
| NO_PROTECTION | RANDOM | 0% | 1.000 ± 0.000 | 1.000 | 702.0 | 415.2 |
| EVENTGUARD | RANDOM | 5% | 1.000 ± 0.000 | 0.983 | 1580.8 | 492.1 |
| FIXED_REDUNDANCY | RANDOM | 5% | 1.000 ± 0.000 | 0.989 | 1370.2 | 479.2 |
| NO_PROTECTION | RANDOM | 5% | 0.800 ± 0.158 | 0.917 | 682.5 | 415.3 |
| EVENTGUARD | RANDOM | 10% | 1.000 ± 0.000 | 0.983 | 1574.3 | 516.5 |
| FIXED_REDUNDANCY | RANDOM | 10% | 0.975 ± 0.079 | 0.989 | 1349.4 | 525.7 |
| NO_PROTECTION | RANDOM | 10% | 0.725 ± 0.219 | 0.872 | 672.1 | 414.1 |
| EVENTGUARD | RANDOM | 20% | 0.975 ± 0.079 | 0.950 | 1544.4 | 622.8 |
| FIXED_REDUNDANCY | RANDOM | 20% | 0.875 ± 0.177 | 0.933 | 1298.7 | 606.2 |
| NO_PROTECTION | RANDOM | 20% | 0.525 ± 0.275 | 0.728 | 638.3 | 415.0 |
| EVENTGUARD | RANDOM | 30% | 0.950 ± 0.105 | 0.894 | 1541.8 | 744.5 |
| FIXED_REDUNDANCY | RANDOM | 30% | 0.750 ± 0.264 | 0.878 | 1257.1 | 620.7 |
| NO_PROTECTION | RANDOM | 30% | 0.500 ± 0.236 | 0.650 | 620.1 | 415.3 |

## Similar-cost and similar-delivery comparisons

Matches are selected automatically within the same loss model across configured loss-rate points. Similar cost uses a 15% relative byte tolerance. The matched points can have different configured loss rates, so these are traffic-budget comparisons across conditions, not same-channel-condition claims.

### Similar transmitted bytes

| Loss model | EG loss | Fixed loss | Byte gap | EG critical delivery | Fixed critical delivery | Difference |
|---|---:|---:|---:|---:|---:|---:|
| BURST | 0% | 0% | 13.9% | 1.000 | 1.000 | +0.000 |
| BURST | 5% | 0% | 12.8% | 0.975 | 1.000 | -0.025 |
| BURST | 10% | 0% | 11.9% | 0.975 | 1.000 | -0.025 |
| BURST | 20% | 0% | 14.1% | 0.900 | 1.000 | -0.100 |
| BURST | 30% | 0% | 13.2% | 0.900 | 1.000 | -0.100 |
| RANDOM | 0% | 0% | 13.9% | 1.000 | 1.000 | +0.000 |
| RANDOM | 5% | 0% | 12.6% | 1.000 | 1.000 | +0.000 |
| RANDOM | 10% | 0% | 12.1% | 1.000 | 1.000 | +0.000 |
| RANDOM | 20% | 0% | 10.0% | 0.975 | 1.000 | -0.025 |
| RANDOM | 30% | 0% | 9.8% | 0.950 | 1.000 | -0.050 |

### Similar critical delivery

| Loss model | EG loss | Fixed loss | Delivery gap | EG bytes | Fixed bytes | Byte difference |
|---|---:|---:|---:|---:|---:|---:|
| BURST | 0% | 0% | 0.000 | 1599.0 | 1404.0 | +195.0 |
| BURST | 5% | 5% | 0.000 | 1583.4 | 1378.0 | +205.4 |
| BURST | 10% | 5% | 0.000 | 1571.7 | 1378.0 | +193.7 |
| BURST | 20% | 20% | 0.000 | 1601.6 | 1310.4 | +291.2 |
| BURST | 30% | 20% | 0.000 | 1589.9 | 1310.4 | +279.5 |
| RANDOM | 0% | 0% | 0.000 | 1599.0 | 1404.0 | +195.0 |
| RANDOM | 5% | 0% | 0.000 | 1580.8 | 1404.0 | +176.8 |
| RANDOM | 10% | 0% | 0.000 | 1574.3 | 1404.0 | +170.3 |
| RANDOM | 20% | 10% | 0.000 | 1544.4 | 1349.4 | +195.0 |
| RANDOM | 30% | 10% | 0.025 | 1541.8 | 1349.4 | +192.4 |

## Paired tests

Paired two-sided t-tests compare matched seeds within each loss condition; p-values are descriptive and no multiplicity correction is applied.

- BURST 0%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.0, n=10, p=1.0.
- BURST 0%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.0, n=10, p=1.0.
- BURST 5%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.0, n=10, p=1.0.
- BURST 5%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.0, n=10, p=1.0.
- BURST 10%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.05, n=10, p=0.16785065605707716.
- BURST 10%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.025, n=10, p=0.34343639613791554.
- BURST 20%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.125, n=10, p=0.05217724279881855.
- BURST 20%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.0, n=10, p=1.0.
- BURST 30%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.125, n=10, p=0.05217724279881855.
- BURST 30%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.05, n=10, p=0.16785065605707716.
- RANDOM 0%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.0, n=10, p=1.0.
- RANDOM 0%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.0, n=10, p=1.0.
- RANDOM 5%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.2, n=10, p=0.003110428310385847.
- RANDOM 5%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.0, n=10, p=1.0.
- RANDOM 10%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.275, n=10, p=0.0032412146013796484.
- RANDOM 10%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.025, n=10, p=0.34343639613791554.
- RANDOM 20%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.45, n=10, p=0.0007252150276033905.
- RANDOM 20%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.1, n=10, p=0.036787497879786045.
- RANDOM 30%, EVENTGUARD vs NO_PROTECTION: critical-delivery mean difference=0.45, n=10, p=0.00015997142806871333.
- RANDOM 30%, EVENTGUARD vs FIXED_REDUNDANCY: critical-delivery mean difference=0.2, n=10, p=0.022367189680572004.

## Generated plots

- `plots/critical_delivery_vs_loss.png`
- `plots/critical_miss_vs_loss.png`
- `plots/overall_delivery_vs_loss.png`
- `plots/burst_loss_performance.png`
- `plots/critical_delivery_vs_overhead.png`
- `plots/bytes_vs_strategy.png`
- `plots/latency_vs_strategy.png`
- `plots/event_class_delivery.png`
