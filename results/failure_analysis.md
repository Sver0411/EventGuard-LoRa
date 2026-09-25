# Failure-case analysis (frozen v1, host simulation only)

All ten channel conditions and all evaluation seeds 31–130 are inspected. A failure includes cost dominance even when delivery ties; such ties must not be presented as a reliability win.

## Strict critical-delivery losses

EventGuard has 0 strict critical-delivery losses across all paired baseline comparisons. The absence of strict losses is expected: correctly classified critical samples already receive the frozen maximum of three copies. It does not establish cost efficiency.

## Conditions with cost dominance

A baseline dominates EventGuard here when its mean critical delivery is at least as high and it uses fewer mean DATA copies within the same model and loss rate.

| Model | Loss | Dominating baseline(s) | EG critical | EG copies | Lowest dominating copies |
|---|---:|---|---:|---:|---:|
| RANDOM_COPY | 0% | FIXED_1, FIXED_2, LINK_ONLY | 1.0000 | 112.00 | 54.00 |
| RANDOM_COPY | 5% | FIXED_2, IMPORTANCE_ONLY | 1.0000 | 116.45 | 108.00 |
| RANDOM_COPY | 10% | IMPORTANCE_ONLY | 0.9985 | 127.83 | 112.00 |
| RANDOM_COPY | 20% | IMPORTANCE_ONLY | 0.9892 | 140.68 | 112.00 |
| RANDOM_COPY | 30% | IMPORTANCE_ONLY | 0.9723 | 142.65 | 112.00 |
| BURST_SAMPLE | 0% | FIXED_1, FIXED_2, LINK_ONLY | 1.0000 | 112.00 | 54.00 |
| BURST_SAMPLE | 5% | FIXED_1, FIXED_2, IMPORTANCE_ONLY, LINK_ONLY | 0.9485 | 122.45 | 54.00 |
| BURST_SAMPLE | 10% | FIXED_1, FIXED_2, IMPORTANCE_ONLY, LINK_ONLY | 0.9108 | 125.68 | 54.00 |
| BURST_SAMPLE | 20% | FIXED_1, FIXED_2, IMPORTANCE_ONLY, LINK_ONLY | 0.7923 | 137.36 | 54.00 |
| BURST_SAMPLE | 30% | FIXED_1, FIXED_2, IMPORTANCE_ONLY, LINK_ONLY | 0.6985 | 140.29 | 54.00 |

## Representative failure: BURST_SAMPLE at 30%

EventGuard and IMPORTANCE_ONLY both deliver 0.6985 of critical events. EventGuard uses 140.29 mean DATA copies versus 112.00. The fixed BURST_SAMPLE calendar erases every copy opportunity within an affected sample; extra copies cannot restore it. Link adaptation adds traffic without critical-delivery improvement.

## Where allocation helps, and where it does not

- RANDOM_COPY 20%: EventGuard critical delivery 0.9892; exact-budget uniform 0.9746, random 0.9769. This is evidence for importance-guided allocation versus event-blind allocation under independent copy loss.
- RANDOM_COPY 30%: EventGuard critical delivery 0.9723; exact-budget uniform 0.9438, random 0.9492. This is evidence for importance-guided allocation versus event-blind allocation under independent copy loss.
- At low loss, delivery saturates near one; extra copies have little opportunity to help. Under common-mode BURST_SAMPLE loss, copies within a lost sample are correlated and cannot recover that sample.

## Mechanism and interpretation

The frozen rule sets CRITICAL to three copies on a GOOD link, already the maximum. DEGRADED or BAD link state therefore cannot increase its copy count. Any combined-policy benefit for CRITICAL delivery versus IMPORTANCE_ONLY is structurally blocked under this cap. Link adaptation may still improve IMPORTANT or overall delivery at additional cost. The baseline synthetic trace and application-layer erasures do not establish on-air benefit.

See `results/ablation/paired_tests.csv` for every predeclared comparison, including effect size and multiplicity-adjusted p-value. No favorable subset was selected after testing.
