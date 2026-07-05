#!/usr/bin/env python3
"""RESEARCH - recurrence-as-salience ablation (longitudinal agent memory).

THESIS. An agent that re-encounters a "gotcha" should recall it more readily the
more often it has recurred. We frame recurrence-boosting as *approximate Bayesian
retrieval with a frequency prior*:

    P(target | query) ∝ P(query | target) · P(target)
                         └ relevance ┘     └ recurrence ┘

so the rank score should fuse a relevance term (cosine) with a recurrence prior.
Nevertwice ships exactly such a fusion (`_recur_boost` + the `_salience_mult`
re-weight). This harness asks the questions a reviewer would:

  Q1  Does fusing recurrence with relevance beat relevance-only - and WHEN?
      (hypothesis: only when relevance is AMBIGUOUS, i.e. several stored lessons
      look alike; with a crisp relevance signal recurrence is a no-op.)
  Q2  What is the optimal fusion weight w*, and does the SHIPPED coefficient land
      near it? (the production additive boost uses 0.03 in `_recur_boost` but an
      inline 0.0003 in `retrieve_relevant` - are either well-calibrated?)
  Q3  Linear recurrence (n−1, shipped) vs a log frequency prior log(n) - which
      fuses better? (a frequency prior is log-scaled in theory.)

METHOD. A controlled, fully-seeded longitudinal world: C topic clusters, each with
several near-duplicate lessons (so relevance alone cannot disambiguate within a
cluster). Each lesson's recurrence is drawn Zipf (a few persistent traps, a long
tail of one-offs). A test query targets a lesson with probability ∝ its recurrence
(persistent traps are what you hit again) and is the target's latent vector plus
Gaussian noise of scale σ (the ambiguity knob). The candidate pool is the target's
cluster. We sweep σ and the fusion weight w, average over many queries × seeds, and
report recall@1/recall@3/MRR with 95% CIs. The SHIPPED ranker (`m.cosine` +
`m._recur_boost`) is measured directly so the numbers describe the real system.

Synthetic latent vectors (not an LLM embedder) are deliberate: they make the
ambiguity axis a controlled variable and the whole study reproducible on CPU in
seconds. This measures the RANKER, not an embedder; it is a mechanism ablation, not
an external benchmark (cf. eval_harness Task A's honesty note).

    python research/recurrence_ablation.py            # report
    python research/recurrence_ablation.py --save     # + recurrence_ablation.json (+ .png if mpl)
    python research/recurrence_ablation.py --quick    # fewer trials (smoke)

Research dep: numpy (matplotlib optional, for the figure). Seeded; deterministic.
"""
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("recurrence_ablation needs numpy (research dep): pip install numpy", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import memory_hook as m

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QUICK = "--quick" in sys.argv
SAVE = "--save" in sys.argv

# ── experiment configuration (all fixed; vary only via these constants) ──
DIM = 48
N_CLUSTERS = 50
PER_CLUSTER = 8                 # near-duplicates per topic → within-cluster ambiguity
INTRA_SPREAD = 0.40            # perturbation around a cluster centroid
ZIPF_A = 1.7                   # recurrence law (heavy head, long tail)
RECUR_CAP = 30
SIGMAS = [0.0, 0.3, 0.6, 0.9, 1.2, 1.6, 2.0]   # query ambiguity (noise scale)
W_GRID = [round(0.1 * i, 1) for i in range(11)]  # relevance↔recurrence fusion weight
QUERIES = 800 if QUICK else 3000
SEEDS = 4 if QUICK else 8


# ── world + query generators (seeded) ──────────────────────────────────

def gen_world(rng):
    """Return (vecs[N,DIM] unit-norm, cluster[N], recurrence[N])."""
    centroids = rng.normal(size=(N_CLUSTERS, DIM))
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    vecs, cluster, recur = [], [], []
    for c in range(N_CLUSTERS):
        for _ in range(PER_CLUSTER):
            v = centroids[c] + INTRA_SPREAD * rng.normal(size=DIM)
            v /= np.linalg.norm(v)
            vecs.append(v)
            cluster.append(c)
            recur.append(min(int(rng.zipf(ZIPF_A)), RECUR_CAP))
    return np.array(vecs), np.array(cluster), np.array(recur, dtype=float)


def _norm01(x):
    lo, hi = x.min(), x.max()
    return (x - lo) / (hi - lo) if hi > lo else np.full_like(x, 0.5)


def run_condition(rng, vecs, cluster, recur, sigma):
    """One query at ambiguity sigma → per-w recall@1/3 + production-ranker hit.
    Returns (rel_only_hit1, prod_hit1, {w: (hit1, hit3, rr)}, log_hit1_at_best)."""
    # target ∝ recurrence (persistent traps recur); pool = its cluster
    p = recur / recur.sum()
    t = rng.choice(len(vecs), p=p)
    pool = np.where(cluster == cluster[t])[0]
    if len(pool) < 2:
        return None
    qv = vecs[t] + sigma * rng.normal(size=DIM)
    qv /= np.linalg.norm(qv)

    sims = vecs[pool] @ qv                       # relevance (cosine, unit vectors)
    rcr = recur[pool]
    rel_n = _norm01(sims)
    rec_lin_n = _norm01(rcr - 1.0)               # shipped form: (n−1), normalized
    rec_log_n = _norm01(np.log(rcr))             # Bayesian frequency prior, normalized
    tgt = int(np.where(pool == t)[0][0])

    def metrics(score):
        order = np.argsort(-score, kind="stable")
        rank = int(np.where(order == tgt)[0][0])  # 0-based
        return (1.0 if rank == 0 else 0.0,
                1.0 if rank < 3 else 0.0,
                1.0 / (rank + 1))

    per_w = {}
    for w in W_GRID:
        per_w[w] = metrics((1 - w) * rel_n + w * rec_lin_n)
    log_best = max(metrics((1 - w) * rel_n + w * rec_log_n)[0] for w in W_GRID)
    rel_only = metrics(rel_n)[0]
    # SHIPPED ranker: raw cosine + production additive _recur_boost (0.03·(n−1))
    prod = sims + np.array([m._recur_boost({"recurrence": int(n)}) for n in rcr])
    prod_hit = metrics(prod)[0]
    return rel_only, prod_hit, per_w, log_best


def aggregate():
    """Sweep σ × seeds × queries → mean recall with 95% CIs."""
    # per σ: rel-only hits, prod hits, log-best hits, and per-w (hit1,hit3,rr) lists
    res = {s: {"rel": [], "prod": [], "log": [],
               "w": {w: {"h1": [], "h3": [], "rr": []} for w in W_GRID}}
           for s in SIGMAS}
    for seed in range(SEEDS):
        rng = np.random.default_rng(1000 + seed)
        vecs, cluster, recur = gen_world(rng)
        for sigma in SIGMAS:
            qrng = np.random.default_rng(7000 + seed * 97 + int(sigma * 1000))
            for _ in range(QUERIES):
                out = run_condition(qrng, vecs, cluster, recur, sigma)
                if out is None:
                    continue
                rel_only, prod_hit, per_w, log_best = out
                res[sigma]["rel"].append(rel_only)
                res[sigma]["prod"].append(prod_hit)
                res[sigma]["log"].append(log_best)
                for w, (h1, h3, rr) in per_w.items():
                    res[sigma]["w"][w]["h1"].append(h1)
                    res[sigma]["w"][w]["h3"].append(h3)
                    res[sigma]["w"][w]["rr"].append(rr)
    return res


from _common import _ci          # W12: shared mean±95%CI helper


def summarize(res):
    rows = []
    for sigma in SIGMAS:
        d = res[sigma]
        rel_m, rel_c = _ci(d["rel"])
        prod_m, prod_c = _ci(d["prod"])
        # best fusion weight by recall@1 at this σ
        w_means = {w: _ci(d["w"][w]["h1"])[0] for w in W_GRID}
        w_star = max(W_GRID, key=lambda w: w_means[w])
        best_m, best_c = _ci(d["w"][w_star]["h1"])
        log_m, _ = _ci(d["log"])
        rows.append({
            "sigma": sigma,
            "relevance_only_R@1": rel_m, "relevance_only_ci": rel_c,
            "best_fusion_w": w_star, "best_fusion_R@1": best_m, "best_fusion_ci": best_c,
            "fusion_lift": best_m - rel_m,
            "shipped_recur_R@1": prod_m, "shipped_recur_ci": prod_c,
            "log_prior_R@1": log_m,
            "recurrence_only_R@1": _ci(d["w"][1.0]["h1"])[0],
        })
    return rows


def coeff_sweep(res, sigma):
    """recall@1 as a function of the fusion weight w, at one ambiguity level."""
    d = res[sigma]
    return [(w, _ci(d["w"][w]["h1"])[0], _ci(d["w"][w]["h1"])[1]) for w in W_GRID]


def make_figure(rows, sweep_mid, sigma_mid, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [figure skipped: matplotlib unavailable - {e}]")
        return None
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    sig = [r["sigma"] for r in rows]
    ax1.errorbar(sig, [r["relevance_only_R@1"] for r in rows],
                 yerr=[r["relevance_only_ci"] for r in rows], marker="o", label="relevance only")
    ax1.errorbar(sig, [r["best_fusion_R@1"] for r in rows],
                 yerr=[r["best_fusion_ci"] for r in rows], marker="s", label="relevance × recurrence (best w)")
    ax1.errorbar(sig, [r["shipped_recur_R@1"] for r in rows],
                 yerr=[r["shipped_recur_ci"] for r in rows], marker="^", label="shipped (_recur_boost)")
    ax1.plot(sig, [r["recurrence_only_R@1"] for r in rows], "--", color="grey", label="recurrence prior only")
    ax1.set_xlabel("query ambiguity σ (relevance noise)")
    ax1.set_ylabel("recall@1")
    ax1.set_title("Recurrence helps exactly when relevance is ambiguous")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    ws = [w for w, _, _ in sweep_mid]
    ax2.errorbar(ws, [v for _, v, _ in sweep_mid], yerr=[c for _, _, c in sweep_mid], marker="o")
    wbest = max(sweep_mid, key=lambda t: t[1])[0]
    ax2.axvline(wbest, color="green", ls=":", label=f"w* = {wbest}")
    ax2.set_xlabel("fusion weight  w   (0 = relevance, 1 = recurrence)")
    ax2.set_ylabel("recall@1")
    ax2.set_title(f"Optimal relevance↔recurrence blend (σ = {sigma_mid})")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main():
    print("=" * 78)
    print("  RECURRENCE-AS-SALIENCE ABLATION  -  relevance × recurrence as a Bayesian")
    print("  frequency prior on a controlled longitudinal agent-memory workload")
    print("=" * 78)
    print(f"  world: {N_CLUSTERS} clusters × {PER_CLUSTER} near-dup lessons (dim {DIM}), "
          f"Zipf(a={ZIPF_A}) recurrence")
    print(f"  trials: {QUERIES} queries × {SEEDS} seeds per σ; 95% CIs; seeded, CPU, $0")

    res = aggregate()
    rows = summarize(res)
    sigma_mid = SIGMAS[len(SIGMAS) // 2]
    sweep_mid = coeff_sweep(res, sigma_mid)

    print(f"\n- recall@1 vs ambiguity σ  (relevance-only vs best relevance×recurrence) -")
    print(f"  {'σ':>4} {'rel-only':>10} {'best w*':>8} {'fusion':>10} {'lift':>8} "
          f"{'shipped':>9} {'log-prior':>10}")
    for r in rows:
        print(f"  {r['sigma']:>4} {r['relevance_only_R@1']:>10.3f} {r['best_fusion_w']:>8} "
              f"{r['best_fusion_R@1']:>10.3f} {r['fusion_lift']:>+8.3f} "
              f"{r['shipped_recur_R@1']:>9.3f} {r['log_prior_R@1']:>10.3f}")

    # headline numbers
    crisp = rows[0]
    amb = max(rows, key=lambda r: r["fusion_lift"])
    print(f"\n  Q1 - crisp relevance (σ=0): fusion lift {crisp['fusion_lift']:+.3f} "
          f"(recurrence is ~a no-op when relevance is clean).")
    print(f"       ambiguous (σ={amb['sigma']}): fusion lift {amb['fusion_lift']:+.3f} "
          f"recall@1 ({amb['relevance_only_R@1']:.3f} → {amb['best_fusion_R@1']:.3f}); "
          f"best blend w*={amb['best_fusion_w']}.")
    print(f"  Q2 - optimal blend at σ={sigma_mid}: "
          f"w*={max(sweep_mid, key=lambda t: t[1])[0]} "
          f"(w=0 relevance-only={sweep_mid[0][1]:.3f}; "
          f"w=1 recurrence-only={sweep_mid[-1][1]:.3f}).")
    log_avg = np.mean([r["log_prior_R@1"] for r in rows])
    fus_avg = np.mean([r["best_fusion_R@1"] for r in rows])
    print(f"  Q3 - log frequency prior vs linear (n−1): "
          f"log avg R@1={log_avg:.3f} vs linear-best avg={fus_avg:.3f} "
          f"({'log wins' if log_avg > fus_avg else 'linear competitive'}).")

    if SAVE:
        out = {"config": {"dim": DIM, "clusters": N_CLUSTERS, "per_cluster": PER_CLUSTER,
                          "intra_spread": INTRA_SPREAD, "zipf_a": ZIPF_A,
                          "queries": QUERIES, "seeds": SEEDS, "sigmas": SIGMAS,
                          "shipped_recur_boost": m.RETRIEVAL_RECUR_BOOST},
               "rows": rows,
               "coeff_sweep_mid_sigma": {"sigma": sigma_mid,
                                         "w_recall1": [(w, v) for w, v, _ in sweep_mid]}}
        p = Path(__file__).resolve().parent / "recurrence_ablation.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        fig = make_figure(rows, sweep_mid, sigma_mid,
                          str(Path(__file__).resolve().parent / "recurrence_ablation.png"))
        if fig:
            print(f"  figure → {fig}")
    print("=" * 78)


if __name__ == "__main__":
    main()
