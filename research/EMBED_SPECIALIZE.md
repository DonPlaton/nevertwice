# nevertwice-embed: lifecycle-supervised embedding specialization (stage 1)

**Date:** 2026-08-18 · **Status:** RESEARCH COMPLETE - positive result recorded, NOT wired
into production (promotion path defined below) · **Hardware:** one RTX 5090, LoRA r=16 on
bge-m3 (568M), training runtime ~19s per round

## Question

Stage 0 (research/TWIN_GATE.md) showed the twin/distinct separation of raw bge-m3 cosine
is narrow on memory notes. Can the embedder itself be specialized - supervision mined from
the memory's own lifecycle, no hand labeling - to widen that margin without hurting
general retrieval?

## Setup

- **Train**: (anchor, positive) pairs → MultipleNegativesRankingLoss (in-batch negatives),
  LoRA r=16 on qkv+dense, 3 epochs, bf16.
- **Frozen eval**, identical across all rounds: 59 held-out lifecycle twin pairs vs 600
  random distinct same-project/type pairs (twin axis), plus a 300-query → 4.3k-doc
  retrieval guard in two query shapes (note title; description head - the latter a
  near-verbatim substring of the document, deliberately easy for string-matching).

## Results

| metric | base bge-m3 | **round 1** | round 2 |
|---|---|---|---|
| twin-recall @ 1% FPR | 0.220 | **0.339** | 0.203 |
| twin-recall @ 0 FP | 0.153 | **0.254** | 0.153 |
| AUC (cosine) | 0.964 | **0.972** | 0.963 |
| guard: title R@1 | 0.877 | **0.933** | 0.893 |
| guard: title R@5 | 0.990 | **1.000** | 1.000 |
| guard: desc-head R@1 | 0.993 | 0.953 | 0.983 |
| guard: desc-head R@5 | 0.993 | 0.990 | 0.993 |

**Round 1** (548 pairs: 148 lifecycle - supersede chains + slug twins - plus 400
same-language LLM paraphrases): the winner on every twin metric (+54% relative
twin-recall at 1% FPR, +66% at 0 FP) AND on title retrieval (+5.6pp R@1). One honest
regression: desc-head R@1 −4pp - the tuned model is less string-matchy on
verbatim-substring queries (R@5 intact at 0.990; production fusion carries BM25 for
exactly that shape).

**Round 2** (719 pairs: 119 lifecycle + 24 reconstructed consolidation pairs + 300
same-language + **300 cross-lingual translation** pairs): a clean NEGATIVE result -
back to baseline on the twin axis despite 30% more data. Two causes, in likely order:
cross-lingual translation positives dominated the batch (42%) while being nearly-free
for an already-multilingual model, diluting the gradient the twin task needed; and the
stricter test-exclusion left fewer of the signal-bearing lifecycle pairs (119 vs 148).
**Lesson: task-relevant pairs beat volume; language invariance is not the same training
signal as lesson identity.**

## Verdict

The hypothesis is CONFIRMED - lifecycle supervision measurably specializes the embedder -
but the round-1 model is **not promoted to production**: swapping the serving embedder
costs a GGUF conversion for Ollama, a full store re-embed, and recalibration of every
cosine threshold (SIM_FLOOR, the write gate, the consolidator), while the shipped stage-0
classifier already delivers precision 1.000 / twin-recall 0.852 on top of BASE embeddings.
The embedder win is real but marginal relative to that, and unproven in composition with
the classifier.

## Promotion path (when it becomes worth it)

1. Retrain the stage-0 classifier with round-1 embeddings as its cosine feature - if the
   composed system beats classifier-on-base materially, proceed.
2. Merge the LoRA adapter, convert to GGUF, serve as `nevertwice-embed` via Ollama;
   `embed_signature` change triggers the documented full re-embed.
3. Recalibrate thresholds on the new space (the calibration scripts exist).
4. Paper angle stands regardless: *"memory-lifecycle supervision for embedding
   specialization"* - the supervision source (supersede/consolidation/absorb events) is
   the novel part, now also stamped durably at consolidation time (`duplicate_of`).
