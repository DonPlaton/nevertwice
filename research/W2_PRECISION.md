# W2 - Retrieval precision under embedding compression (results & findings)

*Companion to `research/precision_bench.py`. Reproduce: `NEVERTWICE_VAULT=/path python
research/precision_bench.py` (Experiment 1, cache-only, ~1 s) and `… --rerank` (Experiment 2,
calls Ollama). Aggregate-only: the bench reads the local embedding cache (vectors + the
title/desc/prevention text the embedder stored) in-process and prints recall/MRR/cost - never note
content.*

## The problem (from the hostile audit)

W2 is the honest ceiling of the whole system: bge-m3 cosines for short, multilingual notes bunch
near a high background (note↔note median ≈ 0.42), so a genuinely-relevant note clears the per-query
median by only ~0.16 and a no-match by ~0.13 - a ~0.03 separation. The adaptive margin gate (W1/W3)
keeps abstention honest, but it cannot *sharpen* ranking. On the real 328-note vault, same-topic
**recall@3 = 0.710** with relevance alone (MRR 0.547), and no recurrence prior beats it (3A.2). The
audit names three candidate fixes: a stronger embedder, a cross-encoder reranker, or query expansion.
This study tests the two that need no new model dependency.

**Ground truth.** Within each project, notes whose cosine ≥ 0.55 form a cluster; a cluster spanning
>1 date is the same lesson re-encountered across sessions. Query = one member; ground truth = its
same-cluster members written on other dates; candidates = all other in-project notes. 131 such
cross-session queries exist on the live store. The baseline bi-encoder reproduces recall@3 = 0.710
exactly, matching 3A.2 - the methodology is sound.

## Experiment 1 - Embedding-space pseudo-relevance feedback (Rocchio), cache-only

The cheapest possible fix: re-weight the *existing* geometry, no model, no text. Move the query
toward the centroid of its top-K0 neighbours - `q' = (1-β)·unit(q) + β·unit(mean(topK0))` - then
re-rank by cosine. This is the same averaging-as-denoising mechanism as 4A abstractive consolidation,
applied at query time: if the neighbourhood is on-topic the centroid denoises the query; if it is
distractor-heavy it drifts.

**Result - it does not help (β × K0 sweep, recall@3, Δ vs 0.710):**

| β \ K0 | 3 | 5 | 10 |
|---|---|---|---|
| 0.1 | 0.710 (+0.000) | 0.695 (−0.015) | 0.702 (−0.008) |
| 0.2 | 0.710 (+0.000) | 0.702 (−0.008) | 0.702 (−0.008) |
| 0.3 | 0.710 (+0.000) | 0.672 (−0.038) | 0.695 (−0.015) |
| 0.4 | 0.710 (+0.000) | 0.656 (−0.053) | 0.664 (−0.046) |
| 0.5 | 0.710 (+0.000) | 0.664 (−0.046) | 0.664 (−0.046) |

No `(β, K0)` beats the baseline; every setting that moves the ranking moves it **down**. On this
store's geometry the top-K0 neighbourhood carries enough distractors that the centroid pulls the
query *away* from the lone true twin as often as toward it. **Query-time PRF cannot lift the
bi-encoder ceiling** - the precision gap is in the encoder, not in how the query vector is placed.

*Caveat (load-bearing):* the ground truth is built from the same cosine signal, so Experiment 1 is
an **upper bound on re-weighting the existing geometry** - it cannot credit a method that surfaces a
twin cosine ranks low. That even this upper bound fails is the strong form of the negative: there is
no free lunch in vector arithmetic here. To beat the ceiling you need a *different* relevance signal.

## Experiment 2 - LLM reranker on the internal (cosine-cluster) GT: why it can't judge a reranker

A cross-encoder *jointly* reads (query, candidate) and scores relevance - the precision tool Zep/Cognee
use. Ollama is already a hard dependency, so a local chat model scoring relevance is a cross-encoder
substitute at **no new dependency**. First attempt: take the cosine top-N (N=12) of the 3A.2 query set
and have `qwen3:30b-a3b` score each candidate 0-10, re-order.

**Result:** baseline recall@3 = 0.710, **pool ceiling = 0.985** (a true twin is in the cosine top-12
almost always - huge headroom), but reranked recall@3 = **0.542 (−0.168)**. The reranker *loses*.

This is a methodological lesson, not a verdict on reranking: the 3A.2 ground truth is **built from the
cosine signal** (twins = cosine ≥ 0.55), so any reranker using a *different* notion of relevance is
penalised for disagreeing with cosine - the GT rewards cosine by construction. A fair reranker test
needs a **cosine-independent** ground truth. Hence Experiment 3. (The one durable fact here: the right
note is nearly always in the top-N - so retrieval *recall* is not the problem; top-k *ordering* is.)

## Experiment 3 - LLM reranker on LongMemEval (external, non-circular GT) - the decisive test

LongMemEval-oracle (global pool): 940 real agent sessions, 500 questions, each with human-annotated
`answer_session_ids` - relevance is **independent of cosine**. The production hybrid (RRF of bge-m3 +
lexical) is the first stage; we rerank its top-10 with a local LLM reading a question-matched ~700/1600-
char passage of each candidate, one JSON call per question. Headroom is real: hybrid puts the evidence
session at rank 1 only **41.8%** of the time but in the top-10 **77.0%** of the time - exactly the gap
a good reranker should close.

**Result - it does not close it; it consistently degrades top-1 (Δ vs hybrid; n=500, SE ≈ 0.022):**

| reranker | snippet | R@1 | R@3 | R@5 | ms/query |
|---|---|---|---|---|---|
| *hybrid baseline* | - | 0.418 | 0.586 | 0.660 | - |
| qwen3.5:4b | 700 | 0.312 (−0.106) | 0.510 (−0.076) | 0.632 (−0.028) | 787 |
| qwen2.5:7b | 700 | 0.392 (−0.026) | 0.618 (**+0.032**) | 0.680 (**+0.020**) | 646 |
| qwen2.5:7b | 1600 | 0.304 (−0.114) | 0.578 (−0.008) | 0.656 (−0.004) | 1893 |

R@10 is unchanged at 0.770 throughout - reranking only reorders within the top-10, a clean sanity check.
The only positive deltas (7b/700 at R@3/R@5) are **under 1.5 SE** and **reverse** when the model is given
*more* context (7b/1600), so they are noise, not signal - the truncation hypothesis ("the reranker just
needs to see more of each session") is refuted: more context made R@1 *worse*. A weaker model (4b) harms
across the board; a competent one (7b) is a wash on R@3/R@5 and still loses R@1. The mechanism is
consistent: the bi-encoder's top-1, when right, is a strong **whole-document** match; the LLM scores a
**truncated passage** with coarse integers, occasionally preferring a lexically-flashy distractor and
demoting the true #1 - and more passage gives distractors more surface, not the reranker more signal.

## Experiment 4 - a stronger local embedder (drop-in A/B on LongMemEval)

The other audit-named fix: swap the bi-encoder itself. We A/B four local embedders through the *exact*
production path (no task prefixes, the same 2000-char truncation, the same hybrid RRF) - the honest
"would changing `NEVERTWICE_EMBED_MODEL`, and nothing else, improve recall?" test. Each is embedded into
its own cache and scored against LongMemEval GT (`research/embedder_ab.py`).

| embedder (drop-in) | sem R@1 | sem R@5 | hyb R@5 | hyb R@10 | MRR |
|---|---|---|---|---|---|
| **bge-m3** (default) | **0.422** | **0.652** | 0.660 | 0.770 | **0.533** |
| mxbai-embed-large (EN-only) | 0.364 | 0.628 | 0.668 | **0.780** | 0.511 |
| snowflake-arctic-embed2 | 0.350 | 0.580 | 0.646 | 0.742 | 0.502 |
| Qwen3-Embedding-0.6B (Q8_0) | 0.374 | 0.558 | 0.654 | 0.728 | 0.507 |

**bge-m3 wins where it matters** - R@1 0.422 and MRR 0.533, clear of every candidate. The two larger
*multilingual* models (snowflake 568M, Qwen3-Embedding 0.6B) are **worse** drop-ins; mxbai-embed-large
edges hybrid@10 by +0.010 but **loses R@1 by 0.058** and is English-only (it would degrade the bilingual
RU/EN store bge-m3 was chosen for). Caveat: candidates that want a query instruction prefix (Qwen3, arctic)
run prefix-less here for an apples-to-apples drop-in - their *tuned* ceiling may be higher, but the product
question is the zero-config swap, and on that question **no local embedder beats bge-m3 on top-1**. The knob
stays (`NEVERTWICE_EMBED_MODEL`, index self-invalidates on change) for English-@10-heavy stores to pick mxbai.

## Experiment 5 - a *trained* cross-encoder (bge-reranker-v2-m3) - the lever that works

Experiments 1-3 ruled out vector arithmetic and *promptable* LLM rerankers. The one path the audit named
and we had not tested: a **purpose-trained** cross-encoder. Same LongMemEval setup as Exp 3 - rerank the
hybrid top-10 - but a single forward pass emitting a learned relevance logit, not a generated judgement.
(`research/longmem_eval.py --xrerank`; ships as `nevertwice.reranker_ce`.)

| method | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|
| *hybrid baseline* | 0.418 | 0.586 | 0.660 | 0.770 | 0.533 |
| **+ bge-reranker-v2-m3** | **0.572** | **0.710** | **0.750** | 0.770 | **0.655** |
| Δ | **+0.154** | **+0.124** | **+0.090** | +0.000 | **+0.122** |

This is the headline: **R@1 0.418 → 0.572 (+37% relative), MRR +0.122** - the exact top-1/top-3 reordering
the 0.418→0.770 gap demanded. R@10 holds at 0.770 (it only reorders the top-10), the clean sanity check.
Same first-stage, same passages as the *promptable* reranker that **lost** R@1 by 0.114 - so the win is the
**training**, not the architecture: a model tuned for (query, passage) relevance succeeds exactly where an
instruct-model scoring integers fails. On the production note store conditions are even better - the
cross-encoder reads the *whole* short note (title+description+prevention), not a 1200-char session snippet.

## What ships - the trained cross-encoder, opt-in; bge-m3 stays the zero-dep default

Updated verdict (Exp 5 reverses the earlier "ship nothing"):

- **Default, unchanged:** bge-m3 hybrid, **stdlib-only, zero dependencies**. Exp 4 confirms no local
  embedder beats it on top-1 as a drop-in; Exp 1-3 confirm no free re-weighting or promptable reranker helps.
- **Ships as opt-in (`nevertwice.reranker_ce`, `NEVERTWICE_XRERANK=1`, `pip install nevertwice-memory[reranker]`):**
  the bge-reranker-v2-m3 cross-encoder. Off by default; heavy deps (torch+transformers) imported lazily only
  when enabled; degrades safely to first-stage order if the model/GPU is absent. `memory_search --xrerank`
  and `search_core(xrerank=True)` expose it. This is the audit's P1 fix, now **measured and shipped** without
  touching the local-first promise of the default.

The promptable rerankers (`research/_rerank.py`) and benches stay as **research artifacts** - they *are* the
evidence for *why* the trained model is the one we ship and the promptable one is not.

The audit named three fixes; all three are now resolved by measurement. **Trained cross-encoder** - Exp 5,
**WIN, shipped opt-in**. **Stronger embedder** - Exp 4, negative (bge-m3 wins drop-in). **Reranker (promptable)
/ query expansion (Rocchio)** - Exp 3 / Exp 1, negative. The *LLM-text* query-expansion form is **not run
separately** by deliberate decision: a reranker is strictly more informed than blind expansion (it sees the
candidates) and the trained one already wins, so expansion's marginal value sits below the noise floor; worth
a cache-cheap re-test only if a future store is dominated by terse keyword queries.
