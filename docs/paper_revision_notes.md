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

## Figures requiring publication redraw

1. **Figure 1, system architecture:** replace the ASCII schematic with a legible diagram showing trace replay, classifier, copy policy, Sensor ESP32-S3, E220 DATA path, Gateway CRC/deduplication, post-reception DATA/ACK injection points, ACK return path, and first-copy accepted-ACK update. Separate programmed erasures from uncontrolled RF anomalies.
2. **Figure 2, decision matrix:** render the existing 3×3 map as a compact vector table or heatmap and visually emphasize the CRITICAL 3/3/3 row. Keep the exact frozen values.
3. **Figure 3, exact-budget host comparison:** redraw archived host results with condition-level means and uncertainty or paired summaries to reduce overplotting. Keep budgets matched per seed; do not present new experimental data.
4. **Figure 4, ablation:** redraw from preserved host results to include IMPORTANCE_ONLY, LINK_ONLY, and EventGuard, with both critical delivery and DATA-copy cost. FIXED_2 may remain as a reference. The current archived figure omits LINK_ONLY and lacks a cost panel.
5. **Figure 5, device Pareto:** replot the preserved 96-run condition means at publication scale with readable labels and, if appropriate, seed-level uncertainty. Show copies and optionally bytes; do not imply frontier membership is an inferential test.
6. **Figure 6, sensitivity:** use preserved host-only results to show link-window and copy-cap effects clearly, with delivery and cost where available. The current linked image shows link-window sensitivity only; do not label it as a cap-effect plot before redrawing.

The manuscript captions describe what the *currently linked* figures actually show. Any redraw must use only the archived underlying results and be checked against the text and tables.

## Verification checklist before submission

- Confirm reported run counts, strategy names, paired seeds, and Table 4 against preserved manifests and analysis files.
- Confirm no manuscript wording treats the 96-run prefix as a preregistered sample or software-injected loss as measured RF PER.
- Confirm the unresolved run103 and IMPORTANCE_ONLY cost advantage remain prominent.
- Validate all figure and repository links after venue-format conversion; provide archival release identifiers in the final submission package.
- Obtain independent field-labeled and measured-channel evidence before making claims beyond the present study.
