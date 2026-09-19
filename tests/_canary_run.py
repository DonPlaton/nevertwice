#!/usr/bin/env python3
"""Break one function on purpose, and check the golden-store proof notices.

A byte-identical snapshot only means something if it *could* have differed. The proof this
repository had before entered 54% of the engine's statements and none of the functions the
headline numbers are made of, so a refactor that broke any of them would have certified clean.

This copies the package to a temp directory, injects `raise RuntimeError("canary")` as the first
statement of the named function, runs the same corpus against the copy, and exits non-zero when
the proof correctly fails. Exit 0 here means the injection went unnoticed - the proof is empty for
that function, and the caller reports it as a failure.

A second mode moves a number instead of raising: `--nudge=E` wraps the named function so its
LOWEST-ranked score shifts up by E. That is the ranking mutation - nothing crashes, one order
changes - and it is the one a refactor of the ranker would produce. Bisecting E measures the
smallest adjacent gap the fixture's ranking still records.

    python tests/_canary_run.py as_of                             # inject a raise
    python tests/_canary_run.py _calibrated_fusion --nudge=0.05    # move one score
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


def nudged(src: str, func: str, epsilon: float) -> str:
    """Return `src` with `func` wrapped so the LOWEST-ranked score it returns moves by `epsilon`.

    A `raise` proves the proof entered a function. It does not prove the proof can see a change
    in what that function *returns* - and ranking is exactly that: nothing crashes, one number
    moves, an order changes.

    WHICH score is moved decides what the mutation measures, and the first draft got it wrong.
    It nudged the ALPHABETICALLY first key, so the epsilon that changed anything was the
    distance from that candidate up to the leader. On a corpus whose queries fused one or two
    candidates that is a forced flip of a pair - not a resolution, and not a number the ranker
    could plausibly move on its own. Nudging the lowest-scoring candidate instead makes it
    climb over the one immediately above it, so a bisection of epsilon finds the SMALLEST
    ADJACENT GAP in the ranking, which is what "the fixture can see a ranking change of size E"
    has to mean. It follows that the corpus must give at least one query three candidates or
    more: with two, every mutation is a pair flip whatever is nudged.

    The target is the minimum by score, ties broken alphabetically - deterministic, no crash.
    """
    if f"def {func}(" not in src:
        raise SystemExit(f"{func}: not a function of the engine")
    body = [
        "",
        "",
        f"_canary_original_{func} = {func}",
        "",
        "",
        f"def {func}(*a, **k):",
        f"    out = _canary_original_{func}(*a, **k)",
        "    if isinstance(out, dict) and out:",
        "        target = min(sorted(out), key=lambda s: out[s])",
        f"        out[target] = out[target] + {epsilon!r}",
        "    return out",
        "",
    ]
    return src + "\n".join(body)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not positional:
        print(__doc__)
        return 2
    func = positional[0]
    flag = next((a for a in sys.argv[1:] if a.startswith("--nudge")), None)
    epsilon = float(flag.split("=", 1)[1]) if flag and "=" in flag else 0.05
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
    source = engine.read_text(encoding="utf-8")
    mutated = nudged(source, func, epsilon) if flag else injected(source, func)
    engine.write_text(mutated, encoding="utf-8", newline="\n")

    #: `--no-canary` so the copy checks only stability and the fixture; recursing would fork
    #: one process per function per level.
    env = dict(os.environ, PYTHONPATH=str(work))
    r = subprocess.run([sys.executable, str(tests / "_test_golden_store.py"), "--no-canary"],
                       capture_output=True, text=True, cwd=work, env=env, timeout=600)
    noticed = r.returncode != 0
    if not noticed:
        what = f"a nudge of {epsilon}" if flag else "the injection"
        sys.stdout.write(f"{func}: the proof did NOT notice {what}\n")
        sys.stdout.write(r.stdout[-1500:])
    return 1 if noticed else 0


if __name__ == "__main__":
    raise SystemExit(main())
