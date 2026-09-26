# Stage 1 confirmatory hardware experiment report

## Outcome

Stage 1 stopped after 0 complete runs and 1 failed STARTed attempt(s); 159 of 160 planned runs were not attempted.
The preregistered stop rule was applied. No retry or later condition was run after the failed attempt.
Smoke gate before Stage 1: 12/12 (PASS). Stage 1 preflight: PASS.
Treatment-level statistics, equal-budget comparisons, and Pareto analysis are unavailable because no condition has a complete paired seed set.

## Frozen provenance

- Firmware recovery path: B (device image recovery; exact canonical image hashes matched the smoke pair).
- Sensor SHA256: `b3cecd078351cc6fe659b0087db05a67a4ca35ab610125a0f0381887281221b4`
- Gateway SHA256: `6f69dffed65e2636b371401db45a483f951189e029b538c2f587d331428936ac`
- Algorithm/config/trace/fault hashes were checked at preflight; no firmware or frozen core was changed.
- Stage 1 outputs are isolated in this directory; the prior v2 smoke reports and raw data were not overwritten.

## Failed attempt diagnostic

- Run: `uniform_budget_random_copy_20_seed31` (UNIFORM_BUDGET, RANDOM_COPY 20%, seed 31).
- Failure: uniform_budget_random_copy_20_seed31: firmware END timeout.
- Sensor DATA TX records: 77; gateway post-injection RX: 56; planned DATA drops logged: 20; physical DATA before injection: 76.
- Uncontrolled physical DATA missing: 1; uncontrolled physical ACK missing: 0.
- Missing copy: sample 25 copy 1; planned DATA drop=False.
- Gateway ACK TX / Sensor physical ACK RX: 56 / 56; logged ACK timeouts: 20.
- CRC/UART/parser error lines captured: 0; loss-calendar mismatches: 0.
- Sensor open ACK wait at last log: `{"sample_id": 25, "copy_index": 1}`. No subsequent ACK or TIMEOUT record for that copy was captured before the runner's firmware END timeout.
- Sensor TX-path markers for the missing copy: `D_TX_BEGIN,25,1,25,64450630; D_TX_UART_DONE,25,1,25,64477915; D_TX_AUX_READY,25,1,25,64481057`.
- Matching Gateway RX markers: `none`.
The evidence places the unexplained disappearance after the Sensor firmware logged UART completion/AUX-ready and before any matching Gateway parser receive marker. It does not distinguish E220 buffering, RF, or the Gateway receive path; this report does not label it an RF loss.
No arbitrary delay was introduced. No parser/firmware change was made during this Stage 1 attempt.

## Research questions

- EventGuard vs Importance Only: not evaluated; no complete Stage 1 pair.
- EventGuard vs Uniform Budget: not evaluated; no complete Stage 1 pair.
- EventGuard vs Random Budget: not evaluated; no complete Stage 1 pair.
- RANDOM_COPY 20%/30% and BURST_SAMPLE 20%/30%: no treatment conclusion; the first attempted condition (UNIFORM_BUDGET, RANDOM_COPY 20%, seed 31) failed.
- Pareto status: unavailable; no completed matched condition.
- Link adaptation usefulness: UNKNOWN from this Stage 1 attempt.
- Host/firmware agreement: full sample-level agreement is unavailable for this truncated run; one unplanned TX-to-Gateway observation gap is confirmed from the retained logs.

## Decision

- Hardware runs completed: 0 / 160 PASS.
- Failed runs: 1; not attempted: 159.
- Uncontrolled physical anomalies: 1 DATA copy; 0 observed missing ACK frames; 0 captured CRC/parser errors.
- Simulation/firmware divergences: full-run parity cannot be scored; one transport-level DATA observation divergence at sample 25 copy 1.
- Need full 400 runs: NO. Stage 1 did not pass; do not expand the matrix.
- Recommended next action: retain this attempt and perform separately authorized receive-path engineering diagnosis before any new confirmatory run. The Stage 1 matrix remains stopped.

This is an incomplete engineering feasibility observation, not a strategy effect estimate. No reliability, cost, or Pareto claim is made from this attempt.
