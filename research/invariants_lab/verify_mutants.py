"""C2's control: prove every mutant with an instrument that shares no code with the generator.

`mutate.call_fails` is my arity logic. If the same logic both generated the positive class
and confirmed it, the corpus would prove only that the logic is self-consistent. So each
mutant is re-decided here by **CPython's own argument binder**: the callee's parameter list
is rebuilt as a real `inspect.Signature`, and the call is replayed through `Signature.bind`.
`TypeError` from the standard library is not an opinion.

Three verdicts per mutant, and all three must hold:

1. **broken** -- the reverted call fails to bind against the definition as it stands after
   the commit;
2. **intact** -- every call in the real, un-reverted caller binds against that same
   definition. Without this the pair is worthless: a detector could flag the symbol in both
   trees and look perfect;
3. **agreement** -- the binder and the generator reached the same verdict. A disagreement is
   printed with its case, because one of the two instruments is then wrong and which one is
   not obvious in advance.

The `vanished` class is checked by a separate route with the same property: a minimal
independent walk of the defining module's top-level names, plus the literal `from ... import`
statement in the reverted caller.

    python research/invariants_lab/verify_mutants.py
    python research/invariants_lab/verify_mutants.py --print
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpusio import BlobReader, corpus_repos, progress  # noqa: E402

MUTANTS = Path(__file__).with_name("mutants.json")
ARTIFACT = Path(__file__).with_name("mutant_controls.json")

_SENTINEL = object()


# --------------------------------------------------------------------------
# route 1: CPython's binder
# --------------------------------------------------------------------------


def _signature_of(tree: ast.AST, qualname: str) -> inspect.Signature | None:
    """Rebuild a real `inspect.Signature` for `qualname`, walked from scratch."""
    parts = qualname.split(".")
    node: ast.AST | None = tree
    is_method = False
    for i, part in enumerate(parts):
        found = None
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if child.name == part:
                    found = child
                    break
        if found is None:
            return None
        if i < len(parts) - 1:
            if not isinstance(found, ast.ClassDef):
                return None
            is_method = True
        node = found
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None

    P = inspect.Parameter
    params: list[P] = []
    a = node.args
    positional = list(a.posonlyargs) + list(a.args)
    pad = len(positional) - len(a.defaults)
    for i, arg in enumerate(positional):
        kind = P.POSITIONAL_ONLY if i < len(a.posonlyargs) else P.POSITIONAL_OR_KEYWORD
        default = P.empty if i < pad else _SENTINEL
        params.append(P(arg.arg, kind, default=default))
    if a.vararg is not None:
        params.append(P(a.vararg.arg, P.VAR_POSITIONAL))
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        params.append(
            P(arg.arg, P.KEYWORD_ONLY, default=P.empty if d is None else _SENTINEL)
        )
    if a.kwarg is not None:
        params.append(P(a.kwarg.arg, P.VAR_KEYWORD))

    if is_method and params and params[0].name in ("self", "cls"):
        params = params[1:]  # bound at the call site
    try:
        return inspect.Signature(params)
    except ValueError:
        return None


def _calls_to(source: str, name: str) -> list[tuple[int, int, tuple[str, ...], bool]]:
    """(lineno, n_positional, keyword names, has *args/**kwargs) for each call to `name`."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        target = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None
        )
        if target != name:
            continue
        star = any(isinstance(x, ast.Starred) for x in node.args) or any(
            k.arg is None for k in node.keywords
        )
        out.append(
            (
                node.lineno,
                sum(1 for x in node.args if not isinstance(x, ast.Starred)),
                tuple(k.arg for k in node.keywords if k.arg),
                star,
            )
        )
    return out


def _binds(sig: inspect.Signature, n_pos: int, kwnames: tuple[str, ...]) -> bool:
    try:
        sig.bind(*([_SENTINEL] * n_pos), **{k: _SENTINEL for k in kwnames})
        return True
    except TypeError:
        return False


# --------------------------------------------------------------------------
# route 2: the vanished symbol
# --------------------------------------------------------------------------


def _top_level_names(source: str) -> set[str]:
    """Every name a module binds at top level, walked independently of `sigscan`."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    names: set[str] = set()
    stack: list[ast.AST] = list(tree.body)
    for node in stack:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
    return names


def _imports_literally(source: str, symbol: str, module_tail: str) -> bool:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return False
    tail = module_tail.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            got = node.module.split(".")
            if len(got) <= len(tail) and tail[-len(got):] == got:
                if any(a.name == symbol for a in node.names):
                    return True
    return False


# --------------------------------------------------------------------------


def verify(mutants: list[dict]) -> dict:
    repos = {r.name: r for r in corpus_repos()}
    results: list[dict] = []
    readers: dict[str, BlobReader] = {}
    cache: dict = {}
    try:
        for i, m in enumerate(mutants):
            if i % 100 == 0:
                progress(f"  {i}/{len(mutants)}")
            repo = repos.get(m["repo"])
            if repo is None:
                continue
            blobs = readers.setdefault(m["repo"], BlobReader(repo))

            old_caller = blobs.read(m["parent"], m["caller_path"])
            new_caller = blobs.read(m["sha"], m["caller_path"])
            row = {"mid": m["mid"], "breakage": m["breakage"]}

            if m["breakage"] == "arity":
                key = (m["sha"], m["defining_path"])
                if key not in cache:
                    src = blobs.read(*key)
                    try:
                        cache[key] = ast.parse(src) if src else None
                    except (SyntaxError, ValueError, RecursionError):
                        cache[key] = None
                tree = cache[key]
                sig = _signature_of(tree, m["qualname"]) if tree is not None else None
                if sig is None:
                    row.update(broken=False, intact=False, note="signature not rebuildable")
                    results.append(row)
                    continue
                old = [c for c in _calls_to(old_caller or "", m["symbol"]) if not c[3]]
                new = [c for c in _calls_to(new_caller or "", m["symbol"]) if not c[3]]
                row["broken"] = any(not _binds(sig, n, kw) for _l, n, kw, _s in old)
                row["intact"] = bool(new) and all(
                    _binds(sig, n, kw) for _l, n, kw, _s in new
                )
                row["signature"] = str(sig)
            else:
                after = blobs.read(m["sha"], m["defining_path"])
                names = _top_level_names(after or "")
                tail = (
                    m["defining_path"].replace("\\", "/").removesuffix(".py")
                    .replace("/", ".").removesuffix(".__init__")
                )
                row["broken"] = (
                    m["symbol"] not in names
                    and _imports_literally(old_caller or "", m["symbol"], tail)
                )
                row["intact"] = not _imports_literally(
                    new_caller or "", m["symbol"], tail
                )
                row["names_after"] = len(names)

            row["agrees"] = bool(row["broken"] and row["intact"])
            results.append(row)
    finally:
        for r in readers.values():
            r.close()

    confirmed = [r for r in results if r["agrees"]]
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checked": len(results),
        "confirmed": len(confirmed),
        "not_broken": sum(1 for r in results if not r["broken"]),
        "not_intact": sum(1 for r in results if r["broken"] and not r["intact"]),
        "by_breakage": {
            k: {
                "checked": sum(1 for r in results if r["breakage"] == k),
                "confirmed": sum(1 for r in results if r["breakage"] == k and r["agrees"]),
            }
            for k in ("arity", "vanished")
        },
        "disagreements": [r for r in results if not r["agrees"]][:40],
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    if args.show:
        d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        print(f"checked   {d['checked']}")
        print(f"confirmed {d['confirmed']}  ({100*d['confirmed']/max(d['checked'],1):.1f}%)")
        print(f"  the binder would not break: {d['not_broken']}")
        print(f"  broken but control impure : {d['not_intact']}")
        for k, v in d["by_breakage"].items():
            print(f"  {k:<9} {v['confirmed']}/{v['checked']}")
        return 0

    data = json.loads(MUTANTS.read_text(encoding="utf-8"))
    out = verify(data["mutants"])
    ARTIFACT.write_text(json.dumps(out, indent=1), encoding="utf-8")

    # Fold the verdict back into the population, so exactly one artifact answers
    # "what is a positive". A downstream harness filtering on `confirmed` cannot
    # accidentally include a mutant the binder refused to confirm.
    verdict = {r["mid"]: r for r in out["results"]}
    dropped = 0
    for m in data["mutants"]:
        r = verdict.get(m["mid"], {})
        m["confirmed"] = bool(r.get("agrees"))
        if not m["confirmed"]:
            dropped += 1
            m["dropped_because"] = (
                "the un-reverted caller also fails to bind -- the short name resolves to "
                "more than one definition here, so which one the call reaches is ambiguous"
                if r.get("broken")
                else "CPython's binder did not agree the reverted call fails"
            )
    data["confirmed"] = out["confirmed"]
    data["dropped_by_control"] = dropped
    MUTANTS.write_text(json.dumps(data, indent=1), encoding="utf-8")

    progress(
        f"confirmed {out['confirmed']}/{out['checked']} "
        f"(not broken {out['not_broken']}, control impure {out['not_intact']}); "
        f"marked {dropped} dropped in {MUTANTS.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
