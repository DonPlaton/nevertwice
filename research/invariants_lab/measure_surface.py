"""F2: the scoping grid -- five surfaces crossed with F1's two policies, on `corpus_dev`.

The grid, the decision rule and the census that withdrew one of the cells before it ran are
in [`SURFACE_F2.md`](SURFACE_F2.md), written first. This file executes them.

Two levers, and the point of measuring them together is that **nobody may assume they
multiply**. The run reports the observed combined flag rate next to the product of the two
marginal reductions, so "not additive" is a number rather than a caution.

One pass over the same 345 clusters and the same 1,000-commit silence pool D5 and F1 used,
at the same seed, so every row here compares to every row there. The verdict is computed
once per tree; policies and surfaces are filters over it, so a difference between cells can
only come from the cell.

    python research/invariants_lab/measure_surface.py
    python research/invariants_lab/measure_surface.py --print
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
import surface as SF  # noqa: E402
from corpora import dev_repos  # noqa: E402
from corpusio import BlobReader, progress  # noqa: E402
import measure_blast_radius as D5  # noqa: E402

CENSUS = Path(__file__).with_name("corpus_census.json")
MUTANTS = Path(__file__).with_name("mutants.json")
ARTIFACT = Path(__file__).with_name("surface_f2.json")

SEED = D5.SEED
SILENCE_SAMPLE = D5.SILENCE_SAMPLE
CEILING = 0.05
POLICIES = ("emit-all", "decidable-only")   # SURFACE_F2.md section 2
br = D5.br


def _cells(verdict, after, symbol: str, qualname: str | None) -> dict:
    """One tree, ten cells: what each keeps and whether it still names the symbol."""
    changes = {c.qualname: c for c in verdict.contract_changes}
    out: dict[str, dict] = {}
    for policy in POLICIES:
        findings = abstain.decide(verdict, after, policy)
        for surf in SF.SURFACES:
            kept = SF.apply(findings, after, changes, surf)
            hit = (qualname is not None and any(
                f.qualname == qualname or f.qualname.rsplit(".", 1)[-1] == symbol
                for f in kept))
            out[policy + "|" + surf] = {"findings": len(kept), "hit": hit}
    return out


def _declaration_census(mutants: list[dict], repos: dict) -> dict:
    """SURFACE_F2.md section 1, recomputed rather than restated."""
    readers: dict[str, BlobReader] = {}
    have = names_it = underscored = unreadable = 0
    try:
        for m in mutants:
            repo = repos.get(m["repo"])
            if repo is None:
                continue
            blobs = readers.setdefault(m["repo"], BlobReader(repo))
            src = blobs.read(m["sha"], m["defining_path"])
            if src is None:
                unreadable += 1
                continue
            if not SF.is_public_by_convention(m["qualname"]):
                underscored += 1
            head = m["qualname"].split(".")[0]
            declares_any = SF.module_declares(src, head) or "__all__" in src
            if declares_any:
                have += 1
            if SF.module_declares(src, head):
                names_it += 1
    finally:
        for r in readers.values():
            r.close()
    n = len(mutants)
    return {"positives": n, "module_has_all": have, "module_names_the_symbol": names_it,
            "share_named": names_it / n if n else float("nan"),
            "private_by_convention": underscored, "unreadable": unreadable}


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

    repos = {r.name: r for r in dev_repos()}
    progress("declaration census over the full answer key")
    declaration = _declaration_census(mutants, repos)

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
            before, after = D5._sources(blobs, entry, m["caller_path"])
            if not before:
                continue
            v = br.check_sources(before, after, scan=after)
            rows.append({"repo": m["repo"], "sha": m["sha"],
                         "cells": _cells(v, after, m["symbol"], m["qualname"])})
    finally:
        for r in readers.values():
            r.close()

    progress("silence pool")
    pool_all = [(r["repo"], e) for r in census["repos"] for e in r.get("silent", [])]
    pool_all.sort(key=lambda x: (x[0], x[1]["sha"]))
    rng_pool = random.Random(SEED)
    pool = (rng_pool.sample(pool_all, SILENCE_SAMPLE)
            if len(pool_all) > SILENCE_SAMPLE else pool_all)
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
            silence.append({"repo": repo_name, "sha": entry["sha"],
                            "cells": _cells(v, after, "", None)})
    finally:
        for rd in pool_readers.values():
            rd.close()

    return {"rows": rows, "silence": silence, "declaration_census": declaration,
            "n_mutants": len(mutants)}


def summarise(raw: dict) -> dict:
    rows, silence = raw["rows"], raw["silence"]
    n, ns = len(rows), len(silence)
    base_key = "emit-all|everything"
    base_recall = sum(1 for r in rows if r["cells"][base_key]["hit"]) / n if n else 0.0
    base_flag = (sum(1 for s in silence if s["cells"][base_key]["findings"] > 0) / ns
                 if ns else 0.0)

    grid = []
    for policy in POLICIES:
        for surf in SF.SURFACES:
            key = policy + "|" + surf
            hits = sum(1 for r in rows if r["cells"][key]["hit"])
            rec = D5.wilson(hits, n)
            flagged = sum(1 for s in silence if s["cells"][key]["findings"] > 0)
            fr = D5.wilson(flagged, ns)
            grid.append({
                "policy": policy, "surface": surf,
                "kind": ("baseline" if surf == "everything"
                         else "convention" if surf in SF.CONVENTIONS else "declaration"),
                "withdrawn": surf in SF.DECLARED,
                "recall": {"hits": hits, "n": n, "point": rec[0], "ci": [rec[1], rec[2]]},
                "recall_cost": base_recall - rec[0],
                "silence": {"flagged": flagged, "n": ns, "rate": fr[0],
                            "ci": [fr[1], fr[2]],
                            "findings_kept": sum(s["cells"][key]["findings"]
                                                 for s in silence)},
            })

    by = {(g["policy"], g["surface"]): g for g in grid}

    def rate(policy, surf):
        return by[(policy, surf)]["silence"]["rate"]

    additivity = {}
    for policy in POLICIES:
        base = rate(policy, "everything")
        w1, w2, w3 = (rate(policy, "public-by-convention"),
                      rate(policy, "shipped-consumers"), rate(policy, "both"))
        predicted = (base * (w1 / base) * (w2 / base)) if base else float("nan")
        additivity[policy] = {
            "everything": base, "public_by_convention": w1, "shipped_consumers": w2,
            "both_observed": w3, "both_if_independent": predicted,
            "observed_minus_predicted": w3 - predicted,
        }

    eligible = [g for g in grid
                if g["silence"]["rate"] <= CEILING and not g["withdrawn"]]
    if eligible:
        named = min(eligible, key=lambda g: (g["recall_cost"], -g["recall"]["point"],
                                             SF.SURFACES.index(g["surface"]),
                                             POLICIES.index(g["policy"])))
        outcome = {"reached_ceiling": True,
                   "named": named["policy"] + " + " + named["surface"],
                   "recall": named["recall"]["point"],
                   "recall_cost": named["recall_cost"],
                   "flag_rate": named["silence"]["rate"]}
    else:
        outcome = {"reached_ceiling": False, "named": None,
                   "statement": "no scoped cell reaches the ceiling"}

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "F2", "corpus": "corpus_dev", "in_sample": True,
        "n_clusters": n, "silence_pool": ns, "n_mutants": raw["n_mutants"],
        "ceiling": CEILING,
        "baseline": {"recall": base_recall, "flag_rate": base_flag},
        "declaration_census": raw["declaration_census"],
        "grid": grid,
        "additivity": additivity,
        "outcome": outcome,
        "note": ("public-by-convention and shipped-consumers are CONVENTIONS, not "
                 "declarations. declared-public is the only declaration and its gate was "
                 "withdrawn in SURFACE_F2.md section 1 on a base rate of 18 of 859."),
    }


def _print(data: dict) -> None:
    c = data["declaration_census"]
    print(f"F2 scoping grid -- corpus_dev, IN SAMPLE, {data['n_clusters']} clusters, "
          f"silence pool {data['silence_pool']}")
    print()
    print("the declaration census, recomputed:")
    print(f"  of {c['positives']} positives, {c['module_names_the_symbol']} "
          f"({c['share_named']:.2%}) are named by their defining module's __all__; "
          f"{c['private_by_convention']} are private by convention")
    print()
    print(f"{'policy':16s} {'surface':22s} {'kind':11s} {'recall':>18s} {'cost':>7s} "
          f"{'flag rate':>18s} {'kept':>7s}")
    print("-" * 106)
    for g in data["grid"]:
        r, s = g["recall"], g["silence"]
        mark = " (withdrawn)" if g["withdrawn"] else ""
        print(f"{g['policy']:16s} {g['surface']:22s} {g['kind']:11s} "
              f"{r['point']:.3f} [{r['ci'][0]:.2f},{r['ci'][1]:.2f}] "
              f"{g['recall_cost']:+7.3f} "
              f"{s['rate']:.3f} [{s['ci'][0]:.2f},{s['ci'][1]:.2f}] "
              f"{s['findings_kept']:7d}{mark}")
    print("-" * 106)
    print()
    print("do the two levers multiply?")
    for policy, a in data["additivity"].items():
        print(f"  {policy:16s} both observed {a['both_observed']:.3f}  "
              f"if independent {a['both_if_independent']:.3f}  "
              f"difference {a['observed_minus_predicted']:+.3f}")
    print()
    out = data["outcome"]
    if out["reached_ceiling"]:
        print(f"named: {out['named']} -- flag rate {out['flag_rate']:.3f}, "
              f"recall {out['recall']:.3f}, cost {out['recall_cost']:.3f}")
    else:
        print("NO scoped cell reaches the ceiling.")
    print()
    print(data["note"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", dest="show")
    args = ap.parse_args()
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
