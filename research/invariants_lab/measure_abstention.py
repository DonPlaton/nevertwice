"""F1: what abstention costs in recall and buys in silence, on the development corpus.

The ladder, the decision rule and the limits on what may be concluded are declared in
[`ABSTENTION_F1.md`](ABSTENTION_F1.md) **before** this ran. This file executes them.

## What this measures, and what it refuses to

`ABSTENTION_F1.md` §1: an abstention rule that declines to emit where the answer key's
oracle returns *undecidable* is a second implementation of that oracle, so **precision
after abstention is circular** and is reported only as a consistency check. The evidence
is the pair (recall, silence-pool flag rate); neither is implied by the abstention rule.

Recall is symbol-level -- *did the checker name the mutated symbol* -- exactly as D5
defined it, so dropping some references for a symbol costs recall only when the last one
goes. The silence pool is the same 1,000-commit sample D5 used, at the same seed.

## Why one pass

Every policy keeps a subset of `emit-all`, so the oracle is consulted once per finding
under P0 and the verdict is reused for every rung. That is not an optimisation detail: it
means the six rows are computed from *identical* trees and identical truth labels, and a
difference between rows can only come from the policy.

    python research/invariants_lab/measure_abstention.py
    python research/invariants_lab/measure_abstention.py --print
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import abstain  # noqa: E402
from abstain import POLICIES  # noqa: E402
import corpora  # noqa: E402
from corpora import repos_for  # noqa: E402
from corpusio import BlobReader, progress  # noqa: E402
import measure_blast_radius as D5  # noqa: E402

#: H1: the corpus is an argument, so Phase V runs the SAME frozen code on a different
#: corpus without editing this file. `_select_corpus` rebinds the paths before any run.
CORPUS = "dev"


def _select_corpus(name: str) -> None:
    global CORPUS, CENSUS, MUTANTS, ARTIFACT
    CORPUS = name
    CENSUS = corpora.census_path(name)
    MUTANTS = corpora.mutants_path(name)
    ARTIFACT = corpora.artifact_path('abstention_f1.json', name)


CENSUS = corpora.census_path(CORPUS)
MUTANTS = corpora.mutants_path(CORPUS)
ARTIFACT = corpora.artifact_path('abstention_f1.json', CORPUS)


SEED = D5.SEED                    # the same commits D5 measured, so the rows compare
SILENCE_SAMPLE = D5.SILENCE_SAMPLE
CEILING = 0.05                    # T1's declared ceiling; not renegotiable here
br = D5.br


def _truths_by_site(found, after, by_qual, blobs, sha) -> dict:
    """The oracle's verdict for every P0 finding, keyed by site.

    Computed once for the superset. Every policy keeps a subset, so re-deriving it per
    policy could only introduce a difference the policy did not cause.
    """
    return {
        (q, p, ln): D5.is_true_finding(q, p, ln, after, by_qual, blobs, sha)
        for q, p, ln in found
    }


def _policy_row(verdict, after, truths, symbol, qualname) -> dict:
    """One commit, six rungs: what each keeps, whether it still names the symbol."""
    out: dict[str, dict] = {}
    for policy in POLICIES:
        kept = abstain.decide(verdict, after, policy)
        sites = [(f.qualname, f.path, f.lineno) for f in kept]
        labels = [truths.get(s) for s in sites]
        out[policy] = {
            "findings": len(kept),
            "hit": any(q == qualname or q.rsplit(".", 1)[-1] == symbol
                       for q, _p, _l in sites),
            "true": sum(1 for t in labels if t is True),
            "false": sum(1 for t in labels if t is False),
            "undecidable": sum(1 for t in labels if t is None),
        }
    return out


def run() -> dict:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    by_repo_entries = {
        r["repo"]: {e["sha"]: e for e in r["eligible"]} for r in census["repos"]
    }
    mutants = [m for m in json.loads(MUTANTS.read_text(encoding="utf-8"))["mutants"]
               if m["confirmed"]]

    by_commit: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in mutants:
        by_commit[(m["repo"], m["sha"])].append(m)
    rng = random.Random(SEED)
    primary = [rng.choice(sorted(v, key=lambda x: x["mid"]))
               for _k, v in sorted(by_commit.items())]
    progress(f"primary sample: {len(primary)} clusters from {len(mutants)} mutants")

    repos = {r.name: r for r in repos_for(CORPUS)}
    rows: list[dict] = []
    readers: dict[str, BlobReader] = {}
    try:
        for i, m in enumerate(primary):
            if i % 50 == 0:
                progress(f"  mutant arm {i}/{len(primary)}")
            repo = repos.get(m["repo"])
            entry = by_repo_entries[m["repo"]].get(m["sha"]) if repo else None
            if repo is None or entry is None:
                continue
            blobs = readers.setdefault(m["repo"], BlobReader(repo))
            row = {"mid": m["mid"], "repo": m["repo"], "sha": m["sha"],
                   "symbol": m["symbol"], "qualname": m["qualname"],
                   "breakage": m["breakage"]}
            for arm, revert in (("mutant", m["caller_path"]), ("real", None)):
                before, after = D5._sources(blobs, entry, revert)
                if not before:
                    continue
                v = br.check_sources(before, after, scan=after)
                found = D5.findings(v)
                by_qual = {c.qualname: c for c in v.contract_changes}
                truths = _truths_by_site(found, after, by_qual, blobs, m["sha"])
                row[arm] = _policy_row(v, after, truths, m["symbol"], m["qualname"])
                if arm == "mutant":
                    # Exploratory, declared in ABSTENTION_F1.md section 5 on 2026-08-28,
                    # after the ladder ran and before it was written up. The named policy
                    # is fixed by the declared decision rule and this cannot change it.
                    # It answers the question the ladder alone cannot: a policy quiet
                    # enough to leave on is only worth having if it still beats a regex
                    # AT THAT flag rate.
                    changed = [c.qualname for c in v.contract_changes]
                    grep = D5.grep_hits(before, after, changed)
                    row["grep"] = grep
                    row["grep_symbol"] = grep.get(m["qualname"], 0)
            rows.append(row)
    finally:
        for r in readers.values():
            r.close()

    # --- the silence pool, the same 1,000 commits D5 used ------------------
    progress("silence pool")
    pool_all = [(r["repo"], e) for r in census["repos"] for e in r.get("silent", [])]
    pool_all.sort(key=lambda x: (x[0], x[1]["sha"]))
    rng_pool = random.Random(SEED)
    pool = (rng_pool.sample(pool_all, SILENCE_SAMPLE)
            if len(pool_all) > SILENCE_SAMPLE else pool_all)
    progress(f"silence pool: {len(pool)} of {len(pool_all)} kept by the census")

    silence: list[dict] = []
    pool_readers: dict[str, BlobReader] = {}
    try:
        for i, (repo_name, entry) in enumerate(pool):
            if i % 100 == 0:
                progress(f"  silence {i}/{len(pool)}")
            repo = repos.get(repo_name)
            if repo is None:
                continue
            blobs = pool_readers.setdefault(repo_name, BlobReader(repo))
            before, after = D5._sources(blobs, entry, None)
            if not before:
                continue
            v = br.check_sources(before, after, scan=after)
            found = D5.findings(v)
            by_qual = {c.qualname: c for c in v.contract_changes}
            truths = _truths_by_site(found, after, by_qual, blobs, entry["sha"])
            rec = {"repo": repo_name, "sha": entry["sha"]}
            for policy in POLICIES:
                kept = abstain.decide(v, after, policy)
                sites = [(f.qualname, f.path, f.lineno) for f in kept]
                labels = [truths.get(s) for s in sites]
                rec[policy] = {
                    "findings": len(kept),
                    "true": sum(1 for t in labels if t is True),
                    "false": sum(1 for t in labels if t is False),
                    "undecidable": sum(1 for t in labels if t is None),
                }
            silence.append(rec)
    finally:
        for rd in pool_readers.values():
            rd.close()

    return {"rows": rows, "silence": silence, "n_mutants": len(mutants),
            "silence_pool_total": len(pool_all)}


def _matched_grep(rows: list[dict], policy: str) -> dict:
    """`git grep`, thresholded so its flag rate matches the checker's under `policy`.

    D5's construction, re-run per rung. At a threshold of one mention grep fires on
    almost every commit and "wins" recall by having no precision at all; the honest
    question is what it recovers when it is held to the *same* noise budget. Ties go to
    grep -- the first threshold whose rate is at or under the checker's is taken.
    """
    n = len(rows)
    if not n:
        return {}
    checker_rate = sum(1 for r in rows
                       if r["mutant"][policy]["findings"] > 0) / n
    thresholds = sorted({v for r in rows for v in r.get("grep", {}).values()} | {1})
    chosen, rate = (thresholds[-1] if thresholds else 1), 1.0
    for th in thresholds:
        got = sum(1 for r in rows
                  if any(v >= th for v in r.get("grep", {}).values())) / n
        if got <= checker_rate:
            chosen, rate = th, got
            break
    hit = sum(1 for r in rows if r.get("grep_symbol", 0) >= chosen)
    b01 = sum(1 for r in rows if r["mutant"][policy]["hit"]
              and r.get("grep_symbol", 0) < chosen)
    b10 = sum(1 for r in rows if not r["mutant"][policy]["hit"]
              and r.get("grep_symbol", 0) >= chosen)
    point, lo, hi = D5.wilson(hit, n)
    return {
        "checker_flag_rate_on_mutant_arm": checker_rate,
        "grep_threshold": chosen,
        "grep_flag_rate": rate,
        "grep_recall": {"point": point, "ci": [lo, hi]},
        "checker_only": b01, "grep_only": b10,
        "mcnemar_p": D5.mcnemar_exact(b01, b10),
    }


def summarise(raw: dict) -> dict:
    rows = [r for r in raw["rows"] if "mutant" in r]
    silence = raw["silence"]
    n, ns = len(rows), len(silence)

    ladder = []
    base_recall = None
    for policy in POLICIES:
        hits = sum(1 for r in rows if r["mutant"][policy]["hit"])
        rec = D5.wilson(hits, n)
        per_cluster = []
        for r in rows:
            real = r.get("real", {}).get(policy, {})
            true = r["mutant"][policy]["true"] + real.get("true", 0)
            false = r["mutant"][policy]["false"] + real.get("false", 0)
            per_cluster.append((true, true + false))
        t = sum(k for k, _ in per_cluster)
        d = sum(v for _, v in per_cluster)
        flagged = sum(1 for s in silence if s[policy]["findings"] > 0)
        frate = D5.wilson(flagged, ns)
        kept_total = sum(s[policy]["findings"] for s in silence)
        undec = sum(s[policy]["undecidable"] for s in silence)
        if base_recall is None:
            base_recall = rec[0]
        ladder.append({
            "policy": policy,
            "recall": {"hits": hits, "n": n, "point": rec[0], "ci": [rec[1], rec[2]]},
            "recall_cost_vs_emit_all": base_recall - rec[0],
            "silence": {
                "n": ns, "flagged": flagged, "rate": frate[0],
                "ci": [frate[1], frate[2]],
                "p_vs_ceiling": D5.binom_p(flagged, ns, CEILING) if ns else float("nan"),
                "findings_kept": kept_total,
                "undecidable_kept": undec,
                "share_undecidable": (undec / kept_total) if kept_total else 0.0,
            },
            # NOT EVIDENCE -- see ABSTENTION_F1.md section 1. Reported so a disagreement
            # between the two implementations of "undecidable" is visible.
            "precision_not_evidence": {
                "true": t, "decidable": d, "point": (t / d) if d else float("nan"),
                "cluster_ci": list(D5.cluster_bootstrap(per_cluster)),
            },
            "grep_at_this_flag_rate": _matched_grep(rows, policy),
            "by_repo": {
                rep: {
                    "n": sum(1 for r in rows if r["repo"] == rep),
                    "hits": sum(1 for r in rows
                                if r["repo"] == rep and r["mutant"][policy]["hit"]),
                    "silence_n": sum(1 for s in silence if s["repo"] == rep),
                    "silence_flagged": sum(1 for s in silence if s["repo"] == rep
                                           and s[policy]["findings"] > 0),
                }
                for rep in sorted({r["repo"] for r in rows})
            },
        })

    # The decision rule, applied exactly as declared: among policies at or under the
    # ceiling, the one with the least recall cost; ties on rate go to higher recall;
    # ties on both go to the weaker (lower) policy, which generalises further.
    eligible = [row for row in ladder if row["silence"]["rate"] <= CEILING]
    if eligible:
        named = min(eligible, key=lambda row: (row["recall_cost_vs_emit_all"],
                                               -row["recall"]["point"],
                                               POLICIES.index(row["policy"])))
        outcome = {"reached_ceiling": True, "named_policy": named["policy"],
                   "recall_cost": named["recall_cost_vs_emit_all"],
                   "flag_rate": named["silence"]["rate"]}
    else:
        best = min(ladder, key=lambda row: row["silence"]["rate"])
        outcome = {
            "reached_ceiling": False, "named_policy": None,
            "lowest_flag_rate": best["silence"]["rate"],
            "lowest_flag_rate_policy": best["policy"],
            "its_recall": best["recall"]["point"],
            "statement": (
                "No policy on the declared ladder reaches the 0.05 ceiling. "
                "ABSTENTION_F1.md section 3.3 forbids approximating it or re-baselining "
                "against a softer number, so this is F1's result."
            ),
        }

    # Collapse guard: a policy emitting nothing scores a perfect flag rate and zero
    # recall, and so does a broken harness. Both counts are reported for every rung.
    collapsed = [row["policy"] for row in ladder
                 if row["silence"]["findings_kept"] == 0 and row["recall"]["hits"] == 0]

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "F1",
        "corpus": "corpus_dev",
        "in_sample": True,
        "n_clusters": n,
        "n_mutants": raw["n_mutants"],
        "silence_pool": {"n": ns, "available": raw["silence_pool_total"]},
        "ceiling": CEILING,
        "ladder": ladder,
        "outcome": outcome,
        "collapsed_policies": collapsed,
        "precision_note": (
            "Every precision figure here is agreement between two implementations of "
            "'undecidable' -- the answer key's oracle and the abstention rule -- and is "
            "NOT evidence about the world. See ABSTENTION_F1.md section 1."
        ),
    }


def _print(data: dict) -> None:
    print(f"F1 abstention ladder -- corpus_dev, IN SAMPLE, {data['n_clusters']} clusters, "
          f"silence pool {data['silence_pool']['n']}")
    print()
    print(f"{'policy':22s} {'recall':>18s} {'cost':>7s} "
          f"{'flag rate':>18s} {'kept':>7s} {'undec':>6s} {'grep@match':>12s}")
    print("-" * 97)
    for row in data["ladder"]:
        r = row["recall"]
        s = row["silence"]
        g = row.get("grep_at_this_flag_rate") or {}
        grep = (f"{g['grep_recall']['point']:.3f} @{g['grep_threshold']}"
                if g else "     -")
        print(f"{row['policy']:22s} "
              f"{r['point']:.3f} [{r['ci'][0]:.2f},{r['ci'][1]:.2f}] "
              f"{row['recall_cost_vs_emit_all']:+7.3f} "
              f"{s['rate']:.3f} [{s['ci'][0]:.2f},{s['ci'][1]:.2f}] "
              f"{s['findings_kept']:7d} {s['share_undecidable']:6.0%} "
              f"{grep:>12s}")
    print("-" * 97)
    out = data["outcome"]
    if out["reached_ceiling"]:
        print(f"named: {out['named_policy']} -- flag rate {out['flag_rate']:.3f} "
              f"at a recall cost of {out['recall_cost']:.3f}")
    else:
        print(f"NO policy reaches the {data['ceiling']} ceiling. "
              f"Lowest is {out['lowest_flag_rate_policy']} at "
              f"{out['lowest_flag_rate']:.3f}, recall {out['its_recall']:.3f}.")
        print(out["statement"])
    if data["collapsed_policies"]:
        print("COLLAPSED (no findings and no recall -- check the harness): "
              + ", ".join(data["collapsed_policies"]))
    print()
    print("precision is omitted from this table on purpose. " + data["precision_note"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", dest="show",
                    help="summarise the committed artifact without re-running")
    corpora.add_corpus_argument(ap)
    args = ap.parse_args()
    _select_corpus(args.corpus)
    if args.show:
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    t0 = time.time()
    data = summarise(run())
    data["seconds"] = round(time.time() - t0, 1)
    ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
