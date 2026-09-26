# Final manuscript audit

Scope: `paper/main.tex` is a venue-neutral conversion of
`docs/paper_v1_submission_draft.md`. This audit checks presentation and claim
consistency; it does not revise the algorithm, experimental records, or results.

## A. Research question

**Clear.** The introduction separates importance allocation, transmission budget,
link-state-aware placement at matched budget, and programmed loss structure.
The original EventGuard-versus-Importance-Only comparison is identified as
budget-confounded.

## B. Contribution and principal conclusion

**Matched to the evidence.** The main defensible contribution is an exact-budget
allocation analysis with a structural CRITICAL ablation and a separate
post-hoc matched-budget host diagnostic. EventGuard and Importance Only
delivered the same CRITICAL events in all 24 selected hardware seed/condition
pairs. The higher IMPORTANT/overall delivery versus lower-budget Importance
Only cannot be attributed to link-state placement alone: IMB reproduces most
of it without link information at EventGuard's exact DATA-copy budget.

## C. Baseline fairness

**Clearly separated.** Uniform Budget and Random Budget match EventGuard's
DATA-copy budget for each condition and seed, without event-label inputs.
Importance Only, Link Only, and fixed-copy strategies are component or cost
references; their comparisons are explicitly identified as non-equal-cost.
The post-hoc IMB baseline also matches EventGuard's DATA-copy budget and
predicted importance while omitting link/ACK history. It is an offline
counterfactual, not an original frozen-v1 strategy. Equal DATA copies do not
imply equal total bytes because ACK activity differs.

## D. Negative result and design interpretation

**Prominent.** The frozen CRITICAL row is 3/3/3 for GOOD/DEGRADED/BAD under a
three-copy cap. Correctly classified CRITICAL samples therefore have no
remaining link-adaptive copy-allocation action. The primary KPI measures
critical-event delivery, while remaining link-dependent actions mainly affect
NORMAL and IMPORTANT traffic. Frozen host and selected hardware `RANDOM_COPY` data show higher IMPORTANT
and overall delivery for EventGuard than the lower-budget Importance Only
rule. The host-only IMB diagnostic retains most of that uplift without link
state and shows no consistent incremental placement advantage for EventGuard
over this specific baseline. This does not establish an RF-channel result.

## E. Statistics

**Appropriately exploratory.** A paired seed/run is the unit, with six hardware
seeds per condition. Differences no larger than `1e-12` are zero; zero pairs
are omitted from signed-rank ranking; tied nonzero absolute differences use
midranks; exact sign enumeration gives the conditional two-sided p-value;
all-tie tests report p=1 and rank-biserial effect size zero. Holm correction
covers the original family of 12 hardware CRITICAL-delivery contrasts. The
separate post-hoc IMB family has four exploratory IMPORTANT/overall contrasts
at RC20/30 and its own Holm correction. Neither family supports confirmatory
significance claims; the hardware subset has only six selected seeds and IMB
was designed after frozen-v1 inspection.

## F. Hardware and channel claims

**Properly bounded.** Real ESP32-S3/E220 devices executed DATA/ACK frames and
the frozen firmware logic. Configured 20%/30% losses were deterministic
post-reception application-layer erasures, not measured RF packet-error rates,
fading, or interference. The E220 path is not a LoRaWAN implementation. UART
serialization time is a proxy, not measured RF airtime; no Joule energy or
field classifier accuracy was measured.

## G. Figures and tables

**Consistent.** Six vector PDF figures are included without changing
their underlying data. Figure 3 identifies host-only, software-injected
`RANDOM_COPY`, 100 paired seeds, and exact budget matching. Figure 5 identifies
six seed-level points per strategy/condition, mean markers, the post-hoc prefix,
descriptive Pareto status, and the absence of RF-PER inference. Figure 6 is
host-only, uses 100 seeds, and contains copy caps 2 and 3 only. Figure 4
shows CRITICAL, IMPORTANT, overall, and DATA-copy outcomes from frozen host runs.
Six LaTeX tables correspond to Markdown Tables 1–6. Table 6 reproduces the
four primary IMB contrasts from preserved `paired_tests.csv`. All 16 Table 4 data rows, including
every displayed number, match the Markdown source exactly.

## H. References

**Complete for the current manuscript.** The 12 cited works have 12 matching
BibTeX entries and no uncited entries. DOI metadata for the ten DOI-bearing
works was checked against publisher-deposited Crossref records; the LoRa
Alliance specification and HotNets paper were checked against their official
pages. The final venue may require a different bibliography style, but no
extra literature was inserted during conversion.

## I. Reproducibility and provenance

**Traceable within the stated scope.** The manuscript retains the frozen
`v1.0.0` release, 8,000 host runs, 11,000 sensitivity runs, 12-run hardware
smoke, planned 160-run Stage 1 matrix, final balanced 96-run prefix, seeds
31–36, 96/96 offline audit, raw logs, manifests, and scripts. The main text
limits repository paths to Data and Code Availability; `paper/README.md`
provides build and source-of-truth guidance. The later partial seed-37 attempt
and unresolved run103 remain disclosed and excluded from paired results.
The separately committed IMB protocol predates its 500 new host outcomes; the
post-hoc run CSV, paired statistics, report, and provenance remain outside the
original frozen `v1.0.0` matrix.

## J. Largest reviewer risks

1. **Why test link adaptation if CRITICAL is 3/3/3?** It was part of the
   original combined-policy motivation. The controlled ablation reveals that
   the frozen policy gives it no copy-allocation action for correctly
   classified CRITICAL samples. The resulting CRITICAL equality is expected,
   while IMPORTANT and overall outcomes reveal remaining policy action. IMB
   checks whether its observed benefit exceeds what a link-blind allocator
   can obtain with the same extra budget.
2. **Why only six hardware seeds?** The 160-run plan was interrupted. Seeds
   31–36 are the earliest contiguous complete balanced prefix, selected by
   execution order, completeness, and auditability, not outcome. The
   post-hoc `n=6` analysis is descriptive, not a preregistered six-seed study.
3. **Why call this a hardware study?** Real ESP32-S3/E220 devices executed the
   DATA/ACK path, firmware policy, parsing, and injected calendar. This
   validates device-path execution and host/firmware parity; it is not a
   measured, varying-RF-channel performance study.
4. **Are synthetic traces sufficient?** They provide controlled, repeatable
   events for mechanism isolation. They do not validate the classifier on
   field-labeled events or cover the diversity of deployed sensing workloads.
5. **Why is Importance Only more efficient for the CRITICAL objective?** It did
   not have higher observed CRITICAL delivery; it had the same delivery at lower
   communication cost. EventGuard had higher IMPORTANT and overall delivery
   than the lower-budget Importance Only rule under `RANDOM_COPY`; IMB retains
   most of that uplift without link state, so neither strategy is universally
   better. The
   unconfirmed run103 ACK receive-path stall also limits broader hardware
   reliability claims beyond the selected clean prefix.

## Verdict

No new numerical inconsistency, citation mismatch, major methodological flaw,
or unqualified claim overreach was found after the IMB integration. The existing
design and scope limitations above remain material for submission review.
Venue-specific author metadata, bibliography style, final layout, and external
replication remain pending.

## Reviewer questions on the post-hoc IMB integration

1. **Did IMPORTANT/overall gains prove link adaptation benefit?** No. The
   original EventGuard-versus-Importance-Only comparison changed both link
   response and DATA-copy budget, so it was confounded.
2. **What does IMB show?** At the same EventGuard DATA-copy budget and with
   the same predicted importance, a link-blind IMB allocator reproduced most
   of the earlier IMPORTANT/overall uplift in 500 host-only `RANDOM_COPY`
   runs. Four primary contrasts were mixed and near zero.
3. **Does this prove link-state adaptation is useless?** No. It shows no
   consistent incremental advantage over this particular deterministic,
   post-hoc, offline link-blind allocator under software-injected loss. It
   does not generalize to every allocator or a measured RF channel.
4. **Was IMB preregistered?** No. Its protocol was committed after inspection
   of frozen v1 results but before IMB outcomes were generated. It is not
   part of the original 8,000-run matrix or the `v1.0.0` release.
