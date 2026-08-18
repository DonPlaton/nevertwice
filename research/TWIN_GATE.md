# Learned twin-gate: lifecycle-supervised duplicate detection

**Date:** 2026-08-18 · **Status:** SHIPPED (stage 0 of embedding specialization) ·
**Stand:** a live 4.4k-note store, production bge-m3 embeddings

## The problem

The write-time dedup gate and the weekly consolidator both scored candidate pairs by raw
embedding cosine. Calibration on live vectors showed why that ceiling is low:

| population | cosine |
|---|---|
| random DISTINCT same-project/type pairs (n=1326) | median 0.425, **max 0.737** |
| exact-slug twin pairs (n=141) | p25 0.761, **median 0.833** |

The twin distribution *straddles* the distinct maximum: any single threshold either lets
twins through (0.92 was measured near-inert - it caught only near-verbatim copies) or
risks false retirements. The calibrated 0.80 gate reaches only 0.689 twin-recall at 0.977
precision.

## The idea: supervision from the memory's own lifecycle

No hand labeling. The memory already KNOWS which pairs are the same lesson, because its
own mechanisms said so:

- **positives**: explicit supersede pairs (a note retired in favor of its replacement,
  n=99) + same-slug `-N` twins the collision path minted before the absorb fix (n=108)
- **negatives**: random distinct-slug same-project/type live pairs (n=800, unfiltered -
  filtering by cosine would teach the model that cosine decides by construction;
  hidden-twin contamination in random pairs measured <0.1%)

## Model

Logistic regression over five features computable at write time with what the gate
already has in hand: cosine, word-jaccard of title+desc, title-token jaccard, entity
overlap, length ratio. Standardized, pure-python GD; the learned weights ship as baked
constants, so the production gate stays stdlib-only.

## Results (held-out 302 pairs, 61 positives)

| gate | precision | twin recall |
|---|---|---|
| cosine @ 0.80 (previous default) | 0.977 | 0.689 |
| **classifier @ 0.90 (new default)** | **1.000** | **0.852** |
| classifier @ 0.50 | 0.982 | 0.902 |

AUC: classifier **0.998** vs cosine-only 0.991.

The most interesting learned coefficient: **word-jaccard is NEGATIVE (-1.88) given
cosine (+3.68)**. At matched semantic similarity, true twins are re-*phrasings* - the
same lesson in different words - while high word overlap signals template-similar but
distinct notes (same project boilerplate, different facts). A raw-cosine gate cannot
express that distinction; that is exactly the gap between "similar text" and "same
lesson".

## Shipping

`NEVERTWICE_WRITE_DEDUP_MODE=twin` (default) scores prefilter survivors
(cosine ≥ `NEVERTWICE_WRITE_DEDUP_PREFILTER`, 0.70) with the classifier and retires at
`NEVERTWICE_WRITE_DEDUP_TWIN_P` (0.90 - the zero-false-positive operating point).
`mode=cosine` is the kill-switch restoring the calibrated 0.80 threshold. Fail-open as
before: no embedder → no gate.

## Next (stage 1)

The same lifecycle supervision, used not for a pair-classifier but to LoRA-specialize the
embedder itself (`nevertwice-embed`): widen the twin/distinct margin in the embedding
space, guard against general-retrieval regression on the store's own query→note bench.
