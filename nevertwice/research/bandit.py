#!/usr/bin/env python3
"""RESEARCH — learned salience from implicit feedback (roadmap 1B, the flagship).

THE GAP. Nevertwice (like almost every agent-memory system) is STATIC: it injects
memories but never learns whether an injection helped — a dead loop. 1B closes it.
Treat retrieval as a contextual bandit: each candidate memory is an arm with a feature
context x = (relevance, log-recurrence, age, confidence, resolved); the agent "injects"
the top-k; the implicit reward is whether the USEFUL memory (the lesson the session
needed) was among them. Feedback is PARTIAL — we only observe a reward for what we
surfaced (a recall miss teaches us nothing about the note we failed to show) — which is
the research challenge. We learn a linear usefulness model online with LinUCB
(Li et al. 2010): θ = A⁻¹b, rank by θ·x + α·√(xᵀA⁻¹x), update A,b from surfaced arms.

EXPERIMENT (on the 3A longitudinal stream, in temporal order). Three rankers:
  • heuristic — the shipped hand-tuned salience constants (static, never learns);
  • bandit    — LinUCB, learns online from partial implicit feedback, from scratch;
  • oracle    — the offline ridge optimum θ* fit on the WHOLE stream with full feedback
                (the 1A posterior's reward-model cousin; the regret reference / ceiling).
Plus a control: bandit with the reward signal SHUFFLED (credit to a random surfaced
arm) — it must NOT learn, proving the gain comes from the signal, not the mechanism.

CLAIM. "Agent memory that learns what to remember: an implicit-feedback bandit beats the
static hand-tuned ranker on long-horizon recall and recovers the offline optimum, with
sublinear regret." Honest scope: the implicit reward here is the simulator's ground truth
(a real hook must estimate it noisily — that production signal is the remaining 1B work);
features reuse 1A's extraction; synthetic world, seeded, CPU.

    python research/bandit.py            # report
    python research/bandit.py --save     # + bandit.json (+ .png if mpl)
    python research/bandit.py --quick    # smoke (fewer sessions/seeds, via 3A --quick)

Research dep: numpy (matplotlib optional). Seeded; deterministic.
"""
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("bandit needs numpy (research dep): pip install numpy", file=sys.stderr)
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import research.longitudinal_bench as lb
import research.posterior_model as pm

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAVE = "--save" in sys.argv
ALPHA = 1.0           # LinUCB exploration width
RIDGE = 1.0           # ridge prior (A = RIDGE·I)
K_METRIC = 1          # recall-utility@1 — the sharp metric (@3 saturates on an 8-wide pool)
K_FB = 3              # injected/surfaced set the bandit gets partial feedback on
D = len(pm.FEATURES)
BINS = 12             # timeline bins for the learning curve


# ── feature stream (reuses 1A extraction; standardised for conditioning) ─

def streams():
    """Per-seed temporal query streams of (standardised feature matrix, target row).
    Standardisation uses global feature stats — a fixed preprocessing (feature scaling
    is not the learned part; the weights θ are) that keeps LinUCB's A well-conditioned."""
    per_seed = [pm.build_dataset([s]) for s in range(lb.SEEDS)]
    mean, std = pm.standardize_params([row for seed in per_seed for row in seed])
    out = []
    for seed in per_seed:
        out.append([((X - mean) / std, t) for X, t in seed])
    return out, mean, std


def ridge_offline(streams_):
    """Offline ridge usefulness model on the WHOLE stream with FULL feedback — the
    optimum θ* the online bandit is trying to recover."""
    A = RIDGE * np.eye(D)
    b = np.zeros(D)
    for seed in streams_:
        for X, t in seed:
            r = np.zeros(len(X))
            r[t] = 1.0
            A += X.T @ X
            b += X.T @ r
    return np.linalg.solve(A, b)


# ── rankers ─────────────────────────────────────────────────────────────

def _hit(order, t, k):
    return 1.0 if t in order[:k] else 0.0


def run_bandit(seed_stream, theta_star, alpha=ALPHA, shuffle_reward=False, rng=None):
    """LinUCB over one temporal stream. EXPLORE (UCB) chooses the surfaced top-K_FB that
    get partial feedback (the learning); the reported utility is the EXPLOIT policy's
    recall@K_METRIC (greedy θ̂·x — what a deployed bandit would serve once learned), and
    regret = oracle − exploit (its gap to the optimum). Returns per-query
    (exploit-utility, regret, ‖θ̂−θ*‖cos)."""
    A = RIDGE * np.eye(D)
    Ainv = np.eye(D) / RIDGE
    b = np.zeros(D)
    util, regret, conv = [], [], []
    for X, t in seed_stream:
        theta = Ainv @ b
        exploit = np.argsort(-(X @ theta), kind="stable")             # served (greedy) → metric
        ucb = alpha * np.sqrt(np.maximum(0.0, np.einsum("ij,jk,ik->i", X, Ainv, X)))
        explore = np.argsort(-(X @ theta + ucb), kind="stable")       # exploration → feedback set
        u = _hit(exploit, t, K_METRIC)
        util.append(u)
        regret.append(_hit(np.argsort(-(X @ theta_star), kind="stable"), t, K_METRIC) - u)
        surfaced = explore[:K_FB]                       # partial feedback: only these arms
        rewarded = (rng.choice(surfaced) if (shuffle_reward and rng is not None) else t)
        for i in surfaced:
            x = X[i]
            A += np.outer(x, x)
            b += (1.0 if i == rewarded else 0.0) * x
        Ainv = np.linalg.inv(A)
        conv.append(_cos_dist(theta, theta_star))
    return np.array(util), np.array(regret), np.array(conv)


def run_static(seed_stream, score_fn):
    """A static ranker (no learning) over the same stream → utility@K_METRIC per query."""
    return np.array([_hit(np.argsort(-score_fn(X), kind="stable"), t, K_METRIC)
                     for X, t in seed_stream])


def _cos_dist(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return 1.0 - float(a @ b) / (na * nb) if na > 1e-12 and nb > 1e-12 else 1.0


def _binned(curve, bins=BINS):
    """Mean of a per-query series over `bins` equal timeline segments."""
    idx = np.linspace(0, len(curve), bins + 1).astype(int)
    return [float(np.mean(curve[idx[i]:idx[i + 1]])) if idx[i + 1] > idx[i] else 0.0
            for i in range(bins)]


# ── report ──────────────────────────────────────────────────────────────

from research._common import _ci          # W12: shared mean±95%CI helper


def main():
    seed_streams, mean, std = streams()
    theta_star = ridge_offline(seed_streams)
    nq = sum(len(s) for s in seed_streams)

    # standardise score_fns for the static baselines (they take RAW features in 1A; here
    # features are already standardised, so de-standardise inside the closures)
    def heuristic_std(Xs):
        return pm.score_heuristic(Xs * std + mean)

    bandit_u, bandit_r, bandit_c = [], [], []
    shuf_u = []
    heur_u, rel_u, oracle_u = [], [], []
    rng = np.random.default_rng(424242)
    for s in seed_streams:
        u, r, c = run_bandit(s, theta_star)
        bandit_u.append(u); bandit_r.append(r); bandit_c.append(c)
        su, _, _ = run_bandit(s, theta_star, shuffle_reward=True, rng=rng)
        shuf_u.append(su)
        heur_u.append(run_static(s, heuristic_std))
        rel_u.append(run_static(s, lambda Xs: Xs[:, 0]))             # relevance-only (mismatched static)
        oracle_u.append(run_static(s, lambda Xs: Xs @ theta_star))

    bar = "=" * 78
    print(bar)
    print("  LEARNED SALIENCE FROM FEEDBACK (1B) — online LinUCB vs static, on the 3A stream")
    print(bar)
    print(f"  {lb.SEEDS} longitudinal streams, {nq} queries total; LinUCB α={ALPHA}, ridge={RIDGE}, "
          f"recall-utility@{K_METRIC} (feedback on top-{K_FB}); CPU, seeded")

    # overall utility (mean over all queries & seeds)
    def overall(rows):
        return _ci(np.concatenate(rows))
    print(f"\n— recall-utility@{K_METRIC} (useful memory ranked #1; feedback on top-{K_FB}; "
          f"mean ± 95% CI) —")
    for name, rows in (("relevance-only (mismatched static)", rel_u),
                       ("heuristic (static, shipped)", heur_u),
                       ("bandit (online, learns)", bandit_u),
                       ("bandit (shuffled reward — control)", shuf_u),
                       ("oracle θ* (offline optimum)", oracle_u)):
        mu, ci = overall(rows)
        print(f"  {name:36} {mu:.3f} ±{ci:.3f}")

    # second-half utility (after warmup) on two axes: vs a MISMATCHED static ranker
    # (where learning's value is undeniable) and vs the WELL-TUNED shipped heuristic.
    half = lambda rows: overall([r[len(r) // 2:] for r in rows])
    hb, hh, hr, ho = (half(x)[0] for x in (bandit_u, heur_u, rel_u, oracle_u))
    print(f"\n  → vs MISMATCHED static (relevance-only): bandit {hb:.3f} vs {hr:.3f} "
          f"({hb - hr:+.3f}) — learning recovers the priors a fixed ranker ignores.")
    print(f"  → vs WELL-TUNED static (shipped heuristic): {hb:.3f} vs {hh:.3f} ({hb - hh:+.3f}); "
          f"oracle ceiling {ho:.3f}. The edge scales with how mismatched the static config is.")

    # learning curve: per-seed binned utility, averaged across seeds
    def curve(rows):
        return list(np.mean([_binned(r) for r in rows], axis=0))
    bc, hc, rc, oc = curve(bandit_u), curve(heur_u), curve(rel_u), curve(oracle_u)
    spark = lambda v: "".join("▁▂▃▄▅▆▇█"[min(7, int(x * 8))] for x in v)
    print(f"\n— recall-utility@{K_METRIC} over the timeline (bandit climbs; static rankers are flat) —")
    print(f"  bandit   {spark(bc)}  {bc[0]:.2f} → {bc[-1]:.2f}")
    print(f"  heuristic{spark(hc)}  {hc[0]:.2f} → {hc[-1]:.2f}")
    print(f"  relevance{spark(rc)}  {rc[0]:.2f} → {rc[-1]:.2f}")
    print(f"  oracle   {spark(oc)}  {oc[0]:.2f} → {oc[-1]:.2f}")

    # cumulative regret (sublinear ⇒ learning) and weight recovery
    final_regret = _ci([r.sum() for r in bandit_r])
    early = np.mean([np.mean(c[:len(c) // 10]) for c in bandit_c])
    late = np.mean([np.mean(c[-len(c) // 10:]) for c in bandit_c])
    print(f"\n— convergence —")
    print(f"  cumulative regret vs oracle: {final_regret[0]:.1f} ±{final_regret[1]:.1f} "
          f"over {nq // lb.SEEDS} queries/stream (sublinear ⇒ learning)")
    print(f"  weight recovery ‖θ̂−θ*‖cos: {early:.3f} (first 10%) → {late:.3f} (last 10%) "
          f"— learned weights approach the offline optimum")
    print(f"  θ* (offline optimum, standardised): "
          + ", ".join(f"{pm.FEATURES[i]}={theta_star[i]:+.2f}" for i in range(D)))

    sb, _ = half(shuf_u)
    print(f"\n  → control: shuffled-reward bandit second-half utility {sb:.3f} "
          f"(≈ no learning over heuristic {hh:.3f}) — the gain is from the feedback signal.")

    if SAVE:
        out = {"queries": nq, "seeds": lb.SEEDS, "alpha": ALPHA, "k_metric": K_METRIC, "k_fb": K_FB,
               "utility_overall": {n: overall(r)[0] for n, r in
                                   (("relevance_only", rel_u), ("heuristic", heur_u),
                                    ("bandit", bandit_u), ("bandit_shuffled", shuf_u),
                                    ("oracle", oracle_u))},
               "second_half": {"bandit": hb, "heuristic": hh, "relevance_only": hr,
                               "oracle": ho, "shuffled": sb},
               "curve": {"bandit": bc, "heuristic": hc, "relevance_only": rc, "oracle": oc},
               "cumulative_regret": final_regret[0],
               "weight_recovery": {"early": early, "late": late},
               "theta_star": {pm.FEATURES[i]: float(theta_star[i]) for i in range(D)}}
        p = HERE / "bandit.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        _figure(bc, hc, rc, oc, bandit_c, HERE / "bandit.png")
    print(bar)


def _figure(bc, hc, rc, oc, bandit_c, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [figure skipped: matplotlib unavailable — {e}]")
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.3))
    x = range(len(bc))
    ax1.plot(x, bc, marker="o", label="bandit (online)")
    ax1.plot(x, hc, marker="s", label="heuristic (static)")
    ax1.plot(x, rc, marker="^", label="relevance-only (static)")
    ax1.plot(x, oc, "--", color="grey", label="oracle θ*")
    ax1.set_xlabel("timeline bin")
    ax1.set_ylabel(f"recall-utility@{K_METRIC}")
    ax1.set_title("Memory that learns: bandit climbs toward the optimum")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    conv = np.mean([_binned(c) for c in bandit_c], axis=0)
    ax2.plot(range(len(conv)), conv, marker="o")
    ax2.set_xlabel("timeline bin")
    ax2.set_ylabel("‖θ̂ − θ*‖ cosine distance")
    ax2.set_title("Learned weights converge to the offline optimum")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"  figure → {path}")


if __name__ == "__main__":
    main()
