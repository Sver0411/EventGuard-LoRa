# Final manuscript audit

Scope: `paper/main.tex` is a venue-neutral conversion of
`docs/paper_v1_submission_draft.md`. This audit checks presentation and claim
consistency; it does not revise the algorithm, experimental records, or results.

## A. Research question

**Clear.** The introduction separates three questions: exact-budget value of
event-aware allocation, incremental value of first-copy ACK link adaptation,
the affected delivery KPI and its cost, and dependence on independent-copy
versus sample-wide programmed erasure.

## B. Contribution and principal conclusion

**Matched to the evidence.** The main defensible contribution is an exact-budget
allocation analysis with a KPI-dependent component ablation. EventGuard and
Importance Only delivered the same CRITICAL events in all 24 selected hardware
seed/condition pairs, while EventGuard used more DATA copies and bytes. Under
`RANDOM_COPY`, EventGuard showed higher IMPORTANT and overall delivery at that
added cost. The paper does not present the combined strategy as generally superior.

## C. Baseline fairness

**Clearly separated.** Uniform Budget and Random Budget match EventGuard's
DATA-copy budget for each condition and seed, without event-label inputs.
Importance Only, Link Only, and fixed-copy strategies are component or cost
references; their comparisons are explicitly identified as non-equal-cost.
Equal DATA copies do not imply equal total bytes because ACK activity differs.

## D. Negative result and design interpretation

**Prominent.** The frozen CRITICAL row is 3/3/3 for GOOD/DEGRADED/BAD under a
three-copy cap. Correctly classified CRITICAL samples therefore have no
remaining link-adaptive copy-allocation action. The primary KPI measures
critical-event delivery, while remaining link-dependent actions mainly affect
NORMAL and IMPORTANT traffic. Frozen host and selected hardware `RANDOM_COPY`
data show higher IMPORTANT and overall delivery for EventGuard at added cost.
This KPI/action-space distinction does not establish an RF-channel gain.

## E. Statistics

**Appropriately exploratory.** A paired seed/run is the unit, with six hardware
seeds per condition. Differences no larger than `1e-12` are zero; zero pairs
are omitted from signed-rank ranking; tied nonzero absolute differences use
midranks; exact sign enumeration gives the conditional two-sided p-value;
all-tie tests report p=1 and rank-biserial effect size zero. Holm correction
covers the 12 critical-delivery contrasts. The manuscript disclaims
confirmatory significance claims because the six-seed subset was chosen after
interruption.

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
shows CRITICAL, IMPORTANT, overall, and DATA-copy outcomes from frozen host runs. Five LaTeX
tables correspond to Markdown Tables 1–5. All 16 Table 4 data rows, including
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

## J. Largest reviewer risks

1. **Why test link adaptation if CRITICAL is 3/3/3?** It was part of the
   original combined-policy motivation. The controlled ablation reveals that
   the frozen policy gives it no copy-allocation action for correctly
   classified CRITICAL samples. The resulting CRITICAL equality is expected,
   while IMPORTANT and overall outcomes reveal remaining policy action.
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
   under `RANDOM_COPY`, so Importance Only is not universally better. The
   unconfirmed run103 ACK receive-path stall also limits broader hardware
   reliability claims beyond the selected clean prefix.

## Verdict

No new numerical inconsistency, citation mismatch, major methodological flaw,
or unqualified claim overreach was found after the KPI-dependent revision. The existing
design and scope limitations above remain material for submission review.
Venue-specific author metadata, bibliography style, final layout, and external
replication remain pending.

## KPI and scope answers after narrative revision

- **Critical KPI:** Link adaptation had no incremental CRITICAL delivery because
  correctly classified CRITICAL samples already received the three-copy cap
  in every link state. Equality is expected from frozen action-space saturation.
- **Secondary KPI:** Link adaptation was not entirely without effect. Under
  `RANDOM_COPY`, higher IMPORTANT and overall delivery was observed for
  EventGuard in the host and selected hardware results, at higher copy/byte cost.
- **Generalization:** These application-layer loss results do not demonstrate
  that link adaptation improves delivery under actual RF fading, interference,
  or range variation.
- **Cost:** Importance Only is more cost-efficient for the evaluated CRITICAL-
  delivery objective. It is not universally superior because EventGuard can
  improve IMPORTANT and overall delivery under `RANDOM_COPY`.
