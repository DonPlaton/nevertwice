# Hard-negative mining — the threshold, declared before the run

**Written 2026-08-27, before any negative was mined and before anything was retrained.**
Task M2 of `.loop/GOAL-NEXT.md`. Hard rule §2.6: no new mechanism without a predefined baseline,
a success threshold declared before the run, and a deletion decision if it loses.

Registered `exempt`: prospective decision rules, not measurements. The measurements land in
`research/EMBED_HARD_NEGATIVES.md`.

---

## What is being changed, and against what

`nevertwice-embed` v1 was trained with `MultipleNegativesRankingLoss` over (anchor, positive)
pairs, where the negatives are **whatever else happened to be in the batch**. That is the cheapest
possible negative: a random other lesson, usually from another domain, usually easy. The single
largest known lever for a retrieval embedder is replacing it with a *mined* negative — a document
the current model already ranks highly and which is nonetheless wrong.

Mining introduces its own failure: a top-ranked "negative" is often a **false** negative, a
document that is genuinely relevant and merely unlabelled. Training against it teaches the model
to push away things it should retrieve. So the mined candidates are filtered with
`bge-reranker-v2-m3`, the cross-encoder M1 already established as the ceiling on this data.

**The baseline is v1 itself**, not stock `bge-m3`. M1 showed v1's advantage over stock does not
resolve on external material; beating stock again would prove nothing new. The question is
whether mining beats the model that is actually shipped.

## Where the threshold must live, and why

M1 measured all three axes of the frozen external set:

| axis | what it did | usable as a threshold? |
|---|---|---|
| `twin` | every model at AUC 1.000, untuned included | **no** — saturated |
| `retrieval_title` | 0.95–0.97 recall@1 for all three | **no** — at ceiling |
| `retrieval_situation` | 0.551 / 0.579 / 0.537 recall@1 | **yes** — the only axis that separates |

A gain declared on a saturated axis or one at ceiling is unfalsifiable, so the threshold lives on
`retrieval_situation` and nowhere else.

Within that axis, **recall@5** is where the measured headroom is. The cross-encoder — the thing
being distilled from — gains recall@5 +0.106 [0.056, 0.162] over stock and gains *nothing* at
recall@1 (−0.014 [−0.074, 0.042]). Mining teaches a bi-encoder to separate near-misses, which is
the recall@5 skill. Declaring the threshold on recall@1 would be declaring it where the source
signal is known not to be.

## The declarations

### N1 — the gate

> Paired against shipped v1 on the same 216 situation queries, **Δ recall@5 > 0 with a 95%
> percentile-bootstrap interval that excludes zero.**

Same benchmark, same seed, same harness, paired because every model answers the same queries.
An interval that contains zero is a failure, not a "promising trend".

### N2 — the guard against buying it with damage elsewhere

> No regression worse than **−0.02** in `retrieval_title` recall@1, point estimate.

v1 already sits 0.010 below stock on the easy axis. A mined model that improves the hard axis by
wrecking the easy one has not improved the product, and this bounds that trade at twice the
regression v1 already carries.

### N3 — reported, not gating

Δ recall@1 and Δ MRR@10 on `retrieval_situation`; the twin axis (to confirm it stays saturated
rather than *breaking*); mined-negative statistics — how many candidates the cross-encoder
rejected as false negatives, and at what score.

## What this run can and cannot detect

216 queries. The tightest paired interval M1 produced on this axis had a half-width of about
**0.053** (the reranker's Δ recall@5, ±0.053 around 0.106). So this comparison can resolve a true
improvement of roughly **five points of recall@5 and no smaller**.

That is stated now, before the result, because it determines what a failure means: **N1 failing
says "no effect of five points or more was demonstrated", not "no effect exists."** A two-point
improvement would be real, invisible here, and not claimable — and the honest response to that is
a larger benchmark, not a softer threshold.

## The deletion decision

Written before the numbers exist.

- **N1 fails, or N2 fails** → the mined model is **deleted**. `research/EMBED_HARD_NEGATIVES.md`
  is published as a negative result, v1 stays the shipped model, and M3 (cross-encoder
  distillation) proceeds from v1 rather than from a mined variant. This project publishes
  negative results; that is the standard it is held to.
- **Both pass** → the mined model becomes the M3 starting point, and the model card records both
  the gain and the interval around it.

Nothing here is decided by how the model looks on the private vault. That number stays reported
and stays labelled unreproducible.

## Hardware note, recorded before the run

The vault records a past mistake on this machine: a training run colliding with Ollama for VRAM.
The instruction for this loop is to stop Ollama before training. **The owner is concurrently
working in two other projects whose memory hooks use Ollama** — their session notes landed in the
store at 18:04 while this was being written — so stopping it would interrupt live work rather
than an idle service.

The rule this run follows: measure free VRAM immediately before the run; proceed only with a
margin large enough that a model loading into Ollama mid-run cannot collide, and record the
measurement in the results. If the margin is not there, the run waits for the owner rather than
killing their tooling. Either way the decision is written down rather than assumed.
