# Capacity sweep — the threshold, declared before the run, and one half that cannot run

**Written 2026-08-27, before any rank was trained.** Task M5 of `.loop/GOAL-NEXT.md`. Hard rule
§2.6. Registered `exempt`: prospective decision rules. Measurements land in
`research/EMBED_CAPACITY.md`.

---

## The task has two halves and only one of them is runnable

M5 asks to re-test two inherited beliefs on the external set rather than carrying them forward:

1. **LoRA rank r1 won the twin axis.** Runnable — the ranks are a training argument.
2. **Cross-lingual pairs hurt.** **Not runnable from committed data**, and saying so now is part of
   the task rather than an excuse discovered afterwards.

`gen_pairs.py` produces same-language twins *only*, precisely because of the belief being tested,
so no cross-lingual arm exists in `train_pairs.jsonl` to compare against. Re-testing it would mean
generating cross-lingual pairs with the LLM corpus generator — a corpus run, not a training run —
and then a fourth training arm. That is a task, not a sweep, and it is named as one rather than
quietly dropped.

**What replaces it** is the question the product actually has: the owner's store is bilingual, so
the frozen set's 284 English and 124 Russian documents are broken out per language. That does not
re-test the training claim and is not presented as if it did.

## The declarations

### S1 — when a swept arm may replace the shipped model

> Paired against shipped v1 on the same 216 situation queries: **Δ recall@5 > 0 with a 95%
> percentile-bootstrap interval excluding zero.**

The same bar as M2's N1 and M3's D1, deliberately. Four tasks have now aimed at this target and a
different bar for each would make them incomparable, and would let a weaker arm through on a
technicality.

### S2 — the inherited belief, re-tested

> Report whether **r1 beats r16** paired on `retrieval_situation` recall@5, with the interval.

This is reported, not gated: the prior claim was made on the twin axis, which M1 found saturated
at AUC 1.000 for every model including the untuned one. **A claim measured on an axis that cannot
discriminate is not a claim that can be confirmed or refuted — only replaced.** So this measures
the same question on an axis that works, and says plainly that it is a replacement rather than a
replication.

### S3 — reported, not gating

Per-language recall on both retrieval axes for every arm; trainable parameters and training
seconds per rank, so the capacity/benefit curve is visible rather than inferred; and whether any
arm damages the twin axis, which no model has yet done.

## What this run can detect

216 situation queries, about **five points of recall@5**, as in M2, M3 and M4. Four arms compared
against one baseline is four comparisons; no multiplicity correction is applied because S1 is a
*promotion* rule rather than a hypothesis test, and the write-up will say that the more arms are
swept the more likely one clears a threshold by chance. With four arms at 95%, that inflation is
real and small; it is stated rather than corrected, because correcting a promotion rule would mean
promoting nothing.

## The deletion decision

- **Every arm that fails S1 is deleted**, and the sweep is published as a curve rather than as a
  winner. That is the expected outcome given M2, M3 and M4.
- **An arm that passes S1** becomes the shipped candidate, and the write-up must state that it was
  selected from four and what that does to the interval.

## Hardware

As in M2–M4: measure free VRAM before each arm, require a 20 GB margin, refuse rather than collide
with the Ollama the owner's other sessions use.
