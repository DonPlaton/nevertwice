"""T1-T2: every surviving mechanism at once, and whether the union is quiet enough to keep.

The two questions Phase T exists to answer, both declared in `PREREGISTRATION.md` §8:

* **T1** -- with every passing invariant switched on at the same time, does the *union* fire on
  more than 5% of the silence pool? If it does: rank and cap, or drop the weakest contributor.
  Do not relax the number.
* **T2** -- do two invariants report the same underlying cause on one diff? Two findings from one
  root cause is one finding and one bug.

## Why the union is not the sum

`INVARIANT_NOTES.md` caps delivery at **one finding per diff**, ranked. So the number that
matters is not how many mechanisms fire but **how often at least one does** -- what a person
actually experiences. Both are reported here, because the gap between them is the cap's whole
value and hiding it would make the cap look free.

Which mechanisms are in the union is not a judgement call: it is whatever passed its own gate.
`blast_radius` (D5) and the complexity ratchet (R3) both failed on flag rate and are excluded,
which is what "deleted" means.

    python research/invariants_lab/measure_together.py
    python research/invariants_lab/measure_together.py --print
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from scipy import stats
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))

import invariant_notes as I  # noqa: E402
import preconfigured as P  # noqa: E402
import scale as S  # noqa: E402
from corpusio import BlobReader, corpus_repos, progress  # noqa: E402

CENSUS = Path(__file__).with_name("corpus_census.json")
ARTIFACT = Path(__file__).with_name("together_t1.json")
SEED = 20260827
SAMPLE = 1000
ALPHA = 0.05
CEILING = 0.05


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = proportion_confint(k, n, alpha=ALPHA, method="wilson")
    return (k / n, float(lo), float(hi))


def hottest_axis(after: dict[str, str]) -> str | None:
    """The identifier most often iterated over, as a project's declared axis stands in.

    The same adversarial choice X4 used: the name most likely to appear in a nested
    loop, so the union's flag rate is an upper bound rather than a flattering one.
    """
    names: Counter[str] = Counter()
    for src in after.values():
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError, RecursionError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.AsyncFor)):
                name = S._iterated_name(node.iter)
                if name:
                    names[name] += 1
    return names.most_common(1)[0][0] if names else None


def build_union(axis: str | None) -> tuple[list[dict], dict]:
    """Every mechanism that passed its own gate, as one ledger and one registry."""
    ledger = P.build_pack()
    registry = dict(P.REGISTRY)
    if axis:
        note = I.make_invariant(
            "scale", "this grows quadratically along a declared axis",
            subject="scale", provenance="declared")
        note["declaration"] = {"axes": [{"name": axis, "unit": "records",
                                         "start": 1_000, "target": 50_000_000}]}
        ledger.append(note)
        registry["scale"] = S.checker
    return ledger, registry


def run() -> dict:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    pool = [(r["repo"], e) for r in census["repos"]
            for e in (r.get("eligible", []) + r.get("silent", []))]
    pool.sort(key=lambda x: (x[0], x[1]["sha"]))
    rng = random.Random(SEED)
    sample = rng.sample(pool, SAMPLE) if len(pool) > SAMPLE else pool
    progress(f"{len(sample)} commits of {len(pool)}")

    repos = {r.name: r for r in corpus_repos()}
    readers: dict[str, BlobReader] = {}
    rows: list[dict] = []
    try:
        for i, (repo_name, entry) in enumerate(sample):
            if i % 100 == 0:
                progress(f"  {i}/{len(sample)}")
            repo = repos.get(repo_name)
            if repo is None:
                continue
            blobs = readers.setdefault(repo_name, BlobReader(repo))
            before: dict[str, str] = {}
            after: dict[str, str] = {}
            for d in entry["deltas"]:
                path = d["path"]
                old = blobs.read(entry["parent"], path)
                new = blobs.read(entry["sha"], path)
                if old is not None and new is not None:
                    before[path], after[path] = old, new
            if not after:
                continue

            axis = hottest_axis(after)
            ledger, registry = build_union(axis)

            # Uncapped: everything every mechanism would say.
            everything = I.check_diff(before, after, ledger=ledger, registry=registry,
                                      cap=-1)
            # Delivered: what a person sees, after the one-per-diff cap.
            delivered = I.check_diff(before, after, ledger=ledger, registry=registry)

            by_checker: Counter[str] = Counter()
            for f in everything:
                note = next((n for n in ledger if n["id"] == f.invariant_id), None)
                by_checker[note["checker"] if note else "?"] += 1

            rows.append({
                "repo": repo_name, "sha": entry["sha"], "axis": axis,
                "raw_findings": len(everything),
                "delivered": len(delivered),
                "mechanisms_firing": len({f.invariant_id for f in everything}),
                "by_checker": dict(by_checker),
                "subjects": sorted({f.subject for f in everything})[:8],
            })
    finally:
        for r in readers.values():
            r.close()
    return {"rows": rows, "pool": len(pool)}


def summarise(raw: dict) -> dict:
    rows = raw["rows"]
    n = len(rows)
    fired = sum(1 for r in rows if r["raw_findings"] > 0)
    union = wilson(fired, n)
    delivered = sum(r["delivered"] for r in rows)
    total_raw = sum(r["raw_findings"] for r in rows)

    per_checker: Counter[str] = Counter()
    commits_per_checker: Counter[str] = Counter()
    for r in rows:
        for checker, count in r["by_checker"].items():
            per_checker[checker] += count
            commits_per_checker[checker] += 1

    # T2: two mechanisms naming the same subject on one diff is one finding and one bug.
    collisions = []
    for r in rows:
        if r["mechanisms_firing"] < 2:
            continue
        subjects = r["subjects"]
        seen = Counter(s.split("[")[0].split("::")[0] for s in subjects)
        duplicated = [s for s, k in seen.items() if k > 1]
        if duplicated:
            collisions.append({"repo": r["repo"], "sha": r["sha"],
                               "subjects": duplicated[:4]})

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n": n, "pool": raw["pool"],
        "union_flag_rate": {"fired": fired, "n": n, "point": union[0],
                            "ci": [union[1], union[2]],
                            "ceiling": CEILING,
                            "p_vs_ceiling": float(stats.binomtest(fired, n, CEILING).pvalue)
                            if n else float("nan"),
                            "passes": union[0] <= CEILING},
        "delivery": {"raw_findings": total_raw, "delivered": delivered,
                     "suppressed_by_the_cap": total_raw - delivered,
                     "commits_with_more_than_one_mechanism":
                         sum(1 for r in rows if r["mechanisms_firing"] > 1)},
        "per_checker": {"findings": dict(per_checker.most_common()),
                        "commits": dict(commits_per_checker.most_common())},
        "interaction": {"colliding_commits": len(collisions),
                        "examples": collisions[:5]},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    if args.show:
        d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        u, dl = d["union_flag_rate"], d["delivery"]
        print(f"commits {d['n']} of a pool of {d['pool']}")
        print(f"T1 union flag rate  {u['point']:.3f} [{u['ci'][0]:.3f}, {u['ci'][1]:.3f}]"
              f"   {u['fired']}/{u['n']}   ceiling {u['ceiling']}   "
              f"{'PASS' if u['passes'] else 'FAIL'}")
        print(f"   raw findings {dl['raw_findings']}, delivered {dl['delivered']}, "
              f"suppressed by the cap {dl['suppressed_by_the_cap']}")
        print(f"   commits where more than one mechanism fired: "
              f"{dl['commits_with_more_than_one_mechanism']}")
        print(f"T2 colliding commits {d['interaction']['colliding_commits']}")
        print()
        print("per checker (findings / commits):")
        for k, v in d["per_checker"]["findings"].items():
            print(f"  {k:<26}{v:>7} / {d['per_checker']['commits'].get(k, 0)}")
        return 0

    raw = run()
    out = summarise(raw)
    out["rows"] = raw["rows"]
    ARTIFACT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
