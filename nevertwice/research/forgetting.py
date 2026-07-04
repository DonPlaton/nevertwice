#!/usr/bin/env python3
"""RESEARCH — principled forgetting under a budget (roadmap 1C).

THESIS. When a memory store must be capped, WHICH notes to keep is a coreset problem.
Pruning by salience alone (keep the highest-recurrence) over-concentrates on the busy
topics and abandons the long tail — so queries about rarely-revisited topics miss
entirely. Choosing the kept set to maximize utility-weighted COVERAGE

    F(S) = Σ_m u(m) · max_{s∈S} sim(m, s)            (facility location, submodular)

keeps a diverse, high-utility coreset (greedy is within 1−1/e of optimal). This is the
SAME selector the production cap uses (consolidate_memory.select_coreset) — token-Jaccard
similarity, lazy-greedy, pure stdlib — so this benchmark tests the shipped code.

EXPERIMENT. Build the store of live lessons at the end of a 3A longitudinal run, prune
it to a budget B by {coreset, salience-sort (the old cap), recency, random}, then measure
recall of the stream's queries against the pruned store, swept over B. Recall here is
TOPIC-coverage @k (a same-topic sibling answers the gotcha — the "a useful memory was
surfaced" semantics), the regime where forgetting the tail hurts.

CLAIM. "Memory consolidation as submodular coreset selection: budget-optimal forgetting
preserves long-tail recall a salience sort discards." Honest scope: on frequency-weighted
EXACT recall a salience sort is competitive (it keeps the head); the coreset's win is
coverage of the tail — reported alongside, not hidden.

    python research/forgetting.py            # report
    python research/forgetting.py --save     # + forgetting.json (+ .png if mpl)
    python research/forgetting.py --quick    # smoke (via 3A --quick)

Research dep: numpy (matplotlib optional). Seeded; deterministic.
"""
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("forgetting needs numpy (research dep): pip install numpy", file=sys.stderr)
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import consolidate_memory as cons
import research.longitudinal_bench as lb

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAVE = "--save" in sys.argv
BUDGETS = [0.2, 0.3, 0.4, 0.6, 0.8]      # fraction of the store kept
KQ = 3                                    # topic-coverage recall@KQ
N_QUERIES = 600                           # held-out queries per seed (sampled ∝ recurrence)


def build_store(seed):
    """The live lessons at the end of a 3A run: vec, token bag, recurrence, age, topic."""
    world, events = lb._world_events(seed)
    last = {}
    for ev in events:                                 # keep each lesson's latest state
        last[ev["lid"]] = ev
    store = []
    for lid, ev in last.items():
        rec = ev["note"][lid]
        store.append({"lid": int(lid), "vec": world["vec"][lid], "tok": world["tok"][lid],
                      "recurrence": int(rec["recurrence"]), "age": float(rec["age"]),
                      "topic": int(world["topic"][lid])})
    return world, store


def prune(store, budget, method, rng):
    """Return the kept lesson-ids under one pruning method."""
    ids = [s["lid"] for s in store]
    rec = {s["lid"]: s for s in store}
    if method == "coreset":                           # the shipped submodular selector
        return cons.select_coreset(ids, budget,
                                   lambda i: rec[i]["recurrence"],
                                   lambda i: rec[i]["tok"])
    if method == "salience":                          # the old cap: keep highest recurrence
        return set(sorted(ids, key=lambda i: rec[i]["recurrence"], reverse=True)[:budget])
    if method == "recency":                           # keep newest
        return set(sorted(ids, key=lambda i: rec[i]["age"])[:budget])
    return set(rng.choice(ids, size=budget, replace=False).tolist())   # random


SIGMA_Q = 0.3                             # crisp queries → recall reflects PRUNING, not query noise


def topic_recall(world, store, kept, queries, qrng):
    """Mean topic-coverage recall@KQ: for each query, is a SAME-TOPIC memory among the
    top-KQ of the kept set by cosine? (a sibling answers the gotcha). Crisp queries
    (SIGMA_Q) so the score reflects what pruning forgot, not query ambiguity."""
    kept_ids = [s["lid"] for s in store if s["lid"] in kept]
    if not kept_ids:
        return 0.0
    V = np.array([world["vec"][i] for i in kept_ids])
    kept_topic = np.array([world["topic"][i] for i in kept_ids])
    hits = 0
    for lid in queries:
        qv, _ = lb.make_query(qrng, world, lid, SIGMA_Q)
        top = np.argsort(-(V @ qv))[:KQ]
        if world["topic"][lid] in kept_topic[top]:
            hits += 1
    return hits / len(queries)


def topics_covered(world, kept):
    """Fraction of all topics with ≥1 kept lesson — the coverage the coreset protects."""
    return len({int(world["topic"][i]) for i in kept}) / lb.N_TOPICS


def redundancy(world, store, kept):
    """Mean pairwise cosine within the kept set (lower = more diverse)."""
    V = np.array([world["vec"][i] for i in kept])
    if len(V) < 2:
        return 0.0
    G = V @ V.T
    iu = np.triu_indices(len(V), k=1)
    return float(G[iu].mean())


METHODS = ("coreset", "salience", "recency", "random")
METRICS = ("head", "uniform", "cov", "red")


def run():
    agg = {meth: {b: {k: [] for k in METRICS} for b in BUDGETS} for meth in METHODS}
    full = {"head": [], "uniform": []}
    for seed in range(lb.SEEDS):
        world, store = build_store(seed)
        n = len(store)
        ids = [s["lid"] for s in store]
        rec_w = np.array([s["recurrence"] for s in store], dtype=float)
        qrng = np.random.default_rng(31337 + seed)
        # HEAD queries: sampled ∝ recurrence (the busy topics — salience's home turf).
        head_q = qrng.choice(ids, size=N_QUERIES, p=rec_w / rec_w.sum()).tolist()
        # UNIFORM queries: a topic chosen uniformly, then a lesson in it — future needs
        # may concern ANY topic, not just the historically busy ones (coverage's turf).
        by_topic: dict = {}
        for s in store:
            by_topic.setdefault(s["topic"], []).append(s["lid"])
        topics = list(by_topic)
        uni_q = [int(qrng.choice(by_topic[topics[int(qrng.integers(len(topics)))]]))
                 for _ in range(N_QUERIES)]
        allkept = {s["lid"] for s in store}
        full["head"].append(topic_recall(world, store, allkept, head_q, np.random.default_rng(777 + seed)))
        full["uniform"].append(topic_recall(world, store, allkept, uni_q, np.random.default_rng(888 + seed)))
        for b in BUDGETS:
            budget = max(1, int(round(b * n)))
            for meth in METHODS:
                kept = prune(store, budget, meth, np.random.default_rng(555 + seed))
                agg[meth][b]["head"].append(topic_recall(world, store, kept, head_q,
                                                         np.random.default_rng(777 + seed)))
                agg[meth][b]["uniform"].append(topic_recall(world, store, kept, uni_q,
                                                            np.random.default_rng(888 + seed)))
                agg[meth][b]["cov"].append(topics_covered(world, kept))
                agg[meth][b]["red"].append(redundancy(world, store, kept))
    return agg, {k: float(np.mean(v)) for k, v in full.items()}


from research._common import _ci          # W12: shared mean±95%CI helper


def _col(agg, metric, b):
    return "".join(f"{_ci(agg[meth][b][metric])[0]:>11.3f}" for meth in METHODS)


def main():
    bar = "=" * 78
    print(bar)
    print("  PRINCIPLED FORGETTING UNDER A BUDGET (1C) — submodular coreset vs salience prune")
    print(bar)
    agg, full = run()
    print(f"  store = live lessons after a 3A run, {lb.SEEDS} seeds; keep-all recall@{KQ}: "
          f"head {full['head']:.3f}, uniform {full['uniform']:.3f}")
    hdr = f"  {'budget':>8}" + "".join(f"{m_:>11}" for m_ in METHODS)

    print(f"\n— UNIFORM-over-topics recall@{KQ} (future may concern any topic — coverage matters) —")
    print(hdr)
    for b in BUDGETS:
        print(f"  {b:>8.0%}{_col(agg, 'uniform', b)}")

    print(f"\n— HEAD recall@{KQ} (queries ∝ recurrence — the busy topics, salience's turf) —")
    print(hdr)
    for b in BUDGETS:
        print(f"  {b:>8.0%}{_col(agg, 'head', b)}")

    print(f"\n— topics covered (fraction of {lb.N_TOPICS} topics with ≥1 kept lesson) —")
    print(hdr)
    for b in BUDGETS:
        print(f"  {b:>8.0%}{_col(agg, 'cov', b)}")

    tight = BUDGETS[0]
    ct, st = _ci(agg["coreset"][tight]["uniform"])[0], _ci(agg["salience"][tight]["uniform"])[0]
    chd, shd = _ci(agg["coreset"][tight]["head"])[0], _ci(agg["salience"][tight]["head"])[0]
    cc, sc = _ci(agg["coreset"][tight]["cov"])[0], _ci(agg["salience"][tight]["cov"])[0]
    cr, sr = _ci(agg["coreset"][tight]["red"])[0], _ci(agg["salience"][tight]["red"])[0]
    print(f"\n  → at a tight {tight:.0%} budget, coreset vs salience-sort:")
    print(f"    UNIFORM recall {ct:.3f} vs {st:.3f} ({ct - st:+.3f}) — coverage protects rare topics.")
    print(f"    HEAD recall    {chd:.3f} vs {shd:.3f} ({chd - shd:+.3f}) — the busy head is barely traded away.")
    print(f"    topics covered {cc:.3f} vs {sc:.3f}; redundancy {cr:.3f} vs {sr:.3f} (lower = diverse).")

    if SAVE:
        out = {"seeds": lb.SEEDS, "kq": KQ, "sigma_q": SIGMA_Q, "keep_all": full, "budgets": BUDGETS,
               "metrics": {meth: {str(b): {k: _ci(agg[meth][b][k])[0] for k in METRICS}
                                  for b in BUDGETS} for meth in METHODS}}
        p = HERE / "forgetting.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        _figure(agg, full, HERE / "forgetting.png")
    print(bar)


def _figure(agg, full, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [figure skipped: matplotlib unavailable — {e}]")
        return
    fig, ax = plt.subplots(figsize=(7, 4.6))
    for meth, mark in (("coreset", "o"), ("salience", "s"), ("recency", "^"), ("random", "x")):
        ax.errorbar(BUDGETS, [_ci(agg[meth][b]["uniform"])[0] for b in BUDGETS],
                    yerr=[_ci(agg[meth][b]["uniform"])[1] for b in BUDGETS], marker=mark, label=meth)
    ax.axhline(full["uniform"], ls="--", color="grey", label="keep-all")
    ax.set_xlabel("budget (fraction of store kept)")
    ax.set_ylabel(f"UNIFORM-over-topics recall@{KQ}")
    ax.set_title("Submodular coreset preserves long-tail recall under a budget")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"  figure → {path}")


if __name__ == "__main__":
    main()
