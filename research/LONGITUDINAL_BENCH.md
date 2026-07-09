# A recurrence-bearing longitudinal benchmark (results & findings)

*Companion to `research/longitudinal_bench.py`. Reproduce: `python research/longitudinal_bench.py --save`
(CPU, seeded, ~15 s). Numbers below: 40 topics × 8 near-dup lessons, 1600 sessions × 6 seeds,
σ = per-query crisp/ambiguous mixture. This is a **mechanism** benchmark on synthetic latent
vectors + token bags - it makes recurrence and ambiguity controlled variables; it is **not** an
embedder test (that is LongMemEval) and absolute numbers are not quoted as external quality.*

## Why it exists

LongMemEval gives a real external recall number, but every session there is distinct
(recurrence ≡ 1), so it **cannot** test whether exploiting lesson recurrence helps. No public
benchmark carries a natural recurrence signal. This benchmark builds one: an agent works tasks over many
sessions, the same gotchas recur with Zipf frequency, memory accumulates with supersession, and
a query at session *t* must recall the lesson answering it from everything written so far. It is
the shared infrastructure the method studies (the posterior model, the bandit, the forgetting
coreset) are measured on, and it emits the implicit-feedback log the bandit learns from
(`--emit-feedback`).

## Headline (recall@1, mean ± 95% CI over 6 seeds)

| ranker | R@1 | R@3 | R@5 | MRR | nDCG |
|---|---|---|---|---|---|
| lexical | 0.641 | 0.934 | 0.991 | 0.788 | 0.842 |
| semantic | 0.673 | 0.900 | 0.980 | 0.794 | 0.845 |
| **hybrid (RRF)** | **0.766** | 0.953 | 0.981 | 0.858 | 0.890 |
| hybrid + recurrence (fixed) | **0.790** | 0.962 | 0.994 | 0.876 | 0.907 |
| hybrid + recurrence (adaptive) | 0.783 | 0.960 | 0.994 | 0.872 | 0.904 |
| shipped (+salience) | 0.656 | 0.875 | 0.968 | 0.780 | 0.834 |

- **Hybrid RRF beats both single signals** (0.766 vs 0.673/0.641) - validates the production
  fusion on external (not internal-linkage) ground truth.
- **Recurrence adds +0.023 over recurrence-blind hybrid**, and it is *concentrated where the
  theory says it should be* - recurrence-aware minus blind, stratified by the target's recurrence:
  `1: −0.036 · 2-3: −0.019 · 4-7: +0.009 · 8+: +0.044` (monotone). The frequency prior pays off
  for genuinely high-recurrence targets and gently hurts one-offs competing against recurring
  distractors - exactly `P(target) ∝ recurrence`.
- **Recurrence's value grows with ambiguity** (Δ recall@1 vs σ: `0.3→+0.006, 0.85→+0.031,
  1.4→+0.032`) - the validation LongMemEval could not give, now with recurrence present.

## Findings (the critic pass) and what was fixed

**The recency-decay term buried old-but-recurring lessons (FIXED).** `shipped` ranked
*below* recurrence-blind hybrid because `_salience_mult` decays by a note's **creation** date:
a lesson logged long ago but repeatedly relevant still decayed, and the tiny additive recurrence
tiebreak could not offset a multiplicative 0.5× decay. Fix: recurrence now **slows forgetting** -
effective age `= age / (1 + log n)` (a frequency prior on survival, no schema change). shipped
recall@1 0.624 → **0.656**; the high-recurrence strata recover most (8+ bucket 0.593 → 0.639).
The residual shipped < hybrid+recur gap is **intended** salience behaviour (confidence- and
resolved-aware nudging optimises "what's worth surfacing", which single-target recall does not
reward) - not a bug.

**Duplicated, undocumented recurrence coefficients (FIXED).** Two boosts live on two scales:
`RETRIEVAL_RECUR_BOOST` (~0.03, raw-cosine paths) and an *inline* `0.0003` in `retrieve_relevant`
(fused-RRF path, whose adjacent-rank gap is ~1/60). The inline magic number is now the named
`RETRIEVAL_RECUR_RRF_BOOST`, documented as a deliberate gentle tiebreaker. The coefficient sweep
confirms ~0.0003 is Pareto-optimal here (larger values hurt crisp queries).

**Adaptive recurrence is inert/negative at the shipped coefficient (OPEN, documented).** At the
0.0003 tiebreaker scale recurrence cannot re-rank a non-tie, so the ambiguity-adaptive machinery
(`_ambiguity`, `AMBIGUITY_K`, `ADAPTIVE_RECUR`) changes nothing (Δ = −0.007 here). It only earns
its keep at coefficients large enough to re-rank (max Δ = +0.051 at coef 0.03) - but no
large-coef+adaptive setting beats the small fixed tiebreaker (0.790). The recurrence ablation
showed adaptive helping in a *different* regime (crisp queries targeting low-recurrence items), so
the honest conclusion is that a **global** adaptive rule is workload-fragile: what is actually
wanted is a **per-query calibrated weight** - precisely the motivation for the calibrated
**posterior model** (`POSTERIOR_MODEL.md`). Left
in place (cheap, env-disablable) pending it; not removed.

**`_ambiguity` is computed from semantic sims only**, ignoring the lexical contribution to
the fused ranking; when lexical is decisive but semantic is bunched the adaptive scaling misfires.
Immaterial at the shipped tiebreaker coefficient (see the coefficient finding above); noted for
the posterior model.

**Benchmark self-audit (bug found and fixed during construction).** The first cut ranked by
*insertion order*: it passed the world's numpy vectors to `memory_hook.cosine`, which requires
python `list`s and returns 0.0 on a numpy array - so all cosines were 0 and the recurrence-blind
"semantic" baseline spuriously *rose* with recurrence (early-inserted = high-propensity). Fixed by
a unit-vector dot-product (`_cos`, ≡ m.cosine for normalised vectors); pinned by
`_test_longitudinal_bench.py` so it cannot silently return.

## Other measurements

- **Temporal ambiguity.** A supersession-blind "use-all" store surfaces **3.39 versions/query**
  for revised lessons; the bi-temporal store returns 1 (the live version) - consistent with
  eval_harness Task B, now in the longitudinal setting.
- **Learning curve.** Static-ranker recall@1 declines 0.72 → 0.57 across the timeline as the
  candidate pool fills - the baseline the feedback-learning bandit (`BANDIT.md`) aims to beat.

## Caveats

Synthetic vectors/tokens make ambiguity and recurrence controllable; this measures the **ranker**,
not an embedder. `P(target) ∝ recurrence` is built into the world (the thesis under test), so the
recurrence lift is a *consistency* result for the mechanism, not evidence that real agent
workloads have this structure - that is what the real-trace replay (`REAL_TRACE.md`) and the
bandit's implicit-feedback stream are for.
