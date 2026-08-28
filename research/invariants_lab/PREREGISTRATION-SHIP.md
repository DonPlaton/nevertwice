# Preregistration — Phases V and E, fixed before a single held-out repository was cloned

**Task H1.** Everything below is declared **before** `corpus_heldout` exists. `corpora.py`'s seal
is closed while this document is being written and opens only when the frozen hashes in §1 match.

`GOAL-SHIP.md` §0.3: every number `invariants/v2` published, and every number Phase F produced, is
**in sample** — the eight repositories were used both to find the defect classes and to score the
gates. Nothing measured on `corpus_dev` may carry a public claim. This document says what will be
measured once, out of sample, and what each result obliges.

---

## 1. The code freeze

Recorded **2026-08-28T17:21:11**, before the first held-out clone. `heldout_seal.json` holds the full digests; `tests/_test_corpus_freeze.py` recomputes every one and fails on the first that moved.

The corpus is an **argument** to every harness (`--corpus dev|heldout`) precisely so that Phase V runs this code rather than an edited copy of it. A harness with `dev_repos()` written into it would have to be changed to measure the held-out set, after its hash was recorded, which is what freezing was for.

| module | sha256 (first 12) |
|---|---|
| `abstain.py` | `adb561436fb2` |
| `audit_axes.py` | `3b6a7865c656` |
| `binding.py` | `b8aa671df7db` |
| `blast_radius_deleted.py` | `8458c07871e0` |
| `complexity.py` | `b86235ecc2fd` |
| `corpora.py` | `af5ebe77db07` |
| `corpus_census.py` | `6c1318f964e4` |
| `corpusio.py` | `245eb3102b4a` |
| `db_authority.py` | `439de6c09c9b` |
| `facade_shapes.py` | `9822fcb8384f` |
| `invariant_notes.py` | `ca6ff133cbe4` |
| `measure_abstention.py` | `355b5e2630c3` |
| `measure_blast_radius.py` | `546c24c9c00b` |
| `measure_coldstart.py` | `d5b1507a4782` |
| `measure_declared_axis.py` | `3dd33da2c6d9` |
| `measure_ratchet.py` | `4eaa238c9022` |
| `measure_scale.py` | `52413d3dfa52` |
| `measure_surface.py` | `beabddae5b42` |
| `measure_together.py` | `1199dbc487e4` |
| `mine_quadratics.py` | `caa0c6a4fae7` |
| `mutate.py` | `7aca3030efe4` |
| `power.py` | `5e3365b765d5` |
| `preconfigured.py` | `1567c6f5554a` |
| `ratchet.py` | `6c56e2c75edd` |
| `scale.py` | `7294b018e1cb` |
| `seam_journal.py` | `05f42a643708` |
| `sigscan.py` | `4ab5bad4ccc4` |
| `surface.py` | `9df57112e338` |
| `verify_mutants.py` | `e4cb27a34631` |

**No mechanism and no harness is edited between this commit and the end of Phase V.** A defect
found in that window is recorded and its effect reasoned about; it is not fixed and re-run, because
a corpus measured twice is a corpus tuned once.

## 2. What is measured out of sample, and what it is measured against

| # | mechanism | state after Phase F | measured in V |
|---|---|---|---|
| 1 | invariant note type | infrastructure, kept | not a detector; not measured |
| 2 | `blast_radius` + `decidable-only` | **finished** (F1, F2) | V1, V2 |
| 3 | complexity ratchet | **finished** (F3): four axes, all with an independent check | V1, V2 |
| 4 | seam journal | process artifact | not a detector; not measured |
| 5 | `scale` static half | **finished** (F4), with a documented capability boundary | V1 |
| 6 | DB authority | enforcement, not a detector | not measured |

## 3. Phase V — the gates

Every threshold is a number, every number has a test, and every failure has a consequence written
next to it. **Measured once.** No tuning after the seal opens.

### V1-A · `blast_radius` under `decidable-only`, out of sample

| | |
|---|---|
| **recall** | ≥ **0.45**, one-sided exact binomial against that floor, α = 0.05 |
| **silence-pool flag rate** | ≤ **0.05**, one-sided exact binomial |
| **against `git grep`** | strictly higher recall at a matched flag rate, exact McNemar, p < 0.05 |
| sample | every confirmed positive's source commit, one mutant per commit; a 1,000-commit silence pool at seed 20260827 |

**Why 0.45 and not 0.60.** `corpus_dev` gave 0.600 [0.55, 0.65], and a floor set at the in-sample
point estimate is a floor that fails half the time by construction. 0.45 is the bottom of the
in-sample interval minus a further 0.10 for the out-of-sample drop, which is the quantity nobody
has measured and the reason Phase H exists. A recall below 0.45 with a flag rate under 0.05 is a
mechanism that is quiet because it says nothing.

**Consequence:** any of the three failing deletes the mechanism, as `blast_radius` was deleted in
T4 — moved out of the package, the deletion pinned by tests, the claim withdrawn.

### V1-B · the complexity ratchet, out of sample

| | |
|---|---|
| **flag rate**, all four axes, every changed file | reported, no threshold |
| **agreement with `ruff`**, cyclomatic | ≥ **0.50** of diffs where `ruff`'s own delta rose, one-sided exact binomial |
| **discrimination** | fires strictly more often on `ruff`-rose than `ruff`-flat diffs, Fisher one-sided p < 0.01 |
| **silence** | ≤ **0.05** |

**Consequence:** the silence gate is the one that has already failed twice on `corpus_dev`
(0.364, then 0.358 after the audit). It is stated again unchanged because `GOAL-SHIP.md` §0.2
forbids renegotiating the ceiling. Failing it deletes the mechanism.

### V1-C · `scale`'s static half, out of sample

| | |
|---|---|
| **flag rate** under a mechanically generated blind declaration | ≤ **0.05** |
| **recall on mined quadratic fixes** | reported with an interval; **scored only if the held-out corpus yields ≥ 20 confirmed fixes** |

**Consequence:** F4 found 0 of 7 real fixes are of a shape it recognises, and
`tests/_test_scale.py` pins six shapes it cannot see. If the held-out corpus supplies ≥ 20 and
recall is below **0.30**, the static half is deleted and the mechanism keeps only its canary and
its declared-axis premise.

### V2 · the union, and the between-block model

- **Combined flag rate** under the one-finding-per-diff cap, raw and delivered counts both
  reported, against the 0.05 ceiling. T1's declared remedy — rank and cap, or drop the weakest
  contributor, never relax the number — applies unchanged.
- **Exploratory, declared as exploratory:** what property of a codebase governs precision? Size,
  age, contributor count, test ratio, deprecation discipline, Python-2 share. Fitted across ≥ 25
  blocks, which is the analysis eight could not support. **No gate depends on it.**

## 4. Phase E — the gate that decides shipping

`GOAL-SHIP.md` §0.2: every number so far is mechanism-level and none of them answers *does the
memory system work better with this in it*. E is that question, and **E4 is G-C**.

### E2 · the base rate, before any gate is declared final

The C3 discipline, applied to this stand as F5 applied it to itself. **Measure how often the
no-mechanism arm fails on these tasks.** If the failure rate is under **0.10**, the stand is
rebuilt with harder tasks before anything is scored — a stand that cannot fail cannot be improved.

**Exit:** a measured, non-zero base rate and the trial count that resolves the declared effect,
computed as F5's was: exact McNemar needs 6 discordant pairs one way, so
`trials ≈ 6 / (base rate × fix rate)`, and the run is refused above **600** trials.

### E3 · harm, counted separately

Not the complement of benefit and not inferable from a flag rate. Four counts, per mechanism and
for the union:

| | |
|---|---|
| **fixed** | the arm with the mechanism succeeds where the arm without it fails |
| **broken** | the arm with the mechanism fails where the arm without it succeeds |
| **ignored** | the mechanism fired and the outcome did not change |
| **false-flag repair** | the mechanism fired on **correct** code and the agent changed that code |

The fourth is the one nobody has measured, and it is the reason harm is a first-class number: a
false flag does not merely cost attention, it can make an agent repair what was never broken.

### E4 · the measurement — **G-C**

| | threshold |
|---|---|
| **net benefit** | more `fixed` than `broken`, exact McNemar on paired outcomes, **p < 0.05** |
| **harm bound** | `broken` + `false-flag repair` ≤ **0.05** of trials, one-sided exact binomial |
| arms | Nevertwice memory alone vs Nevertwice memory plus the surviving mechanisms |
| pairing | by task, seeds fixed, models named and pinned, local Ollama only |
| tasks | drawn from **held-out** repositories, where the correct outcome is knowable independently — reproducing a real change, or completing one whose actual commit is the reference |

**Both must hold.** A mechanism that fixes more than it breaks while breaking more than 5% of
everything is not a mechanism anybody should install, and net benefit alone would hide that.

### E5 · the audit that must run before any of it is believed

Published whether or not it finds anything: empty responses, truncated completions, abstentions
scored as failures, judges that pass a blank answer, and any instrument that cannot be checked
against something it did not write. **This project has hit that failure five times in five
harnesses** — three in T3, a `# noqa` silencing `ruff` in F3, and a canary abstaining on half of
F5's probe because its size ceiling was too low. Assume a sixth.

## 5. The decision

| gate | decided in | if it fails |
|---|---|---|
| **G-A** every mechanism is finished | Phase F | — *(met: F1–F5 are done and each named piece is built or its absence is documented)* |
| **G-B** out-of-sample numbers at declared thresholds | V1, V2 | the mechanism is deleted, and the in-sample/out-of-sample delta is itself published |
| **G-C** the memory measurably works better, harm bounded | E4 | **NO-GO.** Nothing is promoted, the failed mechanisms are moved out and pinned, and the negative result is written at the length a positive one would have had |

**All three must pass to reach G2.** G2 prepares a merge-ready branch and stops: the push, the
merge, the tag and any release are the owner's, one command each, listed in `.loop/REPORT.md`.

## 6. Analyses declared exploratory in advance

Reported, never gating, and labelled on every table:

- the between-block precision model (V2);
- per-block breakdowns of every headline;
- the taxonomy of quadratic-fix shapes on the held-out corpus;
- any comparison between `corpus_dev` and `corpus_heldout` beyond the single declared
  in-sample/out-of-sample delta.
