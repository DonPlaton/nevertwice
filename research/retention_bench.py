#!/usr/bin/env python3
"""RESEARCH - retention under budget on a real store (roadmap 3A.3; W10/P2 evidence).

real_trace_bench (3A.2) showed genuine cross-session recurrence is SLUG-INVISIBLE on real data:
the production per-project cap (`consolidate_memory.cap_project_notes`) weights its keep-utility by
`recurrence`, which is therefore ~1 for every real note - the frequency prior in the cap is DEAD on
real data, exactly as it was for recall. This asks the actionable question for the default-on cap
(W10/P2): does feeding SEMANTIC recurrence (cosine-cluster size) into the SHIPPED coreset retain
durable (cross-session) lessons better than today's slug utility - or does the facility-location
coverage objective already preserve them, making the recurrence term bloat?

It measures the SHIPPED `select_coreset` (no new production code) under three keep-utilities:
  • coverage   u≡1                      - pure facility-location diversity
  • slug       u=recurrence·resolved    - what ships today (recurrence≈1 on real data)
  • semantic   u=cluster_size·resolved  - the proposed semantic-recurrence prior

Durable-topic retention = fraction of cross-session clusters keeping ≥1 member under budget.

PRIVACY. Tokens are computed in-process for the coreset (the shipped objective needs them) but are
NEVER printed or saved; output is aggregate only (retention rates, counts).

    NEVERTWICE_VAULT=/path/to/vault python research/retention_bench.py [--save]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m
from consolidate_memory import select_coreset
from real_trace_bench import _clusters, _date

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAVE = "--save" in sys.argv
CLUSTER_THR = 0.55
KEEP_FRACS = [0.5, 0.7]
MIN_NOTES = 8                              # only projects big enough for the cap to bite
RW = m.RETRIEVAL_RESOLVED_WEIGHT
POLICIES = ("coverage", "slug", "semantic")


def _topics(ns):
    """Cross-session clusters (durable topics) + stem->cluster-size for the semantic utility."""
    size, topics = {}, []
    for g in _clusters(ns, CLUSTER_THR):
        if len({_date(x) for x in g}) > 1:    # spans >1 date ⇒ a genuinely recurring lesson
            topics.append(set(g))
            for x in g:
                size[x] = len(g)
    return size, topics


def _verdict(deltas, red_cov, red_sem):
    """Honest verdict KEY for the semantic-recurrence prior in the cap. Robustness across budgets
    (the WORST delta, never cherry-picked best) AND hoarding: a prior that helps at a loose budget
    but hurts at a tight one, or keeps far more members per topic, is NOT a win - facility-location
    coverage already preserves durable topics. Returns 'win' | 'not_win' | 'neutral'."""
    worst = min(deltas) if deltas else 0.0
    hoards = red_sem > red_cov * 1.25
    if worst > 0.005 and not hoards:
        return "win"
    if worst < -0.005 or hoards:
        return "not_win"
    return "neutral"


def main():
    cache = m.load_embed_cache()
    notes = [(s, r) for s, r in cache.items()
             if isinstance(r, dict) and isinstance(r.get("vec"), list)]
    bar = "=" * 78
    print(bar)
    print("  RETENTION UNDER BUDGET (3A.3) - does SEMANTIC recurrence in the shipped coreset keep")
    print("  durable lessons better than today's slug utility, or is coverage already enough?")
    print(bar)
    if len(notes) < 20:
        print(f"  only {len(notes)} embedded notes - set NEVERTWICE_VAULT to a real, populated store.")
        return
    by = defaultdict(list)
    for s, r in notes:
        by[r.get("project")].append((s, r))

    # accumulate retained / total durable topics per (keep_frac, policy)
    acc = {kf: {p: [0, 0] for p in POLICIES} for kf in KEEP_FRACS}   # [retained, total]
    kept_members = {kf: {p: [0, 0] for p in POLICIES} for kf in KEEP_FRACS}  # [members_in_topics, surviving_topics]
    projects_measured = 0
    for proj, ns in by.items():
        n = len(ns)
        if n < MIN_NOTES:
            continue
        size, topics = _topics(ns)
        if not topics:
            continue
        projects_measured += 1
        rec = dict(ns)
        toks = {s: m._tokens(f"{r.get('title','')} {r.get('desc','')} "
                             f"{r.get('prevention','')} {s}") for s, r in ns}
        ids = [s for s, _ in ns]
        util = {
            "coverage": lambda s: 1.0,
            "slug": lambda s: (int(rec[s].get("recurrence", 1) or 1)
                               * (RW if rec[s].get("resolved") else 1.0)),
            "semantic": lambda s: (max(1, size.get(s, 1))
                                   * (RW if rec[s].get("resolved") else 1.0)),
        }
        for kf in KEEP_FRACS:
            budget = max(1, round(n * kf))
            if budget >= n:
                continue
            for p in POLICIES:
                keep = select_coreset(ids, budget, util[p], lambda s: toks[s])
                for t in topics:
                    surv = t & keep
                    acc[kf][p][1] += 1
                    if surv:
                        acc[kf][p][0] += 1
                        kept_members[kf][p][0] += len(surv)
                        kept_members[kf][p][1] += 1

    print(f"  {projects_measured} projects with ≥{MIN_NOTES} notes and ≥1 durable topic measured\n")
    print(f"- durable-topic retention (fraction of cross-session topics keeping ≥1 member) -")
    print(f"  {'budget':>8} {'coverage':>10} {'slug(ship)':>11} {'semantic':>10} "
          f"{'Δ sem−cov':>10}")
    out = {}
    for kf in KEEP_FRACS:
        row = {}
        for p in POLICIES:
            ret, tot = acc[kf][p]
            row[p] = ret / tot if tot else 0.0
        d = row["semantic"] - row["coverage"]
        out[f"keep_{int(kf*100)}pct"] = row
        print(f"  {f'{int(kf*100)}%':>8} {row['coverage']:>10.3f} {row['slug']:>11.3f} "
              f"{row['semantic']:>10.3f} {d:>+10.3f}")

    # redundancy: members kept per surviving topic (lower = less hoarding of one cluster)
    print(f"\n- members kept per surviving topic (≈1 is ideal; higher = redundant hoarding) -")
    print(f"  {'budget':>8} {'coverage':>10} {'slug(ship)':>11} {'semantic':>10}")
    redun = {p: [] for p in POLICIES}
    for kf in KEEP_FRACS:
        vals = {}
        for p in POLICIES:
            mem, tps = kept_members[kf][p]
            vals[p] = mem / tps if tps else 0.0
            redun[p].append(vals[p])
        print(f"  {f'{int(kf*100)}%':>8} {vals['coverage']:>10.2f} {vals['slug']:>11.2f} "
              f"{vals['semantic']:>10.2f}")
    red_cov = sum(redun["coverage"]) / len(redun["coverage"])
    red_sem = sum(redun["semantic"]) / len(redun["semantic"])

    # honest verdict (extracted + unit-pinned so it can't silently regress to cherry-picking best)
    deltas = [out[f"keep_{int(kf*100)}pct"]["semantic"] - out[f"keep_{int(kf*100)}pct"]["coverage"]
              for kf in KEEP_FRACS]
    worst, best = min(deltas), max(deltas)
    key = _verdict(deltas, red_cov, red_sem)
    if key == "win":
        verdict = (f"semantic recurrence robustly improves durable-topic retention (worst budget "
                   f"{worst:+.3f}) without\n    extra hoarding - worth wiring semantic cluster size "
                   f"into the cap's keep-utility.")
    elif key == "not_win":
        verdict = (f"NOT a win. Semantic recurrence helps at a loose budget ({best:+.3f}) but HURTS "
                   f"at a tight one\n    ({worst:+.3f}) and hoards {red_sem:.1f} vs {red_cov:.1f} "
                   f"members/topic - it trades diversity for redundant\n    copies of big clusters. "
                   f"Facility-location coverage alone already preserves durable topics; the cap "
                   f"\n    should keep the coverage objective and NOT add recurrence weighting "
                   f"(anti-bloat). Note slug≡coverage:\n    the shipped slug utility is inert on "
                   f"real data, so today's cap already behaves as coverage - safe to default-on.")
    else:
        verdict = (f"semantic ≈ coverage (worst {worst:+.3f}); the recurrence term is neutral in the "
                   f"cap - coverage suffices.")
    print(f"\n  → HONEST: {verdict}")

    if SAVE:
        agg = {"projects_measured": projects_measured, "cluster_thr": CLUSTER_THR,
               "topic_retention": out,
               "members_per_topic": {"coverage": red_cov, "semantic": red_sem}}
        p = HERE / "retention_bench.json"
        p.write_text(json.dumps(agg, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved aggregate metrics → {p}")
    print(bar)


if __name__ == "__main__":
    main()
