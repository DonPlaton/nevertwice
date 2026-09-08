# Learned salience from feedback (results & findings)

*Companion to `research/bandit.py` (the flagship). Reproduce: `python research/bandit.py --save`
(CPU, seeded, ~1.5 s). LinUCB over the longitudinal benchmark stream (`LONGITUDINAL_BENCH.md`),
6 streams × ~1530 queries; features reuse the posterior model's extraction (`POSTERIOR_MODEL.md`);
recall-utility@1, feedback on the surfaced top-3.*

## The gap it closes

Nevertwice - like almost every agent-memory system - is **static**: it injects memories but
never learns whether an injection *helped*. This study closes that loop. Retrieval is a contextual
bandit: each candidate is an arm with context `x = (relevance, log-recurrence, age, confidence,
resolved)`; the agent surfaces the top arms; the implicit reward is whether the **useful** memory
was among them. Feedback is **partial** - a recall miss teaches nothing about the note we failed
to show - which is the research challenge. A linear usefulness model is learned online with
**LinUCB** (`θ = A⁻¹b`, rank by `θ·x + α√(xᵀA⁻¹x)`, update from surfaced arms only).

## Results (recall-utility@1, held-out streams)

| ranker | overall | second half (post-warmup) |
|---|---|---|
| relevance-only (mismatched static) | 0.673 | 0.634 |
| heuristic (shipped, well-tuned static) | 0.732 | 0.715 |
| **bandit (online, learns)** | **0.737** | 0.703 |
| bandit (shuffled reward - control) | 0.403 | 0.374 |
| oracle θ* (offline optimum) | 0.777 | 0.759 |

- **It learns from the signal, not the mechanism.** The shuffled-reward control collapses to
  0.40 (vs 0.74 with the true reward), and the learned weights converge to the offline optimum:
  `‖θ̂ − θ*‖` cosine distance **0.255 → 0.051** across the stream. Cumulative regret vs the oracle
  is ~62 over ~1530 queries - **sublinear**, the LinUCB signature.
- **It beats a mismatched static ranker by +0.07** (vs relevance-only, post-warmup) - online
  learning recovers the priors a fixed ranker ignores (chiefly recurrence: `θ*` puts the largest
  positive weight, after relevance, on `log-recurrence`).
- **It matches a well-tuned static ranker within noise** (−0.011 vs the shipped heuristic, inside
  the ±0.01 CIs). Honest reading: the shipped heuristic - after the longitudinal bench's
  recency-decay fix - is *already* near
  the linear optimum for this stationary world, so there is little for online learning to add.
  **The bandit's edge scales with how mismatched the static config is**: large vs relevance-only,
  ~zero vs an already-tuned ranker.

## What this establishes (and what it doesn't)

**Establishes (the novelty):** memory that *learns what to remember* - a closed feedback loop that
recovers the offline-optimal salience online, from noisy partial feedback, provably from the signal
(shuffled control) and not by construction (held-out streams, weight convergence). Almost all
agent-memory systems are static; this is the differentiator.

**Does not (honest scope):** a large recall win over an already-well-tuned ranker on a *stationary*
synthetic world. The implicit reward here is the simulator's ground truth - a production hook must
*estimate* it (was an injected stem referenced next session? did a logged mistake recur?), which is
noisier; that estimator is the remaining production work. The setting where online learning
**decisively** beats any fixed config is **non-stationary** workloads (the optimal weights drift as
a project evolves) - the natural next experiment, where a static ranker structurally cannot keep up.

## Deployment (no new production code - reuses the posterior ranker)

The learned `θ` deploys through the **existing** `NEVERTWICE_RANKER=posterior` ranker: its
log-linear weights `POST_W` are exactly the bandit's feature weights, env-settable
(`NEVERTWICE_POST_W_REL/FREQ/SAL`). So this needs **no** hollow new ranker mode - the research learns
the weights, the posterior ranker serves them. Closing the production loop (a `Feedback/` log of
injected stems + next-session outcome attribution feeding an online update) is scoped but not wired
here, deliberately: shipping a bandit ranker with no real feedback source would be dead code.

## Caveats

Synthetic, seeded, stationary world; relevance = semantic cosine (the posterior model's basis). θ* is the best *linear*
reward model (a fair ceiling for a linear bandit, not a global optimum). Standardisation uses global
feature stats (fixed scaling, not label-peeking; the learned part is θ). LinUCB α=1, ridge=1.
