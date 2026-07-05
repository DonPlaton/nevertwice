# 4A - Abstractive consolidation: the other half of memory's value (results & findings)

*Companion to `research/abstractive.py`. Reproduce: `python research/abstractive.py --save`
(CPU, seeded, ~5 s; `--real` adds the live-vault candidate count). A **mechanism** benchmark on
controlled latent vectors - it isolates *when and why* turning episodes into a principle helps; it
is not an embedder test, and absolute recall is not an external quality number.*

## Why it exists

The real-trace studies settled two things on a live store: relevance already **saturates episode
recall** (recall@3 ≈ 0.71; no recurrence prior beats it - 3A.2) and the frequency prior is
**dormant** (3A.2/3A.3). That forces the field's load-bearing question: beyond a good retriever over
raw logs, what is memory *for*? The dormancy result points at the answer - memory's marginal value
is **abstraction** (many episodic instances of a lesson → one reusable principle) and **forgetting**
(coverage-preserving compression, already validated on real data in 3A.3). This module builds and
stress-tests the abstraction half, the piece no prior agent-memory benchmark isolates.

## The mechanism

A lesson recurs across sessions as K *episodic* notes - each the same latent rule **r** seen through
a different, noisy context (3A.2 found 37 such cross-session clusters on a real vault, slug-invisible).
Each episode is `unit(α·r + β·contextᵢ + noise)`: the rule is shared across the cluster, the context
and noise are instance-specific and zero-mean across it. **Consolidation** replaces the cluster with
one *principle* = the unit-mean of its members. Averaging is denoising: the shared **r** reinforces
while the off-rule components cancel, so the principle recovers the rule that no single context-bound
episode reveals cleanly. The off-rule variance falls ~1/K (recovery gain ~ √K).

## Headline (D=128, R=20, 8 seeds ±95% CI)

**Rule recovery - cosine to the TRUE latent rule (K=8):**

| context β | mean episode | principle | gain |
|---|---|---|---|
| 0.3 | 0.241 | **0.573** | +0.332 |
| 0.6 | 0.141 | **0.374** | +0.233 |
| 1.0 | 0.084 | **0.233** | +0.148 |

**Variance reduction - recovery gain grows with cluster size K (β=1.0):**

| K | mean episode | principle | gain | compression |
|---|---|---|---|---|
| 2 | 0.087 | 0.123 | +0.036 | 2× |
| 4 | 0.080 | 0.158 | +0.078 | 4× |
| 8 | 0.084 | 0.233 | +0.148 | 8× |
| 16 | 0.085 | 0.323 | +0.238 | 16× |

The single-episode signal is K-independent (≈ 0.085); only the principle improves with K - the
signature of averaging-as-denoising.

**Downstream - novel-context rule recall@1 (β=0.3, K=8):** episodic **0.206** → consolidated
**0.439** (+0.233) at **8× compression**. The cleaner rule recovery *translates* to retrieval of
the right lesson for an unseen application.

## The honest boundaries (the critic pass)

- **Consolidation amplifies a present signal; it cannot manufacture one.** At β = 1.0 the rule is
  buried under context, and novel-context recall **collapses toward chance for both stores** (epi
  0.059, con 0.095, 1/R = 0.05). The headline recall lift lives in the regime where the rule is
  recoverable at all (low/moderate β); we report it at a discriminable operating point and show the
  collapse, rather than quoting a best-case number.
- **The downstream recall metric is near-chance at moderate β** by construction (R-way retrieval
  under heavy context noise). That is why the **primary** result is the distractor-free *rule
  recovery* cosine (robustly positive at every β), with recall@1 shown only as the downstream
  translation at a regime where it is meaningful - not as the headline.
- **Instance detail is sacrificed.** A query about a *specific* past instance can no longer recover
  that instance from a principles-only store (rule-level recall stays ≈ 1.0, but the episode's
  specific context is gone). The system mitigates this exactly as the cap does: archive the episodes
  to `Archive/` and link principle → episodes, so detail is one hop away, not lost.
- **Mechanism, not embedder.** Synthetic latent vectors make β and K controlled variables; the
  *relative* ordering (principle > episode, gain ↑ with K) is the claim, not the absolute cosines.
- **Vector-mean is an idealisation of LLM synthesis (the load-bearing gap).** The benchmark's
  principle is the unit-mean of the cluster's *vectors*; a production consolidation would have an LLM
  summarise the cluster's *text* into a principle and then embed it. These are different operators.
  This result is therefore evidence that the *ideal* aggregation recovers the rule and helps - a
  motivation and an upper bound - **not** proof that LLM synthesis attains it. Closing that gap needs
  a real consolidation step measured on a downstream agent task (future work), which is also why we
  do **not** ship LLM principle-synthesis on the strength of this benchmark alone (anti-bloat: 3A.2/
  3A.3 already showed intuitive recurrence uses that did not survive measurement).
  **[GAP CLOSED 2026-06-17 - `CONSOLIDATION_EVAL.md`]** The real operator is now measured on the live
  store (108 leave-one-out queries): the LLM-synthesised principle recovers the held-out occurrence
  *worse* than the vector-mean idealisation (cosine **0.542 vs 0.684**) and worse than the best raw
  episode (**0.642**), and replacing episodes with principles cuts full-store recall@3 **0.824 →
  0.352**. The idealisation does **not** transfer to text synthesis, and - decisively - even the
  model-independent vector-mean ceiling clears the best episode by only +0.042, so no synthesiser
  makes consolidation-by-replacement a same-topic-retrieval win. **Consolidation is therefore NOT
  shipped** (Phase 3): on real single-user data the episodic bi-encoder store beats abstraction for
  the same-topic recall that is ~every real query. 4A stands as the synthetic mechanism; this is the
  honest real-data verdict.

## Real-trace tie-in (aggregate only)

On the live store, **26 cross-session clusters** are consolidation candidates (≥ 3 members spanning
> 1 date), covering **109 episodic notes** - real abstraction opportunities the slug-based system
never aggregates. (Counts only; no note text is read.)

## What it changes

Triangulated with 3A.2/3A.3, the thesis is complete: episode **recall** is saturated by relevance
and the frequency **prior** is dormant on real single-user data - so the defensible marginal value
of agent memory is **abstraction + principled forgetting**. Consolidation is the abstraction
operator, and this is its mechanism benchmark: it recovers the latent rule a recurring lesson
teaches, generalises to unseen applications, and compresses K→1, at a bounded, link-recoverable cost
in instance detail.
