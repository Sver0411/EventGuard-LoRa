# IMB diagnostic engineering log

## Preflight attempt 1 — before any IMB result

The initial host-only runner exited before creating its output directory or executing any new baseline run. Its source-integrity gate compared `results/pre_hardware_v1/experiment_manifest.json` field `baseline_runs_sha256` to `runs.csv`. That manifest field actually fingerprints `runs.json` (as defined in the frozen research-analysis code); the CSV has a different valid SHA-256. The error was an implementation mistake in the new runner's provenance check, not a failed scientific run or an experimental result.

Observed initial preflight output:

```text
AssertionError: frozen v1 source or copy cap changed
```

The correction checks `baseline_runs_sha256` against the existing `runs.json` and records an independent SHA-256 for the `runs.csv` used by this diagnostic. A regression test covers the distinction. No allocator rule, hash ordering, budget, seed, loss rate, endpoint, protocol, frozen source file, or scientific result was changed. No IMB run was completed or discarded before this fix, so subsequent execution starts with all 500 conditions.

## Report-only clarification after the 500-run analysis

After inspecting the generated four-contrast table, the analysis report template was expanded to show EventGuard-minus-Importance-Only and IMB-minus-Importance-Only mean uplifts side by side and to print five decimal places for paired-mean confidence limits. This clarifies the budget-versus-placement question and avoids rounding a narrow negative bound to `-0.0000`. It changes no allocator behavior, run record, numerical estimator, significance family, comparison, seed, or frozen input. At that stage, the 500-run `runs.csv` remained byte-for-byte unchanged; only derived report formatting was regenerated.

## CSV serialization correction

The first completed 500-run attempt used Python `csv.DictWriter`'s default CRLF row terminator. The scientific rows and four diagnostic contrasts passed all checks, but `git diff --check` treats those CR bytes as trailing whitespace. This is an output-serialization bug, not a research discrepancy. The first attempt, including its run CSV, manifest, provenance, report, statistics, and plots, is retained as a compressed engineering archive in the new diagnostic result directory. The writer was changed to emit LF for both run and derived CSV files. The same frozen protocol, code path, seeds, calendar, budget, and allocation are then run again; parsed final run rows must match the archived attempt exactly. The rerun is a serialization correction and is not counted as an additional 500 scientific conditions.
