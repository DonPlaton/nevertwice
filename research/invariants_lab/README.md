# Six mechanisms, measured properly: what the corpus said

**Task T5.** The previous attempt measured one of six mechanisms, half-finished, on a corpus whose
answer key was entirely negative, and reported the result as a verdict on the idea. This run built
the corpus first, declared every threshold in writing before the run that tested it, closed eight
defect classes before measuring anything, and then measured all six.

Two mechanisms passed. Four did not. The reason they failed is the same reason, and it is not the
one anybody predicted.

```bash
python research/invariants_lab/corpus_census.py --print       # the corpus
python research/invariants_lab/power.py --print               # what it can resolve
python research/invariants_lab/measure_blast_radius.py --print
python research/invariants_lab/measure_ratchet.py --print
python research/invariants_lab/measure_scale.py --print
python research/invariants_lab/measure_together.py --print
python research/invariants_lab/measure_coldstart.py --print
```

---

## What came next: `invariants/v3`

This page is the result of `invariants/v2`, and every number on it was measured on the same
eight repositories the defect classes were found on — in sample, and not evidence about a ninth
repository. `GOAL-SHIP.md` picks that up. Its documents, as they land:

**It finished, and the verdict is NO-GO** — [`VERDICT_G1.md`](VERDICT_G1.md). The write-up for a
reader outside this project is [`SHIP_G4.md`](SHIP_G4.md).

| task | what it settles | page |
|---|---|---|
| F0 | the eight become a frozen development set; the held-out corpus is sealed until Phase V | [`CORPORA.md`](CORPORA.md) |
| F1 | abstention — flag rate 0.278 to 0.010, and the margin over `git grep` doubles | [`ABSTENTION_F1.md`](ABSTENTION_F1.md) |
| F2 | the declared surface F2 wanted is not present in found history | [`SURFACE_F2.md`](SURFACE_F2.md) |
| F3 | the ratchet's three unaudited axes, against instruments nobody here wrote | [`AXES_F3.md`](AXES_F3.md) |
| F4 | `scale`'s static half on quadratic fixes real maintainers made: 0 of 7 | [`QUADRATIC_F4.md`](QUADRATIC_F4.md) |
| F5 | the cold-start experiment X1 named, refused on its own arithmetic | [`COLDSTART_F5.md`](COLDSTART_F5.md) |
| H1 | the code freeze and every Phase V and E threshold, before the first clone | [`PREREGISTRATION-SHIP.md`](PREREGISTRATION-SHIP.md) |
| H2 | thirty held-out repositories and a prediction per block | [`HELDOUT_H2.md`](HELDOUT_H2.md) |
| H3 | blocked: DNS resolves nothing, so G-B is unevaluable rather than failed | [`HELDOUT_H3_BLOCKED.md`](HELDOUT_H3_BLOCKED.md) |
| E | the end-to-end gate: harm passes at 0.010, benefit inconclusive at 3 discordant pairs | [`ENDTOEND_E.md`](ENDTOEND_E.md) |

---

## The scoreboard

| # | mechanism | kind | gates | verdict |
|---|---|---|---|---|
| 1 | invariant as a first-class note | infrastructure | 93 contracts | **kept** — [`INVARIANT_NOTES.md`](INVARIANT_NOTES.md) |
| 2 | `blast_radius` | detector | 2 of 4 failed | **deleted** — [`BLAST_RADIUS_D5.md`](BLAST_RADIUS_D5.md) |
| 3 | complexity ratchet | detector | 1 of 4 failed | **deleted** — [`RATCHET_R3.md`](RATCHET_R3.md) |
| 4 | seam journal | process artifact | executed, not scored | **kept** — [`TOGETHER_T1.md`](TOGETHER_T1.md) |
| 5 | scale assertions + 10× canary | detector + dynamic | 4 of 4 passed | **kept, with a caveat** — [`SCALE_X4.md`](SCALE_X4.md) |
| 6 | DB authority + reversibility | enforcement | 4 of 4 passed, on the third build | **kept** — [`DB_AUTHORITY.md`](DB_AUTHORITY.md) |
| | combined flag rate (T1) | — | failed at 0.119 vs 0.05 | **see below** |
| | cold start (T3) | the headline | failed on both models | [`COLDSTART_T3.md`](COLDSTART_T3.md) |

## The finding: scope buys silence, accuracy does not

Every flag rate measured on real corpus commits, in one table:

| mechanism | what it reads | flag rate |
|---|---|---|
| complexity ratchet | every changed file | **0.364** [0.335, 0.394] |
| `blast_radius` | every changed file | **0.278** [0.251, 0.307] |
| union of everything that passed | mixed | 0.119 [0.100, 0.141] |
| scale assertions | only a **declared** axis | 0.073 |
| the three preconfigured heuristics | newly introduced violations only | 0.049 |

**The two deleted mechanisms are the two that read the whole diff.** Neither failed on accuracy:
`blast_radius` finds 91% of genuine stale callers and beats `git grep` at a matched flag rate
(p = 0.0017); the ratchet fires three times more often on diffs an independent tool says got more
complex than on diffs it says did not (Fisher p = 4 × 10⁻⁴⁴). Both are substantially right, and
both talk far too much.

`research/BLAST_RADIUS_PRECISION.md` predicted the opposite — that the findings would be *wrong*.
On a corpus with true positives in it they are mostly right, and there are simply too many. **The
binding constraint on this idea is silence, and it is a property of what a mechanism is allowed to
look at, not of how well it looks.**

## The cold-start claim, which is the one that mattered

The thesis: a scar needs you to fall first, so on a new repository every retrieve-and-inject system
returns nothing *by construction*; an invariant checks the code itself and ships preconfigured.

The pack does fire from minute zero — I3 proves it, and it costs nothing when quiet. But on the
`LIVE_VALIDATION` stand, across 96 generations per model:

| model | pitfall rate, pack off | pack on |
|---|---|---|
| `qwen3.5:4b` | **0.000** [0.000, 0.038] | 0.000 |
| `qwen2.5:3b` | 0.052 [0.022, 0.116] | 0.031, and 2 of 5 errors fixed, 0 broken |

**There was almost nothing to prevent.** And the reason generalises past this pack:

> A preconfigured invariant can only help where the model is wrong. What ships in the box has to
> be **portable**, and portable knowledge is the knowledge models already have.

That is the same wall `research/LIVE_VALIDATION.md` hit from the other side — *"memory's value is
project-specific knowledge, and that is exactly where it fires"*, with three of four invented API
constraints fixed. A scar is useful **because** it is unportable. An invariant that ships must be
general, and general is what training already covers.

**Cold start is real. The cold-start *advantage* has to come from something project-specific that
is still checkable on day one** — and X1's declared growth axis (`records: 1_000 -> 50_000_000`) is
exactly that shape: a fact about this project, knowable before any history exists, in no model's
weights. That experiment is named and was not run.

## What the corpus cost, and what it bought

| | previous run | this run |
|---|---|---|
| repositories | 1, effectively one author | 8, 270–3,638 contributors each |
| commits examined | 150 | 27,368 candidates from 107,686 |
| **true positives available** | **0** | **859**, in 345 independent source commits |
| recall | structurally undefined | measured, with intervals, for every detector |
| answer key | hand-labelled, 5 defects found in the labeller | mechanical, agreed with CPython's binder 868 times of 868 |

**It paid for itself before a gate was evaluated.** Eight defect classes were closed; three could
not have been seen on one disciplined repository:

- a method contract change matched against an attribute call on a **different class** — 1,092 of
  1,299 false findings at the time it was found;
- an **unreadable file read as a file full of deletions** — 5.43% of this corpus is Python 2, and
  one `flask` commit produced 30 findings for symbols defined three lines below;
- a **quadratic diff**: `difflib` on repeated lines took the checker's own suite from 2 s to over
  two minutes, against a spec promising 2.00 s.

## The instrument lesson, five times over

Every time an instrument was checked only against itself, it was wrong; every time it was checked
against something written by someone else, the check found something.

| instrument | independent check | what it found |
|---|---|---|
| the mutation generator | CPython's `Signature.bind` | 2 mutants that were compatibility facades — **the answer key had D4's own blind spot** |
| this project's cyclomatic complexity | `ruff`'s C901 | 4 errors, including counting `and` as a branch and missing a `def` inside a `try:` |
| the scale canary | its own abstentions | 12 of 40 "misses" were the canary declining to answer, **scored as failures** |
| the cold-start judge | an empty response | every truncated completion scored as a clean pass |
| the authority boundary | 15 attacks | 2 landed, on 2 different builds |

**Ungraded is not the same as correct.** That sentence had to be written three times in this run,
in three different harnesses, by three different routes.

## The combined gate, and the choice it forces

T1's declared remedy — *rank and cap, or drop the weakest contributor; do not relax the number* —
applied exactly, produces an uncomfortable answer. Every union containing `scale` is over the 0.05
ceiling, **including `scale` alone at 0.073**. The largest union that satisfies T1 is the three
preconfigured heuristics without it: the rule deletes the only mechanism that passed a measurement
gate with non-circular evidence, and keeps three that have never been measured against anything but
their own definitions.

`scale` is not the weakest contributor. It is the loudest, and those are different words. **The
rule was declared before the measurement precisely so it could not be renegotiated afterwards**,
and this page reports the conflict rather than resolving it by preference. What the rule cannot do
is say which of the two numbers is worth more, and this run does not know.

## What survives, and what a next attempt should do

**Promoted (T4):** nothing into `nevertwice/`. The package `nevertwice/invariants/` is now
**empty**, and `blast_radius` — installed there by the previous run — was moved out, because it
failed its gates. A decision nobody executes is a decision nobody made, and two tests pin it.

**Kept in the lab, with results:** the note type and lifecycle, the seam journal, the scale
assertions, the authority boundary, the corpus, the answer key, and the measurement harnesses.

**Three things a next attempt should carry:**

1. **Silence is the design problem.** Scope a mechanism to something a project *declared*, not to
   whatever it can see. That single property is the entire difference between 0.073 and 0.364.
2. **Emit only what you can decide.** 70% of `blast_radius`'s findings and 91% of its silence-pool
   findings are *undecidable*, and it reports them anyway. Abstention is an untried lever and this
   corpus can measure what it costs in recall.
3. **The cold-start experiment worth running is X1's, not I3's** — a declared, project-specific,
   day-one-checkable fact, rather than a portable Python pitfall the model already avoids.

**Nothing here is refuted by silence.** Two mechanisms failed a declared gate on a powered corpus
with an empty defect list behind them; two passed; two are infrastructure that works. That is a
result, and it is the first time this idea has had one.
