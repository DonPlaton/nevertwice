# Matryoshka representations — the threshold, declared before the run

**Written 2026-08-27, before anything was trained or truncated.** Task M4 of
`.loop/GOAL-NEXT.md`. Hard rule §2.6.

Registered `exempt`: prospective decision rules. The measurements land in
`research/EMBED_MATRYOSHKA.md`.

---

## Why this one is a different kind of bet

M2 and M3 both tried to make the model *more accurate* and both failed. M4 does not: it trains
the model so that the **first 512 and first 256 dimensions of its vector are usable on their
own**. The payoff is index size and latency — a 256-dimensional index is a quarter of a
1024-dimensional one — and the risk is that the full vector gets worse in exchange.

So the question is inverted from the last two tasks. There, the gate asked *did it improve?* Here
it asks *what did truncation cost, and did the full vector survive?* A result of "no accuracy
gain, three quarters of the memory" is a **success** for this task, and stating that before the
run matters, because the same numbers read as a failure under M2's gate.

## What is actually bought, and for whom

The store this serves indexes notes in SQLite with an embedding cache; the owner's live cache is
**96 MB** and growing. At 1024 float32 dimensions a note costs 4 KB of vector. At 256 it costs 1
KB. That is the product claim, and it is a claim about *this* system rather than a benchmark
number — so it is measured as index bytes and query latency, not only as recall.

## The declarations

### K1 — the full vector must not be paid for the truncated one

> At the full 1024 dimensions, paired against shipped v1 on the frozen external set: **no
> regression worse than −0.02** in `retrieval_situation` recall@5, and none worse than −0.02 in
> `retrieval_title` recall@1.

This is the gate. Matryoshka training is a constraint added to the loss, and a constraint can
degrade the unconstrained objective; if it degrades it materially, the memory saving is being
bought with accuracy and the trade has to be made explicitly rather than accidentally.

### K2 — truncation must actually be graceful

> At 512 dimensions: `retrieval_situation` recall@5 within **−0.03** of the same model's 1024-d
> result. At 256 dimensions: within **−0.06**.

Half the dimensions for three points, a quarter for six. If truncation costs more than that, the
representation is not Matryoshka in any useful sense and the mechanism has not done its job —
regardless of how the full vector scores.

The comparison is *within the same model*, because that is what "truncatable" means. A separate
comparison against v1 truncated the same way is reported under K3: a plain model truncated
naively is the baseline any Matryoshka claim has to beat, and this project has been caught before
by a mechanism that never had a baseline.

### K3 — reported, not gating

v1 truncated to 512 and 256 without Matryoshka training, so the gain from the training rather
than from truncation is visible; index bytes at each width; and encode latency, which truncation
does not change but which sets what the saving is worth.

## What this run can detect

216 situation queries, so about **five points of recall@5**, as in M2 and M3. K2's tolerances
(3 and 6 points) are inside and just outside that resolution respectively — meaning a 512-d
result within tolerance is *consistent with* no loss rather than proof of none, and the write-up
must say so rather than claiming truncation is free.

## The deletion decision

- **K1 fails** → the Matryoshka model is deleted, the negative result published, v1 stays
  shipped. The saving is not worth an accuracy regression on a system whose whole product claim
  is retrieval quality.
- **K1 passes, K2 fails** → the model is *still deleted*, and the result is published as
  "Matryoshka training did not produce a truncatable representation here." A full vector that is
  merely unharmed is not a reason to keep a model.
- **Both pass** → it becomes the shipped candidate and M6 packages it, with the index saving
  stated at each width alongside what it costs.

## Hardware

Identical to M2 and M3, for the reason recorded there: measure free VRAM immediately before,
require a 20 GB margin, refuse rather than collide with the Ollama the owner's other sessions use.
