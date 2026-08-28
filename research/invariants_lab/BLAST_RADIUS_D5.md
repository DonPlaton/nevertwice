# Blast radius, measured properly: it finds them, and it will not stop talking

**Task D5.** The defect list was empty, the corpus was powered, and the thresholds were fixed in
writing before the run. Two of the four gates fail, so the declared consequence stands: **the
mechanism is deleted and this is the result.**

```bash
python research/invariants_lab/measure_blast_radius.py
python research/invariants_lab/measure_blast_radius.py --print
```

Artifact: `blast_radius_d5.json`. Thresholds: [`PREREGISTRATION.md`](PREREGISTRATION.md) §3.
Answer key: [`MUTANTS.md`](MUTANTS.md). Power: [`POWER.md`](POWER.md). Defects:
[`DEFECTS.md`](DEFECTS.md).

---

## The verdict

| gate | declared before the run | measured | |
|---|---|---|---|
| **D5-P1** precision | ≥ 0.50, Wilson lower bound > 0.25, on ≥ 20 findings | **0.396** [0.383, 0.410]; cluster-bootstrap **[0.301, 0.498]**; 4,953 decidable findings | **fail** |
| **D5-P2** recall | > 0.50, exact binomial, n = 345 | **0.910** [0.875, 0.936], 314/345, p = 4.4 × 10⁻⁶⁰ | **pass** |
| **D5-P3** beats `git grep` at matched flag rate, ties to grep | strictly greater, exact McNemar | **0.910 vs 0.826**; 55 checker-only, 26 grep-only, p = **0.0017** | **pass** |
| **D5-P4** silence | flag rate ≤ 0.05 on the silence pool | **0.278** [0.251, 0.307], p = 2.7 × 10⁻¹²³ | **fail** |

Gates within a mechanism are a conjunction. Two of four fail, so it is deleted from the lab, not
promoted into `nevertwice/`, and the negative result is published at the length a positive one
would have been.

## This is a different negative result from the last one

`research/BLAST_RADIUS_PRECISION.md` reported **precision 0.000 on 24 findings**, on a corpus with
no true positives, with recall structurally undefined and three known defect classes still in the
code. Nothing could be concluded from it about the idea.

This one concludes something, and the two halves point in opposite directions:

> **It finds the stale callers.** Recall **0.910** — it names the symbol on 314 of 345 commits
> where a caller really was left behind. That number had never been measured. And it **beats
> `git grep`** at a matched flag rate, which the previous run could not even attempt because
> precision 0.000 cannot exceed any score.
>
> **It cannot stop talking.** On commits where a contract changed and *nothing in the repository
> called it*, it fires on **27.8%**. The declared ceiling was 5%. On the diffs it does flag, fewer
> than two findings in five are real.

A mechanism that finds what it is looking for and also reports it everywhere else is not a
mechanism you can put in front of a person. `research/GOAL-NEXT.md` measured this project's worst
number as roughly one wrong interruption per ten turns; this is one per four commits, before any
other invariant is added.

## The discriminating prediction held, which is why the failure is interesting

The prediction fixed in advance: *on a mutant the checker names the symbol; on that mutant's paired
real commit it does not.*

| | |
|---|---|
| named the symbol on the mutant only | **224** |
| named it on the real commit only | **0** |
| exact McNemar | p = 7.4 × 10⁻⁶⁸ |

**Zero.** There is not one commit in 345 where the checker names the symbol on the fixed tree but
not on the broken one. It is genuinely responding to the stale caller and not merely to the
contract change — the thing the paired design exists to separate.

But it *also* names the symbol on **90 of 345** real, fixed commits (26%). It is right about the
breakage and unable to keep quiet about the repair.

## What the numbers rest on

**Precision is decided mechanically.** A finding is true when the symbol it names really has, in
that tree, a call that cannot bind or an import that cannot resolve — decided by the instrument
CPython's binder agreed with 868 times out of 868, applied identically to both arms. The previous
census hand-labelled 26 of 162 sites and found five defects in its own labeller while doing it.

**And it abstains a great deal. 11,763 of 16,716 findings (70%) are undecidable** and are excluded
from the precision denominator rather than counted for either side: a call written `f(*args)` has
no statically knowable arity, and whether an arbitrary receiver reaches *this* class's member is
not decidable from a name. Precision 0.396 is precision **on the 30% that can be decided**, and
that is said here rather than buried.

**A finding is what the checker reports as a problem** — a high-confidence unhandled reference.
Low-confidence references become notes; counting them would measure its diagnostics rather than
its verdict.

**The interval that governs is the cluster bootstrap**, [0.301, 0.498], not the Wilson interval on
findings. 4,953 findings sit in 345 commits and are not independent; `CORPUS.md` §2 declared the
clustering rule before any of this ran. The cluster interval is three times wider and is the one
quoted. It still excludes 0.50.

## Per block, because a pooled number no block resembles is not a measurement

| repository | n | recall | precision |
|---|---:|---|---|
| `django/django` | 107 | 0.944 [0.88, 0.97] | 0.307 [0.28, 0.33] |
| `pytest-dev/pytest` | 76 | 0.934 [0.86, 0.97] | 0.447 [0.41, 0.49] |
| `pallets/flask` | 13 | 0.923 [0.67, 0.99] | **0.836** [0.72, 0.91] |
| `scrapy/scrapy` | 37 | 0.892 [0.75, 0.96] | 0.301 [0.27, 0.33] |
| `sphinx-doc/sphinx` | 79 | 0.886 [0.80, 0.94] | 0.530 [0.50, 0.56] |
| `encode/httpx` | 25 | 0.800 [0.61, 0.91] | 0.439 [0.40, 0.48] |
| `fastapi/fastapi` | 5 | 0.800 [0.38, 0.96] | **0.174** [0.12, 0.25] |
| `psf/requests` | 3 | 1.000 [0.44, 1.00] | 1.000 [0.61, 1.00] |

**Recall is stable across blocks; precision is not.** It ranges from 0.174 to 0.836 — a factor of
five — and the two smallest blocks carry intervals so wide they say almost nothing. Whatever
governs precision is a property of the codebase, not of the checker, and no single number
describes all eight.

`flask`'s 0.836 is worth a sentence: it was selected in C1 as a *hard* block, on the grounds that a
repository with strict deprecation discipline should be difficult. It turned out to be the one the
checker is best on. The prediction was about the base rate of breakages, which held; it was not a
prediction about precision, and the difference is recorded rather than reinterpreted.

### Strata, declared exploratory in advance

| | n | recall |
|---|---:|---|
| the symbol vanished from its module | 91 | 0.945 [0.878, 0.976] |
| the call no longer binds (arity) | 254 | 0.898 [0.854, 0.929] |
| the caller is package code | 236 | 0.919 [0.878, 0.948] |
| the caller is a test file | 109 | 0.890 [0.817, 0.936] |

Nothing hides here. The checker is not blind to test callers, which was the risk `MUTANTS.md`
flagged when 43% of the positive class turned out to live in test files.

## The baseline, at a flag rate that makes the comparison mean something

`git grep` for each changed symbol, handed the symbol list for free — which is generous, since
deriving that list is most of what the checker does. At a threshold of **one** mention grep fires
on almost every commit and "wins" recall by having no precision at all. That is exactly why the
gate was written as a *matched* comparison, and why ties go to grep.

At **2 mentions** grep's flag rate is 0.896, just under the checker's 0.945 — the closest match an
integer threshold allows, and the handicap runs against the checker.

| | recall |
|---|---|
| the checker | **0.910** [0.875, 0.936] |
| `git grep`, matched | 0.826 [0.783, 0.862] |

55 commits the checker caught and grep did not; 26 the other way; exact McNemar **p = 0.0017**.
**The reference analysis earns its keep over a regex.** That is the one thing here worth carrying
into whatever comes next.

## The exploratory arm does not rescue it

Declared in [`PREREGISTRATION.md`](PREREGISTRATION.md) §9 **before this run**: the same checker
with a per-call-site compatibility filter, so a finding survives only when the site cannot bind.
Its *precision* is agreement with the answer key's own rule and is therefore not evidence; its
recall and its flag rate are.

| | as specified | with the filter |
|---|---|---|
| recall | 0.910 [0.875, 0.936] | 0.896 [0.859, 0.924] |
| silence-pool flag rate | 0.278 [0.251, 0.307] | **0.257** [0.231, 0.285] |
| findings kept | 16,716 | 13,725 |

**It costs 1.4 points of recall and buys 2.1 points of silence.** The noise is not
compatible-call noise, so the obvious fix is not the fix. That is worth more to a next attempt
than the filter would have been: the thing to solve is elsewhere.

Where? On the silence pool the checker produced 4,298 findings, of which **3,916 (91%) are
undecidable** and only 66 are true. It is not confidently wrong there — it is *unable to say*, at
volume, and it reports anyway. A mechanism that emitted only what it can decide would be quiet on
that pool almost entirely, and would forfeit an unknown share of its recall doing it. Nobody has
measured that trade, and it is the experiment a next attempt should run first.

## What a next attempt must change

The mechanism is deleted. These are the three things this run establishes, so the next one does
not start from zero:

1. **Reference analysis beats `git grep`, measurably, at a matched flag rate** (p = 0.0017). The
   idea that a symbol's in-repo references locate its stale callers is *not* refuted. It is the
   only part that survives.
2. **Silence is the binding constraint, not precision.** 27.8% against a 5% ceiling. Precision
   0.396 is a symptom of the same thing: the checker reports what it cannot decide.
3. **Emitting only decidable findings is the untried route**, and it is a *different* filter from
   the one tried here — abstention, not compatibility. Its cost in recall is unmeasured, and this
   corpus can measure it.

## What was fixed to get here, and what that cost

D1–D7 closed seven defect classes, five inherited from the previous census and **two the corpus
produced that a single repository could not have shown**:

- a **method contract change matched against an attribute call on a different class** — 1,092 of
  1,299 decidable false findings at the time it was found;
- an **unreadable file read as a file full of deletions** — 5.43% of this corpus is Python 2, and
  one flask commit produced 30 findings for symbols still defined three lines below.

A third, D7, was a performance defect that made the checker unusable rather than wrong: `difflib`
is quadratic in repeated lines, and a 6 MB artifact in the working tree took the checker's own test
suite from 2 seconds to over two minutes, against a spec promising a 2.00 s ceiling.

None of those were visible on 150 commits of one disciplined repository. **The corpus paid for
itself before a single gate was evaluated.**
