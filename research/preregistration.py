#!/usr/bin/env python3
"""RESEARCH - the preregistration's arithmetic (GOAL F8).

F8's exit criterion is *preregistered hypotheses and endpoints*, and the part of a
preregistration that cannot be written by hand is the sample size. F1 through F6 were
exploratory: they generated the hypotheses, on one corpus, written by the author of the notes
those hypotheses are about. Using that same corpus to confirm them would be circular, so the
confirmatory run needs a new corpus - and the only useful question about a new corpus is *how
big must it be*.

This computes that, exactly, for the design the confirmatory run will use: a **paired** McNemar
test on the discordant episodes, which is what F5 established is the right test for arms that
all see the same episodes.

The answer is the uncomfortable part. The effects the exploratory phase actually observed are
small, and a small paired effect needs a corpus far larger than thirty episodes. Publishing the
number before the run is the whole point of a preregistration: it stops "we ran what we had and
it was not significant" from being reported as evidence of no effect.

    python research/preregistration.py            # the power table
    python research/preregistration.py --save     # + research/preregistration.json

Standard library only. Exact binomial throughout; no normal approximation, because the
approximation is unreliable at exactly the discordant-pair counts this design produces.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "preregistration.json"

ALPHA = 0.05
TARGET_POWER = 0.80

#: Endpoints the confirmatory run will test, and the design for each. Written here rather than
#: only in prose so the suite can check that every hypothesis in the document has one.
HYPOTHESES = [
    {
        "id": "H1",
        "statement": "Proactive interventions from the memory system prevent more repeated "
                     "failures than raw lexical recall at a matched false-alarm rate.",
        "endpoint": "prevention rate at each arm's zero-false-alarm operating point",
        "comparison": "nevertwice vs lexical_recall, paired over episodes",
        "test": "exact McNemar on discordant pairs, two-sided, Holm-corrected across "
                "hypotheses",
        "supported_if": "the Holm-corrected p is below alpha AND the difference favours the "
                        "memory arm",
        "falsified_if": "the corrected p is not below alpha, or the difference favours the "
                        "baseline",
        "exploratory_finding": "F5 found the memory arm's successes to be a strict SUBSET of "
                               "lexical recall's on the exploratory corpus - it won zero "
                               "episodes the baseline missed. H1 is therefore expected to fail, "
                               "and is preregistered so that failing is a result rather than an "
                               "omission.",
        "prior_discordance": 0.07,
        "prior_favouring": 0.5,
    },
    {
        "id": "H2",
        "statement": "The memory system and an existing linter are COMPLEMENTARY: their union "
                     "prevents more repeated failures than either alone.",
        "endpoint": "prevention rate of the union arm vs each arm alone",
        "comparison": "union(nevertwice, linter_or_test) vs each, paired over episodes",
        "test": "exact McNemar on discordant pairs, two-sided, Holm-corrected",
        "supported_if": "the union beats BOTH arms at corrected alpha",
        "falsified_if": "the union fails to beat either arm",
        "exploratory_finding": "F5 found ten episodes each arm got that the other missed, and a "
                               "union far above either alone. This is the strongest exploratory "
                               "signal in the whole phase and the one most worth confirming.",
        "prior_discordance": 0.33,
        "prior_favouring": 1.0,
    },
    {
        "id": "H3",
        "statement": "Coverage normalisation is load-bearing: removing it costs prevention at a "
                     "matched false-alarm rate.",
        "endpoint": "prevention rate with and without coverage normalisation",
        "comparison": "full scorer vs the no-coverage ablation, paired over episodes",
        "test": "exact McNemar on discordant pairs, two-sided, Holm-corrected",
        "supported_if": "removing coverage normalisation lowers the prevention rate at "
                        "corrected alpha, OR removes the zero-false-alarm operating point "
                        "entirely",
        "falsified_if": "the ablated scorer still reaches a zero-false-alarm operating point "
                        "AND its prevention rate is not lower at corrected alpha",
        "exploratory_finding": "F4 found that removing coverage normalisation does not merely "
                               "lower recall - it loses the zero-false-alarm operating point "
                               "entirely. It was the only mechanism F4 could name.",
        "prior_discordance": 0.30,
        "prior_favouring": 0.95,
    },
    {
        "id": "H4",
        "statement": "The reader bounds actionability: prevention from an identical surfaced "
                     "memory differs by reader model.",
        "endpoint": "prevention-adoption lift, memory held fixed, across a model ladder",
        "comparison": "smallest vs largest reader, paired over episodes",
        "test": "exact McNemar on discordant pairs, two-sided, Holm-corrected",
        "supported_if": "the lift differs across the ladder at corrected alpha",
        "falsified_if": "it does not differ beyond the interval",
        "exploratory_finding": "F3 found a spread exceeding the sampling interval but a "
                               "NON-MONOTONIC ladder, which at that sample size reads as noise. "
                               "Confirming this needs both a larger corpus and the frontier "
                               "cells that gate G8 currently blocks.",
        "prior_discordance": 0.33,
        "prior_favouring": 0.75,
    },
]

#: Conditions the confirmatory corpus must meet BEFORE any of the above is run. Preregistered
#: because each is a way the result could be quietly weakened after the fact.
CORPUS_REQUIREMENTS = [
    "Written by someone other than the author of the notes. Every exploratory result rests on a "
    "corpus where the same person wrote the failures and the situations heading toward them, "
    "which biases recall upward and cannot be corrected for after the fact.",
    "At least the size the power table below requires for the hypothesis being tested. A run "
    "smaller than that cannot report a null as evidence of no effect.",
    "Episodes drawn from real sessions rather than paraphrased from notes, so no episode can "
    "share the wording of the note it is labelled against.",
    "Labelled before any arm is run on it, by someone who has not seen the arms' outputs.",
    "Published in full with the results, per-episode and per-family, as F5 established.",
]


def _pmf_row(n: int, p: float) -> list:
    """Binomial pmf over k=0..n, built by recurrence.

    Not `math.comb(n, k) * p**k * q**(n-k)`: at the corpus sizes a preregistration has to
    consider, that computes enormous integers and underflowing powers for every cell, and the
    first version of this file simply never finished. The recurrence stays in float range and
    is O(n).
    """
    if p <= 0.0:
        return [1.0] + [0.0] * n
    if p >= 1.0:
        return [0.0] * n + [1.0]
    q = 1.0 - p
    row = [0.0] * (n + 1)
    row[0] = q ** n
    if row[0] == 0.0:                      # underflow at large n: start from the mode instead
        logs = [0.0] * (n + 1)
        base = n * math.log(q)
        for k in range(n + 1):
            logs[k] = base + math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)                 + k * (math.log(p) - math.log(q))
        top = max(logs)
        row = [math.exp(v - top) for v in logs]
        total = sum(row)
        return [v / total for v in row]
    for k in range(n):
        row[k + 1] = row[k] * (n - k) / (k + 1) * (p / q)
    return row


def binom_pmf(k: int, n: int, p: float) -> float:
    """One cell. Kept for the tests, which check it against values with known answers."""
    return _pmf_row(n, p)[k] if 0 <= k <= n else 0.0


def _critical(n_disc: int, alpha: float) -> int:
    """Largest k such that the exact two-sided test rejects when min(b, n-b) <= k.

    Cached per (n_disc, alpha): the rejection region depends only on the discordant count, so
    recomputing it inside the power loop was most of the cost.
    """
    key = (n_disc, alpha)
    if key in _CRITICAL_CACHE:
        return _CRITICAL_CACHE[key]
    row = _pmf_row(n_disc, 0.5)
    tail, k_crit = 0.0, -1
    for k in range(n_disc // 2 + 1):
        tail += row[k]
        if 2 * tail <= alpha:
            k_crit = k
        else:
            break
    _CRITICAL_CACHE[key] = k_crit
    return k_crit


_CRITICAL_CACHE: dict = {}


def mcnemar_rejects(b: int, n_disc: int, alpha: float = ALPHA) -> bool:
    """Does an exact two-sided McNemar reject, with `b` of `n_disc` discordant pairs ours?"""
    if n_disc == 0:
        return False
    return min(b, n_disc - b) <= _critical(n_disc, alpha)


def power_at(n_pairs: int, discordance: float, favouring: float,
             alpha: float = ALPHA) -> float:
    """Exact power of the paired test over `n_pairs` episodes.

    Marginalises over how many pairs turn out discordant (binomial in `discordance`) and, given
    that, how many favour us (binomial in `favouring`). No normal approximation anywhere: at the
    handful of discordant pairs this design produces, the approximation reports power the exact
    test does not have.
    """
    disc_row = _pmf_row(n_pairs, discordance)
    total = 0.0
    for n_disc, p_disc in enumerate(disc_row):
        if p_disc < 1e-12:
            continue
        k_crit = _critical(n_disc, alpha)
        if k_crit < 0:
            continue                        # no rejection region at this discordant count
        row = _pmf_row(n_disc, favouring)
        reject = sum(row[:k_crit + 1]) + sum(row[n_disc - k_crit:])
        total += p_disc * min(1.0, reject)
    return total


def required_n(discordance: float, favouring: float, target: float = TARGET_POWER,
               cap: int = 600) -> int | None:
    """Smallest episode count reaching `target` power. None if `cap` is not enough."""
    if favouring <= 0.5:
        return None            # no effect to detect in the direction of interest
    if power_at(cap, discordance, favouring) < target:
        return None
    lo, hi = 1, cap
    while lo < hi:
        mid = (lo + hi) // 2
        if power_at(mid, discordance, favouring) >= target:
            hi = mid
        else:
            lo = mid + 1
    return lo


def build(save: bool = False) -> dict:
    rows = []
    for h in HYPOTHESES:
        d, f = h["prior_discordance"], h["prior_favouring"]
        n = required_n(d, f)
        rows.append({
            "id": h["id"],
            "discordance_assumed": d,
            "favouring_assumed": f,
            "episodes_for_80_percent_power": n,
            "power_cap_searched": 600,
            "power_at_30_episodes": round(power_at(30, d, f), 4),
            "power_at_100_episodes": round(power_at(100, d, f), 4),
            "note": ("no effect to detect in the stated direction - this hypothesis is "
                     "preregistered as one the exploratory phase expects to FAIL"
                     if n is None else
                     f"a corpus of {n} labelled episodes is the minimum this design needs"),
        })

    payload = {
        "schema_version": 1,
        "generated_by": "python research/preregistration.py --save",
        "design": {
            "test": "exact McNemar on discordant pairs, two-sided",
            "alpha": ALPHA,
            "target_power": TARGET_POWER,
            "correction": "Holm-Bonferroni across all preregistered hypotheses",
            "approximation": "none - exact binomial throughout, because the normal "
                             "approximation is unreliable at the discordant-pair counts this "
                             "design produces",
        },
        "hypotheses": HYPOTHESES,
        "power": rows,
        "corpus_requirements": CORPUS_REQUIREMENTS,
        "status": _status(rows),
    }
    if save:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def _status(rows: list) -> list:
    """What this preregistration commits to, before anything is run."""
    needed = [r for r in rows if r["episodes_for_80_percent_power"]]
    biggest = max((r["episodes_for_80_percent_power"] for r in needed), default=None)
    underpowered = [r["id"] for r in rows if r["power_at_30_episodes"] < TARGET_POWER]
    return [
        "NOTHING HERE HAS BEEN CONFIRMED. F1 through F6 were exploratory: they generated these "
        "hypotheses from one corpus, and using that corpus to confirm them would be circular. "
        "This document exists so the confirmatory run is committed to in advance.",
        (f"The exploratory corpus of 30 positive episodes is underpowered for "
         f"{len(underpowered)} of {len(rows)} hypotheses ({', '.join(underpowered)}); it is "
         f"adequately powered only for {', '.join(r['id'] for r in rows if r['id'] not in underpowered) or 'none'}. "
         f"A confirmatory run needs up to {biggest} labelled episodes, and a null from anything "
         f"smaller is not evidence of no effect."
         if underpowered and biggest else
         "The exploratory corpus is adequately powered for every hypothesis here."),
        "H1 is preregistered as a hypothesis the exploratory phase expects to FAIL. Registering "
        "it anyway is the point: a claim that quietly disappears between exploration and "
        "publication is the failure mode preregistration exists to prevent.",
        "H2 - complementarity with an existing linter - is the strongest exploratory signal and "
        "the one worth the most. It is also the one that reframes the contribution: if it "
        "holds, the finding is not that this system beats the alternatives but that it covers "
        "failure classes static analysis cannot reach.",
        "The confirmatory corpus requirements are preregistered too, because each is a way a "
        "result could be quietly weakened afterwards. The binding one is that somebody other "
        "than the author of the notes must write it.",
    ]


def render(p: dict) -> str:
    L = ["", "Preregistration arithmetic (GOAL F8)", ""]
    d = p["design"]
    L.append(f"  design: {d['test']}, alpha={d['alpha']}, target power={d['target_power']}")
    L.append(f"          correction: {d['correction']}")
    L.append("")
    L.append(f"  {'id':4} {'discord':>8} {'favour':>7} {'n for 80%':>10} "
             f"{'power@30':>9} {'power@100':>10}")
    for r in p["power"]:
        n = r["episodes_for_80_percent_power"]
        L.append(f"  {r['id']:4} {r['discordance_assumed']!s:>8} {r['favouring_assumed']!s:>7} "
                 f"{(n if n else 'n/a')!s:>10} {r['power_at_30_episodes']!s:>9} "
                 f"{r['power_at_100_episodes']!s:>10}")
    L.append("")
    L.append("  Hypotheses:")
    for h in p["hypotheses"]:
        L.append(f"    {h['id']}: {h['statement']}")
        L.append(f"        endpoint      {h['endpoint']}")
        L.append(f"        supported if  {h['supported_if']}")
        L.append(f"        falsified if  {h['falsified_if']}")
    L.append("")
    L.append("  The confirmatory corpus must be:")
    for req in p["corpus_requirements"]:
        L.append("    - " + req.replace(". ", ".\n      "))
    L.append("")
    for line in p["status"]:
        L.append("  * " + line.replace(". ", ".\n    "))
    L.append("")
    return "\n".join(L)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true", help=f"write {OUT.name}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = build(save=args.save)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(render(payload))
        if args.save:
            print(f"  saved -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
