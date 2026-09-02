# The corpus — chosen before it was cloned

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted in 2026-08
> and remain here because deleting a result one was wrong about destroys the record of
> having been wrong. The design, the method and the caveats stand; the numbers do not.
> Each figure's own reason is in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

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
is `research/invariants_lab/corpus_manifest.json`, and the per-commit evidence behind the last two
columns is `research/invariants_lab/corpus_census.json`. The corpus itself is **not in git** — it
lives at `D:\Coding\_nevertwice_polygon\corpus` and is regenerable from the URLs and SHAs below.

**Census window: the entire history of every repository.** Non-merge commits touching a `.py`
file, excluding vendored and generated trees, keeping commits that touch between 2 and
40 Python files. Fewer than two files cannot contain a same-commit caller update;
more than 40 is a bulk rewrite whose contract changes are not the thing being
measured. The window was originally 4,000 commits per repository; C3 found three underpowered
cells and enlarged it, and [`POWER.md`](POWER.md) records both states so the enlargement is
auditable rather than taken on trust.

| repository | HEAD | commits | contributors | census candidates | changed a signature | **eligible** | rate | disk |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `django/django` | `fdfbb711e` | 34,889 | 3,638 | 12,775 | 3,275 | **490** | 0.038 | 356 MB |
| `encode/httpx` | `b5addb64f` | 1,523 | 270 | 612 | 286 | **47** | 0.077 | 12 MB |
| `fastapi/fastapi` | `490334715` | 7,695 | 941 | 555 | 166 | **13** | 0.023 | 94 MB |
| `pallets/flask` | `d318b6834` | 5,556 | 898 | 806 | 236 | **24** | 0.030 | 15 MB |
| `psf/requests` | `5460f467b` | 6,493 | 841 | 664 | 168 | **10** | 0.015 | 19 MB |
| `pytest-dev/pytest` | `fdba12e17` | 17,700 | 1,254 | 4,432 | 1,518 | **209** | 0.047 | 54 MB |
| `scrapy/scrapy` | `dcaa6ced5` | 11,417 | 850 | 2,946 | 1,155 | **154** | 0.052 | 41 MB |
| `sphinx-doc/sphinx` | `e44a40eb2` | 22,413 | 940 | 4,578 | 1,219 | **171** | 0.037 | 140 MB |
| **total** | | **107,686** | | **27,368** | **8,023** | **1,118** | | **732 MB** |

**Disk: 0.73 GB of a 40 GB budget.** Reported again at every phase boundary.

*Eligible* means the commit changed a callable's parameter list, or removed a symbol, **and**
updated a caller of that symbol in a **different file in the same commit**. That is the class C2
mutates: reverting the caller half produces a breakage whose answer key was written by the
repository's own maintainers.

### The selection's prediction held, which is the first thing worth checking

§3 predicted, before any clone, that blocks 1 and 6 would be negative-heavy and blocks 3 and 4
positive-heavy. Ranked by eligible rate the census gives:

| repository | eligible rate | predicted |
|---|---:|---|
| `encode/httpx` | 0.077 | selected for annotation density, not breakage rate — the one miss |
| `scrapy/scrapy` | 0.052 | **positive-heavy — held** |
| `pytest-dev/pytest` | 0.047 | **positive-heavy — held** |
| `django/django` | 0.038 | — |
| `sphinx-doc/sphinx` | 0.037 | — |
| `pallets/flask` | 0.030 | **negative-heavy — held** |
| `fastapi/fastapi` | 0.023 | **negative-heavy — held** |
| `psf/requests` | 0.015 | — |

Four of four directional predictions held. `httpx` was expected to contribute annotation density,
not the highest breakage rate, and its 0.077 is recorded as a miss rather
than smoothed over. The **5.1×** spread
between the extreme blocks is what the per-repository reporting rule exists for: a pooled number
averaging `httpx` with `requests` would describe no real codebase.

### What the census could not read

| repository | parse failures | sources read | rate |
|---|---:|---:|---:|
| `pytest-dev/pytest` | 2,654 | 32,616 | 8.14% |
| `django/django` | 5,534 | 90,942 | 6.09% |
| `sphinx-doc/sphinx` | 1,510 | 35,788 | 4.22% |
| `pallets/flask` | 207 | 4,994 | 4.14% |
| `scrapy/scrapy` | 982 | 23,878 | 4.11% |
| `psf/requests` | 67 | 4,030 | 1.66% |
| `encode/httpx` | 1 | 5,496 | 0.02% |
| `fastapi/fastapi` | 0 | 4,004 | 0.00% |
| **total** | **10,955** | **201,748** | **5.43%** |

Reaching the whole history means reaching Python 2, which `ast.parse` under Python 3.14 refuses.
Counted rather than swallowed, because a census that silently returns "no definitions" for an
unreadable file makes an unreadable history look like a clean one. **The corpus is the
Python-3-parsable part of these histories**, and any claim about "the whole history" is a claim
about the part a modern parser can read.

### What every criterion actually measured

- **Multi-author**: the smallest block has **270**
  contributors, the largest **3,638**. The requirement was
  50. The single-author confound is gone by between five and seventy times over.
- **Non-zero base rate**: **1,118** eligible source commits against the previous
  corpus's **zero**. Whether that is *enough* is not a question this task answers by inspection —
  it is C3's, and C3 ran before any mechanism was measured.
- **Internal fan-in**: 8,023 commits changed a signature;
  1,118 of them
  (13.9%) also updated an in-repo caller.
  The other 86.1% changed a contract
  nothing else in the repository calls — which is the population the *negative* class is drawn
  from, and it is large.

### The answer key does not come from the instrument

`research/invariants_lab/sigscan.py` re-implements signature extraction and reference finding from
scratch, and is used for the census and for C2. It does **not** import
`nevertwice/invariants/blast_radius.py`. If the key and the checker shared an extractor, D2's
tuple-unpacking defect would be a hole in both at once, and the measurement would confirm the
checker's blind spot rather than expose it. `sigscan` records tuple-unpacking targets from its
first line of code, precisely because the checker does not.
