---
title: "The 31x headline assumes every pitfall needs memory - add a linter arm"
labels: help wanted, research
---

<!--
A draft, not a filed issue. Filing these is a maintainer action; the text is kept here so the
work is described even before the issue is opened.

Supersedes the earlier draft "Run the AGENTS.md baseline against the repeat-error headline".
That draft was written when the curated-AGENTS.md arm did not exist and the repeat-error
result was the project's headline. Both premises are gone: F2 built the arm and ran it (the
curated file loses decisively), and every `live_validation.*` claim has since been withdrawn
as stale, so the headline it targeted is no longer published.
-->

## The problem

Exactly **one** comparative claim is still published: `longitudinal.active_vs_inject_token_ratio`
— *"active guards deliver the same prevention for 31x fewer memory tokens than always-injecting
the recalled text"*, cited in `README.md`.

It comes from a simulation (`research/longitudinal_improvement.py`, n=200 tasks, 25 seeds), and
its baseline matrix in `research/BASELINES.md` shows three cells still `not_compared`. The
manifest states the reason for one of them plainly:

> `linter_or_test`: *the simulation assumes every pitfall needs memory to prevent; a linter arm
> would model the share that a static check would have caught for free, and it has not been
> added.*

**That assumption is now known to be wrong.** F5 measured the memory system against an existing
static check on 30 real episodes: each caught 14 of them, they agreed on only 4, and the linter
caught 10 the memory system missed. If a comparable share of the simulation's pitfalls would
have been caught for free, the guard arm is being credited for preventions it did not need to
make — and the token ratio is computed over a denominator that is too large.

The ratio might survive this. It might not. Nobody has run it.

## What to do

1. Add a `linter_or_test` arm to `research/longitudinal_improvement.py`. It costs **zero memory
   tokens** (a static check runs outside the model's context) and prevents a configurable share
   of pitfalls — that share is the parameter the result turns on.
2. **Sweep the share rather than picking one.** F5's corpus puts it near 47%, but that is one
   corpus and an oracle upper bound. Report the ratio as a function of linter coverage from 0 to
   1, the same way F1 publishes a threshold sweep instead of a point.
3. Report the ratio **among the pitfalls the linter does not cover**, which is the quantity the
   claim should have been about all along.
4. Record the verdict in the manifest's `baseline_verdicts` as `beats`, `ties` or `loses_to`
   with the raw file, and run `python tests/_test_baselines.py`.
5. If the ratio collapses at plausible coverage, **say so and narrow the README claim.** This
   project has withdrawn a published headline before; the baseline register exists to make that
   outcome survivable rather than embarrassing.

## Done when

- `longitudinal.active_vs_inject_token_ratio` carries a measured verdict against
  `linter_or_test` instead of `not_compared`;
- the ratio is published as a curve over linter coverage, not a single number;
- the raw result file is committed and the manifest pointer agrees with it;
- `python -m pytest -q` is green.

## Where to look

- `research/longitudinal_improvement.py` — the simulation; it declares `sandbox_guard.isolate()`
  so it never touches a real store.
- `research/longitudinal_results.json` — `arms.v1.mem_tokens` / `arms.v2.mem_tokens`, where the
  31x comes from.
- `research/BASELINES.md` — the matrix and what `not_compared` means.
- `research/cheap_baselines_rules.json` → `linter_or_test.coverage` — the per-class judgements
  F2 made, and the `generosity_note` explaining that they lean in the baseline's favour.
- `research/uncertainty.json` → `complementarity.linter_or_test` — the 10/10/4/6 split.

## Cost

None. Stdlib only, no model, no GPU, no API. This is a simulation arm, not a live run.
