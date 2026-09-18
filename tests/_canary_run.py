#!/usr/bin/env python3
"""Break one function on purpose, and check the golden-store proof notices.

A byte-identical snapshot only means something if it *could* have differed. The proof this
repository had before entered 54% of the engine's statements and none of the functions the
headline numbers are made of, so a refactor that broke any of them would have certified clean.

This copies the package to a temp directory, injects `raise RuntimeError("canary")` as the first
statement of the named function, runs the same corpus against the copy, and exits non-zero when
the proof correctly fails. Exit 0 here means the injection went unnoticed - the proof is empty for
that function, and the caller reports it as a failure.

    python tests/_canary_run.py as_of
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def injected(src: str, func: str) -> str:
    """Return `src` with a raise as the first statement of top-level `def func`."""
    import ast

    tree = ast.parse(src)
    node = next((n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func), None)
    if node is None:
        raise SystemExit(f"{func}: not a top-level function of the engine")
    first = node.body[0]
    #: skip the docstring so the injection lands in executable code, not in front of it
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str) and len(node.body) > 1:
        first = node.body[1]
    lines = src.splitlines(keepends=True)
    indent = " " * first.col_offset
    lines.insert(first.lineno - 1, f'{indent}raise RuntimeError("canary")\n')
    return "".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    func = sys.argv[1]
    work = Path(tempfile.mkdtemp(prefix=f"canary_{func}_"))
    pkg = work / "nevertwice"
    pkg.mkdir(parents=True)
    for p in (ROOT / "nevertwice").glob("*.py"):
        shutil.copy2(p, pkg / p.name)
    tests = work / "tests"
    tests.mkdir()
    for name in ("_golden_store.py", "_test_golden_store.py", "_sandbox.py", "_env_guard.py",
                 "_engine_source.py"):
        shutil.copy2(HERE / name, tests / name)
    for extra in ("sandbox_guard.py",):
        if (ROOT / extra).exists():
            shutil.copy2(ROOT / extra, work / extra)
    if (HERE / "_golden_store_fixture.json").exists():
        shutil.copy2(HERE / "_golden_store_fixture.json", tests / "_golden_store_fixture.json")

    engine = pkg / "_engine.py"
    engine.write_text(injected(engine.read_text(encoding="utf-8"), func),
                      encoding="utf-8", newline="\n")

    #: `--no-canary` so the copy checks only stability and the fixture; recursing would fork
    #: one process per function per level.
    env = dict(os.environ, PYTHONPATH=str(work))
    r = subprocess.run([sys.executable, str(tests / "_test_golden_store.py"), "--no-canary"],
                       capture_output=True, text=True, cwd=work, env=env, timeout=600)
    noticed = r.returncode != 0
    if not noticed:
        sys.stdout.write(f"{func}: the proof did NOT notice the injection\n")
        sys.stdout.write(r.stdout[-1500:])
    return 1 if noticed else 0


if __name__ == "__main__":
    raise SystemExit(main())
