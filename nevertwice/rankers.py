"""Opt-in research rankers — the W11 plugin boundary.

`retrieve_relevant` lazy-loads this module ONLY when `NEVERTWICE_RANKER=posterior` or
`NEVERTWICE_DIVERGENCE>0`, so the default hot path never imports it and the core file carries no
maintenance surface for features the default never runs. One-way import (rankers -> memory_hook),
mirroring index_sqlite. Validated in research/posterior_model.py (1A) and research/divergent.py (2B).
"""
import math

try:
    from . import memory_hook as m
except ImportError:                       # invoked as a top-level module (nevertwice/ on sys.path)
    import memory_hook as m


def posterior_rerank(scores: dict, rec_of: dict) -> dict:
    """NEVERTWICE_RANKER=posterior (1A): rank by the explicit log-posterior
    w_rel·log(rrf) + w_freq·log(n) + w_sal·log(salience), replacing the additive-recurrence +
    multiplicative-salience tail. Each prior is a separable, tunable term (POST_W) — recurrence as a
    true frequency prior nᵂ, salience as a log term. Safe logs: rrf>0 for any candidate in the
    fusion, n≥1, salience>0."""
    out = {}
    for s, rrf in scores.items():
        rec = rec_of.get(s) or {}
        try:
            n = max(1, int(rec.get("recurrence", 1) or 1))
        except (TypeError, ValueError):
            n = 1
        sal = m._salience_mult(s, rec)
        out[s] = (m.POST_W["rel"] * math.log(rrf) + m.POST_W["freq"] * math.log(n)
                  + m.POST_W["sal"] * math.log(max(1e-12, sal)))
    return out


def mmr_rerank(order: list, scores: dict, rec_of: dict, divergence: float) -> list:
    """Re-order candidates by Maximal Marginal Relevance (serendipity knob, 2B): balance the fused
    relevance score against dissimilarity to the already-picked notes, so recall is diverse (fewer
    near-duplicates, more cross-topic surfacing) rather than redundant. Vector-only — a candidate
    without an embedding can't be diversified, so it rides its score. Scores are min-max normalised
    so the relevance↔diversity trade is scale-free."""
    lam = 1.0 - divergence
    sv = [scores[s] for s in order]
    lo, hi = min(sv), max(sv)
    rel = {s: ((scores[s] - lo) / (hi - lo) if hi > lo else 1.0) for s in order}
    vecs = {s: (rec_of.get(s) or {}).get("vec") for s in order}
    chosen, rest = [], list(order)
    while rest:
        if not chosen:
            best = max(rest, key=lambda s: rel[s])
        else:
            def _mmr(s):
                v = vecs[s]
                sim = max((m.cosine(v, vecs[c]) for c in chosen if vecs[c]), default=0.0) if v else 0.0
                return lam * rel[s] - (1.0 - lam) * sim
            best = max(rest, key=_mmr)
        chosen.append(best)
        rest.remove(best)
    return chosen
