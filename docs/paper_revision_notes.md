# EventGuard-LoRa Manuscript Revision Notes

These notes are editorial planning material for `paper_v1_submission_draft.md`, not part of the manuscript or the experimental record. The v1 data, policy, firmware, and statistical outputs were not changed during the prose revision.

## Removed internal notes and submission tasks

- Add venue-specific author names, affiliations, contact details, acknowledgments, and any required disclosure statements after the venue is selected. The repository citation identifies `Sver0411`; do not infer a legal name or institution.
- Apply the selected venue's reference style, word limit, figure format, accessibility requirements, and supplementary-material policy. Bibliographic details for the former four references were checked against publisher or institutional publication records, and Related Work now cites 12 external sources.
- Preserve the key contribution as an exact-budget allocation analysis alongside the negative link-adaptation ablation. Do not strengthen claims of general EventGuard superiority, RF packet-error performance, LoRaWAN operation, field classifier accuracy, energy savings, preregistered `n=6` inference, or resolution of run103.
- Explain near reproduction instructions that `configs/default.json` contains older pilot-oriented matrix defaults; the frozen study manifests and recorded calls define the reported experiment.

## Reviewer risks and evidence boundaries

- The 96-run device set is a post-hoc six-seed complete prefix after interruption, from one Sensor/Gateway pair and software-injected loss. It supports device-path parity, not real-channel robustness.
- The CRITICAL row of the frozen decision matrix is 3/3/3 under the three-copy cap. Reviewers may ask whether link adaptation was given meaningful control over the primary KPI. Section 8 now states the action-space limitation explicitly.
- The classifier and event labels were evaluated on synthetic traces. Four measured sensor modalities are replayed as trace values during benchmark runs; no field-labeled classifier validation is available.
- The later run103 ACK receive-path stall remains unresolved. Its raw evidence is preserved and it is excluded from the complete-prefix paired analysis.
- UART serialization time is a proxy, not measured RF airtime. There is no measured energy, RF PER, multi-node contention, multi-gateway diversity, or duty-cycle result.
- The signed-rank p-values are exploratory/descriptive for `n=6`. A target venue may prefer to foreground paired differences and interval estimates even more prominently.

## Publication figures

Figures 1–6 are complete in `docs/figures/` as vector PDF, text-preserving SVG, and 300-dpi PNG files. Regenerate them from frozen sources with `.venv/bin/python tools/generate_paper_figures.py`; the script checks the numerical inputs and Table 4 before writing figures. Figure 5 displays the six preserved seed-level runs behind each condition mean, and Figure 6 connects only the evaluated copy caps 2 and 3. The figures are descriptive and do not add experimental observations.

Remaining submission work depends on the selected venue: add verified author and affiliation details, apply its manuscript and figure formatting rules, check bibliography style and bibliographic details, proof the final layout and accessibility, decide on supplementary materials, and validate figure, repository, and archival-release links in the submitted package.

## Verification checklist before submission

- Confirm reported run counts, strategy names, paired seeds, and Table 4 against preserved manifests and analysis files.
- Confirm no manuscript wording treats the 96-run prefix as a preregistered sample or software-injected loss as measured RF PER.
- Confirm the unresolved run103 and IMPORTANCE_ONLY cost advantage remain prominent.
- Validate all figure and repository links after venue-format conversion; provide archival release identifiers in the final submission package.
- Obtain independent field-labeled and measured-channel evidence before making claims beyond the present study.
