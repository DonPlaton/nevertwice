# Preregistration

This is what the confirmatory run will test, written down before it is run.

Everything in `research/` so far is **exploratory**. Tasks F1 through F6 built the harnesses,
measured what could be measured on one corpus, and — repeatedly — found less than the project
had been claiming. That work generated the hypotheses below. It cannot also confirm them: the
corpus that suggested a hypothesis is not evidence for it, and a paper that reported those
numbers as confirmation would be reporting the search, not the finding.

So this document fixes four things in advance — the hypotheses, the endpoints, the decision
rules, and what the corpus must be — and `research/preregistration.py` computes the fifth, which
is the one that cannot be written by hand: how large the corpus has to be.

**Submission is gate G7 and is the owner's action.** This file is the part that must exist
first.

## The systems claim

> Transparent agent memory that models recurrence and lifecycle can reduce repeated failures
> through proactive inspectable interventions under a bounded token and latency budget.

Retrieval results stay secondary. The claim is about *interventions*, and the exploratory phase
already narrowed it in ways the confirmatory run must carry:

- against raw lexical recall the system has **not** been shown to help, and on the exploratory
  corpus its successes were a strict subset of that baseline's;
- against an existing linter it **ties** on aggregate while failing on different episodes;
- the only mechanism an ablation could name as load-bearing was **coverage normalisation**;
- the override burden measured in the safety evaluation is **high enough to matter**.

A confirmatory run that ignores those and tests only the flattering version of the claim would
be doing the thing this document exists to prevent.

## Hypotheses and endpoints

The full specification — endpoint, comparison, test, support and falsification rule for each —
lives in `research/preregistration.py::HYPOTHESES` and is emitted to
`research/preregistration.json`, so it is data a suite can check rather than prose somebody has
to re-read.

| | hypothesis | expected |
|---|---|---|
| **H1** | interventions beat raw lexical recall at a matched false-alarm rate | **expected to fail** |
| **H2** | the system and an existing linter are *complementary* — their union beats either alone | the strongest signal |
| **H3** | coverage normalisation is load-bearing | supported exploratorily |
| **H4** | the reader bounds actionability | underpowered so far |

**H1 is registered as a hypothesis we expect to fail.** That is deliberate. A claim that
quietly disappears between exploration and write-up is exactly the failure preregistration
exists to catch, and this project has already published a headline it later had to withdraw.

**H2 reframes the contribution if it holds.** The finding would not be that this system beats
the alternatives, but that it reaches failure classes static analysis cannot — which is a
smaller and more defensible claim than the one the project started with.

## Analysis plan

Fixed in advance, and identical to what F5 established:

- **paired** comparisons throughout, because every arm sees the same episodes — exact McNemar on
  the discordant pairs, two-sided;
- **exact binomial**, never the chi-squared approximation, which is unreliable at the
  discordant-pair counts this design produces;
- **Holm–Bonferroni** across all four hypotheses;
- bootstrap intervals with a **recorded seed**;
- per-episode and per-family results published with the aggregate, and the aggregate recomputed
  with each family removed in turn.

No outcome-dependent choices: not the operating point, not the test, not the subset of
hypotheses reported.

## What the corpus must be

Preregistered because each of these is a way a result could be weakened afterwards without
anybody noticing. The full list is in `research/preregistration.py::CORPUS_REQUIREMENTS`; the
binding one is the first:

**It must be written by somebody other than the author of the notes.** Every exploratory result
rests on a corpus where one person wrote both the past failures and the situations heading
toward them. That biases recall upward, it cannot be corrected for after the fact, and no amount
of statistical care repairs it.

## How large it must be

Computed exactly, not estimated:

```
python research/preregistration.py
```

The exploratory corpus of thirty positive episodes is adequately powered for **one** of the four
hypotheses. The rest need a larger corpus, and the largest needs several times what exists
today. Publishing that number *before* the run is the point: it stops "we ran what we had and it
was not significant" from being reported as evidence of no effect.

## Status

Nothing here has been confirmed. No confirmatory run has been performed, and none can be until a
corpus meeting the requirements above exists. Until then the honest description of this project's
research is: **a set of harnesses, a set of negative and narrowing results, and a plan.**
