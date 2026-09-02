# Real-trace recurrence validation (results & findings)

*Companion to `research/real_trace_bench.py`. Reproduce on any populated store:
`NEVERTWICE_VAULT=/path/to/vault python research/real_trace_bench.py --save` (CPU, no Ollama -
reads the cached vectors only, ~3 s). Numbers below are from one real accumulated vault of
**328 embedded notes across 11 projects**; rerun on yours for your own figures.*

## Why it exists

The longitudinal benchmark (`LONGITUDINAL_BENCH.md`) built a world where `P(target) ∝ recurrence`
is **true by construction** - so its
recurrence lift is a consistency result for the ranker, not evidence that real agent workloads
carry that structure. Its own caveat named the gap and deferred it to a real-trace study. This is
that study. It closes the gap on a live store, with two questions:

1. **Does genuine cross-session recurrence exist** in a real accumulated memory?
2. **Does the production slug-based recurrence counter capture it?**

## Method (privacy-safe by construction)

Read **only** the local embedding cache - vectors + metadata, never raw note text. Within each
project, greedily cluster notes at cosine ≥ a swept threshold (bge-m3 cosines bunch near a high
background ≈ 0.42, so the threshold is swept *above* it - 0.50/0.55/0.60 - not the near-exact
0.92 the dedup uses). A cluster spanning **>1 date** is genuine cross-session recurrence: the
same lesson re-encountered in a later session and re-written as a *distinct* note (different slug).
Output is aggregate only - counts, fractions, recall numbers; no titles, descriptions, projects,
or stems are printed or saved.

## Headline

| cosine ≥ | clusters | cross-session (>1 date) | notes in clusters |
|---|---|---|---|
| 0.50 | 70 | 41 | 292 (89%) |
| **0.55** | **81** | **37** | **244 (74%)** |
| 0.60 | 74 | 19 | 183 (56%) |

**Slug-based recurrence > 1 (what production actually recorded): `0` of 328 notes.**

> **~37 genuine cross-session recurring topics exist at cosine ≥ 0.55, but the slug counter
> recorded 0.** Real recurrence is present yet **slug-invisible**: the extractor rephrases each
> occurrence, so exact-slug matching never fires. Recurrence must be detected by **semantic
> aggregation** (supersession / consolidation), not slug equality. This is the live-data
> justification for the recurrence-loss fixes (carry recurrence across supersession, merge,
> and `--rebuild`) - without semantic aggregation the production counter is structurally blind to
> the very signal the longitudinal benchmark shows is valuable.

## The honest negative - recurrence is NOT a recall re-ranker

The tempting next step is "boost recurring notes at recall time." We **falsified** it on real
data. Query = a member of a cross-session cluster; ground truth = its same-cluster, other-date
members (the same topic seen in another session). Rank in-project notes by relevance, then by
relevance + an additive recurrence prior (`w · log(cluster size)`) swept across weights:

| prior weight w | recall@3 |
|---|---|
| **0.00 (relevance-only)** | **0.710** |
| 0.01 | 0.710 |
| 0.02 | 0.710 |
| 0.03 | 0.679 |
| 0.05 | 0.611 |
| 0.08 | 0.573 |
| 0.12 | 0.489 |
| 0.20 | 0.374 |

**No weight beats relevance-only** (131 queries). Relevance alone is already at ceiling for
same-topic recall; an additive cluster-size prior can only add noise - it promotes members of
*other* large clusters regardless of query relevance. The result is monotone past w ≈ 0.02.

This is not a defeat for recurrence; it **locates** it. Recurrence earns its place in
**retention / consolidation / decay** - *what to keep* when the store is over budget, and *how
slowly a note forgets* (the decay fix: effective age `= age / (1 + log n)`) - **not** in recall
ranking, where relevance already wins. It is consistent with the longitudinal benchmark's own stratified result: the
recurrence lift there concentrated entirely in the highest-recurrence bucket and *hurt* one-offs;
on a real store, where the recall ground truth is already the most-relevant note, that trade has
no upside left to capture.

## What this changes

- **Validates** the supersession/merge/rebuild recurrence-carry fixes on real data: the
  counter is provably blind without them.
- **Validates** placing recurrence in the salience/decay and coreset paths, not the recall scorer.
- **Counsels against** a naive "recurrence boost" on the retrieval hot path - measured here to be
  neutral at best, harmful past a small weight.

## Does recurrence belong in the *cap*? (`retention_bench.py`)

The negative above was about recall. The natural follow-up: the production per-project cap
(`consolidate_memory.cap_project_notes`) weights its keep-utility by `recurrence` - does that help
retention, where the longitudinal benchmark and `FORGETTING.md` showed recurrence pays off on synthetic data? We measured the
**shipped** `select_coreset` (no new production code) on the same store, under three keep-utilities,
at two budgets, scoring **durable-topic retention** = fraction of cross-session topics keeping ≥1
member:

| budget | coverage (u≡1) | slug (shipped) | semantic (u=cluster size) | Δ sem−cov |
|---|---|---|---|---|
| keep 50% | 0.865 | 0.865 | 0.838 | **−0.027** |
| keep 70% | 0.973 | 0.973 | 1.000 | +0.027 |

| members kept per surviving topic | coverage | slug | semantic |
|---|---|---|---|
| keep 50% | 2.06 | 2.06 | 3.81 |
| keep 70% | 2.58 | 2.58 | 3.54 |

Two findings, both honest:

1. **`slug ≡ coverage`, exactly.** The shipped cap's `recurrence·resolved` utility is *inert* on
   real data (recurrence ≡ 1), so today's cap already behaves as pure facility-location coverage -
   which preserves 86.5% / 97.3% of durable topics at 50% / 70% budget. The cap is therefore
   **safe to run** (non-destructive too: excess is archived to `<folder>/Archive/`, not deleted).

2. **Semantic recurrence in the cap is NOT a win - it hoards.** Weighting by cluster size keeps
   **3.7 vs 2.3** members per topic: it stockpiles redundant copies of big clusters, *helping* at a
   loose budget (+0.027) but *hurting* at a tight one (−0.027) where that redundancy crowds out
   other topics. The submodular coverage objective already preserves durable topics without it.
   **Conclusion: keep the coverage objective; do not add recurrence weighting to the cap** - it
   would be intuitive-but-bloat. (The honest verdict is judged on the *worst* budget and the
   hoarding ratio, not a cherry-picked best - pinned by `_test_retention_bench.py`.)

## The full triangulation - the prior is *dormant* on a real young store

The two studies above rule out recall and the cap. The last channel is the salience-decay slowdown
(effective age `= age/(1+log n)`). On this store it is inert for the *same* reason: with recurrence
≡ 1, `1+log(1) = 1`, so the slowdown never fires. And the store is too young for decay to matter at
all - ages span 2-44 days against a 365-day half-life (≤ 8% decay on the oldest note), so even a
*semantic* recurrence count of, say, 8 would move salience ≤ 5%.

So the honest, complete picture: the frequency prior is **dormant across all three channels**
(recall boost, cap utility, decay slowdown) on a real single-user store - not because the mechanism
is wrong (the longitudinal benchmark validates it where recurrence is present and stores age) but because the recurrence
**signal** is slug-invisible (≡ 1) and the store is young. Capturing recurrence semantically would
revive only the decay channel, and only marginally on an aged store - recall and the cap don't
benefit even when fed semantic recurrence, as measured above. The prior's near-term value on real
single-user memory is small; it grows with store **age** and with multi-agent **reuse** that makes
the same lesson genuinely recur. This is the load-bearing honesty caveat for the headline claim.

## Caveats

One real store (328 notes, 11 projects); absolute cluster counts and the retention numbers are
workload-specific - rerun for your own. Cosine clustering is a *proxy* for "same lesson
re-encountered": a high-cosine cross-date pair is strong evidence but not a human-verified label
(the privacy constraint forbids reading the text to adjudicate). The retention test scores keeping
≥1 member per durable topic; it does not model query-time value, and "durable topic" is itself
defined by cross-session recurrence - so it measures *diversity-preservation under budget*, the
property the cap exists to protect.
