# Calibrated score fusion: why rank fusion leaves recall on the table

A short, reproducible study of how Nevertwice fuses its semantic and lexical signals, and
why the shipped ranker changed from reciprocal rank fusion (RRF) to calibrated score
fusion. Every number here is on the public LongMemEval-oracle stand (940 sessions in one
shared store, 500 human-annotated questions, local bge-m3 embedder), scored exactly as
`longmem_eval.py` does. Reproduce with `python nevertwice/research/longmem_eval.py`.

## The question

A memory recall query has two signals: semantic similarity (embedding cosine) and lexical
match (term overlap). The usual way to combine them is RRF, which fuses the two ranked
lists by summing `1/(k + rank)`. RRF is popular because it needs no tuning and no shared
score scale. The cost is that it throws the score magnitudes away: a candidate that BM25
scores far above everything else contributes the same `1/(k+1)` as a candidate that barely
edged into first place.

We measured what that costs.

## Result

| method (same bge-m3) | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|
| semantic only | 0.422 | 0.580 | 0.652 | 0.728 | 0.528 |
| lexical only (BM25) | 0.522 | 0.678 | 0.752 | 0.834 | 0.623 |
| RRF (semantic + token-overlap, the old default) | 0.418 | 0.586 | 0.660 | 0.770 | 0.533 |
| RRF (semantic + BM25) | 0.468 | 0.646 | 0.708 | 0.790 | 0.580 |
| **calibrated fusion (shipped)** | **0.550** | **0.722** | **0.802** | **0.858** | **0.657** |
| + trained cross-encoder (opt-in) | 0.614 | 0.796 | 0.826 | 0.858 | 0.712 |

Two things stand out. First, RRF over the two signals (0.708 R@5) scores *below plain BM25*
(0.752): rank fusion actively discarded the strong lexical magnitude. Second, calibrated
fusion lifts R@5 from 0.66 to 0.80, a 21 percent relative gain over the old default, and
takes top-1 from 0.42 to 0.55.

The mechanism is simple. Z-normalise each signal's scores over the candidate set (mean 0,
sd 1), add them with a fixed weight, and map the sum through a logistic to a positive
score. A candidate missing from one signal gets a low standin for that signal. That is it.
The whole thing is in `memory_hook._calibrated_fusion`, about twenty lines, standard
library only.

```
fused(d) = logistic( w * z(cosine)[d] + z(bm25)[d] )
```

It is robust, not a tuned knife-edge. Across a dense-weight sweep from 0.4 to 1.0 every
setting beat the strongest local competitor on this stand:

| dense weight | R@5 |
|---|---|
| 0.4 | 0.804 |
| 0.5 (default) | 0.802 |
| 0.6 | 0.800 |
| 1.0 | 0.788 |

Calibrated linear score fusion is itself classic information retrieval (CombSUM, Fox and
Shaw, 1994). The contribution here is the measurement: on agent-memory recall, with a
modern multilingual embedder, it decisively beats the rank fusion that most current systems
ship, and it does so without a reranker or any new dependency.

## What we tried and cut (honest negatives)

We did not stop at the first thing that worked. TRIZ-style, we pushed on several axes and
report what failed as plainly as what won.

**Pseudo-relevance feedback** (expand the query with top terms from the first results) hurt
badly: R@1 fell to 0.40. On this stand the expansion adds noise faster than signal.

**Structure-aware spreading activation** (induce a kNN graph over sessions, then propagate
score from a hit to its neighbours) hurt as well, even on the multi-session questions it was
meant to help. The induced graph links topically similar sessions, but a question's several
evidence sessions are linked by the question's logic, not by mutual similarity, so
propagation only pulls in distractors. This is the same lesson as the recurrence prior:
structure helps on a genuinely linked vault (where Nevertwice itself links a bug to its fix
to the pattern that resolved it), and is inert to harmful on a corpus of independent
sessions. It stays a production feature with an honest "inert on this benchmark" note.

**A multiplicative semantic gate** (damp lexical hits whose cosine is low) did not improve
over plain calibrated fusion, so it is not shipped.

**Chunk-level dense retrieval** (embed each session in pieces and score by the best piece,
late-interaction style) is the one cut that needs explanation, because it *did* help on this
stand: it lifted R@5 to 0.814. We still do not ship it, for a principled reason. The gain
comes entirely from the documents being long: a LongMemEval session is thousands of tokens
of raw dialogue, and a single vector for the whole thing is blurry, so scoring the best
chunk recovers the buried evidence turn. Nevertwice does not store raw sessions. It stores
distilled notes, each about one screen, already concentrated on the durable fact. For a
short note the whole-note vector already is the focused signal, so chunking it gives roughly
one chunk and the gain collapses while the cost (several vectors per note, a larger index)
stays. In other words, our distillation front-end already buys what late-interaction buys
elsewhere. Shipping chunk-dense would be paying twice. It remains here as a measured finding.

## Launch-round R&D: four more axes, one win, three honest negatives

Before publishing we ran another deliberate push on the ranker, and ship only what beat the
number above. The full battery is `research/rnd_launch.py` (instant, it reuses the cached
vectors), `research/splade_eval.py`, and `research/QUANTIZATION.md`.

**Learned-sparse (SPLADE) as the lexical arm did not beat BM25.** We encoded all 940 sessions
and 500 questions with `naver/splade-cocondenser-ensembledistil` and fused the sparse signal
the same way. SPLADE alone scored R@5 0.708 and `calibrated(dense, SPLADE)` 0.720, both below
`calibrated(dense, BM25)` at 0.802. Even the union of the two lexical signals (0.768) trailed
BM25 by itself. SPLADE truncates a long session to a passage window while BM25 reads the whole
session, and its term expansion adds noise on specific recall queries. The result is the good
kind of negative: the stdlib BM25 is both better here and free of a torch dependency.

**Online-learned fusion weights had no headroom.** A weight sweep is the oracle a bandit can
only approach, and it shows the fixed 0.5 is already Pareto-optimal: 0.4 buys two questions at
R@1 (0.554) but gives them back at R@5 and R@10, while 0.5 leads on R@5, R@10, and MRR
together. With no fixed weight beating 0.5 across the board, an adaptive learner has nothing to
win, so the weight stays fixed.

**A cross-signal agreement bonus, robust normalisation, and per-query adaptive weighting all
lost.** Adding a `z(cos)·z(bm25)` interaction term (reward candidates both signals rank high)
cost three to four points of R@1 at every strength tried. Swapping the z-score for a
median/MAD robust normalisation dropped R@5 to 0.754, the score pools are not heavy-tailed
enough to need it. Setting the dense weight per query from the lexical peakedness matched R@1
but lost R@5 (0.782). On this stand, plain z-score CombSUM at a fixed 0.5 is the ceiling, and
we say so rather than ship a more complicated ranker that does not pay.

**Binary quantization was the one win, and it is about scale, not recall.** One sign bit per
dimension shrinks the index 32x against the float cache at a four-question R@5 cost, and turns
the brute-force scan into an integer popcount that is about 1100x faster, which is why
Nevertwice needs no ANN dependency to reach a hundred thousand notes. Full study and the
no-database verdict: `research/QUANTIZATION.md`. It ships off by default as
`NEVERTWICE_EMBED_QUANT=binary`.

## What shipped, and why the benchmark matches it

Production (`retrieve_relevant`, and the on-demand `memory_search`) uses calibrated fusion of
whole-note semantic and BM25, with the existing recurrence and salience tail preserved. The
benchmark uses the same algorithm at session level, so the published number is the shipped
ranker, not a flattering variant. `NEVERTWICE_FUSION=rrf` keeps the old behaviour as a
fallback. The trained cross-encoder remains an opt-in second stage that stacks on top
(R@1 0.55 to 0.61 here).

## Reproducibility

Ranking ties are broken deterministically by session id, so recall@k is stable across runs
(Python set iteration order, otherwise randomised per process, was making top-1 wobble by a
point). The recurrence tiebreak constant is scaled to the score range in use (logistic
scores need a larger constant than RRF's `1/60` gaps) and is inert on a no-recurrence corpus,
so it never moves this benchmark.

*Run it:* `python nevertwice/research/longmem_eval.py [--xrerank]` and
`python nevertwice/research/head_to_head.py --only=nevertwice,mem0,langmem,amem`.
