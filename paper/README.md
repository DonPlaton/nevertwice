# `paper/` — manuscript for Zenodo → arXiv

[`anamnesis.md`](anamnesis.md) is the working manuscript (v0.2), assembled from the
measured studies in [`../anamnesis/research/`](../anamnesis/research/). Every empirical
claim in it regenerates from a script on a public dataset — see the *Reproducibility*
section of the paper.

## Status

- **v0.2 — Active Memory is now the headline.** The main contribution is the intervention
  layer (§5: guards / anticipation / counterfactual), the improvement-per-token benchmark, the
  live guard study, and the eff-vs-capability curve — with retrieval + substrate as the
  supporting foundation.
- **Draft complete:** abstract, system, mechanisms, **Active Memory (§5)**, evaluation
  (retrieval head-to-head + QA-accuracy + ablations), reproducibility, limitations.
- **Numbers are live:** `research/longmem_results.json`, `research/head_to_head.json`,
  `research/qa_results*.json`, `research/longitudinal_results.json`,
  `research/live_validation*.json` (+ figures `research/qa_accuracy.png`, `eff_curve.png`).
- **Not yet done (needs the author):** final author/affiliation block, dropping in the two
  figures, and the submission itself.

## Build a PDF (optional)

Markdown keeps the draft reviewable in-repo; convert when you want a PDF:

```bash
# plain PDF
pandoc paper/anamnesis.md -o anamnesis.pdf

# arXiv-style two-column LaTeX (then edit/submit the .tex)
pandoc paper/anamnesis.md -o anamnesis.tex
```

## Submission checklist

1. **Zenodo (do this first — no gatekeeper).** Zenodo issues a DOI immediately and has a
   GitHub integration: create a Zenodo account, link the `DonPlaton/anamnesis` repo, and
   cut a GitHub release — Zenodo archives the tagged source and mints a DOI. Update
   `CITATION.cff` with the DOI afterward. This gives a citable artifact regardless of arXiv.
2. **arXiv (needs an endorsement).** Category `cs.IR` (primary) or `cs.AI`. arXiv requires
   an endorsement for a first submission in a category; line that up before uploading. Use
   the `pandoc … .tex` output as the source, add the figures, and submit. Cross-list to
   `cs.LG` if you keep the bandit/calibration sections prominent.
3. **Cross-reference:** once the DOI/arXiv id exist, add them to `README.md` and
   `CITATION.cff` so the repo and the paper point at each other.

> The repo stays the source of truth; the paper is a snapshot with a citable identifier.
> Keep the anonymity rules in mind for any *non-paper* artifacts — the manuscript,
> `CITATION.cff`, `LICENSE`, and `README` are the only places the real name belongs.
