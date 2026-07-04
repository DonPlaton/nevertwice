# 2C — Rare-event / black-swan memory (results & findings)

*Companion to `research/rare_event.py`. Reproduce: `python research/rare_event.py --save`
(CPU, seeded, ~0.5 s). 10 common clusters × 20 notes + 8 rare precursors, top-5, W=0.35.*

## The thesis & the productive tension (1A ↔ 2C)

1A's recurrence prior says *frequent ⇒ valuable* — right for the gotcha you keep hitting. A
black-swan **precursor** is the exact inverse: seen rarely (once, before the last regime change)
yet decisive. Frequency weighting **buries** it. The fix is the opposite term — an
**inverse-frequency × consequence** salience that up-weights the rare-but-high-impact memory.
The same boost that helps recurring-lesson recall therefore *hurts* tail recall: frequency and
inverse-frequency are **opposite priors**, and no single global salience is right for both.

The world makes this concrete: common patterns recur (high recurrence, no consequence); rare
precursors sit just *off* a normal cluster (subtle — so relevance is ambiguous), with low
recurrence and a high consequence, each preceding a catastrophe by a lead time.

## Results (top-5, 6 seeds)

| salience | TAIL-recall | COMMON-recall | false-alarm |
|---|---|---|---|
| relevance | 0.548 | 0.685 | 0.221 |
| **recurrence (1A)** | **0.013** | 0.637 | 0.001 |
| **rare-event (always-on)** | **0.948** | 0.527 | **0.901** |
| **rare-gated (relevance-gated)** | 0.758 | 0.661 | 0.477 |

- **Frequency weighting is catastrophic for the tail: 0.013.** The recurrence boost (1A) lifts the
  common notes over the rare precursor, so the black-swan analogue is essentially never recalled —
  a vivid demonstration that the recurrence prior is *actively wrong* for tail-risk queries.
- **Inverse-frequency × consequence recovers it: 0.948.** The rare precursor is surfaced where
  frequency-weighting buried it — and warned **+3.7 steps** of lead time before the catastrophe
  (vs 0.06 for recurrence).
- **…but always-on, it cries wolf: false-alarm 0.901.** Up-weighting every high-consequence note
  surfaces a precursor on 90% of *normal* queries too — impractical.
- **The resolution — relevance-gating** (`cosine · (1 + W·rare_salience)`, so consequence amplifies
  *relevance* instead of overriding it): tail-recall stays high (0.758), common recall is kept
  (0.661 ≈ recurrence's 0.637, *no cost*), and false alarms are roughly halved (0.477). That is the
  practical risk operating point on the sensitivity↔specificity frontier.

## Reading

- No single **global** salience wins both query kinds — the frequency/inverse-frequency tension is
  real, and the right design is **context-activated** risk weighting (a risk mode), partially
  unified by relevance-gating.
- Even gated, false alarms (0.477) exceed the relevance baseline (0.221): an **irreducible cost** —
  subtle precursors look like normal patterns, so sensitivity to them necessarily admits some false
  alarms. For a quiet default you activate risk mode only when hunting tail risks.
- This connects tail-risk / black-swan forecasting to the memory core: tail-risk recall is a
  salience-weighting choice, and the recurrence machinery already in place is exactly what must be
  *inverted* for it.

## Scope

Research demonstration of the salience inversion — **no core schema change**. A production
risk-mode would add a `consequence`/`impact` field on notes (analogous to `confidence`) and a
relevance-gated risk weighting; without that field the term is inert, so it is kept out of the
general-purpose core (the same discipline as 2A). Synthetic, seeded; the lead-time is a structural
proxy (warning issued ⇔ precursor recalled), not a forecasting result.
