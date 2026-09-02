# Serendipitous / divergent recall (results & findings)

*Companion to `research/divergent.py`. Reproduce: `python research/divergent.py --save`
(CPU, seeded, ~3 s). 12 clusters × 10 notes + 18 planted bridge notes (each = a normalised
sum of two cluster centroids), top-5 recall, swept over the divergence knob.*

## The idea

Pure-relevance recall is convergent - for a query in cluster *i* it returns the nearest
in-cluster notes and **buries** a *bridge* note that connects *i* to a distant cluster *j*
(a note that could spark a novel connection). A knob `NEVERTWICE_DIVERGENCE ∈ [0,1]` should
trade convergence↔serendipity smoothly. Two divergent modes:

- **MMR** - Maximal Marginal Relevance: `λ·rel − (1−λ)·max-sim-to-selected`, `λ=1−div`. Diverse,
  but diversity ≠ bridging - it spends the budget on *any* dissimilar note.
- **bridge** - `(1−div)·rel + div·bridge(m)`, where `bridge(m)` = product of *m*'s top-2 cosines
  to the cluster centroids (a query-independent **betweenness** proxy: high for a note sitting
  between two clusters). Targets connectors specifically.

## Results

**bridge-recall@5 (a bridge FROM the active cluster surfaced) vs divergence:**

| mode | 0.0 | 0.25 | 0.5 | 0.75 | 1.0 |
|---|---|---|---|---|---|
| relevance | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| MMR | 0.000 | 0.000 | 0.457 | 0.000 | 0.000 |
| **bridge** | 0.000 | 0.072 | 0.862 | **0.945** | 0.649 |

- **Pure relevance buries bridges entirely (0.000)** - the top-5 is all in-cluster.
- **Bridge-aware divergence recovers them (up to 0.945 at div=0.75)** - the headline serendipity result.
- **Diversity alone (MMR) is not enough** - it surfaces a bridge only incidentally (0.457 peak,
  erratic, 0.000 at high div). The non-obvious finding: **serendipity ≠ diversity; it needs the
  structural bridge (betweenness) signal**, not just dissimilarity.
- **Over-divergence hurts** - at div=1.0 the bridge score ignores relevance and picks the
  globally-most-between notes (bridges of *other* pairs), so recall of bridges *from the active
  cluster* drops to 0.649. The optimum is **moderate** divergence (~0.5-0.75).

**Relevance ↔ novelty Pareto (bridge mode, top-5 means):** a smooth, controllable frontier -

| div | relevance | novelty (dist. from home) | cross-cluster rate |
|---|---|---|---|
| 0.0 | 0.908 | 0.050 | 0.000 |
| 0.5 | 0.828 | 0.130 | 0.364 |
| 0.75 | 0.466 | 0.512 | 0.920 |
| 1.0 | 0.134 | 0.857 | 1.000 |

## Shipped

`NEVERTWICE_DIVERGENCE` (default 0 = convergent, no change) re-ranks the top candidates by **MMR**
in `retrieve_relevant` (vector-only - a candidate without an embedding rides its score). This is
the practical "diverse recall" knob: fewer near-duplicate injections, more cross-topic surfacing.
The stronger **bridge-aware** mode needs the cluster graph (centroids/betweenness); that is the
graph-hop production path, left for follow-up - the research above shows it is worth building
(bridge-aware 0.945 vs MMR 0.457 on bridge recovery).

## Caveats

Synthetic, seeded; the LLM-judged "did this spark a *useful* connection" - the genuinely fuzzy
part - is left to a human/LLM study (the roadmap flagged creativity eval as the research risk).
`bridge(m)` is a cheap betweenness proxy (top-2 centroid cosines), not full graph betweenness.
The result is structural: divergent recall *can* surface the connectors a relevance ranker hides,
on a controllable frontier - whether those connectors are *useful* is the downstream question.
