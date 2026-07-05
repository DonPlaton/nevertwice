# 1C - Principled forgetting under a budget (results & findings)

*Companion to `research/forgetting.py`. Reproduce: `python research/forgetting.py --save`
(CPU, seeded, ~1.5 s). Prunes the live store of a 3A run to a budget by four methods,
then measures recall of held-out queries. The selector under test is the SHIPPED one
(`consolidate_memory.select_coreset`), so this benchmarks production, not a fork.*

## The idea

When a store must be capped, *which* notes to keep is a coreset problem. The old cap kept
the highest-salience (recurrence) notes - which over-concentrates on busy topics and can
abandon the long tail. We instead keep the set maximizing utility-weighted **coverage**

    F(S) = Σ_m u(m) · max_{s∈S} sim(m, s)      (facility location - monotone submodular)

so lazy greedy (CELF) is within (1−1/e) of optimal. `sim` is token-Jaccard (sparse via an
inverted index, pure stdlib - no numpy, no N²·dim), `u(m)` is the recurrence (1A frequency)
prior. A utility tiebreak keeps the highest-value representative among near-duplicates.

## What the data shows (6 seeds, budget = fraction of the store kept)

**Coverage and diversity - a clean win (its designed property):**

| budget | metric | coreset | salience-sort | recency | random |
|---|---|---|---|---|---|
| 20% | topics covered (of 40) | **0.854** | 0.717 | 0.758 | 0.742 |
| 20% | kept-set redundancy ↓ | **0.001** | 0.004 | 0.003 | 0.003 |
| 30% | topics covered | **0.979** | 0.875 | 0.900 | 0.867 |

At a tight 20% budget the coreset covers **+0.14 more of the topics** and keeps a set with
**~4× lower redundancy** (mean pairwise cosine) than the salience sort - it does not hoard
near-duplicates of the busy topics while forgetting others.

**Recall - parity (no cost for the coverage gain):**

| budget | recall@3 | coreset | salience-sort |
|---|---|---|---|
| 20% | head (queries ∝ recurrence) | 0.706 | 0.712 |
| 20% | uniform-over-topics | 0.282 | 0.278 |

Head recall (the busy topics - the salience sort's home turf) is barely traded away
(−0.005); uniform-over-topics recall is a statistical tie (+0.005). So the coverage and
diversity gains come at **no recall cost**.

## Honest finding

This is a **coverage/diversity guarantee at recall parity**, not a raw-recall win. On a
*uniformly-active* synthetic world the +0.14 topic-coverage gap does not convert to a recall@3
gap - recall@3 over a large mixed pool is noisy enough to swamp it, and salience already
covers the active topics. The coreset's recall advantage requires either **skewed topic
activity** (genuine tail topics a salience sort would drop entirely) or **within-topic
recurrence concentration** (where the sort piles up near-duplicates); the uniform world has
neither. A surprising honest aside: **recency** is a strong *tail* baseline - recently-created
notes correlate with low recurrence, so "keep newest" happens to retain the tail.

The production change is still justified: the submodular cap is **strictly ≥ on coverage and
= on recall** vs the old salience sort, with a provable (1−1/e) guarantee against catastrophic
topic loss and lower redundancy per kept slot - at no recall downside, opt-in
(`NEVERTWICE_MAX_LIVE_PER_PROJECT`). It is a principled upgrade, not a recall headline, and is
reported as such.

## Shipped

`consolidate_memory.cap_project_notes` now keeps the `select_coreset` set and archives the
rest (was: archive the lowest-salience tail). `select_coreset` is pure stdlib, sparse, and
lazy-greedy; the benchmark imports it directly so the numbers describe the shipped selector.

## Caveats

Synthetic, seeded, uniformly-active world; recall = topic-coverage@3 with crisp queries
(σ=0.3, so the score reflects pruning, not query noise). Token-Jaccard similarity (matching
the stdlib production path); a future variant could use the embedding cosine where vectors are
available. The (1−1/e) bound is for the monotone-submodular coverage objective; the utility
tiebreak does not affect it.
