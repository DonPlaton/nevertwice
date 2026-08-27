# Blast-radius: is it worth having? — thresholds, declared before the run

**Written 2026-08-27, before any finding was labelled and before the baseline was built.**
Task I4 of `.loop/GOAL-NEXT.md`, and the decision point for the whole track. Hard rule §2.6:
no new mechanism without a predefined baseline, a success threshold declared before the run, and
a deletion decision if it loses. `research/BLAST_RADIUS_THRESHOLDS.md` bought the right to ask
this question by passing I1; it did not answer it.

Registered `exempt`: prospective decision rules, not measurements. The measurements land in
`research/BLAST_RADIUS_PRECISION.md`.

---

## The question

I1 showed the checker can be made to fire on dependencies rather than on paperwork. It did not
show that firing is *useful*. A checker that reports 24 dependency findings across 150 commits is
only worth its interruption cost if those findings are mostly real, and only worth its ~1,000
lines of AST machinery if a one-line `git grep` cannot do the same job.

Both halves have to hold. Either one failing deletes the track.

## What gets labelled

**All 24 findings on the 9 flagged commits — a census, not a sample.** Every finding names a
symbol and the specific reference sites the checker believes were left behind. Each site is
labelled independently, and a finding is TRUE if at least one of its sites is TRUE.

A site is **TRUE** when, at the state of the repository *after* that commit, the reference is
incompatible with the symbol's new contract: it would raise (`TypeError`, `NameError`,
`AttributeError`) or would silently use a changed meaning. It is **FALSE** otherwise — including
the very common case of a signature that grew a *defaulted* parameter, where every existing call
site remains correct.

Labelling is **mechanical wherever it can be**, because the author of the checker is not a
neutral judge of its output. Three checks decide most sites without an opinion:

1. **Resolution.** Does the site's module actually reference *this* symbol — imported from the
   changed module — or does it define or import a same-named symbol of its own? A lexical
   collision is a false positive, and this is decidable from the import graph.
2. **Arity.** For a call site against a changed signature: do the positional count and the
   keyword names satisfy the new parameter list, allowing for `*args`/`**kwargs`?
3. **Resolvability.** For a removal: does the name still resolve in that module after the commit?

Anything those three cannot decide is labelled by hand, one line of reasoning each, committed in
the same artifact so the judgement is auditable rather than asserted.

> **A note on the strongest available evidence, and why it is not the label.** Every one of these
> commits shipped with the repository's own suites green. If a caller had really been left broken,
> those suites would very likely have said so. That is a powerful prior, and it is *reported*
> alongside the labels — but it is not used AS the label, because "the tests did not catch it" and
> "it is not a defect" are different statements, and a checker that only ever finds what the tests
> already find is a different (also interesting) finding.

## The baselines

**B1 — `git grep` for the changed symbol.** The cheap baseline the task names. Given the same
set of changed symbols the checker computed, find every lexical mention at the parent commit,
subtract the lines the diff touched, and report what is left. This isolates exactly what the
checker claims to add: `ast` reference resolution — kinds, definition lines, import-versus-call,
confidence, facades — over plain text matching. It is deliberately given the symbol list for
free, which makes it a *stronger* baseline than a real one-liner and therefore a fairer test.

**B0 — flag any commit whose contract changed.** No reference analysis at all. Reported for
context, not as a gate: it flags far more commits, so it cannot be matched to the checker's rate.

## The declarations

### P1 — precision

> Of the 24 labelled findings, **at least 50%** are TRUE.

Not a number the data may choose. Below one in two, reading a finding is worse than a coin flip
and the reader learns to dismiss the checker — the alert-fatigue death the integration spec names
as this mechanism's most likely killer (§8.1). Fifty percent is the floor at which the tool is
worth the interruption, not the level at which it is good.

### P2 — beats the cheap baseline at a matched flag rate

> With B1's threshold swept so it flags the **same 9 commits**, the checker's precision must be
> **strictly greater** than B1's.

Ties go to `git grep`. A thousand lines of AST that draw with a text search have to justify
themselves some other way, and this task is not that justification.

If no threshold makes B1 flag exactly 9 commits, the nearest achievable flag count above 9 is
used and the direction of the mismatch is published, since a baseline that flags *more* commits
is being handicapped in the checker's favour.

### P3 — reported, not gating

Recall against the union of TRUE findings both arms found; B0's flag rate; and how many findings
each arm has that the other does not. Complementarity is what F5 found valuable elsewhere in this
project, and it is worth knowing here even when the headline fails.

## The deletion decision

Written now, before the labels exist.

- **P1 fails, or P2 fails** → the track is deleted. `git branch -D invariants/blast-radius`,
  `master` untouched, and `research/BLAST_RADIUS_PRECISION.md` is published as a negative result
  on `master` in a separate, minimal commit: what was built, what it cost, what it scored, and why
  a deterministic blast-radius checker does not earn its place in this codebase. This project has
  published negative results before and is held to that standard. **A published negative result is
  a complete outcome, not a failure to deliver.**
- **P1 and P2 both pass** → the track continues to I5 (advisory surfaces only). Phase 2 hooks stay
  out of scope for this loop; the spec asks for a week of observation first, and nothing here
  substitutes for that.

## What this does not decide

Whether the checker would earn its place in a *different* codebase. One repository, one author,
one style, 150 commits. The result generalises no further than that, and the write-up will say so
in whichever direction it falls.
