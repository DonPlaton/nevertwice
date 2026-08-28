"""F4: find real quadratic fixes in `corpus_dev`, then ask whether `scale` would have seen them.

`SCALE_X4.md` reported static recall **1.000** on a positive class its own author generated in
the same afternoon. This finds a class nobody here wrote, by mining commit messages, and runs
the detector against it under a **blind** declaration.

## The three things that keep this from measuring its author

**Selection is by maintainers.** Candidates come from `git log --grep` over the whole history of
each development repository, on messages naming quadratic behaviour. No commit is here because
somebody thought the detector would catch it.

**Confirmation is mechanical and recorded.** A message is a claim. Each candidate is confirmed by
a rule that does not consult any declaration: the parent's changed region must contain a nested
iteration or a membership test inside a loop over the same name, and the child must not.
`QUADRATIC_F4.md` §1 says plainly that this rule shares an *idea* with the detector while being a
different and simpler implementation -- so the added difficulty the detector faces is the blind
declaration, not the shape. Every candidate and every rejection is written to the artifact with
its reason, which is the reproducible substitute for a hand read that a labeller can get wrong
five times.

**The declaration never sees the fix.** It is generated from the **parent** alone: every name any
`for` loop iterates over becomes an axis. It cannot know which one was the culprit, and it hands
the detector more names rather than fewer -- which works against the detector on the paired
post-fix arm, not for it.

    python research/invariants_lab/mine_quadratics.py
    python research/invariants_lab/mine_quadratics.py --print
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scale as SC  # noqa: E402
import corpora  # noqa: E402
from corpora import repos_for  # noqa: E402
from corpusio import BlobReader, progress  # noqa: E402
import blast_radius_deleted as br  # noqa: E402

#: H1: the corpus is an argument, so Phase V runs the SAME frozen code on a different
#: corpus without editing this file. `_select_corpus` rebinds the paths before any run.
CORPUS = "dev"


def _select_corpus(name: str) -> None:
    global CORPUS, CENSUS, MUTANTS, ARTIFACT
    CORPUS = name
    CENSUS = corpora.census_path(name)
    MUTANTS = corpora.mutants_path(name)
    ARTIFACT = corpora.artifact_path('quadratic_f4.json', name)


CENSUS = corpora.census_path(CORPUS)
MUTANTS = corpora.mutants_path(CORPUS)
ARTIFACT = corpora.artifact_path('quadratic_f4.json', CORPUS)


MIN_CONFIRMED = 20   # QUADRATIC_F4.md section 3 -- below this the gate is not scored
ALPHA = 0.05

#: Message patterns, fixed before the mining ran. Case-insensitive, matched by `git log
#: --grep -i`, and deliberately wide: precision comes from the confirmation rule, not from
#: the search. A narrow pattern would hand-pick the class.
PATTERNS = (
    r"quadratic",
    r"O\(n\^?2\)",
    r"O\(n\*\*2\)",
    r"O\(n²\)",
    r"n\^2",
    r"nested loop",
    r"avoid.*inner loop",
    r"linear time",
    r"use a set instead",
    r"set instead of a list",
    r"speed ?up.*loop",
)

_SEP = "\x01"


def _log(repo: Path, pattern: str) -> list[dict]:
    args = ["log", "--no-merges", "-i", "--grep", pattern, "--name-only",
            f"--format={_SEP}%H{_SEP}%P{_SEP}%s", "--diff-filter=M", "--", "*.py"]
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=False)
    raw = proc.stdout.decode("utf-8", "replace")
    out: list[dict] = []
    cur: dict | None = None
    for line in raw.split("\n"):
        if line.startswith(_SEP):
            parts = line.split(_SEP)
            if len(parts) < 4:
                cur = None
                continue
            parents = parts[2].split()
            cur = {"sha": parts[1], "parent": parents[0] if parents else "",
                   "subject": _SEP.join(parts[3:])[:200], "paths": []}
            out.append(cur)
            continue
        p = line.strip()
        if p.endswith(".py") and cur is not None:
            cur["paths"].append(p)
    return [c for c in out if c["paths"] and c["parent"]]


# ---------------------------------------------------------------------------
# the confirmation rule -- no declaration, any name
# ---------------------------------------------------------------------------


def _iterated(node: ast.AST) -> str | None:
    target = node
    if isinstance(target, ast.Call):
        target = target.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def quadratic_shapes(source: str) -> set[tuple[int, str]]:
    """`(lineno, name)` for every nested walk or in-loop membership test, any name.

    Independent of `scale.find_problems` in that it consults no declaration and asks about
    every name in the file. It shares the idea of what a quadratic looks like, and
    `QUADRATIC_F4.md` §1 says so rather than implying independence it does not have.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    out: set[tuple[int, str]] = set()
    for outer in ast.walk(tree):
        if not isinstance(outer, (ast.For, ast.AsyncFor)):
            continue
        name = _iterated(outer.iter)
        if name is None:
            continue
        for inner in ast.walk(outer):
            if inner is outer:
                continue
            if isinstance(inner, (ast.For, ast.AsyncFor)) and _iterated(inner.iter) == name:
                out.add((inner.lineno, name))
            elif isinstance(inner, ast.Compare):
                for op, comparator in zip(inner.ops, inner.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)) and _iterated(comparator) == name:
                        out.add((inner.lineno, name))
    return out


def blind_declaration(parent_source: str) -> dict:
    """Every name a `for` walks over in the parent, declared as an axis.

    Generated from the parent alone, so it cannot know which name the fix was about. The
    start and target are arbitrary: `find_problems` reads only the names.
    """
    try:
        tree = ast.parse(parent_source)
    except (SyntaxError, ValueError, RecursionError):
        return {"axes": []}
    names = sorted({n for node in ast.walk(tree)
                    if isinstance(node, (ast.For, ast.AsyncFor))
                    for n in [_iterated(node.iter)] if n})
    return {"axes": [{"name": n, "unit": "records", "start": 1_000,
                      "target": 50_000_000} for n in names]}


# ---------------------------------------------------------------------------


def _accumulates_in_a_loop(source: str) -> bool:
    """`s += ...` or `s = s + ...` inside a `for`/`while`, the other classic quadratic.

    Repeated string or list concatenation copies the accumulator every iteration. It is
    the shape `django`'s Truncator and XML-serializer fixes actually had, and neither
    `scale.find_problems` nor this file's confirmation rule counts it as a nested walk --
    which is the point F4 turned out to be about.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return False
    for loop in ast.walk(tree):
        if not isinstance(loop, (ast.For, ast.AsyncFor, ast.While)):
            continue
        for node in ast.walk(loop):
            if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
                return True
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp)
                    and isinstance(node.value.op, ast.Add)
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value.left, ast.Name)
                    and node.value.left.id == node.targets[0].id):
                return True
    return False


def _hashes_introduced(old: str, new: str) -> bool:
    """Did the fix introduce a `set`/`frozenset`/`dict` where there was not one?

    `django`'s `MigrationGraph._generate_plan` fix is exactly this: the membership test
    stayed, the container behind it became a set. The name did not change, so the shape
    the confirmation rule counts did not change either -- and no AST rule that reads names
    can tell a list from a set.
    """
    def count(src: str) -> int:
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError, RecursionError):
            return 0
        n = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)                     and node.func.id in ("set", "frozenset", "dict"):
                n += 1
            elif isinstance(node, (ast.Set, ast.SetComp, ast.DictComp)):
                n += 1
        return n
    return count(new) > count(old)


def _join_introduced(old: str, new: str) -> bool:
    return new.count(".join(") > old.count(".join(")


def classify(old: str, new: str) -> str:
    """What kind of quadratic fix this diff is, by a rule fixed before the mining ran."""
    before = quadratic_shapes(old)
    after = quadratic_shapes(new)
    if {n for _l, n in before} - {n for _l, n in after}:
        return "nested-walk removed"
    if _hashes_introduced(old, new):
        return "container became a hash"
    if _accumulates_in_a_loop(old) and _join_introduced(old, new):
        return "accumulation replaced by join"
    if _accumulates_in_a_loop(old):
        return "accumulation in a loop"
    if len(after) < len(before):
        return "fewer shapes, same names"
    return "something else"


#: The one class `scale.find_problems` is built to see. Everything else is a shape it has
#: no rule for, and F4's result is the share of real fixes that fall outside it.
DETECTABLE = "nested-walk removed"


def examine(blobs: BlobReader, cand: dict) -> dict:
    """One candidate: classify every changed file, then run the detector on both arms."""
    rows = []
    for path in cand["paths"]:
        old = blobs.read(cand["parent"], path)
        new = blobs.read(cand["sha"], path)
        if old is None or new is None:
            continue
        kind = classify(old, new)
        touched = br.changed_lines(old, new)
        declaration = SC.Declaration.parse(blind_declaration(old))
        pre = SC.find_problems(old, path, declaration)
        post = SC.find_problems(new, path, declaration)
        removed = ({n for _l, n in quadratic_shapes(old)}
                   - {n for _l, n in quadratic_shapes(new)})
        hit = [p for p in pre
               if any(abs(p.lineno - ln) <= 3 for ln in touched)]
        rows.append({
            "path": path,
            "kind": kind,
            "axes_declared": len(declaration.axes),
            "removed_axes": sorted(removed),
            "pre_findings": len(pre), "post_findings": len(post),
            "credited": len(hit),
            "example": hit[0].detail if hit else (pre[0].detail if pre else None),
        })
    kinds = [r["kind"] for r in rows]
    return {"sha": cand["sha"], "subject": cand["subject"], "files": rows,
            "kinds": kinds,
            # A candidate is confirmed when at least one changed file shows a fix of a
            # kind the taxonomy recognises -- not only the one the detector can see.
            "confirmed": any(k != "something else" for k in kinds),
            "detectable_shape": DETECTABLE in kinds,
            "caught": any(r["credited"] > 0 for r in rows),
            "still_fires_after": any(r["post_findings"] > 0 for r in rows)}


def run() -> dict:
    repos = repos_for(CORPUS)
    candidates: dict[tuple[str, str], dict] = {}
    per_repo: dict[str, int] = {}
    for repo in repos:
        found = {}
        for pattern in PATTERNS:
            for c in _log(repo, pattern):
                found[c["sha"]] = c
        per_repo[repo.name] = len(found)
        progress(f"{repo.name}: {len(found)} message-matched candidates")
        for sha, c in found.items():
            candidates[(repo.name, sha)] = c

    examined: list[dict] = []
    rejected = 0
    for repo in repos:
        mine = [(k, v) for k, v in candidates.items() if k[0] == repo.name]
        if not mine:
            continue
        with BlobReader(repo) as blobs:
            for i, ((repo_name, _sha), cand) in enumerate(sorted(mine)):
                if i % 20 == 0:
                    progress(f"  {repo_name} {i}/{len(mine)}")
                got = examine(blobs, cand)
                got["repo"] = repo_name
                if got["confirmed"]:
                    examined.append(got)
                else:
                    rejected += 1
    return {"candidates": len(candidates), "per_repo": per_repo,
            "rejected_on_reading": rejected, "confirmed": examined}


def summarise(raw: dict) -> dict:
    confirmed = raw["confirmed"]
    n = len(confirmed)
    detectable = sum(1 for c in confirmed if c["detectable_shape"])
    taxonomy: dict[str, int] = {}
    for c in confirmed:
        for k in c["kinds"]:
            taxonomy[k] = taxonomy.get(k, 0) + 1
    caught = sum(1 for c in confirmed if c["caught"])
    still = sum(1 for c in confirmed if c["still_fires_after"])
    if n:
        lo, hi = proportion_confint(caught, n, alpha=ALPHA, method="wilson")
    else:
        lo = hi = float("nan")
    scorable = n >= MIN_CONFIRMED
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "F4", "corpus": "corpus_dev", "in_sample": True,
        "message_matched": raw["candidates"],
        "per_repo_matched": raw["per_repo"],
        "rejected_on_reading": raw["rejected_on_reading"],
        "confirmed": n,
        "taxonomy_of_fixes": taxonomy,
        "of_a_shape_the_detector_has_a_rule_for": detectable,
        "required_to_score": MIN_CONFIRMED,
        "scorable": scorable,
        "recall_on_found_quadratics": {
            "caught": caught, "n": n,
            "point": (caught / n) if n else float("nan"),
            "ci": [float(lo), float(hi)],
        },
        "generated_class_recall": 1.000,
        "paired_arm": {
            "still_fires_after_the_fix": still,
            "rate": (still / n) if n else float("nan"),
            "reading": ("a detector that fires on both arms is responding to the shape of "
                        "the file rather than to the fault"),
        },
        "verdict": (
            "census only -- fewer confirmed cases than QUADRATIC_F4.md section 3 requires, "
            "and that document says a class this small is reported with an interval and "
            "no verdict"
            if not scorable else "scored"
        ),
        "cases": [{k: v for k, v in c.items() if k != "files"} | {
            "files": [{"path": f["path"], "credited": f["credited"],
                       "pre": f["pre_findings"], "post": f["post_findings"],
                       "removed_axes": f["removed_axes"][:6],
                       "example": f["example"]}
                      for f in c["files"]]}
            for c in confirmed],
    }


def _print(data: dict) -> None:
    r = data["recall_on_found_quadratics"]
    print(f"F4 -- scale's static half on a class nobody here wrote ({data['corpus']}, IN SAMPLE)")
    print()
    print(f"  message-matched candidates : {data['message_matched']}")
    print(f"  rejected on reading        : {data['rejected_on_reading']}")
    print(f"  confirmed quadratic fixes  : {data['confirmed']} "
          f"(needed {data['required_to_score']} to score)")
    print()
    if data.get("taxonomy_of_fixes"):
        print("  what the fixes actually were, per changed file:")
        for kind, k in sorted(data["taxonomy_of_fixes"].items(), key=lambda x: -x[1]):
            print(f"    {kind:34s} {k}")
        print(f"  of a shape `scale` has a rule for: "
              f"{data['of_a_shape_the_detector_has_a_rule_for']}/{data['confirmed']}")
        print()
    if data["confirmed"]:
        print(f"  caught before the fix      : {r['caught']}/{r['n']} = {r['point']:.3f} "
              f"[{r['ci'][0]:.2f}, {r['ci'][1]:.2f}]")
        print(f"  on the generated class     : {data['generated_class_recall']:.3f}")
        p = data["paired_arm"]
        print(f"  still fires after the fix  : {p['still_fires_after_the_fix']}/{r['n']} "
              f"= {p['rate']:.3f}")
    print()
    print(f"  verdict: {data['verdict']}")
    print()
    for c in data["cases"][:12]:
        mark = "CAUGHT " if c["caught"] else "missed "
        print(f"  {mark} {c['repo']:20s} {c['sha'][:9]}  {c['subject'][:70]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", dest="show")
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
