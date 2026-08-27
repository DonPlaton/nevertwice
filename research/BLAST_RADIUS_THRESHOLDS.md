# Blast-radius calibration — thresholds, declared before the run

**Written 2026-08-27, before any commit was replayed.** Task I1 of `.loop/GOAL-NEXT.md`.
Hard rule §2.6 of `.loop/GOAL.md`: no new mechanism without a predefined baseline, a success
threshold declared before the run, and a deletion decision if it loses. This file is that
declaration. It contains no measurements, which is why it is registered `exempt` rather than
`governed`; the measurements land in `research/BLAST_RADIUS_CALIBRATION.md`.

---

## What is being calibrated, and why it needs calibrating

`nevertwice/invariants/blast_radius.py` answers one question: *you changed a contract, who else
depends on it, and did you update them?* It ships with budgets that were guessed, not measured:

| class | max files | max dirs | max changed lines |
|---|---|---|---|
| L0 | 3 | 1 | 150 |
| L1 | 12 | 4 | 500 |
| L2 | — | — | — |

A twelve-commit spot check on this repository on 2026-08-27 flagged **10 of 12**, and **not one
flag was a dependency finding**. Every one was `over-reach: L0 allows 3 file(s), diff touches 7`
or `L2 (architectural) requires a written plan at .nevertwice/plan.md`. F6 already measured this
project's worst interruption rate at roughly one wrong interruption per ten turns; a checker
that fires on 83% of commits and never once says the thing it exists to say makes the binding
constraint worse, not better.

There is a structural reason, not only a numeric one. The class is *inferred from the diff*
when the agent declares nothing — `_infer_scope` calls a diff L0 precisely because it changed
no contract — and the budget then punishes that same diff for touching four files. The
inference and the budget are reading the same evidence and reaching opposite conclusions.

---

## The declarations

### T1 — flag rate

> On the calibration set, after recalibration, **at most 20%** of commits produce `ok=False`.

Twenty percent is not a number the data will be allowed to choose. It is the point at which an
advisory checker stops being read: a developer who sees a warning on one commit in five still
looks at it, and one in three does not. The measured 83% is the thing being fixed, and a
threshold that 83% could pass would not be a threshold.

### T2 — composition of what remains

> **Zero** of the surviving flags are budget complaints or plan-protocol complaints.

A *dependency finding* is defined here exactly, so this cannot be argued about afterwards: a
problem string of the form `{qualname}: contract changed, N reference(s) left untouched`, which
is emitted only from `Verdict.unhandled` and only for references the confidence rule calls
`high`. Anything else — over-reach on files, dirs or lines, a missing `.nevertwice/plan.md`, a
declared-versus-inferred mismatch — is not a dependency finding.

T2 is the sharper of the two. A tool can hit any flag rate by firing less; only T2 says the
firings it keeps are the ones it was built for.

### T3 — how the budgets get their numbers

> Each budget is set to the **95th percentile** of the observed distribution for its own
> inferred class over the calibration set, rounded up to a round number, and applies **only to
> a scope the caller declared**.

The rule is declared here; the numbers it produces are not known yet and are not allowed to be
chosen after the fact. Two halves, both load-bearing:

*Ninety-fifth percentile.* A declared scope is a promise. A promise that is never exceeded
teaches nothing, and one exceeded on every third commit is noise. One commit in twenty is a
signal worth reading.

*Only when declared.* This repository declares no scope and has no `.nevertwice/` directory, and
a convention a codebase does not use must not generate a problem by default. The spec's own
argument agrees — `--scope` is documented as "the change class the agent declared, for
over-reach detection" — but the shipped code falls back to the inferred class and then charges
the diff for it. After I1 an undeclared diff can only be flagged by evidence found in the code,
never by a rule it never opted into.

### T4 — cost

> Median wall-clock **≤ 2.00 s** per commit on the calibration set; p95 and max reported
> whichever way they fall.

2.00 s is the ceiling the integration spec claims for itself (§3.3), so it is the spec's own
number rather than one chosen to be easy. The same spot check measured **10.3 s** on a
55-file / 17,665-line diff. T4 is reported, not gating: a slow checker is a worse product but a
sound one, and only T1 and T2 decide whether the mechanism survives.

### The calibration set

The **150 most recent commits** reachable from `master` at `7ef8ad2`, each compared against its
own parent. The repository has 217 commits and **no merges**, so no merge policy is needed. The
resolved list of SHAs is committed inside the results artifact, so the set cannot be quietly
reshaped after the fact. Replay happens in a detached `git worktree`; the working repository is
never checked out to a past commit.

---

## The deletion decision

Declared here, before the numbers exist, so that it cannot be renegotiated once they do.

- **T1 or T2 fails** → I1 is `BLOCKED`. The track skips to I4 and publishes the negative result:
  *a deterministic blast-radius checker cannot be calibrated against this repository's history
  without firing mostly on things that are not dependency findings.* Per `.loop/GOAL-NEXT.md`
  that is a complete outcome, not a failure to deliver, and the branch is deleted with
  `git branch -D invariants/blast-radius`.
- **T1 and T2 pass** → the budgets from T3 are written into the module and the track continues
  to I2. Passing here is not a claim that the tool is *useful*; that is I4's question, and I4
  still has to beat `git grep`.
- **T4 fails alone** → recorded as a defect against the spec's published ceiling, and the track
  continues. Speed is not why this mechanism exists.

## What this file does not decide

I4's threshold — precision of dependency findings on a hand-labelled sample, against the
`git grep` baseline — is a separate declaration, written before that run and not before this
one. Passing I1 buys the right to ask I4's question. It does not answer it.
