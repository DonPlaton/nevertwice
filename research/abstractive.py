#!/usr/bin/env python3
"""RESEARCH - abstractive memory consolidation (roadmap 4A; the other half of memory's value).

WHY THIS EXISTS. The real-trace studies (3A.2/3A.3) showed two things on a live store: relevance
already saturates episode *recall* (recall@3 ≈ 0.71, no recurrence prior beats it), and the
frequency prior is dormant. That raises the load-bearing question for the whole field: beyond a good
retriever over raw logs, what is memory *for*? This module argues - and measures - the answer that
the dormancy result points to: memory's marginal value is **abstraction** (turn many episodic
instances of a lesson into one reusable principle) and **forgetting** (coverage-preserving
compression, validated in 3A.3). Here we build and stress-test the abstraction half.

THE MECHANISM. A lesson recurs across sessions as K *episodic* notes - each the same latent rule
seen through a different, noisy context (3A.2 found 37 such cross-session clusters on a real vault,
slug-invisible). Consolidation replaces the cluster with one *principle* = the (normalised) mean of
its members. Averaging is denoising: the shared rule direction reinforces while instance-specific
context and noise (zero-mean across the cluster) cancel - so the principle recovers the latent rule
that no single episode reveals cleanly. Variance of the off-rule component falls ~1/K.

WHAT IT MEASURES (controlled, seeded; a mechanism benchmark, not an embedder test).
  • novel-context rule recall - a query applying the rule in an UNSEEN context. The abstraction
    claim: the denoised principle matches it better than any single context-bound episode.
  • specific-instance recall - the honest trade-off: consolidation loses instance detail; we
    quantify it (and that rule-level recall, the link target, is preserved).
  • compression - K episodes -> 1 principle.
  • a sweep over context strength beta and cluster size K - where consolidation pays off.
  • real-trace tie-in: how many of the real cross-session clusters are consolidation candidates
    (aggregate COUNT only; no note text read).

    python research/abstractive.py                 # leaderboard
    python research/abstractive.py --save           # + abstractive.json (+ .png if matplotlib)
    NEVERTWICE_VAULT=/path python research/abstractive.py --real   # + real-cluster candidate count

Research dep: numpy (matplotlib optional). Seeded; deterministic.
"""
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("abstractive needs numpy (research dep): pip install numpy", file=sys.stderr)
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAVE = "--save" in sys.argv
REAL = "--real" in sys.argv
D = 128                # latent dim
R = 20                 # rules (one cluster each)
K_DEFAULT = 8          # episodes per rule
SEEDS = 8
BETAS = [0.3, 0.6, 1.0]      # context strength relative to the rule (alpha ≡ 1)
OP_BETA = 0.3                # discriminable operating point for the recall demo
KS = [2, 4, 8, 16]           # cluster sizes for the K-sweep (variance reduction ~1/sqrt K)
NOISE = 0.2
ALPHA = 1.0


def _unit(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n == 0, 1.0, n)


def gen_world(rng, k, beta):
    """R rules; each with k episodic instances = alpha*rule + beta*context_i + noise (then unit).
    The principle is the unit-mean of a rule's instances (denoised rule recovery)."""
    rules = _unit(rng.normal(size=(R, D)))
    episodes = np.zeros((R, k, D))
    for j in range(R):
        ctx = rng.normal(size=(k, D))                  # per-instance context (its own situation)
        noise = NOISE * rng.normal(size=(k, D))
        episodes[j] = _unit(ALPHA * rules[j] + beta * ctx + noise)
    principles = _unit(episodes.mean(axis=1))          # consolidation = unit mean of the cluster
    return {"rules": rules, "episodes": episodes, "principles": principles, "k": k, "beta": beta}


def _novel_query(rng, world, j):
    """The rule applied in a FRESH context unseen in any episode (the generalisation test)."""
    ctx = rng.normal(size=D)
    noise = NOISE * rng.normal(size=D)
    return _unit(ALPHA * world["rules"][j] + world["beta"] * ctx + noise)


def _rule_of_top(qv, mem_flat, owner):
    """Rank flattened memories by cosine to qv; return the rule id of the top-1."""
    sims = mem_flat @ qv
    return owner[int(np.argmax(sims))]


def run(k, beta, seeds=SEEDS):
    """Per seed: (1) rule-recovery - cosine of the principle vs the mean episode to the TRUE rule
    (the direct denoising measure, free of distractor-floor confounds); (2) novel-context rule
    recall@1 over the full store, episodic vs consolidated; (3) specific-instance recall (no-harm)."""
    cos_prin, cos_epi, epi_hits, con_hits, spec_epi, spec_con = [], [], [], [], [], []
    for s in range(seeds):
        rng = np.random.default_rng(7000 + s * 97 + int(beta * 1000) + k)
        w = gen_world(rng, k, beta)
        rules = w["rules"]
        epi_flat = w["episodes"].reshape(R * k, D)
        epi_owner = np.repeat(np.arange(R), k)
        con_flat = w["principles"]
        con_owner = np.arange(R)
        # (1) rule recovery - how cleanly each memory aligns to the latent rule it came from
        cos_prin.append(float(np.mean([con_flat[j] @ rules[j] for j in range(R)])))
        cos_epi.append(float(np.mean([w["episodes"][j, i] @ rules[j]
                                      for j in range(R) for i in range(k)])))
        qrng = np.random.default_rng(13000 + s * 131 + int(beta * 1000) + k)
        eh = ch = se = sc = n = 0
        for j in range(R):
            for _ in range(4):                          # several novel applications per rule
                q = _novel_query(qrng, w, j)
                eh += (_rule_of_top(q, epi_flat, epi_owner) == j)
                ch += (_rule_of_top(q, con_flat, con_owner) == j)
                n += 1
            # specific-instance query: ask about one stored episode (no-harm / trade-off)
            i = int(qrng.integers(k))
            qs = _unit(w["episodes"][j, i] + 0.15 * NOISE * qrng.normal(size=D))
            se += (epi_owner[int(np.argmax(epi_flat @ qs))] == j)   # exact instance recoverable
            sc += (con_owner[int(np.argmax(con_flat @ qs))] == j)   # only the rule survives
        epi_hits.append(eh / n)
        con_hits.append(ch / n)
        spec_epi.append(se / R)
        spec_con.append(sc / R)
    return {"cos_prin": _ci(cos_prin), "cos_epi": _ci(cos_epi),
            "recall_epi": _ci(epi_hits), "recall_con": _ci(con_hits),
            "specific_epi": _ci(spec_epi), "specific_con": _ci(spec_con)}


from _common import _ci          # W12: shared mean±95%CI helper


def _real_candidates():
    """Aggregate-only: how many real cross-session clusters are consolidation candidates (>=3
    members spanning >1 date). Reads cached vectors via real_trace_bench; prints no note text."""
    try:
        import memory_hook as m
        from real_trace_bench import _clusters, _date
    except Exception as e:
        return None
    cache = m.load_embed_cache()
    notes = [(s, r) for s, r in cache.items()
             if isinstance(r, dict) and isinstance(r.get("vec"), list)]
    if len(notes) < 20:
        return None
    from collections import defaultdict
    by = defaultdict(list)
    for s, r in notes:
        by[r.get("project")].append((s, r))
    cand = members = 0
    for ns in by.values():
        for g in _clusters(ns, 0.55):
            if len(g) >= 3 and len({_date(x) for x in g}) > 1:
                cand += 1
                members += len(g)
    return {"notes": len(notes), "candidates": cand, "members": members}


def main():
    bar = "=" * 78
    print(bar)
    print("  ABSTRACTIVE CONSOLIDATION (4A) - does a denoised 'principle' recover the latent rule")
    print("  a recurring lesson teaches, better than its raw context-bound episodes? (mechanism)")
    print(bar)

    # (1) THE MECHANISM - direct rule recovery, free of distractor-floor confounds
    print(f"\n- rule recovery: cosine to the TRUE latent rule (K={K_DEFAULT}, D={D}, {SEEDS} seeds "
          f"±95% CI) -")
    print(f"  {'beta':>6} {'mean episode':>16} {'principle':>16} {'gain':>8}")
    by_beta = {}
    for beta in BETAS:
        r = run(K_DEFAULT, beta)
        by_beta[beta] = r
        ce, cee = r["cos_epi"]; cp, cpe = r["cos_prin"]
        print(f"  {beta:>6.1f} {ce:>10.3f}±{cee:.3f} {cp:>10.3f}±{cpe:.3f} {cp-ce:>+8.3f}")
    print(f"  (the principle is consistently closer to the rule - averaging cancels the "
          f"instance-specific context)")

    # (2) variance reduction ~1/sqrt(K): the rule-recovery gain grows with cluster size
    print(f"\n- rule-recovery gain vs cluster size K (beta=1.0) - variance reduction ~1/√K -")
    print(f"  {'K':>6} {'mean episode':>14} {'principle':>12} {'gain':>8} {'compression':>12}")
    by_k = {}
    for k in KS:
        r = run(k, 1.0)
        by_k[k] = r
        ce = r["cos_epi"][0]; cp = r["cos_prin"][0]
        print(f"  {k:>6} {ce:>14.3f} {cp:>12.3f} {cp-ce:>+8.3f} {f'{k}x':>12}")

    # (3) does cleaner recovery TRANSLATE to retrieval? Only when the rule is recoverable at all.
    op = by_beta[OP_BETA]
    re_, ree = op["recall_epi"]; rc, rce = op["recall_con"]
    print(f"\n- downstream: novel-context rule recall@1 at a discriminable beta={OP_BETA} "
          f"(K={K_DEFAULT}) -")
    print(f"  episodic {re_:.3f}±{ree:.3f}   consolidated {rc:.3f}±{rce:.3f}   gain {rc-re_:+.3f}"
          f"  (+{K_DEFAULT}x compression)")
    hb = by_beta[max(BETAS)]
    print(f"  HONEST BOUNDARY: at beta={max(BETAS)} both collapse toward chance "
          f"(epi {hb['recall_epi'][0]:.3f}, con {hb['recall_con'][0]:.3f}, ~{1/R:.3f}=1/R) - when "
          f"context\n    overwhelms the rule, consolidation amplifies a present signal, it cannot "
          f"manufacture one.")

    # (4) honest trade-off: specific-instance recall is sacrificed for abstraction + compression
    print(f"\n- honest trade-off: specific-instance recall@1 (beta={OP_BETA}, K={K_DEFAULT}) -")
    print(f"  episodic (exact instance)  {op['specific_epi'][0]:.3f}   consolidated (rule only) "
          f"{op['specific_con'][0]:.3f}")
    print(f"  (instance detail is sacrificed; reachable via a principle→episode link if episodes "
          f"are archived, not a fresh search)")

    d_rule = by_beta[1.0]["cos_prin"][0] - by_beta[1.0]["cos_epi"][0]
    print(f"\n  → HEADLINE: consolidation recovers the latent rule a recurring lesson teaches "
          f"(+{d_rule:.3f} cosine\n    at beta=1, growing with K), lifting novel-context recall "
          f"+{rc-re_:.3f} at {K_DEFAULT}x compression - the\n    measurable case that memory's "
          f"marginal value is ABSTRACTION, not episode recall (which relevance\n    already "
          f"saturates, 3A.2). The cost is instance detail, kept reachable by a link.")

    real = None
    if REAL:
        real = _real_candidates()
        if real:
            print(f"\n- real-trace tie-in (aggregate only) -")
            print(f"  {real['candidates']} cross-session clusters on the live store are consolidation "
                  f"candidates\n    (≥3 members, >1 date), covering {real['members']} episodic notes - "
                  f"real abstraction opportunities.")
        else:
            print("\n  (--real: no populated vault cache found; set NEVERTWICE_VAULT)")

    if SAVE:
        out = {"D": D, "R": R, "seeds": SEEDS, "noise": NOISE, "op_beta": OP_BETA,
               "by_beta": {f"{b}": r for b, r in by_beta.items()},
               "by_k": {f"{k}": r for k, r in by_k.items()},
               "real_candidates": real}
        p = HERE / "abstractive.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        try:
            _figure(by_beta, by_k, HERE / "abstractive.png")
            print(f"  figure → {HERE / 'abstractive.png'}")
        except Exception:
            pass
    print(bar)


def _figure(by_beta, by_k, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    betas = sorted(by_beta)
    a1.plot(betas, [by_beta[b]["cos_epi"][0] for b in betas], "o-", label="mean episode")
    a1.plot(betas, [by_beta[b]["cos_prin"][0] for b in betas], "s-", label="principle")
    a1.set_xlabel("context strength β"); a1.set_ylabel("cosine to true rule")
    a1.set_title("Rule recovery (denoising)"); a1.legend(); a1.grid(alpha=0.3)
    ks = sorted(by_k)
    a2.plot(ks, [by_k[k]["cos_prin"][0] - by_k[k]["cos_epi"][0] for k in ks], "d-", color="green")
    a2.set_xlabel("cluster size K"); a2.set_ylabel("recovery gain (principle − episode)")
    a2.set_title("Gain grows with K (variance reduction ~1/√K)"); a2.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


if __name__ == "__main__":
    main()
