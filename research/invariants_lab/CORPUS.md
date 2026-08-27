# The corpus — chosen before it was cloned

**Task C1.** Written and committed *before* a single `git clone` ran, so the selection cannot be
read backwards from what the repositories turned out to contain. The manifest of what was
actually cloned — SHA, commit count, disk cost, measured signature-change rate — is appended
in a second commit, under [§4](#4-manifest-appended-after-cloning).

Skill used: `experimental-design`.

---

## 1. Why the previous corpus could not produce a result

`research/BLAST_RADIUS_PRECISION.md` measured precision **0.000 on 24 findings** over 150 commits
of this repository. That number is not a measurement of the checker. Three structural reasons,
each of which the design below is built to remove:

| defect in the old corpus | what it made impossible |
|---|---|
| one repository, effectively one author | the result is about a style, not about a class of code. No blocking factor, so repository effects and mechanism effects are the same effect |
| base rate of true breakages near zero | precision has no denominator that can move. With no true positive available, **any** firing scores 0 — mechanically, not empirically |
| no answer key independent of the mechanism | recall was defined against "the union of TRUE findings both arms found" — an empty set, so recall was structurally undefined |

The corpus is therefore the deliverable of Phase C, and no mechanism is measured until it exists.

## 2. The design, stated in the vocabulary that makes its errors visible

- **Unit of analysis:** one commit. The detector and the baseline both run on the *same* commit,
  so the two arms are **paired** — the analysis is McNemar / paired-proportion, not two
  independent samples.
- **Blocking factor:** the **repository**. Author style, typing density, deprecation discipline
  and module granularity are nuisance variation that would otherwise inflate the error term and,
  worse, be indistinguishable from the mechanism. Every headline number is reported **per
  repository as well as pooled**, and a pooled number that one repository dominates is reported
  as such.
- **Replication is at the commit level, and clustered.** A single source commit can contain
  several changed signatures. Mutating each of them yields correlated positives that are *not*
  independent replicates. This is pseudoreplication in its exact textbook form, and it is the
  §0 mistake wearing new clothes. Two rules follow, both binding on C2:
  1. **At most one mutant per source commit** enters the primary analysis.
  2. Where more than one is generated, intervals are **cluster-bootstrapped with the source
     commit as the cluster**, never treated as independent draws.
- **What is randomised:** which eligible source commits are mutated, and which are held out as
  negatives — a seeded draw, stratified by repository, so no repository's share of the positive
  class is decided by how convenient its history was to process.
- **What is deliberately *not* randomised:** the repositories themselves. Eight repositories are
  a purposive sample, not a random one, and the write-up says so. The claim a purposive sample
  can support is "across these eight kinds of Python codebase", never "across Python".

### The confound that selection has to defeat

If every repository were chosen for having *lots* of breaking changes, the corpus would be
enriched for the exact commits the detector is best at, and precision would be measured on a
population no user has. So the set is chosen to **span** API discipline, not to maximise it: two
repositories with a formal deprecation policy and a reputation for not breaking callers are in
the set precisely because they should be *hard*, and their per-block numbers are reported
separately rather than averaged away.

## 3. The eight repositories, and the property each contributes

Selection criteria applied to every candidate, all four required:

1. **Python-majority**, because every detector here is a Python-AST detector.
2. **Multi-author** — 50 or more distinct contributors — so no single person's idiom is the
   finding.
3. **Internal fan-in**: modules that call each other, not a flat script collection. A library
   whose functions are only called by users cannot supply a same-commit caller update.
4. **Permissive licence** (BSD / MIT / Apache-2.0). Nothing from the corpus is republished — only
   SHAs and counts — but a repository whose licence would make a derived artifact awkward is not
   worth the argument.

| # | repository | licence | the axis it contributes | why this project's own history lacks it |
|---|---|---|---|---|
| 1 | `pallets/flask` | BSD-3 | **strict deprecation discipline, small surface.** Expected to be a *hard* block: few genuine breakages, mostly internal | this project has no deprecation policy at all, so nothing tested the "correctly stayed silent" case against a repo that earns its silence |
| 2 | `psf/requests` | Apache-2.0 | **sparse typing, long history, frozen public API.** Annotation-only changes are rare here, so it is the control against which typed repos are read | this repo is annotated throughout; a detector tuned on it has never seen unannotated call sites |
| 3 | `pytest-dev/pytest` | MIT | **the highest internal fan-in in the set** — a plugin architecture where internal helpers have dozens of in-repo callers. The richest source of same-commit signature-plus-caller changes | 150 commits of a single package with shallow call graphs; the E4 seam was the only real fan-in event |
| 4 | `scrapy/scrapy` | BSD-3 | **component/middleware architecture and sustained internal churn** — renames and moves that really do break in-repo callers | the only move in this project was a facade-preserving extraction, which is why facades are the one shape I2 learned |
| 5 | `encode/httpx` | BSD-3 | **modern async, dense annotations, keyword-only signatures.** The direct stress test for D1 (widening) and D3 (annotation-only) | mixed sync/async and partial typing; the widened-signature class was 33% of findings and never had a repo built out of it |
| 6 | `fastapi/fastapi` | MIT | **extreme default-heavy and keyword-only signatures**; parameters are added constantly and almost always compatibly. A precision trap by construction | nothing here adds five defaulted parameters to a public signature in one commit |
| 7 | `django/django` | BSD-3 | **the most multi-author codebase in the set, with a written backwards-compatibility policy and a deprecation ladder.** Breakages here are *announced*, which gives an independent secondary answer key | no policy, no ladder, no independent record of what was intended to break |
| 8 | `sphinx-doc/sphinx` | BSD-2 | **large plugin API with a documented internal/external boundary**, and a history of moving internals between modules | the module-attribute and import-reaching-past shapes of D4 are exactly this repo's idiom, and were enumerated here from prose rather than from code |

Two of the eight (1, 6) are expected to be **negative-heavy** and two (3, 4) **positive-heavy**.
That spread is the point: a detector that scores well only on blocks 3 and 4 has been measured,
and the per-block table will say so.

### Rejected candidates, and why — so the selection is auditable

| candidate | rejected because |
|---|---|
| `Textualize/rich`, `psf/black` | effectively single-author for most of the relevant history; reintroduces the confound this corpus exists to remove |
| `numpy/numpy`, `pandas-dev/pandas` | the Python-C boundary dominates the interesting changes; the AST detectors cannot see across it, so a miss would be uninformative |
| `ansible/ansible` | roughly 2 GB of history for a module collection with shallow internal fan-in — the worst disk-to-signal ratio in the shortlist |
| `home-assistant/core` | enormous, and its integrations are near-independent leaf packages: high commit count, low cross-module dependency |
| this repository | it is the instrument's home. Kept as a **ninth, out-of-corpus reference block** only, never pooled |

### Disk

Budget is **40 GB**; `D:` had 773 GB free at C1. All eight are full-history clones — mutation
generation needs `git log -p` across the history, so `--depth` is not an option. The shortlist
estimate is well under 2 GB in total, and the manifest reports the true figure.

## 4. Manifest, appended after cloning

Written by `research/invariants_lab/corpus_census.py` from the clones; the machine-readable form
is `research/invariants_lab/corpus_manifest.json`, and the per-commit evidence behind the last
two columns is `research/invariants_lab/corpus_census.json`. The corpus itself is **not in git**
— it lives at `D:\Coding\_nevertwice_polygon\corpus` and is regenerable from the URLs and SHAs below.

**Census window, declared before the run and uniform across blocks:** the newest
4,000 commits per repository that touch a `.py` file, non-merge, excluding
vendored and generated trees, keeping commits that touch between 2 and 40 Python
files. Fewer than two files cannot contain a same-commit caller update; more than
40 is a bulk rewrite whose contract changes are not the thing being measured.

| repository | HEAD | commits | contributors | census candidates | changed a signature | **eligible** | rate | disk |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `django/django` | `fdfbb711e` | 34,889 | 3,638 | 2,512 | 626 | **85** | 0.034 | 356 MB |
| `encode/httpx` | `b5addb64f` | 1,523 | 270 | 612 | 287 | **47** | 0.077 | 12 MB |
| `fastapi/fastapi` | `490334715` | 7,695 | 941 | 555 | 170 | **12** | 0.022 | 94 MB |
| `pallets/flask` | `d318b6834` | 5,556 | 898 | 806 | 239 | **25** | 0.031 | 15 MB |
| `psf/requests` | `5460f467b` | 6,493 | 841 | 664 | 174 | **11** | 0.017 | 19 MB |
| `pytest-dev/pytest` | `fdba12e17` | 17,700 | 1,254 | 1,976 | 669 | **95** | 0.048 | 54 MB |
| `scrapy/scrapy` | `dcaa6ced5` | 11,417 | 850 | 1,829 | 654 | **86** | 0.047 | 41 MB |
| `sphinx-doc/sphinx` | `e44a40eb2` | 22,413 | 940 | 1,817 | 519 | **74** | 0.041 | 140 MB |
| **total** | | **107,686** | | **10,771** | **3,338** | **435** | | **732 MB** |

**Disk: 0.73 GB of a 40 GB budget.** Reported again at every phase boundary.

*Eligible* means the commit changed a callable's parameter list, or removed a symbol, **and**
updated a caller of that symbol in a **different file in the same commit**. That is the class C2
mutates: reverting the caller half produces a breakage whose answer key was written by the
repository's own maintainers.

### The selection's prediction held, which is the first thing worth checking

§3 predicted, before any clone, that blocks 1 and 6 would be negative-heavy and blocks 3 and 4
positive-heavy. Ranked by eligible rate the census gives:

| | repository | eligible rate | predicted |
|---|---|---|---|
| most breakage-rich | `encode/httpx` | 0.077 | positive-heavy was predicted for pytest/scrapy; httpx exceeding both is the one miss |
| | `pytest-dev/pytest` | 0.048 | **positive-heavy — held** |
| | `scrapy/scrapy` | 0.047 | **positive-heavy — held** |
| | `sphinx-doc/sphinx` | 0.041 | — |
| | `django/django` | 0.034 | — |
| | `pallets/flask` | 0.031 | **negative-heavy — held** |
| | `fastapi/fastapi` | 0.022 | **negative-heavy — held** |
| least | `psf/requests` | 0.017 | — |

Four of four directional predictions held; `httpx` was expected to contribute annotation density,
not the highest breakage rate, and its 0.077 is recorded as a miss rather than smoothed over.
The spread — a factor of **4.5** between the most and least breakage-rich block — is what the
per-repository reporting rule exists for: a pooled number that quietly averages `httpx` with
`requests` would describe no real codebase.

### What every criterion actually measured

- **Multi-author**: the smallest block has **270** contributors, the largest **3,638**. The
  requirement was 50. The single-author confound is gone by three to seventy times over.
- **Non-zero base rate**: **435** eligible source commits, against the previous
  corpus's **zero**. Whether 435 is *enough* is not a question this task may
  answer by inspection — it is C3's, and C3 runs before any mechanism is measured.
- **Internal fan-in**: 3,338 commits changed a signature; 435
  of them (13.0%) also updated an in-repo
  caller. The other 87.0% changed a
  contract nothing else in the repository calls — which is itself the population the *negative*
  class is drawn from, and it is large.

### The answer key does not come from the instrument

`research/invariants_lab/sigscan.py` re-implements signature extraction and reference finding
from scratch, and is used for the census and for C2. It does **not** import
`nevertwice/invariants/blast_radius.py`. If the key and the checker shared an extractor, D2's
tuple-unpacking defect would be a hole in both at once, and the measurement would confirm the
checker's blind spot rather than expose it. `sigscan` records tuple-unpacking targets from its
first line of code, precisely because the checker does not.
