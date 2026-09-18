# Retrieval as posterior inference (results & findings)

*Companion to `research/posterior_model.py`. Reproduce: `python research/posterior_model.py --save`
(CPU, seeded, ~2.4 s). Fit on the longitudinal benchmark world (`LONGITUDINAL_BENCH.md`), train
seeds {0,1,2,3}, held-out test
seeds {4,5}; relevance = semantic cosine (no lexical/RRF - this isolates the salience stack).*

## The model

Nevertwice ranks with a hand-tuned stack: cosine relevance + an additive log-recurrence boost +
a multiplicative salience re-weight (recency decay × resolved × confidence). This is exactly the
log-posterior of one generative model, `score(m│q) = log P(q│m) + Σ_k log P_k(m)`:

| term | distribution | log-contribution |
|---|---|---|
| relevance likelihood | `P(q│m) ∝ exp(cos/T)` | `w_cos · cos` (temperature link) |
| frequency prior | `P_freq ∝ nᵝ` | `w_freq · log n` |
| recency (survival) | `P_rec ∝ exp(−λ·age)` | `w_rec · age` (hazard, not a fixed half-life) |
| reliability | `P_rel ∝ exp(β·conf)` | `w_conf · conf` |
| status gate | resolved/superseded | `w_res · [resolved]` |

So per-query ranking is **linear** in `(cos, log n, age, conf, resolved)` - a conditional-logit /
Plackett-Luce top-1 model. We fit the weights by maximum likelihood (the per-query softmax) on the
train seeds and evaluate generalization on held-out seeds.

## Results (held-out test, recall@1)

| ranker | R@1 | R@3 | R@5 | MRR | nDCG |
|---|---|---|---|---|---|
| relevance-only (cosine) | 0.673 | 0.892 | 0.978 | 0.792 | 0.843 |
| heuristic (shipped constants) | 0.699 | 0.917 | 0.985 | 0.814 | 0.861 |
| **posterior (fitted)** | **0.769** | 0.957 | 0.995 | 0.864 | 0.899 |

- **Posterior − heuristic = +0.070 recall@1** on held-out worlds. Honest caveat: the posterior is
  fit to recall on this world's distribution, so out-performing the hand-tuned constants
  in-distribution is *partly by construction* - it is not a free lunch. The contribution is that
  the stack **is** a calibratable, interpretable posterior whose priors can be read off and fit,
  and that calibration recovers recall the mismatched hand-tuned constants leave on the table.

## What the fit reveals

**Fitted weights (standardized).** `cos +1.75` (relevance dominant), `log_recurrence +1.02`
(strong frequency prior), `age −0.52` (older ⇒ less useful - the survival model's sign, vs the
shipped fixed 365-day half-life), `confidence −0.02` and `resolved +0.10` (near-zero **here**).

**Leave-one-prior-out (recall@1 lost when a prior is removed and refit).**

| prior removed | Δ recall@1 |
|---|---|
| frequency (recurrence) | **−0.058** |
| recency | −0.000 |
| reliability (confidence) | +0.001 |
| status (resolved) | −0.000 |

**Frequency (recurrence) is the load-bearing prior** - the single most valuable signal beyond
relevance, validating the recurrence-as-salience thesis from the other direction. The
recency/confidence/status priors contribute ≈0 **in this world** - *not* because they are useless
in general, but because the benchmark world does not make old / low-confidence / resolved notes less
likely to be the answer (no such correlation is built in). The honest reading: a calibrated
posterior **learns to down-weight priors that are uninformative for the workload** - which is
precisely why it beats the heuristic that applies them uniformly. A real corpus where age or
confidence is predictive would show different contributions; the *method* (fit the weights) is the
point, not these specific magnitudes.

**Calibration (ECE = 0.004).** The predicted P(target│pool) matches the empirical target rate
across the whole range (e.g. 0.45→0.44, 0.85→0.85, 0.97→0.98) - the relevance link is
well-calibrated on held-out data, so `P(q│m)` genuinely predicts relevance rather than merely ordering it.

## Shipped: `NEVERTWICE_RANKER=posterior`

`retrieve_relevant` gains an opt-in posterior mode: it replaces the additive-recurrence +
multiplicative-salience tail with the explicit log-linear posterior
`w_rel·log(rrf) + w_freq·log(n) + w_sal·log(salience)` (`POST_W`, env-tunable). Default ranker
stays `hybrid` (zero regression - all suites green). Recurrence enters as a true frequency prior
`nᵂ` rather than an additive ε. This static log-linear form is the basis the feedback bandit
(`BANDIT.md`) learns online.

## Caveats

Relevance here is semantic cosine only (no lexical/RRF), so absolute numbers differ from the
longitudinal benchmark's fused leaderboard - this isolates the salience-stack question. The priors are inert on a
no-recurrence/no-metadata corpus (LongMemEval: recurrence ≡ 1, no age/confidence), where the
posterior reduces to relevance-only - consistent with that benchmark's by-construction inertness.
The +0.070 is in-distribution generalization across seeds of one synthetic world, not a real-trace
result; that is what the bandit's implicit-feedback stream and the real-trace replay
(`REAL_TRACE.md`) are for.
