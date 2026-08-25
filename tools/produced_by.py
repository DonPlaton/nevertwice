#!/usr/bin/env python3
"""Resolve a manifest claim's `command` to the repository files it actually depends on.

A number is produced by code, not by the file it was written into. Before this module the
manifest stamped every claim with the commit that last *touched the artifact* - which for
this repository was a directory move, so 133 claims all carried one commit and none of them
named the engine that computed the number. `produced_by` closes that: it is the set of
tracked source files the claim's command imports, transitively, so
`git log -1 -- <produced_by>` answers "has the code moved since this number was measured?".

The closure is computed statically, by walking `import` statements with `ast`. Nothing is
executed - the point is to describe commands that cannot be run here (a paid API, a GPU) just
as precisely as the ones that can.

Usage:

    python tools/produced_by.py "python research/longmem_eval.py"    # one command
    python tools/produced_by.py --all                                # every command in the manifest
    python tools/produced_by.py --write                              # stamp produced_by into the manifest

Standard library only.
"""
from __future__ import annotations

import argparse
import ast
import json
import shlex
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001 - a redirected stream may not support it
    pass

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"

# Where a bare module name may live. `nevertwice` is on this list because sandbox_guard puts
# the package directory on sys.path (sandbox_guard.py:167), so every harness writes
# `import memory_hook`, not `import nevertwice.memory_hook`. Leaving it off is how a closure
# ends up naming the harness and none of the engine it measures - the exact drift B8 exists to
# catch. Over-inclusion only makes the freshness check stricter, so ambiguity is resolved
# towards including the file.
SEARCH_ROOTS = ("", "nevertwice", "research", "tools", "examples", "tests")


class UnresolvableCommand(Exception):
    """The command does not name a Python entry point inside this repository."""


def entry_for(command: str) -> Path:
    """Map a manifest `command` onto the repo file that runs first."""
    parts = shlex.split(command)
    if not parts or Path(parts[0]).name not in ("python", "python3", "py"):
        raise UnresolvableCommand(f"not a python invocation: {command!r}")
    rest = parts[1:]
    if not rest:
        raise UnresolvableCommand(f"no entry point: {command!r}")
    if rest[0] == "-m":
        if len(rest) < 2:
            raise UnresolvableCommand(f"-m with no module: {command!r}")
        path = _module_file(rest[1])
        if path is None:
            raise UnresolvableCommand(f"module not in this repository: {rest[1]!r}")
        return path
    candidate = ROOT / rest[0]
    if candidate.is_file():
        return candidate
    raise UnresolvableCommand(f"no such entry file: {rest[0]!r}")


def _module_file(dotted: str, near: Path | None = None) -> Path | None:
    """`nevertwice.stats` -> nevertwice/stats.py, or the package __init__.

    `near` is the directory of the importing file, searched first: that is what CPython does
    with sys.path[0], and it is why `import _rerank` inside research/ finds research/_rerank.py.
    """
    rel = Path(*dotted.split("."))
    bases = [near / rel] if near is not None else []
    bases += [(ROOT / root / rel if root else ROOT / rel) for root in SEARCH_ROOTS]
    for base in bases:
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
    return None


# Deferred-import helpers whose first argument is the module name. `_sibling` is this
# repository's own resolver (memory_hook.py:74) and is the *only* way several engine modules
# are reached - `nevertwice/rankers.py`, the ranker the retrieval numbers measure, appears
# nowhere in an `import` statement. A closure built from `import` statements alone would
# therefore declare the retrieval claims independent of the ranker.
DEFERRED_IMPORTERS = ("_sibling", "import_module")


def _imports(path: Path) -> list[tuple[str, int]]:
    """Every imported module name in `path`, as (dotted name, relative level).

    Covers `import`/`from ... import`, including inside functions, plus the deferred
    helpers in DEFERRED_IMPORTERS called with a string literal.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            if node.module:
                found.append((node.module, level))
            if level:                  # `from . import x` - each name may be a submodule
                found += [(alias.name, level) for alias in node.names]
            else:
                # `from pkg import sub` where sub is itself a module in this repository
                found += [(f"{node.module}.{a.name}", 0) for a in node.names if node.module]
        elif isinstance(node, ast.Call):
            name = _called_name(node.func)
            if name in DEFERRED_IMPORTERS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.append((first.value.lstrip("."), 0))
    return found


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _resolve(name: str, level: int, source: Path) -> Path | None:
    if level:
        base = source.parent
        for _ in range(level - 1):
            base = base.parent
        target = base / Path(*name.split(".")) if name else base / "__init__.py"
        for candidate in (target.with_suffix(".py"), target / "__init__.py", target):
            if candidate.is_file() and candidate.suffix == ".py":
                return candidate
        return None
    return _module_file(name, near=source.parent)


def closure(command: str) -> list[str]:
    """Repo-relative source files the command depends on, entry point first, then sorted."""
    entry = entry_for(command)
    seen: set[Path] = {entry}
    queue = [entry]
    while queue:
        current = queue.pop()
        for name, level in _imports(current):
            found = _resolve(name, level, current)
            if found is None:
                continue               # a third-party or stdlib import - not ours to track
            found = found.resolve()
            if found not in seen:
                seen.add(found)
                queue.append(found)
    entry_rel = entry.resolve().relative_to(ROOT).as_posix()
    rels = sorted(p.relative_to(ROOT).as_posix() for p in seen)
    rels.remove(entry_rel)
    return [entry_rel] + rels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", help="a manifest command, quoted")
    parser.add_argument("--all", action="store_true", help="every distinct command in the manifest")
    parser.add_argument("--write", action="store_true", help="stamp produced_by into the manifest")
    args = parser.parse_args(argv)

    if args.command and not args.all and not args.write:
        for rel in closure(args.command):
            print(rel)
        return 0

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    commands = sorted({c["command"] for c in manifest["claims"]})
    resolved: dict[str, list[str]] = {}
    failures: list[str] = []
    for command in commands:
        try:
            resolved[command] = closure(command)
        except UnresolvableCommand as exc:
            failures.append(f"{command}: {exc}")

    if args.write:
        for claim in manifest["claims"]:
            deps = resolved.get(claim["command"])
            if deps is not None:
                claim["produced_by"] = deps
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
        print(f"stamped produced_by on "
              f"{sum(1 for c in manifest['claims'] if 'produced_by' in c)} claims")
    else:
        for command in commands:
            deps = resolved.get(command)
            print(f"{command}")
            if deps is None:
                print("    <unresolved>")
            else:
                for rel in deps:
                    print(f"    {rel}")

    for failure in failures:
        print(f"UNRESOLVED  {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
