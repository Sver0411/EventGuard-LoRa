# Frozen v1 ablation study

Source: 8,000 host-only evaluation runs, seeds 31–130. Each contrast keeps loss model, rate, seed, trace, and canonical channel calendar fixed. Fixed and component ablations are not equal-cost comparisons; only budget baselines are.

## A. Importance contribution

FIXED_2, IMPORTANCE_ONLY, and EVENTGUARD are shown in [importance_contribution.png](importance_contribution.png). Importance allocation gives CRITICAL samples three copies; it is the main source of their delivery benefit. Compare costs as well as delivery in `summary.csv`.

## B. Link contribution

IMPORTANCE_ONLY, LINK_ONLY, and EVENTGUARD are shown in [link_contribution.png](link_contribution.png). The frozen cap of three copies means that a correctly predicted CRITICAL sample already has the maximum under IMPORTANCE_ONLY. Link adaptation can improve IMPORTANT/overall delivery under RANDOM_COPY loss but cannot add CRITICAL copies in this design.

## C. Exact DATA-copy budget

EVENTGUARD, UNIFORM_BUDGET, and RANDOM_BUDGET are shown in [budget_fairness.png](budget_fairness.png). Exact copy counts were checked per matched run. The two budget baselines allocate without truth, classifier output, or sensor values.

## Paired tests

`paired_tests.csv` contains all 20 predeclared comparisons: 2 models × 5 rates × (EVENTGUARD−IMPORTANCE_ONLY, EVENTGUARD−UNIFORM_BUDGET). Zero differences are excluded from signed ranks; all-tie comparisons have p=1 and effect size 0. Rank-biserial correlation is the effect size. Holm-adjusted p-values cover all 20 tests. Mean-difference 95% CIs are descriptive and are not used to select conditions.

- RANDOM_COPY 0%: critical delivery EG=1.0000, importance=1.0000, uniform=1.0000; mean DATA copies EG=112.00, importance=112.00, uniform=112.00.
- RANDOM_COPY 5%: critical delivery EG=1.0000, importance=1.0000, uniform=1.0000; mean DATA copies EG=116.45, importance=112.00, uniform=116.45.
- RANDOM_COPY 10%: critical delivery EG=0.9985, importance=0.9985, uniform=0.9908; mean DATA copies EG=127.83, importance=112.00, uniform=127.83.
- RANDOM_COPY 20%: critical delivery EG=0.9892, importance=0.9892, uniform=0.9746; mean DATA copies EG=140.68, importance=112.00, uniform=140.68.
- RANDOM_COPY 30%: critical delivery EG=0.9723, importance=0.9723, uniform=0.9438; mean DATA copies EG=142.65, importance=112.00, uniform=142.65.
- BURST_SAMPLE 0%: critical delivery EG=1.0000, importance=1.0000, uniform=1.0000; mean DATA copies EG=112.00, importance=112.00, uniform=112.00.
- BURST_SAMPLE 5%: critical delivery EG=0.9485, importance=0.9485, uniform=0.9485; mean DATA copies EG=122.45, importance=112.00, uniform=122.45.
- BURST_SAMPLE 10%: critical delivery EG=0.9108, importance=0.9108, uniform=0.9108; mean DATA copies EG=125.68, importance=112.00, uniform=125.68.
- BURST_SAMPLE 20%: critical delivery EG=0.7923, importance=0.7923, uniform=0.7923; mean DATA copies EG=137.36, importance=112.00, uniform=137.36.
- BURST_SAMPLE 30%: critical delivery EG=0.6985, importance=0.6985, uniform=0.6985; mean DATA copies EG=140.29, importance=112.00, uniform=140.29.
