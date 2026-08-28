"""D4: enumerate the shapes a symbol uses to survive a move, from the corpus.

`research/BLAST_RADIUS_PRECISION.md` names two shapes I2 did not solve and adds that
there is *"no reason to believe the list ends"*. Guessing the third from this
repository's habits is how the list stayed short the first time, so the shapes are
read off eight repositories with between 270 and 3,638 contributors instead.

The method is a disagreement hunt, and it needs no labelling:

* the **checker** says a symbol was removed from a file, after its own facade
  resolution has run;
* the **answer key** says the name still resolves there -- it is defined, or bound
  by an import, or bound by an assignment;
* every such disagreement is a facade the checker cannot see, and the AST node that
  binds the name is the shape.

Nothing here is a measurement of the mechanism. It is defect discovery, which is
what has to happen *before* D5 is allowed to run at all.

    python research/invariants_lab/facade_shapes.py
    python research/invariants_lab/facade_shapes.py --print
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpora import dev_repos
from corpusio import BlobReader, progress  # noqa: E402
import mutate  # noqa: E402
import sigscan  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CENSUS = Path(__file__).with_name("corpus_census.json")
ARTIFACT = Path(__file__).with_name("facade_shapes.json")

_spec = importlib.util.spec_from_file_location(
    "_nt_blast_radius_d4", ROOT / "research" / "invariants_lab" / "blast_radius_deleted.py"
)
br = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = br
_spec.loader.exec_module(br)


def binding_shape(source: str, name: str) -> tuple[str, str] | None:
    """How *source* binds *name* at module level: (shape id, the line that does it).

    The shape id is what D4's fix has to learn to recognise, so it is derived from
    the AST node rather than from a regex over the text.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    lines = source.splitlines()

    def line_of(node: ast.AST) -> str:
        i = getattr(node, "lineno", 0) - 1
        return lines[i].strip()[:160] if 0 <= i < len(lines) else ""

    stack: list[ast.AST] = list(tree.body)
    guarded = False
    for node in stack:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    rel = "relative" if node.level else "absolute"
                    aliased = "aliased" if alias.asname else "plain"
                    where = "guarded" if guarded else "top"
                    return (f"from-import/{rel}/{aliased}/{where}", line_of(node))
                if alias.name == "*":
                    return ("star-import", line_of(node))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == name:
                    return ("import-module", line_of(node))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names = [
                    n.id for n in ast.walk(target) if isinstance(n, ast.Name)
                ]
                if name not in names:
                    continue
                v = node.value
                if isinstance(v, ast.Attribute):
                    same = "same-name" if v.attr == name else "renamed"
                    return (f"assign-attribute/{same}", line_of(node))
                if isinstance(v, ast.Name):
                    same = "same-name" if v.id == name else "renamed"
                    return (f"assign-name/{same}", line_of(node))
                if isinstance(v, ast.Call):
                    return ("assign-call", line_of(node))
                if isinstance(target, (ast.Tuple, ast.List)):
                    return ("assign-tuple", line_of(node))
                return (f"assign-{type(v).__name__.lower()}", line_of(node))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return ("annassign", line_of(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return ("redefined", line_of(node))
        elif isinstance(node, (ast.If, ast.Try)):
            guarded = True
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
    return None


def reference_truth(after: dict, change, ref, blobs, sha: str) -> bool | None:
    """Is this reported reference really stale? None when it cannot be decided.

    The removal hunt above covers only one half of the census's "moved and still
    resolves" class -- the half where the *definition* survives. The other half is a
    *reference* that survives: the checker names a call site as unhandled, and the
    call binds perfectly well against the new signature. This is the same mechanical
    bar C2 used to build the positive class, pointed at the checker's own output.
    """
    if change.reason != "signature":
        return None
    new_def = sigscan.scan_defs(after.get(change.path, "")).get(change.qualname)
    if new_def is None or new_def.kind != "func":
        return None
    src = after.get(ref.path)
    if src is None:
        src = blobs.read(sha, ref.path)
    if src is None:
        return None
    short = change.qualname.rsplit(".", 1)[-1]
    sites = [s for s in sigscan.scan_refs(src, {short})
             if s.is_call and s.lineno == ref.lineno]
    if not sites:
        return None
    # `call_fails` returns None when the call shape is not statically knowable --
    # `f(*args)`, `f(**kw)`. Treating that as "binds fine" would score the checker
    # against a verdict nobody reached; undecidable is not the same as false.
    decidable = [s for s in sites if not s.has_star]
    if not decidable:
        return None
    return any(mutate.call_fails(s, new_def) is not None for s in decidable)


def reference_shape(source: str, name: str, lineno: int) -> str:
    """The AST form of the reference at *lineno*, so a fix has something to target."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return "unparsable"
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) != lineno:
            continue
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == name:
                return "call/attribute"
            if isinstance(fn, ast.Name) and fn.id == name:
                return "call/name"
        if isinstance(node, ast.ImportFrom):
            return "import/from"
        if isinstance(node, ast.Import):
            return "import/plain"
    return "other"


def hunt(limit_per_repo: int | None = None) -> dict:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    by_name = {r["repo"]: r for r in census["repos"]}
    shapes: Counter[str] = Counter()
    examples: dict[str, list[dict]] = {}
    ref_shapes: Counter[str] = Counter()
    ref_examples: dict[str, list[dict]] = {}
    checked = 0
    removals = 0
    refs_reported = 0
    refs_high = 0
    refs_decided = 0
    refs_false = 0

    for repo in dev_repos():
        entries = by_name.get(repo.name, {}).get("eligible", [])
        if limit_per_repo:
            entries = entries[:limit_per_repo]
        progress(f"== {repo.name} ({len(entries)} commits)")
        with BlobReader(repo) as blobs:
            for n, e in enumerate(entries):
                if n % 100 == 0:
                    progress(f"  {n}/{len(entries)}")
                paths = sorted({d["path"] for d in e["deltas"]})
                before = {}
                after = {}
                for p in paths:
                    old = blobs.read(e["parent"], p)
                    new = blobs.read(e["sha"], p)
                    if old is None or new is None:
                        continue
                    before[p], after[p] = old, new
                if not before:
                    continue
                checked += 1
                verdict = br.check_sources(before, after, scan=after)

                by_qual = {c.qualname: c for c in verdict.contract_changes}
                for qualname, refs in verdict.unhandled.items():
                    change = by_qual.get(qualname)
                    if change is None:
                        continue
                    for ref in refs:
                        refs_reported += 1
                        # A finding is what the checker REPORTS AS A PROBLEM.
                        # Low-confidence references become notes, not problems
                        # (`_confidence`: a short or ambiguous name, or too many
                        # hits), and counting them as findings would measure the
                        # checker's diagnostics rather than its verdict.
                        if ref.confidence != "high":
                            continue
                        refs_high += 1
                        truth = reference_truth(after, change, ref, blobs, e["sha"])
                        if truth is None:
                            continue
                        refs_decided += 1
                        if truth:
                            continue          # a genuinely stale caller
                        refs_false += 1
                        src = after.get(ref.path) or blobs.read(e["sha"], ref.path) or ""
                        shape = reference_shape(
                            src, qualname.rsplit(".", 1)[-1], ref.lineno)
                        ref_shapes[shape] += 1
                        ref_examples.setdefault(shape, [])
                        if len(ref_examples[shape]) < 4:
                            ref_examples[shape].append({
                                "repo": repo.name, "sha": e["sha"][:9],
                                "path": ref.path, "lineno": ref.lineno,
                                "symbol": qualname,
                                "line": (src.splitlines()[ref.lineno - 1].strip()[:150]
                                         if 0 < ref.lineno <= len(src.splitlines()) else ""),
                            })

                for change in verdict.contract_changes:
                    # `kind` changes are excluded: `def` becoming `async def` keeps
                    # the name and still breaks every caller that does not await it,
                    # so "the name resolves" is not evidence the finding is false.
                    if change.reason != "removed":
                        continue
                    removals += 1
                    src = after.get(change.path, "")
                    top = change.qualname.split(".")[0]
                    if "." in change.qualname:
                        continue  # a method, judged with its class
                    still = (
                        top in sigscan.scan_defs(src)
                        or top in sigscan.import_bindings(src)
                    )
                    if not still:
                        continue
                    shape = binding_shape(src, top)
                    key = shape[0] if shape else "unknown"
                    shapes[key] += 1
                    examples.setdefault(key, [])
                    if len(examples[key]) < 4:
                        examples[key].append(
                            {
                                "repo": repo.name,
                                "sha": e["sha"][:9],
                                "path": change.path,
                                "symbol": top,
                                "line": shape[1] if shape else "",
                            }
                        )

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commits_checked": checked,
        "removals_reported": removals,
        "false_removals": sum(shapes.values()),
        "shapes": dict(shapes.most_common()),
        "examples": examples,
        "references_reported": refs_reported,
        "references_high_confidence": refs_high,
        "references_decidable": refs_decided,
        "references_false": refs_false,
        "reference_shapes": dict(ref_shapes.most_common()),
        "reference_examples": ref_examples,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    if args.show:
        d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        print(f"commits checked   {d['commits_checked']}")
        print(f"removals reported {d['removals_reported']}")
        print(f"of those, FALSE   {d['false_removals']} "
              f"({100*d['false_removals']/max(d['removals_reported'],1):.1f}%)")
        print()
        print(f"references reported  {d['references_reported']}")
        print(f"  high confidence    {d.get('references_high_confidence', '?')} "
              f"(the rest become notes, not problems)")
        print(f"  decidable          {d['references_decidable']}")
        print(f"  of those, FALSE    {d['references_false']} "
              f"({100*d['references_false']/max(d['references_decidable'],1):.1f}%)")
        for shape, n in d.get("reference_shapes", {}).items():
            print(f"  ref {shape:<36} {n:>5}")
            for ex in d["reference_examples"].get(shape, [])[:2]:
                print(f"      {ex['repo']}:{ex['sha']} {ex['path']}:{ex['lineno']} "
                      f"::{ex['symbol']}")
                print(f"        {ex['line']}")
        print()
        for shape, n in d["shapes"].items():
            print(f"  {shape:<40} {n:>5}")
            for ex in d["examples"].get(shape, [])[:2]:
                print(f"      {ex['repo']}:{ex['sha']} {ex['path']}::{ex['symbol']}")
                print(f"        {ex['line']}")
        return 0

    out = hunt(limit_per_repo=args.limit)
    ARTIFACT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}: {out['false_removals']} false removals, "
             f"{len(out['shapes'])} shapes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
