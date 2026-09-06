# Two corpora, and a seal between them

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted and remain
> here because deleting a result one was wrong about destroys the record of having been
> wrong. The design, the method and the caveats stand; the numbers do not. Each figure's
> own reason and date are in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**Task F0.** The corpus described in [`CORPUS.md`](CORPUS.md) is now named `corpus_dev`, frozen,
and demoted: nothing measured on it may carry a public claim again. The corpus that will carry
them does not exist yet, and the point of this task is that it cannot be looked at before it does.

```bash
python research/invariants_lab/corpora.py     # both corpora, the seal, disk against budget
python tests/_test_corpus_freeze.py           # the freeze and both halves of the seal
```

---

## 1. Why the eight repositories stop being evidence

They were used twice. Defect classes D1–D7 were **found** by reading findings on those eight, and
every gate was then **scored** on the same eight. That is a development set doing its job — the
whole value of D1–D7 is that eight real repositories showed a checker eight things one repository
could not — and it is not evidence about a ninth repository.

The number that makes the concern concrete is already published: precision ranged **0.174 to
0.836** across the eight blocks, a factor of five, and `BLAST_RADIUS_D5.md` concluded *"whatever
governs precision is a property of the codebase, not of the checker."* If a codebase property
moves precision by 5×, then a checker tuned against eight known codebases has been tuned against
eight values of that property.

| | `corpus_dev` | `corpus_heldout` |
|---|---|---|
| repositories | 8, pinned | ≥ 25, none of the eight, ≥ 6 domains |
| role | find defects, tune, measure the cost of a change | measure once |
| status | **frozen 2026-08-28** | **sealed until Phase V** |
| positives | 859 in 345 independent source commits | ≥ 5,000 mutants over ≥ 2,000 source commits |
| tuning against it | legitimate, and expected | forbidden, and enforced |
| a number from it | internal; never published as a gate result | every published gate result |

## 2. The freeze

[`corpus_dev.json`](corpus_dev.json) pins all eight to a 40-character HEAD, with licence, slug,
contributor count, and the positives each contributes. The counts are not restated from memory —
`tests/_test_corpus_freeze.py` recomputes them from `mutants.json` and fails if the freeze drifts
from the answer key it claims to describe.

| repository | contributors | eligible commits | positives | source commits |
|---|---|---|---|---|
| `django/django` | 3,638 | 487 | 311 | 107 |
| `pytest-dev/pytest` | 1,254 | 202 | 157 | 76 |
| `fastapi/fastapi` | 941 | 13 | 11 | 5 |
| `sphinx-doc/sphinx` | 940 | 171 | 157 | 79 |
| `pallets/flask` | 898 | 24 | 14 | 13 |
| `scrapy/scrapy` | 850 | 153 | 144 | 37 |
| `psf/requests` | 841 | 9 | 6 | 3 |
| `encode/httpx` | 270 | 47 | 59 | 25 |
| **total** | | **1,106** | **859** | **345** |

`django` alone carries 36% of the positives and 31% of the source commits, which is why every
number in this project is reported per block as well as pooled. `eligible commits` is the census's
count; `corpus_manifest.json` recorded four of them one or seven higher at clone time, and that
value is kept in the freeze as `eligible_commits_manifest` rather than quietly dropped.

## 3. The seal, and why it is two locks rather than one

The failure this guards against is not somebody deciding to cheat. It is a measurement script
whose glob widens by one directory, reads the held-out clones during development, and converts a
held-out corpus into a second in-sample one — after which there is no third.

**Runtime.** `corpora.heldout_repos()` raises `HeldOutSealed` while
[`heldout_seal.json`](heldout_seal.json) is closed. It raises rather than returning an empty list
on purpose: an empty list is indistinguishable from *"the corpus is not built yet"*, and a caller
will happily carry on and report a number computed over nothing. An exception cannot be mistaken
for a measurement.

**Static.** No module in the lab outside `corpora.HELDOUT_READERS` may contain the strings
`corpus_heldout`, `HELDOUT_ROOT` or `heldout_repos` at all, so the runtime guard cannot be walked
around with a path literal. A Phase-H or Phase-V script joins that allowlist by being written and
named there, never by a glob widening underneath it.

Both locks run without the polygon on disk, so the suite is green on a fresh clone.

## 4. Naming, which is the part that actually decays

A helper called `corpus_repos()` reads whichever corpus it was pointed at, and a reader of the
call site cannot tell which. That ambiguity is the mechanism by which a held-out set quietly stops
being held out, so the ambiguous names were **removed rather than deprecated**:

- `corpusio.CORPUS_ROOT` and `corpusio.corpus_repos()` are gone. `corpusio` now reads git and
  knows nothing about where clones live.
- Every call site says `dev_repos()` — the census, the mutation generator, the facade shapes
  scan, the mutant verifier, and all five measurement harnesses.
- A test scans the lab for any surviving unqualified `corpus_repos` or `CORPUS_ROOT` and fails on
  one.

The seal opens in **H1**, after the code is frozen and `PREREGISTRATION-SHIP.md` is committed,
and the seal record stores the frozen hashes it was opened against.
