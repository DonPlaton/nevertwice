#!/usr/bin/env python3
"""PREREG-V3 TB10: the confirmatory statistics give the answers that can be known in advance.

A bespoke bootstrap or a hand-rolled Holm is exactly the code that looks right and is off by one,
so every method here is held to an answer computed another way:

* the sign-flip test on four units enumerates all 16 sign patterns: its smallest two-sided p is
  2/16 = 0.125, a mixed-sign case is counted by hand, and a unit whose Δ is 0 flips nothing;
* the wild cluster bootstrap-t is recomputed pattern by pattern with a plain loop (explicit bootstrap
  sample, explicit CR1 standard error) and must agree with the vectorised version; with identical
  clusters only the two constant patterns are as extreme, p = 2 / 2**G; Webb weights below G = 12,
  Rademacher from 12; the null is imposed (shifting data and null together changes nothing);
* TOST at the margin: Δ exactly at +5 pp can never be called equivalent, Δ = 0 on ten units reaches
  p = 1/1024 on each side;
* Holm on a textbook vector, including the step-down stop;
* the §9.5 simulator reproduces the closed-form paired-binomial power at ICC 0 and one run, holds
  its declared run correlation r and its declared ICC, and covers at ~95 % where the method is sound.

No network, no model, fixed seeds.

    python tests/research/_test_v3_stats.py
"""
from __future__ import annotations

import importlib.util
import itertools
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

import _env_guard  # noqa: F401,E402  hermetic like every suite, though nothing here reaches a network

# By path, under its own name: `stats` on sys.path is the product's nevertwice/stats.py.
_spec = importlib.util.spec_from_file_location("v3_stats", ROOT / "research" / "v3" / "stats.py")
st = importlib.util.module_from_spec(_spec)
sys.modules["v3_stats"] = st
_spec.loader.exec_module(st)

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def close(a: float, b: float, tol: float = 1e-12) -> bool:
    return abs(a - b) <= tol


print("\n- the sign-flip test, by enumeration -")
r = st.signflip([0.1, 0.2, 0.3, 0.4])
check("four positive units: 16 patterns, exact, two-sided p = 0.125",
      r["exact"] and r["patterns"] == 16 and close(r["p"], 0.125), str(r))
r = st.signflip([0.1, 0.2, 0.3, 0.4], alternative="greater")
check("... one-sided (greater) p = 1/16", close(r["p"], 1 / 16), str(r["p"]))
r = st.signflip([0.4, 0.3, 0.2, -0.1])
check("mixed signs [.4 .3 .2 -.1]: |sum| >= 0.8 in 4 of 16 patterns, p = 0.25", close(r["p"], 0.25), str(r["p"]))
r = st.signflip([0.5, 0.0, 0.0])
check("zero units flip nothing: [.5 0 0] has 2 patterns and p = 1", r["patterns"] == 2 and close(r["p"], 1.0), str(r))
r = st.signflip([0.0, 0.0])
check("all-zero Δ: p = 1", close(r["p"], 1.0), str(r))
r = st.signflip([0.3] * 40)
check("forty positive units are sampled (2**40 > draws): p = 1/(1 + 99,999)",
      not r["exact"] and r["patterns"] == st.SIGNFLIP_DRAWS and close(r["p"], 1 / (1 + st.SIGNFLIP_DRAWS)), str(r["p"]))
check("... and the same seed gives the same p", close(st.signflip([0.3, -0.1] * 20)["p"], st.signflip([0.3, -0.1] * 20)["p"]))
rows = ["a"] * 3 + ["b"]
y = [0.2, 0.2, 0.2, -0.4]
check("row-size weighting is the pooled mean", close(st.signflip(y, rows)["estimate"], np.mean(y)))
check("equal weighting counts each row once", close(st.signflip(y, rows, weighting="equal")["estimate"], (0.2 - 0.4) / 2))

print("\n- the wild cluster bootstrap-t -")
check("Webb below G = 12", st.weight_kind(11) == "webb")
check("Rademacher from G = 12", st.weight_kind(12) == "rademacher")


def brute_wild_t(y, clusters, null, kind):
    """Pattern by pattern: build y*, refit the mean, CR1 standard error from explicit residuals."""
    y = np.asarray(y, float)
    labels = sorted(set(clusters))
    idx = [np.array([c == lab for c in clusters]) for lab in labels]
    g, n = len(labels), len(y)

    def t_of(v, b0):
        b = v.mean()
        ss = sum(((v[m] - b).sum()) ** 2 for m in idx)
        se = math.sqrt(g / (g - 1) * ss) / n
        return (b - b0) / se

    t_obs = t_of(y, null)
    points = st.RADEMACHER if kind == "rademacher" else st.WEBB
    hits = total = 0
    for w in itertools.product(points, repeat=g):
        ystar = y.copy()
        for wg, m in zip(w, idx):
            ystar[m] = null + wg * (y[m] - null)
        total += 1
        hits += abs(t_of(ystar, null)) >= abs(t_obs) - 1e-9 * max(1.0, abs(t_obs))
    return t_obs, hits / total


rng = np.random.default_rng(7)
cl = np.repeat(np.arange(4), [3, 5, 2, 6])
yv = rng.choice([-1.0, 0.0, 0.0, 1.0], size=cl.size) + 0.1
for null in (0.0, 0.05):
    t_b, p_b = brute_wild_t(yv, list(cl), null, "webb")
    r = st.wild_cluster_t(yv, cl, null=null)
    check(f"G = 4 Webb, null {null}: 1,296 patterns enumerated, exact", r["exact"] and r["patterns"] == 6 ** 4, str(r))
    check(f"... t and p equal the pattern-by-pattern loop (t {t_b:.4f}, p {p_b:.4f})",
          close(r["t"], t_b, 1e-9) and close(r["p"], p_b, 1e-12), f"t {r['t']} p {r['p']}")
cl12 = np.repeat(np.arange(12), 2)
y12 = rng.choice([-1.0, 0.0, 1.0], size=cl12.size) + 0.2
t_b, p_b = brute_wild_t(y12, list(cl12), 0.0, "rademacher")
r = st.wild_cluster_t(y12, cl12)
check("G = 12 Rademacher: 4,096 patterns enumerated and equal to the loop",
      r["kind"] == "rademacher" and r["exact"] and r["patterns"] == 4096 and close(r["p"], p_b, 1e-12),
      f"{r['kind']} {r['patterns']} p {r['p']} vs {p_b}")
same = np.tile([1.0, 0.0], 12)
r = st.wild_cluster_t(same, np.repeat(np.arange(12), 2))
check("identical clusters: only the two constant patterns are as extreme, p = 2/4096", close(r["p"], 2 / 4096), str(r["p"]))
r = st.wild_cluster_t(np.tile([0.1, 0.2], 12), np.repeat(np.arange(12), 2))
check("... also where the decimals leave rounding residue in the CR1 sum (t = inf, p = 2/4096)",
      math.isinf(r["t"]) and close(r["p"], 2 / 4096), f"t {r['t']} p {r['p']}")
cl14 = np.repeat(np.arange(14), 3)
y14 = rng.choice([-1.0, 0.0, 1.0], size=cl14.size)
r = st.wild_cluster_t(y14, cl14)
check("G = 14 is sampled: 9,999 draws, p = (1 + hits) / 10,000", not r["exact"] and r["patterns"] == st.BOOT_DRAWS
      and close(r["p"] * (1 + st.BOOT_DRAWS), round(r["p"] * (1 + st.BOOT_DRAWS))), str(r))
a = st.wild_cluster_t(y14, cl14, null=0.1)
b = st.wild_cluster_t(y14 + 0.3, cl14, null=0.4)
check("the null is imposed: shifting data and null together changes neither t nor p",
      close(a["t"], b["t"], 1e-9) and close(a["p"], b["p"]), f"{a['p']} vs {b['p']}")
check("two-sided p is symmetric in the sign of Δ", close(st.wild_cluster_t(y14, cl14)["p"], st.wild_cluster_t(-y14, cl14)["p"]))

print("\n- TOST at the margin -")
t0 = st.tost(st.signflip, [0.0] * 10)
check("Δ = 0 on ten units: each side p = 1/1024, TOST p = 1/1024",
      close(t0["p_lower"], 1 / 1024) and close(t0["p_upper"], 1 / 1024) and close(t0["p"], 1 / 1024), str(t0))
t5 = st.tost(st.signflip, [0.05] * 10)
check("Δ exactly at +5 pp: the upper side cannot reject, TOST p = 1", close(t5["p_upper"], 1.0) and close(t5["p"], 1.0), str(t5))
t6 = st.tost(st.signflip, [0.06] * 10)
check("Δ beyond the margin (+6 pp): TOST p = 1", close(t6["p"], 1.0), str(t6))
tw = st.tost(st.wild_cluster_t, y14 * 0.01, cl14)
check("the bootstrap-t TOST takes the larger one-sided p", close(tw["p"], max(tw["p_lower"], tw["p_upper"])), str(tw))

print("\n- Holm, step-down -")
h = st.holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.005})
check("textbook vector: d and a rejected, c and b not",
      [h[k]["reject"] for k in "dacb"] == [True, True, False, False], str(h))
check("... adjusted p 0.02, 0.03, 0.06, 0.06",
      all(close(h[k]["p_adj"], v) for k, v in zip("dacb", (0.02, 0.03, 0.06, 0.06))), str({k: h[k]["p_adj"] for k in h}))
h = st.holm({"x": 0.02, "y": 0.02, "z": 0.049})
check("the step-down stops: 0.049 <= 0.05 but the first step failed, nothing rejects",
      not any(v["reject"] for v in h.values()), str(h))
v = st.verdicts({"m1": {"estimate": 0.1, "p": 0.001, "p_tost": 0.9},
                 "m2": {"estimate": -0.1, "p": 0.002, "p_tost": 0.9},
                 "m3": {"estimate": 0.0, "p": 0.9, "p_tost": 0.001},
                 "m4": {"estimate": 0.02, "p": 0.5, "p_tost": 0.4}})
check("verdicts: ahead, behind, equivalent, unresolved",
      [v[k] for k in ("m1", "m2", "m3", "m4")] == ["ahead", "behind", "equivalent", "unresolved"], str(v))
check("the Bonferroni display level for F = 3 is 1 - 0.05/3", close(st.bonferroni_level(3), 1 - 0.05 / 3))

print("\n- intervals invert the member's own test -")
iv = st.interval(st.signflip, [0.1, 0.2, 0.3, 0.4], level=0.95)
check("four units cannot reject at 5 %: the interval is the whole range, both ends open",
      iv["lo_open"] and iv["hi_open"] and iv["lo"] == -1.0 and iv["hi"] == 1.0, str(iv))
yy = np.r_[np.full(30, 0.4), np.full(10, -0.2)]
iv = st.interval(st.signflip, yy, level=0.95)
check("forty units: the interval holds the estimate and excludes 0",
      iv["lo"] < iv["estimate"] < iv["hi"] and iv["lo"] > 0 and not (iv["lo_open"] or iv["hi_open"]), str(iv))
p_edge = st.signflip(yy, null=iv["lo"] - 2e-4)["p"]
check("... just outside the lower end the test rejects", p_edge <= 0.05, str(p_edge))

print("\n- S6 rows -")
pr = st.row_deltas([0.1, 0.3, -0.2, 0.0], ["6K", "6K", "32K", "32K"])
check("per-row Δ", close(pr["6K"], 0.2) and close(pr["32K"], -0.1), str(pr))
trend = st.length_trend({"a": 0.3, "b": 0.2, "c": 0.1}, {"a": 8, "b": 16, "c": 32})
check("length trend: Δ falling 0.1 per doubling has slope -0.1", close(trend, -0.1, 1e-12), str(trend))

print("\n- the §9.5 simulator -")
alpha = 0.05
for dlt in (0.0, 0.06, 0.1):
    des = st.Design(sizes=(1,) * 200, runs=1, icc=0.0, discordance=0.275, method="cluster_t")
    sim = st.power(des, dlt, alpha, sims=3000)
    cf = st.closed_form_power(dlt, des.n, des.discordance, alpha) if dlt else alpha
    check(f"ICC 0, one run, n 200, CR1 t: power at Δ {dlt} is {sim:.3f}, closed form {cf:.3f} (±0.03)",
          abs(sim - cf) <= 0.03)
size = st.power(st.Design(sizes=(5,) * 40, runs=1, icc=0.0), 0.0, alpha, sims=1500, draws=999)
check(f"the bootstrap-t never over-rejects: size {size:.3f} <= 0.05 + 2 SE",
      size <= 0.05 + 2 * math.sqrt(0.05 * 0.95 / 1500))
ys, codes = st.simulate(st.Design(sizes=(200,), runs=2, r=0.8, icc=0.0, discordance=0.3), 0.0, 400, 11)
var2 = float(ys.var())
check(f"two runs at r 0.8 average to Var = d(1 + r)/2 = 0.270 (got {var2:.4f})", abs(var2 - 0.3 * 1.8 / 2) < 0.006)


def icc_recovered(icc: float, d: float, sims: int, seed: int, m_q: int = 20) -> float:
    ys, codes = st.simulate(st.Design(sizes=(m_q,) * 30, runs=1, icc=icc, discordance=d), 0.0, sims, seed)
    means = np.stack([np.bincount(codes, y) / m_q for y in ys])
    total = float(ys.var(axis=1).mean())
    return (float(means.var(axis=1, ddof=1).mean()) - total / m_q) / (1 - 1 / m_q) / total


for icc, d, tol in ((0.2, 0.3, 0.03), (0.05, 0.275, 0.02)):
    try:
        got = icc_recovered(icc, d, 800, 13)
        check(f"declared ICC {icc} at d {d} is recovered from the cluster means (got {got:.3f}, ±{tol})",
              abs(got - icc) < tol)
    except ValueError as exc:
        check(f"declared ICC {icc} at d {d} is recovered from the cluster means", False, repr(exc))
ys, _ = st.simulate(st.Design(sizes=(10,) * 30, runs=1, icc=0.05, discordance=0.275), 0.15, 400, 17)
check(f"the cluster effect keeps the mean at Δ = 0.15 (got {ys.mean():.4f})", abs(ys.mean() - 0.15) < 0.005)
try:
    st.simulate(st.Design(sizes=(5,)), 0.5, 1, 1)
    check("|Δ| above the discordance is refused", False)
except ValueError:
    check("|Δ| above the discordance is refused", True)
try:
    st.simulate(st.Design(sizes=(5,) * 4, icc=0.3, discordance=0.275), 0.0, 1, 1)
    check("an unreachable ICC is refused, not clipped", False)
except ValueError:
    check("an unreachable ICC is refused, not clipped", True)
des6 = st.Design(sizes=(30,) * 4, runs=5, strata=True)
ys6, codes6 = st.simulate(des6, 0.06, 150, 21)
one_by_one = np.array([st.signflip(y, codes6, draws=1999, seed=5)["p"] <= 0.05 for y in ys6])
batched = st._rejects(des6, ys6, codes6, 0.05, 1999, 5)
check(f"the batched S6 sign-flip rejects like the one-dataset test ({batched.mean():.3f} vs {one_by_one.mean():.3f}, "
      f"agreement {np.mean(batched == one_by_one):.3f} >= 0.95)", np.mean(batched == one_by_one) >= 0.95)
size6 = st.power(des6, 0.0, 0.05, sims=1500, draws=1999)
check(f"the S6 sign-flip never over-rejects: size {size6:.3f} <= 0.05 + 2 SE",
      size6 <= 0.05 + 2 * math.sqrt(0.05 * 0.95 / 1500))
cov = st.coverage(st.Design(sizes=(5,) * 20, runs=2, icc=0.05), sims=800, draws=999)
check(f"G = 20 bootstrap-t covers at least 93 % (got {cov:.3f})", cov >= 0.93)
m = st.mde(st.Design(sizes=(5,) * 40, runs=1, icc=0.0), alpha, sims=400, draws=499, step=0.02)
check("MDE is the first grid point whose power reaches 0.8", m["mde"] is not None and m["curve"][m["mde"]] >= 0.8
      and all(p < 0.8 for d, p in m["curve"].items() if d < m["mde"]), str(m))

print(f"\nv3 stats: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
