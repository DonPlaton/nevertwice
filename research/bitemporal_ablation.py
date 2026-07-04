#!/usr/bin/env python3
"""RESEARCH — bi-temporal point-in-time recall ablation (companion to the
recurrence study). Quantifies what a bi-temporal memory buys over the two flat
baselines every other agent-memory store uses.

A fact (a config value, a decision, an API contract) gets REVISED over a project's
life. Three recall policies when asked about it:

  - bi-temporal : return the version whose [valid_from, valid_to) contains the
                  query date  (what Nevertwice stores via supersession + valid_*).
  - use-newest  : always return the latest version  (Mem0/Zep/most stores).
  - use-all     : return every version  (a naive similarity search) → the agent is
                  handed CONTRADICTORY facts and must guess.

The question is not "is correct indexing correct" (it is, by construction) but the
MAGNITUDE: how wrong is use-newest as a function of (a) how often the fact was
revised and (b) how far in the past the query lands — and how much contradiction
does use-all dump on the agent. Fully seeded; CPU; seconds.

    python research/bitemporal_ablation.py [--save] [--quick]

Research dep: numpy (matplotlib optional). Deterministic.
"""
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("bitemporal_ablation needs numpy (research dep): pip install numpy", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QUICK = "--quick" in sys.argv
SAVE = "--save" in sys.argv

HORIZON = 365                                  # project lifetime (days)
REVISIONS = [1, 2, 3, 5, 8, 12]                # number of versions a fact goes through
FACTS = 200 if QUICK else 1500
QUERIES_PER_FACT = 6 if QUICK else 20
SEEDS = 3 if QUICK else 6
AGE_BUCKETS = 6                                # for the accuracy-vs-query-age curve


def make_fact(rng, n_versions):
    """A fact's life: sorted revision dates → [valid_from, valid_to) windows."""
    if n_versions == 1:
        cuts = []
    else:
        cuts = sorted(rng.choice(range(1, HORIZON), size=n_versions - 1, replace=False).tolist())
    bounds = [0] + cuts + [HORIZON]
    return [(bounds[i], bounds[i + 1]) for i in range(n_versions)]   # version i valid in [from,to)


def version_at(windows, date):
    for i, (lo, hi) in enumerate(windows):
        if lo <= date < hi:
            return i
    return len(windows) - 1


def run():
    # accuracy per policy per revision-count, plus an accuracy-vs-age curve, plus
    # use-all ambiguity (avg versions returned)
    acc = {r: {"bitemporal": [], "newest": []} for r in REVISIONS}
    ambiguity = {r: [] for r in REVISIONS}
    age_curve = {b: {"bitemporal": [], "newest": []} for b in range(AGE_BUCKETS)}
    for seed in range(SEEDS):
        rng = np.random.default_rng(2000 + seed)
        for r in REVISIONS:
            for _ in range(FACTS // len(REVISIONS)):
                w = make_fact(rng, r)
                latest = len(w) - 1
                for _q in range(QUERIES_PER_FACT):
                    qd = int(rng.integers(0, HORIZON))
                    truth = version_at(w, qd)
                    bt = 1.0          # bi-temporal uses the same [from,to) lookup → exact by construction
                    nw = 1.0 if latest == truth else 0.0          # use-newest: right only if query ∈ latest window
                    acc[r]["bitemporal"].append(bt)
                    acc[r]["newest"].append(nw)
                    ambiguity[r].append(len(w))                   # use-all hands back all
                    # query age = how far in the past, bucketed 0(recent)..AGE_BUCKETS-1(old)
                    age = (HORIZON - 1 - qd) / HORIZON
                    b = min(AGE_BUCKETS - 1, int(age * AGE_BUCKETS))
                    age_curve[b]["bitemporal"].append(bt)
                    age_curve[b]["newest"].append(nw)
    return acc, ambiguity, age_curve


def _m(xs):
    return float(np.mean(xs)) if xs else 0.0


def summarize(acc, ambiguity, age_curve):
    by_rev = []
    for r in REVISIONS:
        by_rev.append({
            "revisions": r,
            "bitemporal_acc": _m(acc[r]["bitemporal"]),
            "newest_acc": _m(acc[r]["newest"]),
            "advantage": _m(acc[r]["bitemporal"]) - _m(acc[r]["newest"]),
            "useall_avg_versions": _m(ambiguity[r]),
        })
    by_age = [{"age_bucket": b,
               "bitemporal_acc": _m(age_curve[b]["bitemporal"]),
               "newest_acc": _m(age_curve[b]["newest"])}
              for b in range(AGE_BUCKETS)]
    return by_rev, by_age


def make_figure(by_rev, by_age, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [figure skipped: {e}]")
        return None
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    rev = [r["revisions"] for r in by_rev]
    ax1.plot(rev, [r["bitemporal_acc"] for r in by_rev], "o-", label="bi-temporal")
    ax1.plot(rev, [r["newest_acc"] for r in by_rev], "s-", label="use-newest")
    ax1.plot(rev, [1.0 / r for r in rev], ":", color="grey", label="1 / revisions (theory)")
    ax1.set_xlabel("revisions per fact")
    ax1.set_ylabel("point-in-time accuracy")
    ax1.set_title("use-newest decays as ~1/revisions; bi-temporal stays exact")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    xs = [f"{int(100*b/AGE_BUCKETS)}–{int(100*(b+1)/AGE_BUCKETS)}%" for b in range(AGE_BUCKETS)]
    ax2.plot(xs, [a["bitemporal_acc"] for a in by_age], "o-", label="bi-temporal")
    ax2.plot(xs, [a["newest_acc"] for a in by_age], "s-", label="use-newest")
    ax2.set_xlabel("query age (how far in the past, % of project life)")
    ax2.set_ylabel("point-in-time accuracy")
    ax2.set_title("use-newest is right only for recent queries")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    ax2.tick_params(axis="x", labelsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main():
    print("=" * 78)
    print("  BI-TEMPORAL POINT-IN-TIME RECALL ABLATION  —  bi-temporal vs use-newest")
    print("  vs use-all, over revised facts on a 365-day project")
    print("=" * 78)
    print(f"  {FACTS} facts × {QUERIES_PER_FACT} point-in-time queries × {SEEDS} seeds; seeded, CPU")
    acc, ambiguity, age_curve = run()
    by_rev, by_age = summarize(acc, ambiguity, age_curve)

    print(f"\n— point-in-time accuracy by revision count —")
    print(f"  {'revisions':>9} {'bi-temporal':>12} {'use-newest':>11} {'advantage':>10} "
          f"{'use-all #ver':>12}")
    for r in by_rev:
        print(f"  {r['revisions']:>9} {r['bitemporal_acc']:>12.3f} {r['newest_acc']:>11.3f} "
              f"{r['advantage']:>+10.3f} {r['useall_avg_versions']:>12.2f}")

    print(f"\n— accuracy vs query age (recent → old) —")
    print(f"  {'age band':>10} {'bi-temporal':>12} {'use-newest':>11}")
    for a, lab in zip(by_age, [f"{int(100*b/AGE_BUCKETS)}-{int(100*(b+1)/AGE_BUCKETS)}%"
                               for b in range(AGE_BUCKETS)]):
        print(f"  {lab:>10} {a['bitemporal_acc']:>12.3f} {a['newest_acc']:>11.3f}")

    heavy = by_rev[-1]
    print(f"\n  → at {heavy['revisions']} revisions: use-newest point-in-time accuracy "
          f"{heavy['newest_acc']:.3f} (it returns the wrong version "
          f"{1-heavy['newest_acc']:.0%} of the time); bi-temporal stays exact. "
          f"use-all dumps {heavy['useall_avg_versions']:.1f} contradictory versions/query.")
    print(f"  → oldest-query band: use-newest {by_age[-1]['newest_acc']:.3f} vs "
          f"bi-temporal {by_age[-1]['bitemporal_acc']:.3f} — 'just use the latest' is "
          f"near-useless for historical questions.")

    if SAVE:
        out = {"config": {"horizon_days": HORIZON, "revisions": REVISIONS, "facts": FACTS,
                          "queries_per_fact": QUERIES_PER_FACT, "seeds": SEEDS},
               "by_revision": by_rev, "by_query_age": by_age}
        p = Path(__file__).resolve().parent / "bitemporal_ablation.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        fig = make_figure(by_rev, by_age,
                          str(Path(__file__).resolve().parent / "bitemporal_ablation.png"))
        if fig:
            print(f"  figure → {fig}")
    print("=" * 78)


if __name__ == "__main__":
    main()
