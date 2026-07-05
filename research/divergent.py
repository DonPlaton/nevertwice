#!/usr/bin/env python3
"""RESEARCH - serendipitous / divergent recall (roadmap 2B).

THESIS. Everyone optimizes relevance (convergence). Sometimes the useful memory is the
distant-but-bridgeable one - a note connecting two active-but-unlinked clusters, which a
pure-relevance ranker buries under nearer in-cluster notes. A controllable knob
`NEVERTWICE_DIVERGENCE ∈ [0,1]` should trade convergence↔serendipity WITHOUT destroying
relevance, and a bridge-aware variant should surface those connectors specifically.

THREE recall modes (select top-k from the pool for a query active in cluster i):
  • relevance - top-k by cosine (convergent; the baseline).
  • MMR       - Maximal Marginal Relevance: λ·rel − (1−λ)·max-sim-to-selected, λ=1−div
                (diverse, but diversity ≠ bridging - it may pick any far note).
  • bridge    - (1−div)·rel + div·bridge(m), bridge(m)=product of m's top-2 cosines to the
                cluster centroids: a query-independent betweenness proxy, high for a note that
                sits between two clusters (the graph machinery's structural bridge, made cheap).

EXPERIMENT (synthetic world with planted bridge notes = normalize(c_a + c_b)):
  • bridge-recall@k - does a bridge FROM the active cluster appear in the top-k? (swept over div)
  • relevance ↔ novelty Pareto - top-k mean relevance vs mean distance-from-home-cluster (the
    controllable frontier)
  • cross-cluster surfacing rate - fraction of top-k outside the home cluster

CLAIM. "Divergent recall: controllable serendipity in agent memory, and the relevance-surprise
frontier." Honest scope: structural metrics only - the LLM-judged "did this spark a useful
connection" is the fuzzy part left to a human/LLM study; synthetic, seeded, CPU.

    python research/divergent.py            # report
    python research/divergent.py --save     # + divergent.json (+ .png if mpl)
    python research/divergent.py --quick    # smoke (fewer queries/seeds)

Research dep: numpy (matplotlib optional). Seeded; deterministic.
"""
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("divergent needs numpy (research dep): pip install numpy", file=sys.stderr)
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QUICK = "--quick" in sys.argv
SAVE = "--save" in sys.argv
DIM = 48
N_CLUSTERS = 12
PER_CLUSTER = 10
N_BRIDGES = 18                      # planted notes between random cluster pairs
SPREAD = 0.05                       # tight in-cluster perturbation (scaled for DIM) → pure
#                       relevance fills top-k with in-cluster notes and BURIES the bridges
SIGMA_Q = 0.05                      # crisp query noise
K = 5                              # top-k recall set
DIVS = [0.0, 0.25, 0.5, 0.75, 1.0]
QUERIES = 200 if QUICK else 800
SEEDS = 3 if QUICK else 6


def gen_world(rng):
    """Centroids, in-cluster notes, and planted bridge notes (between two clusters)."""
    cent = rng.normal(size=(N_CLUSTERS, DIM))
    cent /= np.linalg.norm(cent, axis=1, keepdims=True)
    vecs, cluster, bridges_of = [], [], []          # bridges_of[i] = set of clusters a note bridges
    for c in range(N_CLUSTERS):
        for _ in range(PER_CLUSTER):
            v = cent[c] + SPREAD * rng.normal(size=DIM)
            vecs.append(v / np.linalg.norm(v))
            cluster.append(c)
            bridges_of.append(set())
    for _ in range(N_BRIDGES):
        a, b = rng.choice(N_CLUSTERS, size=2, replace=False)
        v = cent[a] + cent[b]                        # equidistant → a bridge
        vecs.append(v / np.linalg.norm(v))
        cluster.append(-1)                           # not a home cluster
        bridges_of.append({int(a), int(b)})
    return {"vec": np.array(vecs), "cluster": np.array(cluster),
            "bridges_of": bridges_of, "cent": cent}


def bridge_score(world):
    """Betweenness proxy per note: product of its top-2 cosines to the cluster centroids -
    high when a note sits between two clusters, ~0 when it's firmly inside one."""
    sims = world["vec"] @ world["cent"].T            # (N, N_CLUSTERS)
    top2 = np.sort(sims, axis=1)[:, -2:]
    return np.clip(top2[:, 0], 0, None) * np.clip(top2[:, 1], 0, None)


def select(mode, div, qv, world, bscore):
    """Return the top-K note indices under a recall mode."""
    rel = world["vec"] @ qv
    if mode == "relevance" or div == 0.0:
        return list(np.argsort(-rel)[:K])
    if mode == "bridge":
        score = (1 - div) * rel + div * bscore
        return list(np.argsort(-score)[:K])
    # MMR (vectorised): greedy λ·rel − (1−λ)·max sim-to-selected; one matmul per pick
    lam = 1 - div
    chosen = []
    max_sim = np.full(len(rel), -1.0)               # max similarity to the chosen set
    avail = np.ones(len(rel), dtype=bool)
    for _ in range(K):
        marg = lam * rel - (1 - lam) * np.clip(max_sim, 0, None)
        marg[~avail] = -np.inf
        best = int(np.argmax(marg))
        chosen.append(best)
        avail[best] = False
        max_sim = np.maximum(max_sim, world["vec"] @ world["vec"][best])
    return chosen


def run():
    modes = ("relevance", "mmr", "bridge")
    # metrics[mode][div] = {bridge_recall, relevance, novelty, cross}
    agg = {md: {d: {k: [] for k in ("br", "rel", "nov", "cross")} for d in DIVS} for md in modes}
    for seed in range(SEEDS):
        wrng = np.random.default_rng(11000 + seed)
        world = gen_world(wrng)
        bscore = bridge_score(world)
        cent = world["cent"]
        qrng = np.random.default_rng(22000 + seed)
        for _ in range(QUERIES):
            i = int(qrng.integers(N_CLUSTERS))
            qv = cent[i] + SIGMA_Q * qrng.normal(size=DIM)
            qv /= np.linalg.norm(qv)
            for md in modes:
                for d in DIVS:
                    sel = select(md, d, qv, world, bscore)
                    br = any(i in world["bridges_of"][s] for s in sel)
                    rel = float(np.mean([world["vec"][s] @ qv for s in sel]))
                    nov = float(np.mean([1 - world["vec"][s] @ cent[i] for s in sel]))
                    cross = float(np.mean([world["cluster"][s] != i for s in sel]))
                    agg[md][d]["br"].append(1.0 if br else 0.0)
                    agg[md][d]["rel"].append(rel)
                    agg[md][d]["nov"].append(nov)
                    agg[md][d]["cross"].append(cross)
    return agg


def _m(xs):
    return float(np.mean(xs)) if xs else 0.0


def main():
    bar = "=" * 78
    print(bar)
    print("  SERENDIPITOUS / DIVERGENT RECALL (2B) - controllable convergence↔surprise")
    print(bar)
    print(f"  world: {N_CLUSTERS} clusters × {PER_CLUSTER} notes + {N_BRIDGES} planted bridges (dim {DIM}); "
          f"{QUERIES}×{SEEDS} queries; top-{K}")
    agg = run()

    print(f"\n- bridge-recall@{K} (a bridge FROM the active cluster surfaced) vs divergence -")
    print(f"  {'divergence':>11}" + "".join(f"{d:>8}" for d in DIVS))
    for md in ("relevance", "mmr", "bridge"):
        cells = "".join(f"{_m(agg[md][d]['br']):>8.3f}" for d in DIVS)
        print(f"  {md:>11}{cells}")
    base = _m(agg["relevance"][0.0]["br"])
    bb = _m(agg["bridge"][0.75]["br"])
    mm = _m(agg["mmr"][0.75]["br"])
    print(f"  → at div=0 (pure relevance) bridge-recall {base:.3f}; bridge-aware@0.75 {bb:.3f}, "
          f"MMR@0.75 {mm:.3f} - bridges are specifically recovered, diversity alone less so.")

    print(f"\n- relevance ↔ novelty Pareto (bridge mode, top-{K} means) vs divergence -")
    print(f"  {'div':>5} {'relevance':>10} {'novelty':>9} {'cross-cluster':>14}")
    for d in DIVS:
        print(f"  {d:>5} {_m(agg['bridge'][d]['rel']):>10.3f} {_m(agg['bridge'][d]['nov']):>9.3f} "
              f"{_m(agg['bridge'][d]['cross']):>14.3f}")
    print("  → the knob trades relevance for novelty/cross-cluster surfacing on a smooth frontier.")

    if SAVE:
        out = {"clusters": N_CLUSTERS, "bridges": N_BRIDGES, "k": K, "divs": DIVS,
               "bridge_recall": {md: {str(d): _m(agg[md][d]["br"]) for d in DIVS}
                                 for md in agg},
               "pareto_bridge": {str(d): {"relevance": _m(agg["bridge"][d]["rel"]),
                                          "novelty": _m(agg["bridge"][d]["nov"]),
                                          "cross": _m(agg["bridge"][d]["cross"])} for d in DIVS}}
        p = HERE / "divergent.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        _figure(agg, HERE / "divergent.png")
    print(bar)


def _figure(agg, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [figure skipped: matplotlib unavailable - {e}]")
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.3))
    for md, mark in (("relevance", "o"), ("mmr", "s"), ("bridge", "^")):
        ax1.plot(DIVS, [_m(agg[md][d]["br"]) for d in DIVS], marker=mark, label=md)
    ax1.set_xlabel("divergence knob")
    ax1.set_ylabel(f"bridge-recall@{K}")
    ax1.set_title("Bridge recovery vs divergence")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    ax2.plot([_m(agg["bridge"][d]["rel"]) for d in DIVS],
             [_m(agg["bridge"][d]["nov"]) for d in DIVS], marker="o")
    for d in DIVS:
        ax2.annotate(f"{d}", (_m(agg["bridge"][d]["rel"]), _m(agg["bridge"][d]["nov"])), fontsize=7)
    ax2.set_xlabel("top-k relevance")
    ax2.set_ylabel("top-k novelty (distance from home)")
    ax2.set_title("Relevance-surprise frontier (bridge mode)")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"  figure → {path}")


if __name__ == "__main__":
    main()
