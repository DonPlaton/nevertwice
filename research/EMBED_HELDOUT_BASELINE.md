# The embedding baseline on evidence a stranger can check

**Run 2026-08-27** on the frozen external held-out set (M0), RTX 5090, fp16.
Artifacts: `research/embed_universal/heldout/baseline_v1.json` (every query's rank, every pair's
score), benchmark `sha256 272cca36b1ab…`.

```bash
python research/embed_universal/heldout_set.py --verify     # the benchmark has not moved
python research/embed_universal/heldout_eval.py             # rerun the table
python research/embed_universal/heldout_eval.py --print     # summarise what was committed
```

---

## The headline does not transfer

`nevertwice-embed` ships with recall 0.220 → 0.475 at 1% FPR, measured on the owner's private
vault. On an external set nobody's vault touched, **the improvement over stock `bge-m3` cannot be
resolved from noise.**

| paired against stock, same queries | Δ recall@1 | Δ recall@5 | McNemar (won/lost) |
|---|---|---|---|
| nevertwice-embed, situation queries | +0.028 [−0.005, 0.065] (withdrawn at stage D: the stand changed, the next campaign re-measures it) | +0.032 [−0.005, 0.069] | 10 / 4, p = .18 |
| nevertwice-embed, title queries | −0.010 [−0.025, 0.003] | 0.000 [0.000, 0.000] | 2 / 6, p = .29 |

Both intervals on the difference contain zero. The point estimate leans the right way on the
axis that matters and the wrong way on the easy one, and neither lean survives 216 and 408
queries. **This is not a claim that the fine-tune is worthless** — it is a claim that the
evidence supporting it was never external, and that the external evidence is silent.

The comparison is paired on purpose. Every model answers the same queries, so reading two
marginal intervals and noting that they overlap asks a weaker question than the data supports;
the interval on the *difference* is the one that answers it. Both are published.

## The table

Percentile bootstrap, 2000 resamples, seed 20260827, over pairs for the twin axis and queries for
retrieval. Not a normal approximation: recall at a threshold is bounded and skewed, and a
symmetric interval would overstate precision near the ends.

### twin — saturated, and therefore useless for ranking models

| model | AUC | recall@1%FPR | recall@0FP |
|---|---|---|---|
| stock bge-m3 | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| nevertwice-embed | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| bge-reranker-v2-m3 | 1.000 [1.000, 1.000] | 1.000 [0.990, 1.000] | 0.978 [0.944, 1.000] |

690 pairs, 90 positive. Every model is perfect, including the untuned one, so **this axis cannot
discriminate anything and should not be used to justify a model.** The synthetic twins are
LLM rewrites of one lesson, and rewriting preserves far more surface than a real duplicate note
written months apart does. The cross-encoder scoring *below* the bi-encoders at zero false
positives is the only signal here, and it is a curiosity, not a finding.

That the prior evaluation reported `synth_twin` AUC 1.0 for all three models and still treated
the axis as evidence is the thing to carry forward.

### retrieval_title — the easy control, behaving like one

| model | recall@1 | recall@5 | MRR@10 |
|---|---|---|---|
| stock bge-m3 | 0.973 [0.956, 0.988] | 0.998 [0.993, 1.000] | 0.985 [0.975, 0.993] |
| nevertwice-embed | 0.963 [0.944, 0.980] | 0.998 [0.993, 1.000] | 0.980 [0.969, 0.990] |
| bge-reranker-v2-m3 | 0.949 [0.926, 0.971] | 1.000 [1.000, 1.000] | 0.972 [0.960, 0.984] |

408 queries. The query is a verbatim prefix of the document it must find, so this measures
whether a model is broken, not whether it is good. It is kept precisely so the table shows where
a benchmark stops discriminating — and the prior evaluation's headline retrieval axis was this
one.

### retrieval_situation — the axis that separates them

| model | recall@1 | recall@5 | MRR@10 |
|---|---|---|---|
| stock bge-m3 | 0.551 [0.486, 0.620] | 0.801 [0.745, 0.852] | 0.662 [0.609, 0.717] |
| nevertwice-embed | 0.579 [0.509, 0.643] (withdrawn at stage D: the stand changed, the next campaign re-measures it) | 0.833 [0.782, 0.880] | 0.683 [0.628, 0.735] |
| **bge-reranker-v2-m3** | 0.537 [0.472, 0.602] | **0.907 [0.870, 0.944]** | 0.682 [0.631, 0.730] |

216 queries: a forward-looking *prevention* sentence that must find a note it does not quote.
Recall@1 falls from 0.97 to 0.55 the moment the query stops being a copy of the answer, which is
the honest measure of how hard the product's actual retrieval problem is.

## The ceiling, and where the headroom actually is

The cross-encoder reranks the stock retriever's top 50, so its recall is capped by that
retriever's recall@50 — **0.968** on situation queries, which is the real ceiling for this
cascade and is reported so the cap is visible rather than implied.

Against stock, paired: **Δ recall@5 = +0.106 [0.056, 0.162]**, the only interval in this study
that excludes zero (withdrawn at stage D: the stand changed, the next campaign re-measures it). At recall@1 it gains nothing (−0.014 [−0.074, 0.042]) and it is *worse* on
the title axis.

Read together that is a specific, actionable finding rather than "the cross-encoder is better":
**the reranker knows which five documents matter but not which one**, and a bi-encoder distilling
from it should expect to inherit recall@5 and not top-1 precision. It also costs what a
cross-encoder costs — 60 s against 8 s for a bi-encoder over the same benchmark, with 50 forward
passes per query and no index.

## What this sets up

- **M2's threshold must be declared on `retrieval_situation`.** The twin axis is saturated and
  the title axis is at ceiling; a gain declared on either would be unfalsifiable.
- **Recall@5 is where the measured headroom is**, and it is where distillation from this
  reranker (M3) can be expected to land.
- **The private-vault number stays reported and stays labelled unreproducible.** It is not
  withdrawn — it may well be true of that vault — but it is not evidence anyone else can weigh,
  and the model card must say so.

## Limits

One benchmark, 408 documents, LLM-written notes over three public domains. It is external and
frozen, which the vault number is not, but it is synthetic: a model that learned the generator's
register rather than the task would look good here too. The generator is committed and hashed
beside the set so that suspicion is at least checkable.
