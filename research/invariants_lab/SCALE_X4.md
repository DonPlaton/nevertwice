# Scale assertions: the first mechanism to pass, and the reason to distrust half of it

**Tasks X1–X4.** All four gates pass. Before the numbers, the caveat that governs how to read
two of them.

```bash
python research/invariants_lab/measure_scale.py --print
```

Artifact: `scale_x4.json`. Thresholds: [`PREREGISTRATION.md`](PREREGISTRATION.md) §5.

---

## Read S1 and S2 with a hand over one eye

The positive class here is **generated, not found**. Four templates produce the quadratics and
four matched templates produce the linear controls — and those templates and the detector's
rules were **written by the same author in the same afternoon**. Recall 1.000 on four shapes a
detector was built to find is close to tautological, and no amount of interval-reporting changes
that.

`PREREGISTRATION.md` §5 asked for injected positives and this is what that asks for. It is still
the weakest evidence on this page, and it is stated first rather than buried under the numbers it
qualifies.

**What is not circular:** S3, the canary, which runs the code and measures time and memory,
sharing no rule with the static detector; S4, the silence contract; and the exploratory
corpus flag rate below, measured on 738 real commits the templates had nothing to do with.

## The verdict

| gate | declared before the run | measured | |
|---|---|---|---|
| **X4-S1** recall on quadratics | > 0.90 | **1.000** [0.985, 1.000], 260/260, p = 2.3 × 10⁻¹² | **pass** |
| **X4-S2** false positives on matched linear code | ≤ 0.05 | **0.000** [0.000, 0.015], 0/260 | **pass** |
| **X4-S3** the canary discriminates on every matched pair | every pair | **40/40**, 0 abstentions; quadratic flagged 40, linear flagged 0 | **pass** |
| **X4-S4** no declared axis, no finding | contract | **true**, on all 260 quadratics | **pass** |

The memory arm of the canary agrees: the quadratic allocator is flagged, the linear one is not.

## The design decision that separates this from the two deleted mechanisms

> A project declares its growth axes. **Without a declared axis there is nothing to check, and
> that is correct behaviour rather than a gap.**

`blast_radius` and the complexity ratchet fire on any repository they are pointed at, and both
died on flag rate — 27.8% and 36.4% against a 5% ceiling. This one answers only questions it was
asked. Silence is not something engineered afterwards here; it is the default, and S4 makes it a
contract rather than a hope.

### The exploratory silence test, because "it only fires when asked" invites a question

Non-gating, declared in `PREREGISTRATION.md` §9 before it ran. For each of 1,000 sampled corpus
commits the axis is **not invented**: it is the identifier most often iterated over in the files
that commit changed — what a project declaring its hottest collection would name. Choosing the
*most-iterated* name is deliberately adversarial, since it is the one most likely to appear in a
nested loop.

| mechanism | flag rate on real commits | ceiling |
|---|---|---|
| complexity ratchet | 0.364 [0.335, 0.394] | 0.05 |
| `blast_radius` | 0.278 [0.251, 0.307] | 0.05 |
| **scale assertions** | **0.099** [0.079, 0.123] | 0.05 |

**Three to four times quieter than either deleted mechanism, under an adversarial choice of
axis** — and on a real project the axis is chosen by the project, not by whichever name a nested
loop happens to favour, so 0.099 is an upper bound.

It is still twice the 5% that would be a ceiling if this were a gate, and that is not smoothed
over. 262 of the 1,000 commits contained no loop at all and are excluded rather than counted as
silence they did not earn.

## Two defects, both in this harness, both found by the measurement disagreeing with itself

**The canary abstained and the score counted it as a miss.** The first run reported S3 as
**28/40** — a failure. Every miss had a `nan` time factor: at n = 150–175 the quadratic finished
in under 0.1 ms, the canary said *"timing is noise here"* and returned `ok=True`, and the harness
scored `not ok` as "did not flag". An abstention is not a negative verdict.

The fix is in the mechanism, not the score, because the fault is the mechanism's: `canary()` now
**doubles the input until the base run is measurable** before deciding anything. A scale test
that only works if the caller guesses `n` correctly is a scale test that reports whatever the
caller guessed. With that, abstentions went to **zero** and discrimination to **40/40**.

**The memory template was not quadratic.** It allocated `i % 50 + 1` per row — bounded, therefore
linear. The memory arm had no valid positive in it and measured nothing, while appearing to
report a result. Fixed to `[0] * i`, and the arm now discriminates. Reported as a template bug
rather than as a finding, because that is what it was.

Both are the same shape as the errors the ruff comparison found in R1 and the binder found in C2:
**an instrument checked only against itself reports whatever it happens to do.**

## What this does and does not establish

**Establishes:** a detector scoped to a declared axis is *structurally* quieter than one scoped
to a whole repository — 0.099 against 0.278 and 0.364 — and a 10× dynamic canary separates
quadratic from linear behaviour reliably once it sizes its own input.

**Does not establish:** that the static rules find quadratics **they were not designed for**. Four
shapes, four templates, one author. The obvious next experiment is the one this run did not do:
take the corpus's own real quadratic bugs — commits whose message says *"fix O(n²)"* — and ask
whether the detector would have caught them. That is a found, not generated, positive class, and
it is the difference between "passes" and "works".

Recorded so it is not mistaken for an oversight: the mechanism passes its declared gates and is
therefore a **promotion candidate** under T4, subject to T1's combined flag rate. On the evidence
above, its static half deserves the corpus test before anyone relies on it.
