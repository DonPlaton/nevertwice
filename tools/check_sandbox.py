#!/usr/bin/env python3
"""No script may reach the memory store without saying which store it means.

Three times a script in this repository has written into the owner's live memory vault
(2026-08-13, 2026-08-18, 2026-08-25). Each time the offending file was one nobody thought
of as dangerous - a test helper, then a five-line demo - and each time the fix was applied
to the one directory where it happened.

This is the check that generalises it. Every `.py` under `examples/`, `research/` and
`tools/` that imports a project module resolves `config.VAULT` the moment it is imported,
and on a developer's machine that is a populated store. So each of them must first make one
of two declarations, both from `sandbox_guard`:

    sandbox_guard.isolate()             a throwaway store, verified after the fact
    sandbox_guard.allow_live(reason)    deliberately a real store, with a written reason

Neither is a comment: `isolate()` asserts that config actually landed in the sandbox, and
`allow_live()` names the store on stderr before the script touches it.

Three rules:

1. **entry points** (anything that is not an underscore-prefixed helper) that import a
   project module must be armed *before* their first module-level project import. Importing
   an already-armed sibling counts - that runs its `isolate()` - but only if that import
   comes first, which is exactly the bug `research/forgetting.py` had: it imported
   `consolidate_memory` on one line and the armed `longitudinal_bench` on the next.
2. **helpers** (`_name.py` with no `__main__` block) do not arm - that would hijack their
   importer - so instead every entry point that imports them must itself be armed.
3. any file that names `NEVERTWICE_HOME` without also naming `NEVERTWICE_VAULT` is an error.
   `config` resolves `env("VAULT") or NEVERTWICE_HOME`, so pinning HOME alone loses to an
   inherited VAULT. That single asymmetry caused every one of the three incidents, and it
   is visible in the source text without running anything.

Only git-tracked files are scanned: `research/embed_universal/data/` holds cloned third-party
repositories, and vendored code is not this repository's contract to keep.

    python tools/check_sandbox.py            # check, exit 1 on any violation
    python tools/check_sandbox.py --list     # also print what each file was classified as
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "nevertwice"
SCANNED = ("examples", "research", "tools")

# Fallback only, when git is not available to tell us what is vendored.
SKIP_DIRS = {"__pycache__", "sample-store", ".pytest_cache", "data", "node_modules"}

ARM_CALLS = ("isolate", "allow_live")


def project_modules() -> frozenset:
    """Module names that resolve the store when imported, read from the package itself."""
    stems = {p.stem for p in PKG.glob("*.py") if not p.stem.startswith("__")}
    return frozenset(stems | {"nevertwice"})


PROJECT = project_modules()


def tracked_files(suffixes: tuple) -> list:
    """Git-tracked files under the scanned folders, or a filtered walk if git is absent."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", *SCANNED],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        rels = [r for r in out.split("\0") if r]
        paths = [ROOT / r for r in rels]
    except (OSError, subprocess.SubprocessError):
        paths = []
        for folder in SCANNED:
            base = ROOT / folder
            if base.is_dir():
                paths += [p for p in base.rglob("*")
                          if not any(part in SKIP_DIRS for part in p.parts)]
    return sorted(p for p in paths if p.suffix in suffixes and p.is_file())


class Facts:
    """What the checks need to know about one file, gathered in a single AST walk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rel = path.relative_to(ROOT).as_posix()
        self.tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"),
                              filename=str(path))
        self.own_arm_line: int | None = None
        self.arm_kind: str | None = None
        self.first_project_import: int | None = None
        self.imports: set = set()
        self.import_line: dict = {}          # module-level imports only, name -> lineno
        self.has_main = any(isinstance(n, ast.If) and _is_main_guard(n)
                            for n in ast.walk(self.tree))
        self._walk()

    @property
    def is_helper(self) -> bool:
        return self.path.name.startswith("_") and not self.has_main

    @property
    def reaches_store(self) -> bool:
        return bool(self.imports & PROJECT)

    def _record(self, name: str, lineno: int, *, top_level: bool) -> None:
        root = name.split(".")[0]
        self.imports.add(root)
        if not top_level:
            return
        self.import_line.setdefault(root, lineno)
        if root in PROJECT and self.first_project_import is None:
            self.first_project_import = lineno

    def _walk(self) -> None:
        # Module-level statements first: their line numbers are what execution order means.
        for node in self.tree.body:
            self._scan_statement(node)
        # Then everything, to catch imports that only happen inside a function.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._record(alias.name, node.lineno, top_level=False)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                self._record(node.module, node.lineno, top_level=False)

    def _scan_statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._record(alias.name, node.lineno, top_level=True)
            return
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            self._record(node.module, node.lineno, top_level=True)
            return
        if isinstance(node, (ast.Try, ast.If, ast.With)):
            for child in node.body:
                self._scan_statement(child)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return                        # runs later, not at import time
        # Any other module-level statement: an arming call may be nested inside an
        # expression (`STORE = str(sandbox_guard.isolate(...))`), and it still runs here.
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                self._note_arm(child)

    def _note_arm(self, call: ast.Call) -> None:
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr in ARM_CALLS):
            return
        if not (isinstance(func.value, ast.Name) and func.value.id == "sandbox_guard"):
            return
        if self.own_arm_line is None:
            self.own_arm_line, self.arm_kind = call.lineno, func.attr


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__"
                    for c in test.comparators))


def arming(f: Facts, siblings: dict) -> tuple:
    """(line, description) at which this file becomes armed, or (None, None).

    Either its own `sandbox_guard` call, or the module-level import of a sibling that makes
    one - whichever runs first.
    """
    best, why = f.own_arm_line, (f"sandbox_guard.{f.arm_kind}()" if f.arm_kind else None)
    for name, line in f.import_line.items():
        sib = siblings.get(name)
        if sib is None or sib is f or sib.own_arm_line is None:
            continue
        if best is None or line < best:
            best, why = line, f"via {sib.path.name}"
    return best, why


def check_entry_points(files: list, by_dir: dict) -> list:
    problems = []
    for f in files:
        if f.is_helper or not f.reaches_store:
            continue
        line, why = arming(f, by_dir[f.path.parent])
        if line is None:
            problems.append(
                f"{f.rel}: imports {sorted(f.imports & PROJECT)[0]} - which resolves the "
                f"store - without sandbox_guard.isolate() or .allow_live(reason)")
        elif f.first_project_import is not None and line > f.first_project_import:
            problems.append(
                f"{f.rel}: armed on line {line} ({why}), after the project import on line "
                f"{f.first_project_import} - too late to matter")
    return problems


def check_helpers(files: list, by_dir: dict) -> list:
    problems = []
    for f in files:
        if not f.is_helper or f.own_arm_line is not None or not f.reaches_store:
            continue
        importers = [g for g in files
                     if g.path.parent == f.path.parent and g is not f
                     and f.path.stem in g.imports]
        if not importers:
            problems.append(
                f"{f.rel}: helper reaches the store but nothing imports it - either it is "
                f"an entry point and must arm, or it is dead")
            continue
        unarmed = sorted(g.rel for g in importers
                         if arming(g, by_dir[g.path.parent])[0] is None)
        if unarmed:
            problems.append(f"{f.rel}: helper reaches the store and is imported by unarmed "
                            f"{', '.join(unarmed)}")
    return problems


def check_home_without_vault() -> list:
    """The asymmetry behind all three incidents, visible in the source text."""
    problems = []
    for path in tracked_files((".py", ".sh")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "NEVERTWICE_HOME" in text and "NEVERTWICE_VAULT" not in text:
            problems.append(
                f"{path.relative_to(ROOT).as_posix()}: pins or reads NEVERTWICE_HOME but "
                f"never NEVERTWICE_VAULT - config prefers VAULT, so an inherited value "
                f"wins and the store moves")
    return problems


def collect() -> list:
    out = []
    for path in tracked_files((".py",)):
        try:
            out.append(Facts(path))
        except SyntaxError as exc:
            print(f"  FAIL {path.relative_to(ROOT).as_posix()}: cannot parse - {exc}")
            raise SystemExit(1) from None
    return out


def main(argv: list) -> int:
    files = collect()
    by_dir: dict = {}
    for f in files:
        by_dir.setdefault(f.path.parent, {})[f.path.stem] = f

    problems = (check_entry_points(files, by_dir) + check_helpers(files, by_dir)
                + check_home_without_vault())

    if "--list" in argv:
        for f in files:
            if not f.reaches_store:
                kind = "-"
            elif f.is_helper:
                kind = "helper (armed by its importers)"
            else:
                line, why = arming(f, by_dir[f.path.parent])
                kind = f"{why} @{line}" if line else "UNARMED"
            print(f"  {f.rel:<44} {kind}")
        print()

    reaching = sum(1 for f in files if f.reaches_store)
    if problems:
        print(f"sandbox lint: {len(problems)} violation(s)")
        for p in problems:
            print(f"  FAIL {p}")
        return 1
    print(f"sandbox lint: {len(files)} tracked files scanned, {reaching} reach the store, "
          f"all declared before their first project import")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
