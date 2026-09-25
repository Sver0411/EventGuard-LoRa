# Frozen v1 sensitivity analysis

Host-only simulation runs: 11000. All cells use the same evaluation seeds 31–130, five loss rates, and both main loss models. No parameter was selected from these results; the primary 8,000-run evaluation remains unchanged.

| Axis | Values | Status |
|---|---|---|
| CRITICAL threshold | 2.0, 2.5, 3.0, 3.5, 4.0 | All evaluated |
| Link window | 8, 12, 16, 24 | All evaluated |
| Maximum redundancy | 2, 3 | Evaluated |
| Maximum redundancy | 4 | Unsupported in frozen v1; no fabricated fourth opportunity |

`summary.csv` reports mean, median, sample standard deviation, and 95% mean CI for delivery, cost, link-state transitions, and classifier behavior. `runs.csv` preserves each seed-level observation. Plots: [threshold](critical_threshold.png), [window](link_window.png), [copy cap](max_redundancy.png).

The unsupported cap=4 is a scientific boundary: v1 firmware, policy, and the fixed three-slot fault calendar all cap at three. Simulating a fourth slot with the current loss plan would silently assign it no erasure and produce a false benefit. Any cap=4 study must be a separately versioned algorithm and channel experiment.

## Critical Threshold

- RANDOM_COPY 20%: 2.0: delivery 0.9892, copies 141.51; 2.5: delivery 0.9892, copies 140.81; 3.0: delivery 0.9892, copies 140.68; 3.5: delivery 0.9892, copies 140.32; 4.0: delivery 0.9892, copies 139.95.
- RANDOM_COPY 30%: 2.0: delivery 0.9723, copies 142.72; 2.5: delivery 0.9723, copies 142.66; 3.0: delivery 0.9723, copies 142.65; 3.5: delivery 0.9723, copies 142.58; 4.0: delivery 0.9723, copies 142.52.
- BURST_SAMPLE 20%: 2.0: delivery 0.7923, copies 138.80; 2.5: delivery 0.7923, copies 137.62; 3.0: delivery 0.7923, copies 137.36; 3.5: delivery 0.7923, copies 137.05; 4.0: delivery 0.7923, copies 136.75.
- BURST_SAMPLE 30%: 2.0: delivery 0.6985, copies 140.51; 2.5: delivery 0.6985, copies 140.31; 3.0: delivery 0.6985, copies 140.29; 3.5: delivery 0.6985, copies 140.22; 4.0: delivery 0.6985, copies 140.18.

## Link Window

- RANDOM_COPY 20%: 8: delivery 0.9892, copies 140.14; 12: delivery 0.9892, copies 140.68; 16: delivery 0.9892, copies 141.23; 24: delivery 0.9892, copies 142.82.
- RANDOM_COPY 30%: 8: delivery 0.9723, copies 142.46; 12: delivery 0.9723, copies 142.65; 16: delivery 0.9723, copies 142.52; 24: delivery 0.9723, copies 142.81.
- BURST_SAMPLE 20%: 8: delivery 0.7923, copies 135.68; 12: delivery 0.7923, copies 137.36; 16: delivery 0.7923, copies 136.97; 24: delivery 0.7923, copies 139.29.
- BURST_SAMPLE 30%: 8: delivery 0.6985, copies 139.03; 12: delivery 0.6985, copies 140.29; 16: delivery 0.6985, copies 140.31; 24: delivery 0.6985, copies 140.60.

## Max Redundancy

- RANDOM_COPY 20%: 2: delivery 0.9600, copies 102.01; 3: delivery 0.9892, copies 140.68.
- RANDOM_COPY 30%: 2: delivery 0.9185, copies 101.00; 3: delivery 0.9723, copies 142.65.
- BURST_SAMPLE 20%: 2: delivery 0.7923, copies 100.15; 3: delivery 0.7923, copies 137.36.
- BURST_SAMPLE 30%: 2: delivery 0.6985, copies 99.52; 3: delivery 0.6985, copies 140.29.
