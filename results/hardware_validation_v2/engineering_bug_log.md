# Engineering Bug Log

## Stage 1 runner preparation (2026-09-26)

- **Issue:** The Stage 1 execution planner always placed `EVENTGUARD` first in each condition, which could couple strategy with time/order drift.
- **Fix:** Seed-shuffle all four strategies (`EVENTGUARD`, `IMPORTANCE_ONLY`, `UNIFORM_BUDGET`, `RANDOM_BUDGET`) within each model/rate/seed condition with execution-order seed `4170411`; save the generated order before the first run.
- **Issue:** The previous hardware runner learned equal DATA-copy budgets by running EventGuard on hardware first.
- **Fix:** Precompute all 40 budgets from the frozen host reference before any Stage 1 START; both budget baselines receive the exact condition-matched count.
- **Issue:** A `--skip-flash` Stage 1 could still depend on mutable `build/` binaries, and actual file hashes were not compared to the prior manifest.
- **Fix:** Stage 1 is blocked unless using the recovered `artifacts/final_stage1_firmware/` pair, and build/flash are prohibited. Exact file, image-parser, device-dump, board MAC, and smoke-manifest hashes are gated.
- **Issue:** A run interrupted after START could leave logs without a failed manifest, and a later invocation could rerun an incomplete condition.
- **Fix:** Persist `started` provenance before issuing START; on exceptions save partial raw logs and a failed manifest; Stage 1 preflight rejects any failed/incomplete attempt.
- **Issue:** Per-sample manifests omitted ground truth, predicted importance, copy-level physical/injected DATA and ACK outcomes, and the final link state.
- **Fix:** Include those fields and compare both before- and after-sample link states against the frozen host reference.
- **Issue:** The general research analysis hash guard rejected the smoke-validated diagnostic-only insertions in `firmware/main/main.c`, although all frozen source lines remained intact.
- **Fix:** Allow only the explicitly marked additive diagnostic extension when the frozen baseline lines remain present in order; all Python policy, trace/fault code, and frozen C policy files remain exact-hash gated.

No firmware source, EventGuard strategy, importance classifier, link estimator, trace, fault calendar, configuration, or evaluation seed was changed.
