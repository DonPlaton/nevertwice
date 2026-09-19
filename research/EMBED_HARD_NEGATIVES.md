# Hard-negative mining — a negative result, and a specific one

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted and remain
> here because deleting a result one was wrong about destroys the record of having been
> wrong. The design, the method and the caveats stand; the numbers do not. Each figure's
> own reason and date are in
> [`research/evidence_manifest.json`](evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**Run 2026-08-27** on the frozen external held-out set, RTX 5090, fp16. Thresholds were fixed
first in [`EMBED_M2_THRESHOLD.md`](EMBED_M2_THRESHOLD.md) and committed before anything was
mined. Artifacts: `research/embed_universal/heldout/hard_v1.json` (every query's rank),
`heldout/mining_stats.json`, `heldout/training_hard_v1.json`.

---

## Verdict: N1 fails. The mined model is deleted; v1 stays shipped.

Paired against the shipped model on the same 216 situation queries:

| | declared before the run | measured | |
|---|---|---|---|
| **N1** Δ recall@5, situation | > 0, interval excluding zero | **−0.023 [−0.051, +0.005]** | **fail** |
| **N2** title recall@1 regression | no worse than −0.02 | **+0.012** — it improved | pass |

McNemar on situation recall@1: 3 won, 6 lost, exact p = .51. The point estimate is negative on
the axis the threshold was declared on. There is no reading of this in which mining helped where
it was supposed to.

Per the deletion decision written before the run: the mined checkpoint is deleted, v1 remains the
shipped model, and M3 (cross-encoder distillation) proceeds from v1 rather than from a mined
variant.

## But it did do something, and what it did is the finding

The mined model is **better than v1 on the easy axis**, and that is the only paired improvement
anywhere in this run:

| paired vs shipped v1 | Δ recall@1 | Δ recall@5 | McNemar |
|---|---|---|---|
| `retrieval_title` | **+0.012 [+0.003, +0.025]** | +0.003 [0.000, +0.007] | 5 won / 0 lost, p = .06 |
| `retrieval_situation` | −0.014 [−0.042, +0.014] | −0.023 [−0.051, +0.005] | 3 won / 6 lost, p = .51 |

The title interval excludes zero and nothing was lost — five queries fixed, none broken. v1 sat
0.010 *below* stock `bge-m3` on that axis; the mined model sits 0.003 above it. **Mining repaired
a regression, on the axis that was already at ceiling and could not matter, while moving the axis
that does matter the wrong way.**

## Why, and the part worth carrying forward

The negatives were mined from the **positive pool** — other lessons' rewrites — because that is
the population a retriever confuses. It is also a population of near-duplicate *notes* written in
the same register. So the model was trained to separate one note from a very similar note, which
is precisely the `retrieval_title` skill, and the numbers say it learned it.

The `retrieval_situation` query is not a note. It is a forward-looking sentence that must find a
note it does not quote, and no negative in the mined set had that shape. **The mining population
never matched the target query distribution, and mining can only teach what its negatives
demonstrate.**

That is a more useful sentence than "hard negatives did not help here." It says what a next
attempt must change: mine negatives *against situation-shaped queries*, not against notes. The
frozen set already contains 216 of those, and they are held out — so the negatives would have to
be generated, not borrowed, which is a corpus problem rather than a training one.

## The mining itself worked

| | |
|---|---|
| pairs mined from | 1463 |
| candidates scored by the cross-encoder | 17556 |
| rejected as **false** negatives | 2053 (11.7%) |
| triplets kept | 2910 |
| anchors left with no usable negative | 6 |

Rejected candidates score a median of 0.769 against kept candidates' 0.194, on a scale where true
positives sit at 0.9997 — a clean separation, so the filter was doing real work rather than
passing everything through. Roughly one mined negative in nine was a document the cross-encoder
considers genuinely relevant, which is the hazard the filter exists for and a number worth
knowing: an unfiltered miner on this corpus would have trained against ~2000 false negatives.

**The filter's first version rejected all 17556.** It was written as a *relative* margin — "within
1.0 of the true positive" — on the theory that a cross-encoder's scale is not comparable across
queries. `bge-reranker-v2-m3` emits a sigmoid probability, so 1.0 spans the entire range. The run
that found it also measured the scale, and the constant is now the reranker's own decision
boundary of 0.5 rather than a number chosen here. The success thresholds were not touched: a
mis-specified instrument is a bug, and fixing it after it rejects 100% is not threshold-shopping.

## What was held constant, so the result attributes to mining

Base `bge-m3`, LoRA r=16 on qkv+dense, 3 epochs, batch 24, lr 1e-4,
`MultipleNegativesRankingLoss`, seed 11 — all of them v1's. The single difference is the training
example: `(anchor, positive, hard_negative)` instead of `(anchor, positive)`. 128 s, peak 15.5 GB.

The merged checkpoint was verified **cls**-pooled through the published load path before it was
evaluated. That check exists because a sibling script's review note records the trap: a bare HF
checkpoint has no `modules.json`, so sentence-transformers silently rebuilds it mean-pooled, and
every number would have come from a pipeline the weights were never trained for.

## Hardware, as the threshold document required

Free VRAM measured immediately before the run: **30.2 of 31.8 GB**, against a required margin of
20. Ollama was left running deliberately — the owner was working in two other projects whose
memory hooks use it, and their notes landed in the store at 18:04 — so stopping it would have
interrupted live work rather than an idle service. The margin is what made that safe, and it was
measured rather than assumed. The run refuses rather than proceeding when the margin is absent.

## Limits

216 queries resolve about five points of recall@5 and no smaller, as stated before the run. This
result is a −0.023 point estimate, so it is not "mining hurts" either — it is "no improvement of
five points or more was demonstrated, and the sign points the wrong way." One corpus, one
mining strategy, one rank.
