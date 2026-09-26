# Cross-encoder distillation — the threshold, declared before the run

**Written 2026-08-27, before any teacher score was collected and before anything was retrained.**
Task M3 of `.loop/GOAL-NEXT.md`. Hard rule §2.6: no new mechanism without a predefined baseline,
a threshold declared before the run, and a deletion decision if it loses.

Registered `exempt`: prospective decision rules. The measurements land in
`research/EMBED_DISTILLATION.md`.

---

## What is being tried, and why this one is different from M2

M2 mined hard negatives and failed: it taught the model to separate near-duplicate *notes*,
because that is what the mined negatives were, and the axis that matters asks a different
question. Distillation changes the supervision rather than the negatives. The teacher —
`bge-reranker-v2-m3` — scores (query, document) pairs directly, and M1 measured exactly where
that teacher is better than the student:

| paired vs stock, situation queries | Δ recall@1 | Δ recall@5 |
|---|---|---|
| the teacher | −0.014 [−0.074, 0.042] | **+0.106 [0.056, 0.162]** (withdrawn at stage D: the stand changed) |

So the teacher has something to teach, it is concentrated at recall@5, and it is measured rather
than assumed. That is the difference from M2, where the signal's shape was never checked against
the target.

**The student starts from v1**, the shipped model — M2's variant was deleted.

## The supervision

The teacher is asked the question the benchmark asks: for a situation-shaped query, which
document is right? Training queries come from the training half of the corpus (never the frozen
held-out set), each paired with its own note and with the student's current top neighbours. The
teacher scores every pair; the student is trained to reproduce the *margins* between those scores
rather than a hard label, which is what makes it distillation rather than relabelled mining.

`MarginMSELoss` is the standard form and is what the loss will be unless it proves unavailable,
in which case the substitution and its reason are recorded before the run rather than after.

## The declarations

### D1 — the gate

> Paired against shipped v1 on the same 216 situation queries, **Δ recall@5 > 0 with a 95%
> percentile-bootstrap interval that excludes zero.**

Identical in form to M2's N1, deliberately: the two are alternative routes to the same target and
a different bar for each would make them incomparable.

### D2 — the guard

> No regression worse than **−0.02** in `retrieval_title` recall@1, point estimate, and no
> regression worse than **−0.02** in `retrieval_situation` recall@1.

M2 needed only the first. This adds the second because distillation from a teacher that is
*worse* at recall@1 can plausibly drag top-1 precision down with it — the teacher's own numbers
say it might, so it is guarded rather than hoped about.

### D3 — reported, not gating

Δ MRR@10; the twin axis (that it stays saturated rather than breaking); the fraction of the
teacher's recall@5 advantage recovered — `Δ student / 0.106` (that figure withdrawn at stage D) — which is the number that says
whether distillation is worth continuing even if D1 fails narrowly.

## What this run can detect

216 queries, same as M2: roughly **five points of recall@5 and no smaller**. The teacher's own
advantage is 10.6 points, so a student recovering half of it would clear the gate and a student
recovering a third would not be visible. **D1 failing therefore means "less than about half the
teacher's advantage was transferred", not "distillation does not work."**

## The deletion decision

- **D1 fails, or D2 fails** → the distilled model is **deleted**, `research/EMBED_DISTILLATION.md`
  is published as a negative result, and v1 remains shipped. M4 (Matryoshka) then proceeds from
  v1, since it is an orthogonal change to the representation rather than to the training signal.
- **Both pass** → the distilled model becomes the M4 starting point and the shipped candidate,
  and the model card records the gain with its interval.

## Hardware

Same rule as M2, and for the same recorded reason: measure free VRAM immediately before the run,
require a 20 GB margin, refuse rather than collide with the Ollama the owner's other sessions are
using. The measurement goes into the training record either way.
