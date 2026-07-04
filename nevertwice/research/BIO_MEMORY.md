# 2A — Replication-weighted, bi-temporal memory for scientific claims (results)

*Companion to `research/bio_memory.py`. Reproduce: `python research/bio_memory.py --save`
(CPU, stdlib, instant). The part of the roadmap that serves the life-extension mission directly.*

## The mapping

The agent-memory primitives map onto longevity-research claims with no new machinery:

| memory primitive | scientific-claims meaning |
|---|---|
| recurrence | # independent **replications** (a result seen 5× is more trustworthy than a one-off) |
| bi-temporal `valid_to` | **belief revision** ("what did we believe about resveratrol in 2006?") |
| supersession / `contradicts` | a **refuted or revised** claim |

So the *currently best-supported* finding is: among claims that name the queried intervention
(entity-gated) and are **current** (valid window contains now, not refuted), the one with the
most replications. The same `as_of` query returns the era-correct belief; the supersession
links flag what's been overturned.

## Corpus

25 curated longevity claims with their **real** replication/revision arcs — resveratrol→SIRT1
(fluorophore-assay artifact, refuted), the antioxidant/free-radical null, GDF11 rejuvenation
reversal, telomere-length Mendelian-randomization reversal, the CR-primate NIA-vs-Wisconsin
split, parabiosis "dilution not youth factors", plus deliberate recent **single-study hype**
(2023 resveratrol, 2024 NMN). Public knowledge, so the gold labels are defensible and the study
is fully offline. `ingest_drugage()` sketches the adapter to a real structured source
(DrugAge/GenAge); the curated set is the shipped demonstration. Relevance is entity-gated lexical
overlap (offline, no embedder) — **shared by every method**, so the comparison isolates the
validity/replication/contradiction layers, not retrieval hygiene.

## Results

| task | metric | flat-newest | lexical-only | **bio-memory** |
|---|---|---|---|---|
| current best-supported finding | accuracy (10 topics) | 0.700 | 0.800 | **1.000** |
| " | serves an overturned claim ↓ | 0.000 | 0.100 | **0.000** |
| as-of belief (10 era-queries) | accuracy | 0.300 | — | **1.000** |
| contradiction detection (7 overturned) | F1 | — | recency 0.875 | **1.000** |

- **Current best-supported: 1.00 vs 0.70 / 0.80.** flat-newest is fooled by the latest
  single-study hype (returns the 2023/2024 claims); lexical-only serves a *refuted* claim 10% of
  the time. bio-memory resists both — replication weighting demotes the one-off, the current/
  contradiction filter excludes the refuted.
- **As-of belief: 1.00 vs 0.30.** Returning the version current *then* (incl. since-refuted ones
  like resveratrol→SIRT1 in 2006) where "use newest" is anachronistic.
- **Contradiction F1 1.00** — the supersession/`contradicts` structure recovers every overturned
  claim; a recency heuristic (flag the older of each pair) reaches only 0.875.

## Honest scope

This is a **proof-of-concept of the mapping** on a curated corpus, not an unbiased benchmark.
bio-memory's 1.00 partly reflects that the corpus was built to exercise the canonical failure
modes (hype, refutation, era-belief) and the gold is "the best-supported current claim" — which
bio-memory is designed to return. The diagnostic results are the **baselines' specific failures**
(flat-newest 0.70 on hype; lexical-only serving refuted claims; flat-newest 0.30 on era-queries),
which the existing primitives fix with no domain-specific code. A real GenAge/DrugAge ingestion
(via the adapter) and a held-out claim set would turn this into a quantitative benchmark — the
natural P2 (mission paper) follow-up. No core schema change was made: the claim-memory is a
research application of the shipped `as_of`/recurrence/supersession primitives, deliberately kept
out of the general-purpose core.
