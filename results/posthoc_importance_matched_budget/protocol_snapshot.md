# Post-hoc importance-matched-budget host diagnostic protocol

## Status and question

**Post-hoc diagnostic designed after inspection of frozen v1 results. Not preregistered. Not part of the original 8,000-run v1 matrix. Not part of frozen v1.0.0.** This protocol is fixed before any `IMPORTANCE_MATCHED_BUDGET` (IMB) result is generated. It asks whether EventGuard's higher IMPORTANT and overall delivery than `IMPORTANCE_ONLY` under `RANDOM_COPY` reflects only its larger DATA-copy budget or whether its link-state-aware placement differs from a specified importance-aware, link-blind placement at the same budget.

This is a **host-only offline counterfactual**. IMB is given EventGuard's realized total DATA-copy budget from the preserved `results/pre_hardware_v1/runs.csv`. It is not a deployable online policy that can know this total beforehand. It does not enter the frozen v1 strategy list, hardware firmware, or original evaluation matrix.

## Frozen inputs and scope

- Loss model: `RANDOM_COPY` only.
- Configured loss rates: 0%, 5%, 10%, 20%, 30%.
- Evaluation seeds: every integer from 31 through 130 inclusive; one deterministic 54-sample trace realization per seed, shared across strategies at that seed.
- Exactly 500 **new IMB host runs** (five rates × 100 seeds). EventGuard and Importance Only observations are read from frozen v1 CSV; they are not counted as new runs.
- Primary diagnostic rates, fixed before execution: 20% and 30%. Rates 0%, 5%, and 10% are descriptive sensitivity only.
- Four primary exploratory paired contrasts: EventGuard minus IMB for IMPORTANT delivery and overall delivery, each at 20% and 30%. CRITICAL delivery, class allocation, and cost are secondary/sanity outcomes.
- The matched canonical DATA/ACK calendar is keyed by model, loss rate, seed, sample index, and copy opportunity as in frozen `LossPlan`. IMB may use that calendar only when scoring outcomes after allocation; the allocator does not receive it.

## `IMPORTANCE_MATCHED_BUDGET_V1` allocation

Inputs to the pure allocator are only `(predicted_classes, budget, seed)`. The predicted class sequence comes from the unchanged frozen ImportanceClassifier on the unchanged trace. The allocator must not receive ground truth, sensor values or severity score, link state, ACK/first-copy history, channel-calendar outcomes, or delivery outcomes.

1. Read `B = physical_data_transmissions` from the matching frozen EventGuard row for the loss rate and seed.
2. Assign the Importance Only base mapping: predicted NORMAL = 1, IMPORTANT = 2, CRITICAL = 3. Let `B_base` be its sum; assert `B >= B_base` and `B <= 3 × number_of_samples`.
3. Compute `extra = B - B_base`. Spend it first on predicted IMPORTANT samples, adding at most one copy per sample (2→3).
4. If extra remains, spend it on predicted NORMAL samples in two complete rounds: first 1→2, then 2→3. No sample may exceed three copies. CRITICAL has no headroom.
5. Within each class, sort sample indices by the ascending raw 32-byte SHA-256 digest of the UTF-8 string `IMPORTANCE_MATCHED_BUDGET_V1|seed={decimal_seed}|sample_index={decimal_zero_based_index}|class={UPPERCASE_CLASS}`. Break any hash tie by ascending sample index. Use this same NORMAL order in both rounds.
6. Assert exactly `B` total copies were assigned. There is no outcome-dependent reallocation or subsequent policy adaptation.

This design matches EventGuard's DATA-copy count and predicted-importance information while withholding link-state and ACK-history information. The particular fixed hash placement is one specified link-blind counterfactual, not the best possible importance-only placement.

## Evaluation and accounting

Replay IMB's static copy counts against the same frozen canonical `RANDOM_COPY` DATA/ACK erasures. An unerased DATA copy yields gateway receipt, logical delivery (deduplicated per sample), and one ACK frame. An unerased ACK counts as accepted but does not change the precomputed IMB allocation. DATA bytes use 26 bytes/copy and ACK bytes use 13 bytes/frame; estimated communication time is `(DATA bytes + ACK bytes) × 10 / 9600` seconds, a UART serialization proxy, not RF airtime or energy. Record ground-truth CRITICAL and IMPORTANT delivery, overall delivery, DATA copies, ACK frames, bytes, and retrospective class traffic shares. Ground truth is used only for scoring.

For all 500 pairs, assert exact EventGuard/IMB DATA-copy equality, trace SHA-256 equality, and canonical calendar identifier equality. Assert the frozen predicted-class sequence and Importance Only base budget against the matching frozen run. As an independent simulator sanity check, replay the frozen Importance Only base allocations on this same trace/calendar and require the delivery and cost fields to equal the existing Importance Only host CSV row. Preserve all failures; do not select or replace seeds.

## Analysis frozen before IMB execution

The statistical unit is a paired seed/run, never a packet or sample. For each of the four primary contrasts report `n=100`, both means, mean and median paired difference, sample SD, two-sided 95% Student-t CI of the paired mean (using the repository's existing `describe`/`confidence_interval` convention), wins/ties/losses, nonzero pairs, exact conditional two-sided Wilcoxon signed-rank p-value, and rank-biserial effect size. Treat differences within `1e-12` as ties, as in the existing code. Apply Holm correction across **only these four post-hoc primary contrasts**, a family separate from the original 12 hardware CRITICAL comparisons. All p-values and CIs are exploratory descriptors, not confirmatory evidence. Report CRITICAL delivery, cost, allocation, and 0%/5%/10% conditions descriptively without another significance family.

Generate two diagnostic plots under `results/posthoc_importance_matched_budget/plots/`: delivery-versus-loss-rate series and primary paired differences. Importance Only, if shown, must be labeled as a **cost-unmatched** reference. Do not alter paper Figures 1–6 or any frozen results.

## Interpretation and stopping rule

EventGuard versus IMB identifies a contrast with the same DATA-copy budget, predicted class information, trace, and canonical calendar. It tests placement against **this specific deterministic link-blind allocator** under software-injected `RANDOM_COPY`. Equal DATA-copy budget does not guarantee equal ACK or total-byte cost. If outcomes are equal, the previous EventGuard-versus-Importance-Only secondary gain is consistent with extra budget rather than demonstrated link-aware placement quality. If IMB is better, report that the frozen link-state allocation did not outperform this simpler matched-budget allocator. If EventGuard is better, report the paired effect, uncertainty, wins/ties/losses, and cost check without changing the manuscript. None of these outcomes validates RF channel prediction or general IoT reliability.

No hardware follow-up, firmware change, parameter tuning, replacement seed, or manuscript revision is part of this diagnostic. An implementation defect may be corrected only with a recorded bug and preserved failed attempt; results must not drive allocation-rule changes.
