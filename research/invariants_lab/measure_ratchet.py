"""R3: measure the ratchet against an instrument it did not write.

`PREREGISTRATION.md` §4 states the trap this measurement has to avoid, before it ran:

> A ratchet's positive class is *defined by its own metric*. A diff that raised cyclomatic
> complexity above a file's baseline is a positive because the metric says so. Recall
> against that definition is 1.0 by construction and measures nothing.

So the gates are **agreement with an independent instrument** and **the cost of the
firing**, never recall against itself:

| gate | threshold | what it tests |
|---|---|---|
| **R3-C1** silence | flag rate ≤ 0.05 on the silence pool | can anyone leave it switched on |
| **R3-C2** agreement | fires on > 0.50 of diffs where **`ruff`'s own delta** says complexity rose | does it find what another tool finds |
| **R3-C3** discrimination | fires strictly more often on `ruff`-rose diffs than on `ruff`-flat diffs | is it responding to complexity at all |

`ruff` replaces the `radon` named in the preregistration; PyPI and GitHub were both
unreachable from this machine and the substitution is logged in §9 rather than made
quietly. It is the stronger choice for the property `radon` was chosen for -- independence
-- since it shares neither language nor authors with anything here.

    python research/invariants_lab/measure_ratchet.py
    python research/invariants_lab/measure_ratchet.py --print
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

from scipy import stats
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))

import complexity as C  # noqa: E402
import ratchet as R  # noqa: E402
from corpusio import BlobReader, corpus_repos, progress  # noqa: E402

CENSUS = Path(__file__).with_name("corpus_census.json")
ARTIFACT = Path(__file__).with_name("ratchet_r3.json")

SEED = 20260827
SAMPLE = 1000          # PREREGISTRATION.md section 4, gates R3-C1..C3
ALPHA = 0.05


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = proportion_confint(k, n, alpha=ALPHA, method="wilson")
    return (k / n, float(lo), float(hi))


def ruff_delta(before: dict[str, str], after: dict[str, str],
               workdir: Path) -> int | None:
    """How much total McCabe complexity `ruff` says this diff added, over shared files.

    Only files present on both sides count. A new file adds complexity trivially and
    says nothing about whether existing code got worse, which is the claim under test.
    """
    shared = [p for p in after if p in before and p.endswith(".py")]
    if not shared:
        return None
    total = 0
    seen = False
    for i, path in enumerate(shared):
        old_file = workdir / f"old_{i}.py"
        new_file = workdir / f"new_{i}.py"
        old_file.write_text(before[path], encoding="utf-8")
        new_file.write_text(after[path], encoding="utf-8")
        old = C.ruff_complexity(old_file)
        new = C.ruff_complexity(new_file)
        if old is None or new is None:
            continue
        seen = True
        # Sum per name; a name that vanished contributes its own removal.
        names = set(old) | set(new)
        total += sum(new.get(n, 0) - old.get(n, 0) for n in names)
    return total if seen else None


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
    tmp = Path(tempfile.mkdtemp(prefix="ratchet_r3_"))
    try:
        for i, (repo_name, entry) in enumerate(sample):
            if i % 50 == 0:
                progress(f"  {i}/{len(sample)}")
            repo = repos.get(repo_name)
            if repo is None:
                continue
            blobs = readers.setdefault(repo_name, BlobReader(repo))
            paths = sorted({d["path"] for d in entry["deltas"]}
                           | {h["path"] for h in entry.get("caller_updates", [])})
            before: dict[str, str] = {}
            after: dict[str, str] = {}
            for p in paths:
                old = blobs.read(entry["parent"], p)
                new = blobs.read(entry["sha"], p)
                if old is None or new is None:
                    continue
                before[p], after[p] = old, new
            if not before:
                continue

            baseline = R.make_baseline(before)
            note = {"id": "i-ratchet", "message": "the ratchet", "baseline": baseline}
            fired = bool(R.checker(before, after, note))
            delta = ruff_delta(before, after, tmp)
            rows.append({
                "repo": repo_name, "sha": entry["sha"],
                "fired": fired,
                "ruff_delta": delta,
                "n_regressions": len(R.regressions(baseline, after)),
            })
    finally:
        for r in readers.values():
            r.close()
    return {"rows": rows, "pool": len(pool), "sampled": len(sample)}


def summarise(raw: dict) -> dict:
    rows = raw["rows"]
    n = len(rows)
    flagged = sum(1 for r in rows if r["fired"])
    overall = wilson(flagged, n)

    decided = [r for r in rows if r["ruff_delta"] is not None]
    rose = [r for r in decided if r["ruff_delta"] > 0]
    flat = [r for r in decided if r["ruff_delta"] <= 0]
    on_rose = wilson(sum(1 for r in rose if r["fired"]), len(rose))
    on_flat = wilson(sum(1 for r in flat if r["fired"]), len(flat))

    a = sum(1 for r in rose if r["fired"])
    b = len(rose) - a
    c = sum(1 for r in flat if r["fired"])
    d = len(flat) - c
    try:
        _odds, fisher_p = stats.fisher_exact([[a, b], [c, d]], alternative="greater")
    except ValueError:
        fisher_p = float("nan")

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n": n, "pool": raw["pool"],
        "silence": {"flagged": flagged, "rate": overall[0],
                    "ci": [overall[1], overall[2]],
                    "p_vs_0.05": float(stats.binomtest(flagged, n, 0.05).pvalue)
                    if n else float("nan")},
        "agreement": {
            "decided": len(decided),
            "ruff_rose": len(rose), "ruff_flat": len(flat),
            "fires_on_rose": {"k": a, "n": len(rose), "point": on_rose[0],
                              "ci": [on_rose[1], on_rose[2]],
                              "p_vs_0.50": float(stats.binomtest(a, len(rose), 0.50).pvalue)
                              if rose else float("nan")},
            "fires_on_flat": {"k": c, "n": len(flat), "point": on_flat[0],
                              "ci": [on_flat[1], on_flat[2]]},
            "fisher_p_one_sided": float(fisher_p),
        },
        "by_repo": {
            repo: {
                "n": sum(1 for r in rows if r["repo"] == repo),
                "flagged": sum(1 for r in rows if r["repo"] == repo and r["fired"]),
            }
            for repo in sorted({r["repo"] for r in rows})
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    if args.show:
        d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        s, ag = d["silence"], d["agreement"]
        print(f"commits {d['n']} of a pool of {d['pool']}")
        print(f"C1 flag rate     {s['rate']:.3f} [{s['ci'][0]:.3f}, {s['ci'][1]:.3f}]"
              f"   p vs 0.05 = {s['p_vs_0.05']:.3g}")
        fr, ff = ag["fires_on_rose"], ag["fires_on_flat"]
        print(f"C2 on ruff-rose  {fr['point']:.3f} [{fr['ci'][0]:.3f}, {fr['ci'][1]:.3f}]"
              f"   {fr['k']}/{fr['n']}   p vs 0.50 = {fr['p_vs_0.50']:.3g}")
        print(f"C3 on ruff-flat  {ff['point']:.3f} [{ff['ci'][0]:.3f}, {ff['ci'][1]:.3f}]"
              f"   {ff['k']}/{ff['n']}   Fisher p = {ag['fisher_p_one_sided']:.3g}")
        print(f"   decidable by ruff: {ag['decided']}  "
              f"(rose {ag['ruff_rose']}, flat {ag['ruff_flat']})")
        return 0

    raw = run()
    out = summarise(raw)
    out["rows"] = raw["rows"]
    ARTIFACT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
