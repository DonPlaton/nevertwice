---
title: "Run the AGENTS.md baseline against the repeat-error headline"
labels: help wanted, research
---

<!--
A draft, not a filed issue. Filing these is a maintainer action; the text is kept here so the
work is described even before the issue is opened.
-->

## The problem

The headline result of this project is that a fired guard cuts a real model's repeat-error
rate. `research/BASELINES.md` says a headline may only be published once it clears six
baselines, and for this one the **curated `AGENTS.md`** arm is marked `not_compared`.

That gap is the difference between two very different claims:

- *this system works* - the machinery of distilling a lesson, storing it, matching it against
  a proposed edit, and firing at the right moment;
- *writing the rule down works* - a human pasting the same one-line rule into an `AGENTS.md`
  the agent reads on every turn.

Until the second is measured, the first is not established. The gap is published rather than
hidden, which is the point of the baseline register, but published gaps are meant to close.

## What to do

1. Add an `agents_md` arm to `research/live_validation.py`, alongside the existing
   *no memory* and *active memory* arms. The arm prepends the same one-line prevention text
   the guard would have fired, as a persistent instruction rather than a triggered one.
2. Hold everything else fixed: same tasks, same model, same temperature, same trial count,
   same objective static check. The comparison is only meaningful if the *delivery mechanism*
   is the single variable.
3. Report the **token cost** of each arm as well as the error rate. The active-memory claim is
   about error prevention per token, so an arm that wins on errors and loses on tokens is a
   real result and must be reported as one.
4. Record the verdict in the manifest as `beats`, `ties` or `loses_to`, with the raw file, and
   run `python tests/_test_baselines.py`.

## Done when

- the `live_validation.repeat_error.relative_reduction` claim carries a measured verdict
  against `agents_md` instead of `not_compared`;
- the raw result file is committed and the manifest pointer agrees with it;
- if the guard **loses**, that is written down in `research/LIVE_VALIDATION.md` and the README
  claim is narrowed. This project has published negative results before and the baseline
  policy exists precisely to make that outcome survivable.

## Where to look

- `research/BASELINES.md` - the six baselines and the current verdict matrix.
- `research/live_validation.py` - the harness; it declares `sandbox_guard.isolate()` so it
  never touches a real store.
- `research/LIVE_VALIDATION.md` - the write-up the result belongs in.

## Warning about cost

This arm calls a real model, so running it spends money. Say in the issue which provider and
how many calls you plan before starting, and keep the trial count to what actually separates
the arms.
