# Which corpus did Phase V actually measure? Three artifacts say the wrong one

**Task V, before any analysis.** The unattended chain finished at 04:53 on 2026-08-29 and left
eleven artifacts. Three of them call themselves in-sample while sitting under a `*_heldout.json`
name. Before a single number is interpreted, this page establishes what each artifact measured —
**from the artifact's own contents, never from its label.**

```bash
python research/invariants_lab/provenance_audit.py
python research/invariants_lab/provenance_audit.py --print
```

Artifact: `provenance_v.json`.

---

## The verdict

**Ten of eleven artifacts are decided, and every decided one is held-out.** The three labels are
wrong; the data behind them is not.

| artifact | label | derived from data | route |
|---|---|---|---|
| `abstention_f1_heldout.json` | `corpus_dev` | **heldout** | 28 held-out block names, 0 development |
| `blast_radius_d5_heldout.json` | — | heldout | 29 block names |
| `corpus_census_heldout.json` | — | heldout | 33 block names |
| `mutant_controls_heldout.json` | — | heldout | 29 block names |
| `mutants_heldout.json` | — | heldout | 32 block names |
| `power_ship_heldout.json` | `heldout` | **undecided** | a derived gate table; carries no corpus-bearing data |
| `quadratic_f4_heldout.json` | `corpus_dev` | **heldout** | 28 held-out block names, 0 development |
| `ratchet_r3_heldout.json` | — | heldout | 28 block names |
| `scale_x4_heldout.json` | — | heldout | census differs from the development run |
| `surface_f2_heldout.json` | `corpus_dev` | **heldout** | `n_mutants` 6,755 within 5% of held-out's 6,868 |
| `together_t1_heldout.json` | — | heldout | 28 block names |

## Why the label is wrong and the data is not

`measure_abstention.py` accepts `--corpus`, and line 388 calls `_select_corpus(args.corpus)`,
which **rebinds the corpus paths before anything runs**. That is the mechanism H1 froze precisely
so Phase V could run identical code against a different corpus without editing a file.

Lines 329–330 then build the output dict with two literals:

```python
"corpus": "corpus_dev",
"in_sample": True,
```

They are constants, not readings of `args.corpus`. Line 347 prints `IN SAMPLE` the same way. So
the provenance string was never a measurement — it was a sentence written before the corpus
existed and never revisited. `measure_surface.py` and `mine_quadratics.py` carry the same three
lines; `power_ship.py` does not, which is why it alone reports `heldout` correctly and why the
artifacts disagree with each other.

**This is the seventh time in this project an instrument has reported a constant where a measured
fact belonged**, and the fourth time under the heading *ungraded is not the same as correct*. It
is the cheapest failure mode in the catalogue and the hardest to see, because a constant is always
well-formed.

## The three routes, and why more than one was needed

**1 · Block names.** `corpus_manifest.json` names eight repositories and `heldout_manifest.json`
names thirty; the audit checks and reports that **the intersection is empty**. Any artifact whose
raw text carries repository slugs is therefore decided outright. Eight artifacts fall here, each
carrying 28–33 held-out names and **zero** development names — `numpy_numpy`, `pandas-dev_pandas`,
`scipy_scipy`, `huggingface_transformers`, `python_mypy` and the rest, none of which exists in the
development corpus at all.

**2 · Census shape.** `surface_f2_heldout.json` carries no block names but does carry
`n_mutants = 6,755` and `n_clusters = 2,595`. The development answer key holds 859 mutants over
345 source commits and the held-out one holds 6,868 over 2,623. There is no confusing the two.

**3 · Exclusion, and it is labelled the weakest.** `scale_x4_heldout.json` has neither. It does
carry a silence census, and the development run's own artifact records the same fields:

| | held-out artifact | development artifact |
|---|---|---|
| commits considered | 834 | 738 |
| no axis present | 166 | 262 |
| fired | 153 | 73 |

Different data. That **falsifies** the development corpus rather than identifying the held-out
one, and it is only conclusive because `heldout_seal.json` admits exactly two corpora. The audit
says so in the route string rather than presenting elimination as recognition.

**`power_ship_heldout.json` is left undecided, on purpose.** It is H4's gate-resolution table:
its content is which thresholds the corpus can resolve, and it carries no repository name and no
count that distinguishes one corpus from the other. Its own label says `heldout` and its input —
`corpus_census_heldout.json` — is decided by route 1. Neither of those is evidence *from this
artifact*, so it is reported as undecided instead of quietly inheriting. Nothing in V1 or V2 rests
on it alone.

## What was deliberately not done

**The frozen modules were not edited.** H1 hashed 29 files, and `PREREGISTRATION-SHIP.md` §1 says
amending one after held-out numbers have been seen is a deviation rather than a correction — the
freeze is the only reason these numbers mean anything. A one-character fix to a label would be
indistinguishable, in the hash, from a fix to a threshold.

So the correction lives here, with its evidence, and the literal stays wrong in the frozen source
until Phase V is fully written up. **When it is fixed, it should be fixed by deriving the string
from `args.corpus`**, so the field becomes a reading rather than a claim, and by adding the
assertion that would have caught it: an artifact written to `*_heldout.json` must not describe
itself as in-sample.

## What this means for the numbers

Every Phase V figure is **out of sample**. `GOAL-SHIP.md` §0.3 named the problem this run existed
to fix — *every published number is in sample* — and as of this audit that sentence is false for
the first time in the project's history. The labels were wrong in the harmless direction: they
under-claimed. Had they pointed the other way, three held-out results would have been read as
development results and the whole run would have been worthless.

That asymmetry is luck, not design, and the assertion named above is what turns it into design.
