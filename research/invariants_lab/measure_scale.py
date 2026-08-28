"""X4: measure the scale detector against injected positives it did not design.

The positive class is **generated**, not found, so its size is chosen here rather than by
the corpus: `PREREGISTRATION.md` §5 asks for at least 250 injections, which clears the 229
that a 0.95-versus-0.90 comparison needs.

| gate | threshold | why it is higher than the others |
|---|---|---|
| **X4-S1** recall on quadratics | > 0.90 | a missed quadratic is the entire failure this exists to prevent |
| **X4-S2** false positives on linear code | ≤ 0.05 | matched in length and shape, so it cannot win by responding to size |
| **X4-S3** the canary discriminates | every matched pair | at 10x the quadratic exceeds its envelope and the linear one does not |
| **X4-S4** no declared axis, no finding | contract | silence is the default, not an achievement |

## The positives are generated from templates, and the templates are the answer key

Each is a shape that is quadratic **in the declared axis** by construction -- a nested walk,
a membership test against a list inside a loop over it, an accumulating concatenation. The
negatives are the same shapes made linear: a set instead of a list, one loop instead of two,
an append instead of a concatenation. Matched in length and in the identifiers they use, so
a detector cannot score by noticing that positives are longer or busier.

    python research/invariants_lab/measure_scale.py
    python research/invariants_lab/measure_scale.py --print
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from scipy import stats
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scale as S  # noqa: E402
from corpusio import progress  # noqa: E402

ARTIFACT = Path(__file__).with_name("scale_x4.json")
SEED = 20260827
N_EACH = 260          # PREREGISTRATION.md section 5 asks for >= 250
N_CANARY = 40         # >= 30
ALPHA = 0.05

NL = "\n"


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = proportion_confint(k, n, alpha=ALPHA, method="wilson")
    return (k / n, float(lo), float(hi))


# --------------------------------------------------------------------------
# the templates
# --------------------------------------------------------------------------


QUADRATIC = [
    # nested walk over the same axis
    lambda axis, i: (
        f"def scan_{i}({axis}, seen):{NL}"
        f"    out = []{NL}"
        f"    for a in {axis}:{NL}"
        f"        for b in {axis}:{NL}"
        f"            if a != b:{NL}"
        f"                out.append((a, b)){NL}"
        f"    return out{NL}"),
    # membership against the axis, inside a loop over the axis
    lambda axis, i: (
        f"def dedupe_{i}({axis}):{NL}"
        f"    out = []{NL}"
        f"    for item in {axis}:{NL}"
        f"        if item in {axis}:{NL}"
        f"            out.append(item){NL}"
        f"    return out{NL}"),
    # materialise the whole axis
    lambda axis, i: (
        f"def load_{i}({axis}):{NL}"
        f"    rows = list({axis}){NL}"
        f"    total = 0{NL}"
        f"    for row in rows:{NL}"
        f"        total += 1{NL}"
        f"    return total{NL}"),
    # slurp a stream that is the axis
    lambda axis, i: (
        f"def ingest_{i}({axis}):{NL}"
        f"    lines = {axis}.readlines(){NL}"
        f"    count = 0{NL}"
        f"    for line in lines:{NL}"
        f"        count += 1{NL}"
        f"    return count{NL}"),
]

LINEAR = [
    # one walk, a set for the lookup
    lambda axis, i: (
        f"def scan_{i}({axis}, seen):{NL}"
        f"    out = []{NL}"
        f"    known = set(seen){NL}"
        f"    for a in {axis}:{NL}"
        f"        if a in known:{NL}"
        f"            out.append(a){NL}"
        f"    return out{NL}"),
    lambda axis, i: (
        f"def dedupe_{i}({axis}):{NL}"
        f"    out = []{NL}"
        f"    known = set(){NL}"
        f"    for item in {axis}:{NL}"
        f"        if item not in known:{NL}"
        f"            known.add(item){NL}"
        f"            out.append(item){NL}"
        f"    return out{NL}"),
    lambda axis, i: (
        f"def load_{i}({axis}):{NL}"
        f"    total = 0{NL}"
        f"    for row in {axis}:{NL}"
        f"        total += 1{NL}"
        f"    return total{NL}"),
    lambda axis, i: (
        f"def ingest_{i}({axis}):{NL}"
        f"    count = 0{NL}"
        f"    for line in {axis}:{NL}"
        f"        count += 1{NL}"
        f"    return count{NL}"),
]

AXIS_NAMES = ["records", "rows", "events", "documents", "samples", "frames"]


def declaration_for(axis: str) -> dict:
    return {"axes": [{"name": axis, "unit": "records",
                      "start": 1_000, "target": 50_000_000}]}


def note_for(axis: str) -> dict:
    return {"id": "i-scale", "message": "this grows quadratically along a declared axis",
            "declaration": declaration_for(axis)}


# --------------------------------------------------------------------------
# the canary's runnable pairs
# --------------------------------------------------------------------------


def quadratic_run(n: int) -> int:
    data = list(range(n))
    hits = 0
    for a in data:
        if a in data:            # O(n) inside O(n)
            hits += 1
    return hits


def linear_run(n: int) -> int:
    data = list(range(n))
    known = set(data)
    hits = 0
    for a in data:
        if a in known:
            hits += 1
    return hits


def quadratic_memory(n: int) -> int:
    """Genuinely quadratic in total allocation: row `i` is `i` long.

    The first version used `i % 50 + 1`, which is bounded and therefore linear -- the
    memory arm had no valid positive in it and measured nothing. Reported as a
    template bug rather than as a result, and fixed.
    """
    rows = []
    for i in range(n):
        rows.append([0] * i)
    return len(rows)


def linear_memory(n: int) -> int:
    total = 0
    for i in range(n):
        total += i % 50 + 1
    return total


# --------------------------------------------------------------------------


def corpus_silence(sample: int = 1000) -> dict:
    """Exploratory, non-gating: how often does it fire on real commits?

    S1 and S2 are measured against templates written alongside the detector, so their
    perfection is close to tautological and says nothing about a real repository. The
    question a reader actually has is the one that killed the other two mechanisms:
    **with a plausible axis declared, how often does this fire on ordinary work?**

    The axis is not invented. For each commit it is the identifier most often iterated
    over in the files that commit changed -- which is what a project declaring its
    hottest collection would name. Choosing the *most-iterated* name is deliberately
    adversarial: it is the axis most likely to appear in a nested loop.
    """
    import ast as _ast
    import json as _json
    from collections import Counter

    from corpora import dev_repos
    from corpusio import BlobReader

    census = _json.loads(
        (Path(__file__).with_name("corpus_census.json")).read_text(encoding="utf-8"))
    pool = [(r["repo"], e) for r in census["repos"]
            for e in (r.get("eligible", []) + r.get("silent", []))]
    pool.sort(key=lambda x: (x[0], x[1]["sha"]))
    rng = random.Random(SEED)
    chosen = rng.sample(pool, sample) if len(pool) > sample else pool

    repos = {r.name: r for r in dev_repos()}
    readers: dict[str, object] = {}
    fired = considered = no_axis = 0
    try:
        for i, (repo_name, entry) in enumerate(chosen):
            if i % 200 == 0:
                progress(f"  silence {i}/{len(chosen)}")
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

            names: Counter[str] = Counter()
            for src in after.values():
                try:
                    tree = _ast.parse(src)
                except (SyntaxError, ValueError, RecursionError):
                    continue
                for node in _ast.walk(tree):
                    if isinstance(node, (_ast.For, _ast.AsyncFor)):
                        name = S._iterated_name(node.iter)
                        if name:
                            names[name] += 1
            if not names:
                no_axis += 1
                continue
            axis = names.most_common(1)[0][0]
            considered += 1
            if S.checker(before, after, note_for(axis)):
                fired += 1
    finally:
        for r in readers.values():
            r.close()

    point, lo, hi = wilson(fired, considered)
    return {"considered": considered, "fired": fired, "no_axis_present": no_axis,
            "rate": point, "ci": [lo, hi],
            "p_vs_0.05": float(stats.binomtest(fired, considered, 0.05).pvalue)
            if considered else float("nan")}


def run() -> dict:
    rng = random.Random(SEED)

    positives = []
    for i in range(N_EACH):
        axis = rng.choice(AXIS_NAMES)
        template = QUADRATIC[i % len(QUADRATIC)]
        positives.append((axis, template(axis, i), i % len(QUADRATIC)))
    negatives = []
    for i in range(N_EACH):
        axis = rng.choice(AXIS_NAMES)
        template = LINEAR[i % len(LINEAR)]
        negatives.append((axis, template(axis, i), i % len(LINEAR)))

    progress(f"{len(positives)} quadratics, {len(negatives)} matched linear")

    def fires(axis: str, src: str) -> bool:
        return bool(S.checker({}, {"m.py": src}, note_for(axis)))

    caught = [(a, s, t) for a, s, t in positives if fires(a, s)]
    tripped = [(a, s, t) for a, s, t in negatives if fires(a, s)]

    by_template = {}
    for idx in range(len(QUADRATIC)):
        hit = sum(1 for _a, _s, t in caught if t == idx)
        total = sum(1 for _a, _s, t in positives if t == idx)
        fp = sum(1 for _a, _s, t in tripped if t == idx)
        by_template[str(idx)] = {"caught": hit, "n": total, "false_positives": fp}

    # S4: with no declared axis the mechanism must report nothing at all.
    silent_without_declaration = all(
        not S.checker({}, {"m.py": src}, {"id": "i-scale", "declaration": {}})
        for _a, src, _t in positives
    )

    progress("canary")
    pairs = []
    for i in range(N_CANARY):
        n = 150 + (i % 7) * 25
        q = S.canary(quadratic_run, n=n)
        lin = S.canary(linear_run, n=n)
        pairs.append({"n": n,
                      "quadratic_flagged": (not q.ok) and not q.abstained,
                      "linear_flagged": (not lin.ok) and not lin.abstained,
                      "abstained": q.abstained or lin.abstained,
                      "quadratic_time_factor": q.time_factor,
                      "linear_time_factor": lin.time_factor})

    mem = S.canary(quadratic_memory, n=400)
    mem_lin = S.canary(linear_memory, n=400)

    progress("corpus silence (exploratory)")
    silence = corpus_silence()

    return {
        "corpus_silence": silence,
        "positives": len(positives), "negatives": len(negatives),
        "caught": len(caught), "tripped": len(tripped),
        "by_template": by_template,
        "silent_without_declaration": silent_without_declaration,
        "canary_pairs": pairs,
        "memory_canary": {"quadratic_flagged": not mem.ok,
                          "quadratic_detail": mem.detail,
                          "linear_flagged": not mem_lin.ok,
                          "linear_detail": mem_lin.detail},
    }


def summarise(raw: dict) -> dict:
    recall = wilson(raw["caught"], raw["positives"])
    fp = wilson(raw["tripped"], raw["negatives"])
    pairs = raw["canary_pairs"]
    decidable = [p for p in pairs if not p.get("abstained")]
    discriminating = sum(1 for p in decidable
                         if p["quadratic_flagged"] and not p["linear_flagged"])
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recall": {"k": raw["caught"], "n": raw["positives"], "point": recall[0],
                   "ci": [recall[1], recall[2]],
                   "p_vs_0.90": float(stats.binomtest(raw["caught"], raw["positives"],
                                                      0.90).pvalue)},
        "false_positives": {"k": raw["tripped"], "n": raw["negatives"], "point": fp[0],
                            "ci": [fp[1], fp[2]],
                            "p_vs_0.05": float(stats.binomtest(raw["tripped"],
                                                               raw["negatives"],
                                                               0.05).pvalue)},
        "by_template": raw["by_template"],
        "silent_without_declaration": raw["silent_without_declaration"],
        "canary": {"pairs": len(pairs), "decidable": len(decidable),
                   "abstained": len(pairs) - len(decidable),
                   "discriminating": discriminating,
                   "quadratic_flagged": sum(1 for p in pairs if p["quadratic_flagged"]),
                   "linear_flagged": sum(1 for p in pairs if p["linear_flagged"])},
        "memory_canary": raw["memory_canary"],
        "corpus_silence": raw.get("corpus_silence"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    if args.show:
        d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        r, f, c = d["recall"], d["false_positives"], d["canary"]
        print(f"S1 recall on quadratics   {r['point']:.3f} "
              f"[{r['ci'][0]:.3f}, {r['ci'][1]:.3f}]   {r['k']}/{r['n']}   "
              f"p vs 0.90 = {r['p_vs_0.90']:.3g}")
        print(f"S2 false positives        {f['point']:.3f} "
              f"[{f['ci'][0]:.3f}, {f['ci'][1]:.3f}]   {f['k']}/{f['n']}   "
              f"p vs 0.05 = {f['p_vs_0.05']:.3g}")
        print(f"S3 canary discriminates   {c['discriminating']}/{c.get('decidable', c['pairs'])}"
              f" decidable of {c['pairs']} pairs   "
              f"(abstained {c.get('abstained', 0)}; quadratic flagged "
              f"{c['quadratic_flagged']}, linear flagged {c['linear_flagged']})")
        print(f"S4 silent with no axis    {d['silent_without_declaration']}")
        print()
        for idx, v in d["by_template"].items():
            print(f"  template {idx}: caught {v['caught']}/{v['n']}, "
                  f"false positives {v['false_positives']}")
        cs = d.get("corpus_silence") or {}
        if cs.get("considered"):
            print()
            print("EXPLORATORY, non-gating -- flag rate on real commits with a declared axis:")
            print(f"  {cs['fired']}/{cs['considered']} = {cs['rate']:.3f} "
                  f"[{cs['ci'][0]:.3f}, {cs['ci'][1]:.3f}]   "
                  f"p vs 0.05 = {cs['p_vs_0.05']:.3g}   "
                  f"({cs['no_axis_present']} commits had no loop to declare an axis from)")
        m = d["memory_canary"]
        print(f"  memory canary: quadratic flagged {m['quadratic_flagged']}, "
              f"linear flagged {m['linear_flagged']}")
        return 0

    raw = run()
    out = summarise(raw)
    out["raw_canary_pairs"] = raw["canary_pairs"]
    ARTIFACT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
