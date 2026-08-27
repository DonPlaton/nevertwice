"""C3: how large an effect can this corpus actually see?

The question the previous run never asked. It reported precision 0.000 on 24 findings
and treated that as a verdict; with 24 findings the 95% interval on a precision of 0.5
runs from 0.29 to 0.71, so even a *good* detector could not have been told from a
coin there. Whether a corpus can resolve the threshold you are about to declare is a
property of the corpus, and it is knowable before any mechanism runs.

Everything here is a **minimum detectable effect** at a fixed n, or the n a stated
effect would need. Nothing here is observed power: computing power from an effect you
have already measured is a deterministic function of the p-value and says nothing new.
No mechanism has been measured yet, which is the point of running this first.

    python research/invariants_lab/power.py
    python research/invariants_lab/power.py --print
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
from scipy import stats
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpusio import progress  # noqa: E402

MUTANTS = Path(__file__).with_name("mutants.json")
CENSUS = Path(__file__).with_name("corpus_census.json")
ARTIFACT = Path(__file__).with_name("power.json")

ALPHA = 0.05
TARGET = 0.80
SEED = 20260827


# --------------------------------------------------------------------------
# one proportion: recall on the positives, precision on the findings
# --------------------------------------------------------------------------


def exact_power_one_prop(n: int, p0: float, p1: float, alpha: float = ALPHA) -> float:
    """Power of an exact binomial test of H0: p = p0 when the truth is p1.

    Exact rather than normal-approximate because the interesting n here are in the
    tens, where the normal approximation is optimistic by several points and would
    quietly bless an underpowered corpus.
    """
    k = np.arange(n + 1)
    # two-sided exact rejection region: outcomes at least as extreme as observed
    pmf0 = stats.binom.pmf(k, n, p0)
    # Build the two-sided region by accumulating the least likely outcomes under H0.
    order = np.argsort(pmf0)
    cum = np.cumsum(pmf0[order])
    keep = order[cum <= alpha]
    reject = np.zeros(n + 1, dtype=bool)
    reject[keep] = True
    return float(stats.binom.pmf(k, n, p1)[reject].sum())


def n_for_one_prop(p0: float, p1: float, target: float = TARGET,
                   alpha: float = ALPHA, cap: int = 4000) -> int | None:
    for n in range(5, cap + 1):
        if exact_power_one_prop(n, p0, p1, alpha) >= target:
            return n
    return None


def mde_one_prop(n: int, p0: float, target: float = TARGET, alpha: float = ALPHA,
                 direction: str = "up") -> float | None:
    """The truth furthest from p0 that is still detected at `target` power."""
    grid = np.arange(p0 + 0.005, 0.999, 0.005) if direction == "up" else \
        np.arange(p0 - 0.005, 0.001, -0.005)
    for p1 in grid:
        if exact_power_one_prop(n, p0, float(p1), alpha) >= target:
            return round(float(p1), 3)
    return None


def ci_width(n: int, p: float) -> tuple[float, float]:
    lo, hi = proportion_confint(int(round(p * n)), n, alpha=ALPHA, method="wilson")
    return round(float(lo), 3), round(float(hi), 3)


# --------------------------------------------------------------------------
# McNemar: the detector against the baseline, on the same commits
# --------------------------------------------------------------------------


def mcnemar_power(n: int, p_detector: float, p_baseline: float, rho: float,
                  alpha: float = ALPHA, sims: int = 4000, seed: int = SEED) -> float:
    """Simulated power of an exact McNemar test on `n` paired commits.

    `rho` is the correlation between the two arms' successes. It matters more than
    either marginal: two arms that agree on almost everything produce few discordant
    pairs, and McNemar sees only the discordant pairs. A baseline built from the same
    symbol list as the detector -- which `git grep` is -- is strongly correlated with
    it by construction, so assuming independence would flatter the design badly.
    """
    rng = np.random.default_rng(seed)
    # Gaussian copula: two correlated Bernoullis with the requested marginals.
    z = rng.multivariate_normal([0, 0], [[1, rho], [rho, 1]], size=(sims, n))
    a = z[:, :, 0] < stats.norm.ppf(p_detector)
    b = z[:, :, 1] < stats.norm.ppf(p_baseline)
    b01 = (a & ~b).sum(axis=1)
    b10 = (~a & b).sum(axis=1)
    # exact (binomial) McNemar on the discordant pairs
    m = b01 + b10
    p = np.array([
        1.0 if mi == 0 else float(stats.binomtest(int(k), int(mi), 0.5).pvalue)
        for k, mi in zip(b01, m)
    ])
    return float((p < alpha).mean())


def n_for_mcnemar(p_detector: float, p_baseline: float, rho: float,
                  target: float = TARGET, cap: int = 1200) -> int | None:
    lo, hi = 5, cap
    if mcnemar_power(cap, p_detector, p_baseline, rho) < target:
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if mcnemar_power(mid, p_detector, p_baseline, rho, sims=2500) >= target:
            hi = mid
        else:
            lo = mid + 1
    return lo


# --------------------------------------------------------------------------


def corpus_shape() -> dict:
    d = json.loads(MUTANTS.read_text(encoding="utf-8"))
    ms = [m for m in d["mutants"] if m.get("confirmed")]
    per = Counter((m["repo"], m["sha"]) for m in ms)
    sizes = np.array(sorted(per.values()))
    mbar = float((sizes ** 2).sum() / sizes.sum())   # size-weighted mean cluster size
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    sig = census["totals"]["signature_change_commits"]
    elig = census["totals"]["eligible_commits"]
    return {
        "mutants": len(ms),
        "clusters": len(sizes),
        "max_cluster": int(sizes.max()),
        "median_cluster": int(np.median(sizes)),
        "weighted_mean_cluster": round(mbar, 2),
        "deff": {
            str(icc): round(1 + (mbar - 1) * icc, 2) for icc in (0.2, 0.5, 0.8, 1.0)
        },
        "n_eff_if_pooled": {
            str(icc): round(len(ms) / (1 + (mbar - 1) * icc), 1)
            for icc in (0.2, 0.5, 0.8, 1.0)
        },
        "negatives_paired": len(sizes),
        "negatives_silent_pool": sig - elig,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    if args.show:
        d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        s = d["corpus"]
        print(f"positives: {s['mutants']} mutants in {s['clusters']} clusters "
              f"(max {s['max_cluster']}, median {s['median_cluster']})")
        print(f"pooling all {s['mutants']} would cost DEFF "
              f"{s['deff']['1.0']} at ICC=1 -> n_eff {s['n_eff_if_pooled']['1.0']}")
        print()
        print(f"{'question':<52}{'n have':>8}{'n need':>8}{'verdict':>10}")
        print("-" * 78)
        for r in d["questions"]:
            print(f"{r['question']:<52}{r['n_available']:>8}"
                  f"{str(r['n_required']):>8}{r['verdict']:>10}")
        print()
        print("minimum detectable effects at the n this corpus has:")
        for r in d["mde"]:
            print(f"  {r['label']:<48} {r['floor']:<6} -> {r['mde']}")
        return 0

    progress("shape")
    shape = corpus_shape()
    n_pos = shape["clusters"]
    n_neg_pool = shape["negatives_silent_pool"]

    questions = []

    def ask(question: str, n_available: int, n_required: int | None) -> None:
        ok = n_required is not None and n_required <= n_available
        questions.append({
            "question": question,
            "n_available": n_available,
            "n_required": n_required,
            "verdict": "POWERED" if ok else "UNDERPOWERED",
        })

    progress("recall questions")
    ask("recall 0.8 vs a floor of 0.5", n_pos, n_for_one_prop(0.5, 0.8))
    ask("recall 0.7 vs a floor of 0.5", n_pos, n_for_one_prop(0.5, 0.7))
    ask("recall 0.6 vs a floor of 0.5", n_pos, n_for_one_prop(0.5, 0.6))
    ask("recall 0.9 vs a floor of 0.8", n_pos, n_for_one_prop(0.8, 0.9))
    ask("recall 0.95 vs a floor of 0.9 (the quadratic case, X4)", n_pos,
        n_for_one_prop(0.9, 0.95))

    progress("precision questions")
    for k in (24, 50, 100, 177):
        ask(f"precision 0.8 vs a floor of 0.5, on {k} findings", k,
            n_for_one_prop(0.5, 0.8))

    progress("flag-rate questions")
    ask("flag rate 0.02 vs a ceiling of 0.05, on silent commits", n_neg_pool,
        n_for_one_prop(0.05, 0.02))
    ask("flag rate 0.03 vs a ceiling of 0.05, on silent commits", n_neg_pool,
        n_for_one_prop(0.05, 0.03))

    progress("mcnemar (simulated)")
    mcnemar = []
    for det, base, rho in (
        (0.80, 0.50, 0.5), (0.80, 0.60, 0.5), (0.80, 0.70, 0.5),
        (0.80, 0.50, 0.8), (0.80, 0.60, 0.8), (0.80, 0.70, 0.8),
    ):
        need = n_for_mcnemar(det, base, rho)
        mcnemar.append({
            "detector": det, "baseline": base, "correlation": rho,
            "n_required": need,
            "power_at_n": round(mcnemar_power(n_pos, det, base, rho), 3),
            "verdict": "POWERED" if need is not None and need <= n_pos
                       else "UNDERPOWERED",
        })
        progress(f"  det={det} base={base} rho={rho} -> n={need}")

    progress("minimum detectable effects")
    mde = [
        {"label": "recall, against a floor of 0.50", "floor": 0.50,
         "mde": mde_one_prop(n_pos, 0.50)},
        {"label": "recall, against a floor of 0.80", "floor": 0.80,
         "mde": mde_one_prop(n_pos, 0.80)},
        {"label": "recall, against a floor of 0.90", "floor": 0.90,
         "mde": mde_one_prop(n_pos, 0.90)},
        {"label": "precision on 24 findings (the old run's n)", "floor": 0.50,
         "mde": mde_one_prop(24, 0.50)},
        {"label": "precision on 50 findings", "floor": 0.50,
         "mde": mde_one_prop(50, 0.50)},
        {"label": "precision on 100 findings", "floor": 0.50,
         "mde": mde_one_prop(100, 0.50)},
        {"label": "flag rate, against a ceiling of 0.05", "floor": 0.05,
         "mde": mde_one_prop(min(n_neg_pool, 2000), 0.05, direction="down")},
    ]

    widths = {
        str(n): {str(p): ci_width(n, p) for p in (0.2, 0.5, 0.8)}
        for n in (24, 50, 100, 177, 495)
    }

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "alpha": ALPHA, "target_power": TARGET, "seed": SEED,
        "corpus": shape,
        "questions": questions,
        "mcnemar": mcnemar,
        "mde": mde,
        "ci_widths": widths,
    }
    ARTIFACT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
