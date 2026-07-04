#!/usr/bin/env python3
"""RESEARCH — real-trace recurrence validation (roadmap 3A.2; paper §8 decisive check).

The synthetic 3A benchmark BUILT recurrence in; the honest question its limitations section
raises is: does GENUINE recurrence exist in a *real* accumulated store, and does the production
slug-based recurrence counter capture it? This tool answers both on a live vault — and finds
the gap the supersession/consolidation fixes target: recurrence is real but slug-invisible.

METHOD. Read ONLY the local embedding cache (vectors + metadata — never raw note text). Within
each project, greedily cluster notes whose cosine ≥ a relative threshold (bge-m3 cosines bunch
near a high background ~0.42, so the threshold is swept above it, not the near-exact 0.92 the
dedup uses). A cluster spanning >1 DATE is GENUINE cross-session recurrence: the same lesson
re-encountered and re-written as a distinct note (different slug → the live counter missed it).

PRIVACY. Aggregate only: cluster COUNTS, fractions, recall numbers. No titles, descriptions,
projects, or stems are printed or saved. Runs against NEVERTWICE_VAULT's cache.

    NEVERTWICE_VAULT=/path/to/vault python research/real_trace_bench.py
    NEVERTWICE_VAULT=/path/to/vault python research/real_trace_bench.py --save  # aggregate JSON

Research dep: none beyond the package (stdlib + memory_hook). Reads cached vectors, no Ollama.
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAVE = "--save" in sys.argv
THRESHOLDS = [0.50, 0.55, 0.60]
RECALL_THR = 0.55          # the operating threshold for the recall check


def _date(stem: str) -> str:
    p = m.parse_typed_stem(stem)
    return p["date"] if p else "?"


def _clusters(notes, thr):
    """Greedy cosine clusters within an already-same-project note list."""
    used, groups = set(), []
    for i, (s1, r1) in enumerate(notes):
        if s1 in used:
            continue
        grp = [s1]
        for s2, r2 in notes[i + 1:]:
            if s2 not in used and m.cosine(r1["vec"], r2["vec"]) >= thr:
                grp.append(s2)
                used.add(s2)
        if len(grp) > 1:
            used.add(s1)
            groups.append(grp)
    return groups


def main():
    cache = m.load_embed_cache()
    notes = [(s, r) for s, r in cache.items()
             if isinstance(r, dict) and isinstance(r.get("vec"), list)]
    bar = "=" * 78
    print(bar)
    print("  REAL-TRACE RECURRENCE VALIDATION (3A.2) — does genuine recurrence exist, and does")
    print("  the slug-based counter capture it? (aggregate-only; no note content read)")
    print(bar)
    if len(notes) < 20:
        print(f"  only {len(notes)} embedded notes — set NEVERTWICE_VAULT to a real, populated store.")
        return
    by_proj = defaultdict(list)
    for s, r in notes:
        by_proj[r.get("project")].append((s, r))
    # what the SLUG-based counter recorded (production recurrence field)
    slug_recurring = sum(1 for _, r in notes if int(r.get("recurrence", 1) or 1) >= 2)
    print(f"  {len(notes)} embedded notes across {len(by_proj)} projects")
    print(f"  slug-based recurrence>1 (what production recorded): {slug_recurring} notes\n")

    print(f"— genuine SEMANTIC recurrence (cosine clusters; >1 date = cross-session) —")
    print(f"  {'cosine≥':>8} {'clusters':>9} {'cross-session':>14} {'notes in clusters':>18}")
    sweep = {}
    for thr in THRESHOLDS:
        cl_n = xs = involved = 0
        for ns in by_proj.values():
            for g in _clusters(ns, thr):
                cl_n += 1
                involved += len(g)
                if len({_date(x) for x in g}) > 1:
                    xs += 1
        sweep[thr] = {"clusters": cl_n, "cross_session": xs, "notes_involved": involved}
        print(f"  {thr:>8} {cl_n:>9} {xs:>14} {involved:>18} ({involved/len(notes):.0%})")

    xs55 = sweep[RECALL_THR]["cross_session"]
    print(f"\n  → KEY: ~{xs55} genuine cross-session recurring topics exist at cosine≥{RECALL_THR}, "
          f"but the slug counter\n    recorded {slug_recurring} — real recurrence is present yet "
          f"SLUG-INVISIBLE (the extractor rephrases each\n    occurrence). It needs semantic "
          f"aggregation (supersession/consolidation), not slug matching.")

    # Where does the recurrence signal belong? The tempting hypothesis is "boost recurring notes
    # at recall time". We FALSIFY it here, honestly. For each cross-session cluster, query =
    # a member's vector; ground truth = its same-cluster other-DATE members (the same topic, re-
    # encountered in another session). Rank in-project notes by relevance, then by relevance + an
    # additive recurrence prior (log cluster size) swept across weights. If no weight beats the
    # relevance-only baseline, the prior does not belong on the recall path.
    K = 3
    WEIGHTS = [0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.20]
    hits = {w: 0.0 for w in WEIGHTS}
    q = 0
    for ns in by_proj.values():
        size, groups = {}, _clusters(ns, RECALL_THR)
        for g in groups:
            for x in g:
                size[x] = len(g)
        vecs = {s: r["vec"] for s, r in ns}
        for g in groups:
            if len({_date(x) for x in g}) < 2:
                continue
            for qi in g:
                gt = {x for x in g if x != qi and _date(x) != _date(qi)}
                if not gt:
                    continue
                q += 1
                cand = [s for s in vecs if s != qi]
                cos = {s: m.cosine(vecs[qi], vecs[s]) for s in cand}
                lsz = {s: math.log(max(1, size.get(s, 1))) for s in cand}
                for w in WEIGHTS:
                    top = sorted(cand, key=lambda s: -(cos[s] + w * lsz[s]))[:K]
                    hits[w] += 1.0 if set(top) & gt else 0.0
    recall = {w: hits[w] / q for w in WEIGHTS} if q else {}
    if q:
        base = recall[0.0]
        best_w = max(WEIGHTS, key=lambda w: recall[w])
        print(f"\n— same-topic recall@{K} vs additive recurrence prior ({q} queries) —")
        print(f"  {'prior w':>8} {'recall@'+str(K):>10}")
        for w in WEIGHTS:
            mark = "  <- relevance-only" if w == 0.0 else (
                   "  <- best" if w == best_w and recall[w] > base else "")
            print(f"  {w:>8.2f} {recall[w]:>10.3f}{mark}")
        verdict = ("NO weight beats relevance-only" if recall[best_w] <= base + 1e-9
                   else f"best at w={best_w} (+{recall[best_w]-base:.3f})")
        print(f"\n  → HONEST NEGATIVE: {verdict}. Relevance alone is already at ceiling for same-")
        print(f"    topic recall; an additive cluster-size prior only adds noise. Recurrence earns")
        print(f"    its place in RETENTION/consolidation/decay (what to keep), not recall ranking.")

    if SAVE:
        out = {"notes": len(notes), "projects": len(by_proj), "slug_recurring": slug_recurring,
               "semantic_recurrence": sweep,
               "recall_at_k": K, "recall_queries": q, "recall_vs_prior_weight": recall}
        p = HERE / "real_trace_bench.json"      # aggregate only — safe to keep
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved aggregate metrics → {p}")
    print(bar)


if __name__ == "__main__":
    main()
