#!/usr/bin/env python3
"""The confirmatory statistics of PREREG-V3 §9.2, §9.3 and the §9.5 design check (TB10).

Every function takes per-question differences Δ = ours − competitor, already averaged over runs
over the questions valid for both arms (§9.2 "Estimate"); how Δ is built belongs to the harness.

* ``wild_cluster_t`` - the wild cluster bootstrap-t with the null imposed (WCR) for S5 and S7:
  clusters are conversations or trajectories; Rademacher weights for G >= 12, Webb's six-point
  weights for G < 12; 9,999 draws, seed 20260926.
* ``signflip`` - the stratified sign-flip permutation test for S6 FC-SH: per-question signs, the
  statistic is the row-size-weighted mean Δ; 99,999 draws, or every pattern when there are fewer.
  ``signflip_cluster_means`` is the confirmatory-family slot's branch (b): the exact sign-flip on
  cluster means that replaces the bootstrap-t when its synthetic coverage falls short.
* ``tost`` - both one-sided p-values from the same method with Δ shifted by the ±5 pp margin.
* ``holm`` and ``verdicts`` - Holm step-down across the family, first on superiority, then on TOST.
* ``interval`` - the displayed interval by inverting the member's own test at the Bonferroni level.
* ``Design``, ``power``, ``mde``, ``coverage`` - the §9.5 simulator: the MDE at power 0.8 and the
  coverage of the nominal 95 % interval at Δ = 0, at a stand's real G, m and runs.

p-values. When the draws cover every weight or sign pattern the test is exact: p is the share of
patterns whose statistic is at least as extreme as the observed one, the identity pattern included,
so the smallest attainable two-sided p over n units is 2 / 2**n (0.125 at n = 4). Otherwise the
patterns are sampled and p = (1 + hits) / (1 + draws), never 0. "At least as extreme" compares with
a relative tolerance of 1e-9, so the identity pattern always counts itself despite rounding.

Standard error. The bootstrap-t studentizes with the CR1 cluster-robust standard error of the mean
(small-sample factor G / (G − 1)); the factor cancels between t and t*, so it changes no p-value.

    python tests/research/_test_v3_stats.py
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

SEED = 20260926
BOOT_DRAWS = 9_999
SIGNFLIP_DRAWS = 99_999
TOST_MARGIN = 0.05
FWER = 0.05
WEBB_BELOW_G = 12
MAX_ENUMERATED = 1 << 17      # memory ceiling for an enumerated pattern matrix (131,072 rows)
_REL_TOL = 1e-9

RADEMACHER = np.array([-1.0, 1.0])
WEBB = np.array([-math.sqrt(1.5), -1.0, -math.sqrt(0.5), math.sqrt(0.5), 1.0, math.sqrt(1.5)])

ALTERNATIVES = ("two-sided", "greater", "less")


# ── weights and patterns ────────────────────────────────────────────────

def weight_kind(g: int) -> str:
    """Rademacher for G >= 12, Webb six-point for G < 12 (§9.2)."""
    return "rademacher" if g >= WEBB_BELOW_G else "webb"


def weight_matrix(g: int, draws: int, seed: int, kind: str) -> tuple[np.ndarray, bool]:
    """(W, exact): W is draws × g cluster weights, or every pattern when that is fewer rows.

    Enumerating is the sampled test with its Monte Carlo noise removed; the identity pattern is
    one of the rows, so an exact p is never below 1 / patterns.
    """
    points = RADEMACHER if kind == "rademacher" else WEBB
    if kind not in ("rademacher", "webb"):
        raise ValueError(f"unknown weight kind {kind!r}")
    patterns = len(points) ** g
    if patterns <= draws and patterns <= MAX_ENUMERATED:
        return np.array(list(itertools.product(points, repeat=g)), dtype=float), True
    rng = np.random.default_rng(seed)
    return points[rng.integers(0, len(points), size=(draws, g))], False


def _sign_matrix(n: int, draws: int, seed: int) -> tuple[np.ndarray, bool]:
    return weight_matrix(n, draws, seed, "rademacher")


def _p_value(stat: float, stars: np.ndarray, alternative: str, exact: bool) -> float:
    """Share of patterns at least as extreme as `stat` (see the module docstring)."""
    if alternative not in ALTERNATIVES:
        raise ValueError(f"alternative must be one of {ALTERNATIVES}, not {alternative!r}")
    stars = np.where(np.isnan(stars), 0.0, stars)      # nan_to_num would also turn ±inf finite
    if math.isnan(stat):
        stat = 0.0
    tol = _REL_TOL * max(1.0, abs(stat)) if math.isfinite(stat) else 0.0
    if alternative == "two-sided":
        hits = np.count_nonzero(np.abs(stars) >= abs(stat) - tol)
    elif alternative == "greater":
        hits = np.count_nonzero(stars >= stat - tol)
    else:
        hits = np.count_nonzero(stars <= stat + tol)
    return float(hits / len(stars)) if exact else float((1 + hits) / (1 + len(stars)))


def _codes(groups: Sequence) -> tuple[np.ndarray, int]:
    """Integer codes in the groups' natural sort order (numbers numerically, strings as strings)."""
    arr = np.asarray(groups)
    if arr.dtype == object:
        arr = arr.astype(str)
    _, codes = np.unique(arr, return_inverse=True)
    codes = codes.astype(int).ravel()
    return codes, int(codes.max()) + 1 if len(codes) else 0


# ── the wild cluster bootstrap-t (S5, S7) ───────────────────────────────

def _cr1_t(b: np.ndarray, sum_w2s2: np.ndarray, cross: np.ndarray, sum_n2: float, n: int, g: int) -> np.ndarray:
    """t = b / se_CR1 with b = β* − β0, from the three sums that give se without a B × G × D tensor:

    Σ_g (w_g S_g − n_g b)² = Σ w_g² S_g² − 2 b Σ w_g S_g n_g + b² Σ n_g².

    The expansion cancels to rounding noise where the residual sums are exactly 0 (identical
    clusters, the identity pattern of such data); a sum below 1e-12 of its own scale is 0, so that
    pattern's t is ±inf like the observed one and counts itself. 0 / 0 is t = 0.
    """
    scale = sum_w2s2 + b * b * sum_n2
    ss = sum_w2s2 - 2.0 * b * cross + b * b * sum_n2
    ss = np.where(ss <= 1e-12 * scale, 0.0, ss)
    se = np.sqrt(g / (g - 1) * ss) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        t = b / se
    return np.where((se == 0) & (b == 0), 0.0, t)


def wild_cluster_t(delta: Sequence[float], clusters: Sequence, *, null: float = 0.0,
                   alternative: str = "two-sided", draws: int = BOOT_DRAWS, seed: int = SEED,
                   kind: str | None = None) -> dict:
    """Wild cluster bootstrap-t for the mean of Δ, the null β = `null` imposed (WCR).

    y*_i = β0 + w_g (y_i − β0); t* = (β̂* − β0) / se_CR1(y*). Returns the observed t, p, G, n,
    the weight kind, the number of patterns and whether the test was exact.
    """
    y = np.asarray(delta, dtype=float)
    codes, g = _codes(clusters)
    if y.size != codes.size:
        raise ValueError("delta and clusters differ in length")
    if g < 2:
        raise ValueError(f"the bootstrap-t needs at least 2 clusters, got {g}")
    n = y.size
    kind = kind or weight_kind(g)
    weights, exact = weight_matrix(g, draws, seed, kind)
    ng = np.bincount(codes, minlength=g).astype(float)
    sum_n2 = float(ng @ ng)
    s = np.bincount(codes, y - null, minlength=g)
    # The observed t is the identity pattern of the same formula, so it always counts itself.
    t = float(_cr1_t(np.array([s.sum() / n]), np.array([s @ s]), np.array([s @ ng]), sum_n2, n, g)[0])
    b = (weights @ s) / n
    t_star = _cr1_t(b, (weights ** 2) @ (s ** 2), weights @ (s * ng), sum_n2, n, g)
    return {"method": "wild_cluster_t", "estimate": float(y.mean()), "null": null, "t": t,
            "p": _p_value(t, t_star, alternative, exact), "alternative": alternative, "G": g, "n": n,
            "kind": kind, "patterns": int(len(weights)), "exact": exact, "seed": seed}


# ── the sign-flip permutation test (S6 FC-SH; branch (b) on cluster means) ──

def _unit_weights(n: int, strata: Sequence | None, weighting: str) -> np.ndarray:
    """Per-unit weights of the statistic. "size": row-size-weighted mean of row means, which is the
    pooled mean over units; "equal": every row counts once."""
    if strata is None or weighting == "size":
        return np.full(n, 1.0 / n)
    if weighting != "equal":
        raise ValueError(f"weighting must be 'size' or 'equal', not {weighting!r}")
    codes, r = _codes(strata)
    sizes = np.bincount(codes, minlength=r).astype(float)
    return 1.0 / (r * sizes[codes])


def signflip(delta: Sequence[float], strata: Sequence | None = None, *, null: float = 0.0,
             alternative: str = "two-sided", draws: int = SIGNFLIP_DRAWS, seed: int = SEED,
             weighting: str = "size", unit_weights: Sequence[float] | None = None,
             chunk: int = 8_192) -> dict:
    """Stratified sign-flip test of the weighted mean of Δ − `null`.

    Units with Δ − null = 0 contribute nothing under any sign, so the pattern count is 2 ** (non-zero
    units); when that is at most `draws` every pattern is enumerated and the test is exact.
    """
    y = np.asarray(delta, dtype=float)
    n = y.size
    if n == 0:
        raise ValueError("no units")
    if strata is not None and len(strata) != n:
        raise ValueError("delta and strata differ in length")
    w = np.asarray(unit_weights, dtype=float) if unit_weights is not None else _unit_weights(n, strata, weighting)
    wd = w * (y - null)
    stat = float(wd.sum())
    active = wd[wd != 0]
    m = active.size
    if m == 0:
        return {"method": "signflip", "estimate": float((w * y).sum()), "null": null, "statistic": 0.0,
                "p": 1.0, "alternative": alternative, "n": n, "patterns": 1, "exact": True, "seed": seed}
    exact = (1 << m) <= draws and (1 << m) <= MAX_ENUMERATED if m < 63 else False
    if exact:
        signs, _ = _sign_matrix(m, draws, seed)
        stars = signs @ active
    else:
        rng = np.random.default_rng(seed)
        parts, left = [], draws
        while left:
            k = min(chunk, left)
            parts.append((rng.integers(0, 2, size=(k, m)) * 2 - 1).astype(float) @ active)
            left -= k
        stars = np.concatenate(parts)
    return {"method": "signflip", "estimate": float((w * y).sum()), "null": null, "statistic": stat,
            "p": _p_value(stat, stars, alternative, exact), "alternative": alternative, "n": n,
            "patterns": int(len(stars)), "exact": exact, "seed": seed}


def cluster_means(delta: Sequence[float], clusters: Sequence) -> tuple[np.ndarray, np.ndarray]:
    """(means, sizes) per cluster, in sorted cluster order."""
    y = np.asarray(delta, dtype=float)
    codes, g = _codes(clusters)
    sizes = np.bincount(codes, minlength=g).astype(float)
    return np.bincount(codes, y, minlength=g) / sizes, sizes


def signflip_cluster_means(delta: Sequence[float], clusters: Sequence, **kw) -> dict:
    """Branch (b): the exact sign-flip test on cluster means, weighted by cluster size so the
    statistic is still the mean over questions (the estimand of §9.2)."""
    means, sizes = cluster_means(delta, clusters)
    out = signflip(means, unit_weights=sizes / sizes.sum(), **kw)
    out.update(method="signflip_cluster_means", G=len(means), n=int(sizes.sum()))
    return out


# ── TOST, Holm, verdicts ────────────────────────────────────────────────

Test = Callable[..., dict]


def tost(test: Test, *args, margin: float = TOST_MARGIN, **kw) -> dict:
    """Two one-sided tests from the same method: H0 Δ <= −margin (upper tail) and H0 Δ >= +margin
    (lower tail). The TOST p is the larger of the two."""
    lower = test(*args, null=-margin, alternative="greater", **kw)["p"]
    upper = test(*args, null=margin, alternative="less", **kw)["p"]
    return {"p_lower": lower, "p_upper": upper, "p": max(lower, upper), "margin": margin}


def holm(pvalues: Mapping[str, float], alpha: float = FWER) -> dict[str, dict]:
    """Holm step-down: sort ascending, compare the i-th (0-based) with alpha / (m − i), stop at the
    first acceptance. p_adj is the monotone adjusted p-value, capped at 1."""
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(ordered)
    out, still, running = {}, True, 0.0
    for i, (name, p) in enumerate(ordered):
        threshold = alpha / (m - i)
        running = max(running, min(1.0, (m - i) * p))
        still = still and p <= threshold
        out[name] = {"p": p, "threshold": threshold, "reject": still, "p_adj": running}
    return out


def verdicts(members: Mapping[str, Mapping[str, float]], alpha: float = FWER) -> dict[str, str]:
    """§9.2 decision rule before the robustness qualifiers. Each member carries `estimate`, `p`
    (two-sided superiority) and `p_tost`. Holm on p across the family: a rejection reads ahead or
    behind by the sign of the estimate; otherwise Holm on p_tost across the family: equivalent;
    otherwise unresolved."""
    sup = holm({k: v["p"] for k, v in members.items()}, alpha)
    eq = holm({k: v["p_tost"] for k, v in members.items()}, alpha)
    out = {}
    for k, v in members.items():
        if sup[k]["reject"] and v["estimate"] != 0:
            out[k] = "ahead" if v["estimate"] > 0 else "behind"
        elif eq[k]["reject"]:
            out[k] = "equivalent"
        else:
            out[k] = "unresolved"
    return out


def bonferroni_level(family_size: int, alpha: float = FWER) -> float:
    """The displayed intervals' confidence level, 1 − alpha / F (§9.2)."""
    return 1.0 - alpha / family_size


def interval(test: Test, *args, level: float, bounds: tuple[float, float] = (-1.0, 1.0),
             tol: float = 1e-4, **kw) -> dict:
    """The interval {β0 : two-sided p(β0) > 1 − level}, by bisection on each side of the estimate.

    The same seed gives the same weights at every β0, so p(β0) is a deterministic function; an end
    that never reaches the cut stays at the bound and is flagged open (an exact test whose smallest
    p exceeds the cut can reject nothing, so its interval is the whole range).
    """
    cut = 1.0 - level
    p_at = lambda b0: test(*args, null=b0, alternative="two-sided", **kw)["p"]  # noqa: E731
    est = test(*args, null=0.0, alternative="two-sided", **kw)["estimate"]

    def edge(outer: float) -> tuple[float, bool]:
        if p_at(outer) > cut:
            return outer, True
        inner = est
        while abs(outer - inner) > tol:
            mid = (inner + outer) / 2
            if p_at(mid) > cut:
                inner = mid
            else:
                outer = mid
        return inner, False

    lo, lo_open = edge(bounds[0])
    hi, hi_open = edge(bounds[1])
    return {"estimate": est, "level": level, "lo": lo, "hi": hi, "lo_open": lo_open, "hi_open": hi_open}


# ── S6 descriptive rows ─────────────────────────────────────────────────

def row_deltas(delta: Sequence[float], rows: Sequence) -> dict[str, float]:
    """Each row's mean Δ (printed beside the S6 FC-SH verdict)."""
    y = np.asarray(delta, dtype=float)
    keys = np.asarray(rows, dtype=object).astype(str)
    return {k: float(y[keys == k].mean()) for k in sorted(set(keys))}


def length_trend(per_row: Mapping[str, float], row_tokens: Mapping[str, float]) -> float:
    """OLS slope of per-row Δ on log2(row length) - descriptive, never tested."""
    x = np.log2(np.array([row_tokens[k] for k in per_row], dtype=float))
    y = np.array(list(per_row.values()), dtype=float)
    if len(x) < 2 or np.ptp(x) == 0:
        return float("nan")
    x = x - x.mean()
    return float((x @ (y - y.mean())) / (x @ x))


# ── the §9.5 design check ───────────────────────────────────────────────

@dataclass(frozen=True)
class Design:
    """One confirmatory member's design, as the simulator draws it.

    Data model (declared here so the auditor can gate it): every question i in cluster g has a paired
    outcome per run with P(Δ = +1) = (d + Δ_g)/2, P(Δ = −1) = (d − Δ_g)/2, P(Δ = 0) = 1 − d, where d
    is the discordance. The cluster effect Δ_g is a Beta variable rescaled to [−d, d] with mean Δ and
    variance τ² = icc · (d − Δ²): E[Δ_i] = Δ and Var(Δ_i) = d − Δ² hold exactly, so icc is exactly the
    share of Var(Δ_i) between clusters, and nothing is clipped (a clipped normal would shrink the
    variance and pull the mean off Δ). Feasible while τ² < d² − Δ². Runs: each run keeps the
    question's first paired draw with probability sqrt(r) and redraws it otherwise, so two runs' Δ
    correlate at r. Graded scores (BEAM nuggets): a question's score is the mean of `nuggets` such
    draws, each kept from the run's draw with probability sqrt(nugget_corr). Fixed strata (S6 rows)
    carry no random effect: icc is ignored and the rows are the strata of the sign-flip test.

    Tests: "" is the stand's §9.2 method (the bootstrap-t on clusters, the sign-flip on strata);
    "signflip_cluster_means" is branch (b); "cluster_t" is the CR1 t against t_{G−1}, the analytic
    reference of §9.5.
    """
    sizes: tuple[int, ...]              # questions per cluster (or per row)
    runs: int = 1
    r: float = 0.8
    discordance: float = 0.275
    icc: float = 0.05
    nuggets: int = 1
    nugget_corr: float = 1.0
    strata: bool = False                # True: S6 rows, sign-flip; False: clusters, bootstrap-t
    method: str = ""

    @property
    def G(self) -> int:  # noqa: N802 - the §9.5 name
        return len(self.sizes)

    @property
    def n(self) -> int:
        return int(sum(self.sizes))

    @property
    def test(self) -> str:
        return self.method or ("signflip" if self.strata else "wild_cluster_t")


def _paired(rng: np.random.Generator, p_plus: np.ndarray, p_minus: np.ndarray, shape: tuple) -> np.ndarray:
    u = rng.random(shape)
    return (u < p_plus).astype(float) - ((u >= p_plus) & (u < p_plus + p_minus)).astype(float)


def cluster_effects(delta: float, d: float, icc: float, size: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    """Δ_g: a Beta on [−d, d] with mean `delta` and variance icc · (d − delta²)."""
    var = icc * (d - delta * delta)
    if var <= 0:
        return np.full(size, delta)
    mu = (delta + d) / (2 * d)
    v = var / (4 * d * d)
    nu = mu * (1 - mu) / v - 1
    if nu <= 0:
        raise ValueError(f"icc {icc} is not reachable at discordance {d} and Δ {delta}: "
                         f"needs icc·(d − Δ²) < d² − Δ²")
    return -d + 2 * d * rng.beta(mu * nu, (1 - mu) * nu, size=size)


def simulate(design: Design, delta: float, sims: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """(Y, codes): Y is sims × n run-averaged per-question Δ; codes are the cluster/row labels."""
    rng = np.random.default_rng(seed)
    d = design.discordance
    if not 0 < d <= 1 or abs(delta) > d:
        raise ValueError(f"|delta| must not exceed the discordance ({delta} vs {d})")
    codes = np.repeat(np.arange(design.G), design.sizes)
    n = design.n
    icc = 0.0 if design.strata else design.icc
    dq = cluster_effects(delta, d, icc, (sims, design.G), rng)[:, codes]
    p_plus, p_minus = (d + dq) / 2, (d - dq) / 2
    keep_run, keep_nug = math.sqrt(design.r), math.sqrt(design.nugget_corr)
    base = _paired(rng, p_plus, p_minus, (sims, n))
    total = np.zeros((sims, n))
    for _ in range(design.runs):
        run_q = np.where(rng.random((sims, n)) < keep_run, base, _paired(rng, p_plus, p_minus, (sims, n)))
        if design.nuggets == 1:
            total += run_q
            continue
        acc = np.zeros((sims, n))
        for _ in range(design.nuggets):
            acc += np.where(rng.random((sims, n)) < keep_nug, run_q, _paired(rng, p_plus, p_minus, (sims, n)))
        total += acc / design.nuggets
    return total / design.runs, codes


def _rejects(design: Design, ys: np.ndarray, codes: np.ndarray, alpha: float, draws: int, seed: int) -> np.ndarray:
    """Two-sided rejection at `alpha` for each simulated dataset, one weight/sign matrix for all."""
    out = np.empty(len(ys), dtype=bool)
    test, g = design.test, design.G
    if test in ("wild_cluster_t", "cluster_t"):
        ng = np.bincount(codes, minlength=g).astype(float)
        sum_n2 = float(ng @ ng)
        weights, exact = weight_matrix(g, draws, seed, weight_kind(g)) if test == "wild_cluster_t" else (None, True)
        if test == "cluster_t":
            from scipy.stats import t as student_t
            crit = float(student_t.ppf(1 - alpha / 2, g - 1))
        for lo in range(0, len(ys), 256):
            block = ys[lo:lo + 256]
            n = block.shape[1]
            s = np.stack([np.bincount(codes, row, minlength=g) for row in block], axis=1)   # G × D
            t = _cr1_t(s.sum(axis=0) / n, (s ** 2).sum(axis=0), (s * ng[:, None]).sum(axis=0), sum_n2, n, g)
            if test == "cluster_t":
                out[lo:lo + len(block)] = np.abs(t) > crit
                continue
            b = (weights @ s) / n
            t_star = _cr1_t(b, (weights ** 2) @ (s ** 2), weights @ (s * ng[:, None]), sum_n2, n, g)
            for j in range(block.shape[0]):
                out[lo + j] = _p_value(float(t[j]), t_star[:, j], "two-sided", exact) <= alpha
        return out
    if test == "signflip_cluster_means":
        for j, y in enumerate(ys):
            out[j] = signflip_cluster_means(y, codes, draws=draws, seed=seed)["p"] <= alpha
        return out
    if test == "signflip":
        wd = ys / ys.shape[1]                       # row-size weighting = the pooled mean
        if min(np.count_nonzero(wd, axis=1)) < 63 and (1 << int(min(np.count_nonzero(wd, axis=1)))) <= draws:
            for j, y in enumerate(ys):
                out[j] = signflip(y, codes, draws=draws, seed=seed)["p"] <= alpha
            return out
        # Every dataset is sampled, not enumerated: one sign matrix serves them all (a unit whose Δ is
        # 0 adds 0 under either sign, so sampling over all n units is the same test).
        stat = wd.sum(axis=1)
        cut = np.abs(stat) - _REL_TOL * np.maximum(1.0, np.abs(stat))
        rng, hits, left = np.random.default_rng(seed), np.zeros(len(ys)), draws
        while left:
            k = min(4_096, left)
            stars = (rng.integers(0, 2, size=(k, wd.shape[1])) * 2 - 1).astype(float) @ wd.T
            hits += np.count_nonzero(np.abs(stars) >= cut, axis=0)
            left -= k
        return (1 + hits) / (1 + draws) <= alpha
    raise ValueError(f"unknown test {test!r}")


def power(design: Design, delta: float, alpha: float, *, sims: int = 2_000, draws: int = BOOT_DRAWS,
          seed: int = SEED) -> float:
    """Share of `sims` simulated datasets at true Δ = `delta` whose two-sided test rejects at `alpha`."""
    ys, codes = simulate(design, delta, sims, seed)
    return float(_rejects(design, ys, codes, alpha, draws, seed + 1).mean())


def mde(design: Design, alpha: float, *, target: float = 0.8, step: float = 0.01, sims: int = 2_000,
        draws: int = BOOT_DRAWS, seed: int = SEED) -> dict:
    """The smallest Δ on a `step` grid (1 pp) whose power reaches `target`; None if none up to the
    discordance. The grid's powers are returned alongside."""
    grid, curve = [], {}
    k = 1
    while k * step <= design.discordance + 1e-12:
        grid.append(round(k * step, 10))
        k += 1
    for dlt in grid:
        curve[dlt] = power(design, dlt, alpha, sims=sims, draws=draws, seed=seed)
        if curve[dlt] >= target:
            return {"mde": dlt, "alpha": alpha, "target": target, "curve": curve}
    return {"mde": None, "alpha": alpha, "target": target, "curve": curve}


def coverage(design: Design, *, level: float = 0.95, sims: int = 2_000, draws: int = BOOT_DRAWS,
             seed: int = SEED) -> float:
    """Coverage of the nominal `level` interval at Δ = 0: the interval inverts the test, so it covers
    0 exactly when the test at 1 − level does not reject 0."""
    return 1.0 - power(design, 0.0, 1.0 - level, sims=sims, draws=draws, seed=seed)


def closed_form_power(delta: float, n: int, discordance: float, alpha: float) -> float:
    """The paired-binomial normal approximation at ICC 0 and one run: Δ_i iid in {−1, 0, 1} with
    Var = d − Δ² (d under the null). The simulator must reproduce it (the auditor's known answer)."""
    from statistics import NormalDist
    z = NormalDist()
    crit = z.inv_cdf(1 - alpha / 2) * math.sqrt(discordance)
    sd = math.sqrt(discordance - delta * delta)
    return z.cdf((abs(delta) * math.sqrt(n) - crit) / sd) + z.cdf((-abs(delta) * math.sqrt(n) - crit) / sd)
