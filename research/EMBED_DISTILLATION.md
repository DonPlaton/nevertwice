# Cross-encoder distillation — a second negative result, and the guard that caught it

**Run 2026-08-27** on the frozen external held-out set, RTX 5090, fp16. Thresholds were fixed
first in [`EMBED_M3_THRESHOLD.md`](EMBED_M3_THRESHOLD.md) and committed before any teacher score
was collected. Artifacts: `research/embed_universal/heldout/distil_v1.json`,
`heldout/distillation_v1.json`.

---

## Verdict: both gates fail. The distilled model is deleted; v1 stays shipped.

Paired against the shipped model on the same queries:

| | declared before the run | measured | |
|---|---|---|---|
| **D1** Δ recall@5, situation | > 0, interval excluding zero | **−0.056 [−0.102, −0.009]** | **fail** |
| **D2** title recall@1 regression | no worse than −0.02 | **−0.101 [−0.132, −0.066]** | **fail** |
| **D2** situation recall@1 regression | no worse than −0.02 | −0.037 [−0.097, +0.023] | fail |

D1's interval excludes zero **on the wrong side**: this is not "no improvement demonstrated", it
is a measured regression. On the title axis, McNemar counts 5 queries won and 46 lost.

## The guard fired, and it was written for exactly this

`EMBED_M3_THRESHOLD.md` added a second clause to D2 that M2 did not need:

> *A teacher that is worse at recall@1 can plausibly drag top-1 precision down with it — the
> teacher's own numbers say it might, so it is guarded rather than hoped about.*

M1 had measured the teacher as **worse than stock at recall@1** (−0.014) and much better at
recall@5 (+0.106). Distilling its full ranking transfers both. The student inherited the shape of
the teacher's judgement — including the part that is worse than what the student already had —
and the damage is concentrated exactly where the teacher is weak. It did not even inherit the
strength: recall@5 fell too.

**A teacher that beats you on one metric and loses on another is not a teacher you can distil
wholesale.** That sentence is the result.

## Two attempts, and the first one was my error

The first run produced a model **17 points worse on both retrieval axes** and broke the twin axis
(recall@0FP 1.000 → 0.700). That was not a finding about distillation; it was a specification
error, and it is published here because the correction is only trustworthy if the mistake is
visible.

`MarginMSELoss` asks the student to reproduce the teacher's margin **as a number**. The student's
similarity is cosine over normalised embeddings, so its margin cannot leave [−2, 2]. The
teacher's logit margins span **[−13.2, +15.3]**. The student was being asked for values that do
not exist in its output space, and the optimiser did what optimisers do with an unreachable
target: drove the embeddings towards the extremes.

| | first run | corrected |
|---|---|---|
| target scale | raw teacher logits, spread 28.5 | divided by the raw margins' IQR (5.51) |
| target IQR | [−0.13, 5.38] | [−0.02, 0.98] |
| situation recall@5 | 0.662 | 0.778 |
| title recall@1 | 0.797 | 0.862 |
| twin recall@0FP | 0.700 | 0.856 |

The divisor is **computed from the data**, not chosen: the raw margins' own interquartile range,
which puts the target's IQR at about 1 and keeps the order and relative sizes the teacher
assigned. It was not tuned until the result improved — one rule, applied once, and the result
still fails.

This is the same class of mistake as M2's false-negative filter, which was written as a relative
margin against a sigmoid output and rejected 100% of candidates. **Both were constants on the
wrong scale, and both were caught by looking at the instrument's own output distribution before
believing its verdict.** The lesson is cheap to state and evidently not cheap to remember: before
trusting a number a model produces, look at the range it produces numbers in.

## What was set up correctly, so the failure is attributable

- **The queries are situation-shaped** — each a training lesson's *prevention* sentence, the same
  shape as the benchmark's hard axis. This is what M2 got wrong: its mined negatives were
  near-duplicate notes, so it taught the title axis. Here the supervision matched the target.
- **The teacher was read as logits**, checked before training. Its sigmoid saturates on this data
  (true positives at 0.9997), so probability margins would have been ~1.0 everywhere and the
  target nearly constant — a null result for a reason unrelated to distillation. Guarded, and the
  guard is in the code.
- **1429 situation queries, 2858 triples**, and in 743 of them (26%) the teacher scores the
  retrieved neighbour **above** the labelled gold. The teacher disagrees with the labels a
  quarter of the time, which is real information and is what distillation is supposed to be good
  at absorbing.
- **The student started from v1** and one thing changed from M2: the supervision, not the
  negatives.
- **The merged checkpoint was verified cls-pooled** through the published load path before
  evaluation.

## Hardware

Free VRAM 30.2 of 31.8 GB against a required margin of 20; Ollama left running for the reason
recorded in M2 — the owner's other sessions were using it. Peak allocation 9.7 GB, 274 s.

## What this does not say

It does not say distillation cannot work here. It says **this teacher, distilled this way, makes
this student worse**, and the guard that predicted the mechanism was written before the run. Two
routes remain untried and are named rather than implied: distil only the teacher's recall@5
behaviour (rerank-order within a shortlist, not absolute margins), or use a teacher that
dominates the student on every axis rather than trading one for another. Neither is M3.

Per the deletion decision written before the run: the distilled checkpoint is deleted, v1 remains
shipped, and M4 proceeds from v1 — it changes the representation rather than the training signal,
so it is orthogonal to what failed here.
