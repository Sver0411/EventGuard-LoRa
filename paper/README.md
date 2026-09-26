# Venue-neutral LaTeX manuscript

`main.tex` is a LaTeX conversion of the frozen manuscript text in
`../docs/paper_v1_submission_draft.md`. It uses the six existing vector PDF
figures copied from `../docs/figures/`; `references.bib` contains the same
12 external works cited by the Markdown manuscript. Author, affiliation,
and email are placeholders pending a submission venue and verified metadata.

## Build

From this directory, a standard TeX installation can run:

```sh
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

Alternatively:

```sh
latexmk -pdf main.tex
```

Tectonic is also supported when available:

```sh
tectonic --keep-logs main.tex
```

The expected output is `main.pdf`. Check the build log for undefined
citations/references and overfull boxes after any venue-template conversion.

## Source and scope

- Manuscript text source: `../docs/paper_v1_submission_draft.md`.
- Frozen final figures: `../docs/figures/`; this folder contains copies, not regenerated charts.
- Experimental results and provenance remain under `../results/`.

The LaTeX conversion does not modify frozen v1 results, algorithm, firmware,
raw logs, or the `v1.0.0` release. Numerical statements and limitations in
`main.tex` should be kept aligned with the Markdown manuscript when editing
for a venue.
