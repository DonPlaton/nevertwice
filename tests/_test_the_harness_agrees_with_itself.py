#!/usr/bin/env python3
"""The instrument that judges every other gate, judged: bare pytest must not call a red suite green.

`tests/test_self_checks.py` runs each `tests/_test_*.py` as a script and reads its **return
code**, which is correct. `pyproject.toml` sets `python_files = ["test_*.py"]`, so a bare
`python -m pytest` never collects these files by itself - but naming one on the command line
collects it anyway, and then only its module-level `test_*` functions run. Those functions call
a `check()` that *prints* `FAIL` and increments a counter; it never raises. So:

    $ python tests/_test_freshness.py            -> exit 1
    $ python -m pytest tests/_test_freshness.py  -> 6 passed

Two channels, one tree, opposite verdicts - measured on 2026-09-19, and the reason a green was
claimed twice on a red HEAD. The fix is one function per counting suite: a final collected check
that asserts the suite's own failure counter is zero, so the two channels cannot disagree.

This suite enforces that rule structurally over every suite in the repository, and proves
behaviourally - on synthetic suites of the same shape, through a real `pytest` subprocess - that
the guard is what turns the false green red.

    python tests/_test_the_harness_agrees_with_itself.py
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: no suite here may reach the live store

GUARD = "test_zz_every_check_passed"
SUITES = tuple(sorted((ROOT / "tests").glob("_test_*.py"))) + tuple(
    sorted((ROOT / "tests" / "research").glob("_test_*.py"))
)
#: How a counting suite spells its verdict: the expression its exit code is built from.
VERDICT = re.compile(r"(?:SystemExit|sys\.exit|return)\(?\s*1 if (\w+) else 0")

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def classify(path: Path) -> dict:
    """What kind of suite is this, and can bare pytest see its failures?

    A suite is *counting* when it records failures in a module-level counter that its exit code
    reads, rather than by raising. Only a counting suite with collectible `test_*` functions can
    go falsely green: a suite whose checks are plain `assert`s fails pytest on its own, and a
    suite with no module-level `test_*` function is not collected at all - naming it on the
    command line runs its module body, and its `sys.exit` becomes a collection error, which is
    noisy but never a passing report.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    tests = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith("test_")]
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            declared.update(node.names)
    hit = VERDICT.search(src)
    counter = hit.group(1) if hit else None
    guard_src = ""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == GUARD:
            guard_src = ast.get_source_segment(src, node) or ""
    return {"path": path, "tests": tests, "declared": declared, "counter": counter,
            "counting": bool(tests and declared and counter and counter in declared),
            "guard_src": guard_src}


REPORT = [classify(p) for p in SUITES]
COUNTING = [r for r in REPORT if r["counting"]]


def test_the_repository_still_has_counting_suites() -> None:
    print("\n- there is something to guard -")
    check("at least one suite records failures in a counter instead of raising",
          bool(COUNTING),
          "no counting suite found; if every suite now raises, delete this file with a note")
    check("every suite parses", len(REPORT) == len(SUITES))


def test_every_counting_suite_guards_its_counter() -> None:
    print("\n- a counting suite cannot report passed while its counter is nonzero -")
    missing = [r["path"].name for r in COUNTING if GUARD not in r["tests"]]
    check(f"every counting suite defines {GUARD}()", not missing,
          f"{len(missing)} without it: " + ", ".join(sorted(missing)[:6]))

    wrong = [r["path"].name for r in COUNTING
             if GUARD in r["tests"] and r["counter"] not in r["guard_src"]]
    check("and the guard asserts the same counter the exit code reads", not wrong,
          ", ".join(sorted(wrong)[:6]))

    late = [r["path"].name for r in COUNTING
            if GUARD in r["tests"] and r["tests"][-1] != GUARD]
    check("and the guard is the last collected check, so every other one has run first",
          not late, ", ".join(sorted(late)[:6]))


def test_a_suite_that_does_not_count_needs_no_guard() -> None:
    """The rule is scoped, not universal - a suite whose checks raise is already honest."""
    print("\n- the rule does not fire on suites that raise -")
    raising = [r for r in REPORT if r["tests"] and not r["declared"]]
    check("some suites check by raising", bool(raising), "none found")
    check("none of them was asked for a guard",
          not any(GUARD in r["tests"] for r in raising),
          "a raising suite carries a counter guard it does not need")


# ------------------------------------------------------------------ behaviour

_SHAPE = '''import sys

PASSED = 0
FAILED = 0


def check(name, condition):
    global PASSED, FAILED
    print(("  ok   " if condition else "  FAIL ") + name)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_something():
    check("RED_LABEL", False)
{guard}

if __name__ == "__main__":
    test_something()
    sys.exit(1 if FAILED else 0)
'''

_GUARD_SRC = '''

def {guard}():
    assert FAILED == 0, f"{{FAILED}} check(s) failed"
'''


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def _fixture(tmp: Path, name: str, *, guarded: bool, red: bool = True) -> Path:
    body = _SHAPE.format(guard=_GUARD_SRC.format(guard=GUARD) if guarded else "")
    if not red:
        body = body.replace('check("RED_LABEL", False)', 'check("GREEN_LABEL", True)')
    path = tmp / name
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def test_the_defect_is_real_and_the_guard_closes_it() -> None:
    """Two suites of the same shape, one guarded, run through a real pytest subprocess."""
    print("\n- measured through pytest itself -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        bare = _fixture(tmp, "_test_bare_fixture.py", guarded=False)
        held = _fixture(tmp, "_test_guarded_fixture.py", guarded=True)

        bare_script = _run([sys.executable, bare.name], tmp)
        check("the unguarded fixture exits nonzero as a script", bare_script.returncode == 1,
              f"exit {bare_script.returncode}")
        bare_pytest = _run([sys.executable, "-m", "pytest", bare.name, "-q"], tmp)
        check("and bare pytest calls that same red file green - the defect this suite is about",
              bare_pytest.returncode == 0 and "passed" in bare_pytest.stdout,
              "the fixture no longer reproduces the defect, so the guard check below proves "
              "nothing: " + bare_pytest.stdout.strip()[-200:])

        held_script = _run([sys.executable, held.name], tmp)
        check("the guarded fixture still exits nonzero as a script",
              held_script.returncode == 1, f"exit {held_script.returncode}")
        held_pytest = _run([sys.executable, "-m", "pytest", held.name, "-q"], tmp)
        check("and bare pytest now agrees with the exit code", held_pytest.returncode != 0,
              "pytest stayed green on a red suite that carries the guard")
        check("the guard is what fails, and it names the counter",
              GUARD in held_pytest.stdout and "check(s) failed" in held_pytest.stdout,
              held_pytest.stdout.strip()[-200:])


def test_a_green_suite_stays_green_under_both_channels() -> None:
    """The guard must not invent failures: a suite with no FAIL passes both ways."""
    print("\n- the guard is silent when there is nothing to report -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        path = _fixture(tmp, "_test_green_fixture.py", guarded=True, red=False)
        script = _run([sys.executable, path.name], tmp)
        check("the script exits zero", script.returncode == 0, f"exit {script.returncode}")
        py = _run([sys.executable, "-m", "pytest", path.name, "-q"], tmp)
        check("and pytest passes it too", py.returncode == 0, py.stdout.strip()[-200:])


def test_zz_every_check_passed() -> None:
    assert FAILED == 0, f"{FAILED} check(s) failed"


def main() -> int:
    for fn in (test_the_repository_still_has_counting_suites,
               test_every_counting_suite_guards_its_counter,
               test_a_suite_that_does_not_count_needs_no_guard,
               test_the_defect_is_real_and_the_guard_closes_it,
               test_a_green_suite_stays_green_under_both_channels):
        fn()
    print(f"\nharness honesty: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
