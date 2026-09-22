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

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _is_main_guard(node: ast.AST) -> bool:
    """`if __name__ == "__main__":` - the block pytest never runs."""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    left = node.test.left
    return isinstance(left, ast.Name) and left.id == "__name__"


def _names(node: ast.AST | None) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} if node else set()


def _depends_on(value: ast.AST | None, tests: tuple[ast.AST, ...]) -> set[str]:
    """The names an exit rests on: those in its value, plus those in every `if` guarding it."""
    out = _names(value)
    for t in tests:
        out |= _names(t)
    return out


def _assigned_outside_main(body: list[ast.stmt]) -> set[str]:
    """Module-level names that exist when pytest merely imports the file.

    A counter assigned inside `if __name__ == "__main__":` does not exist under pytest, so a
    suite whose verdict rests on one cannot be guarded by asserting it - and does not need to
    be: such a suite reports failures by raising, which pytest already catches.
    """
    out: set[str] = set()
    for stmt in body:
        if _is_main_guard(stmt):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assign):
                out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(node.target, ast.Name):
                    out.add(node.target.id)
    return out


def _nonzero_exits(tree: ast.Module) -> list[tuple[ast.AST | None, set[str]]]:
    """Every way the module can leave with a nonzero code, and what that depends on.

    Classification is by the EXIT PATH, not by how the verdict is spelled. The first version of
    this suite matched `(sys.exit|return)(1 if X else 0)` and demanded `global X`, which declared
    `tests/_test_memory_hook.py` - module-level `failures: list[str]`, `if failures: sys.exit(1)`
    - not a counting suite at all. It was: 13 collectible functions, `13 passed` under bare
    pytest on a file whose script exits 1. Matching a second spelling would have been the sixth
    round of chasing shapes (T1 row 162 records the fifth); the basis has to be the exit itself.

    So: find each `sys.exit` / `SystemExit` / `return`-from-the-function-that-feeds-them, take
    the names its value depends on, and add the names in every `if` that guards it - because
    `if failures: sys.exit(1)` puts the verdict in the condition, not in the code.
    """
    exits: list[tuple[ast.AST | None, set[str]]] = []
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    expanded: set[str] = set()

    def walk(node: ast.AST, tests: tuple[ast.AST, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            deeper = tests + (child.test,) if isinstance(child, ast.If) else tests
            value: ast.AST | None = None
            found = False
            if isinstance(child, ast.Raise):
                exc = child.exc
                if isinstance(exc, ast.Call) and _names(exc.func) & {"SystemExit"}:
                    value, found = (exc.args[0] if exc.args else None), True
                elif isinstance(exc, ast.Name) and exc.id == "SystemExit":
                    found = True
            elif isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                call = child.value
                target = call.func
                name = target.attr if isinstance(target, ast.Attribute) else getattr(
                    target, "id", "")
                if name == "exit":
                    value, found = (call.args[0] if call.args else None), True
            if found:
                # `sys.exit(main())` - the verdict lives in main's returns, so follow it once.
                if isinstance(value, ast.Call) and getattr(value.func, "id", "") in funcs:
                    callee = value.func.id
                    if callee not in expanded:
                        expanded.add(callee)
                        walk_returns(funcs[callee])
                elif value is None or not (isinstance(value, ast.Constant)
                                           and value.value in (0, None)):
                    exits.append((value, _depends_on(value, deeper)))
            walk(child, deeper)

    def walk_returns(fn: ast.FunctionDef) -> None:
        def inner(node: ast.AST, tests: tuple[ast.AST, ...]) -> None:
            for child in ast.iter_child_nodes(node):
                deeper = tests + (child.test,) if isinstance(child, ast.If) else tests
                if isinstance(child, ast.Return):
                    v = child.value
                    if v is None or not (isinstance(v, ast.Constant) and v.value in (0, None)):
                        exits.append((v, _depends_on(v, deeper)))
                inner(child, deeper)
        inner(fn, ())

    walk(tree, ())
    return exits


def _reachable_asserts(body: list[ast.stmt], in_try: bool = False) -> bool:
    """Is there an `assert` here whose failure would reach the process?

    `assert`, not `raise`: in these suites a bare `raise` is almost always an INJECTION - a
    stub that fails on purpose so the code under test can be watched handling it - while an
    `assert` is a check. `_test_failure_injection.py` is the case that settles it: the only
    `raise` in the file is inside `fake_urlopen`, the fault it injects.

    An assert inside a `try` body does not count: the suite may be catching it. One in an
    `except` or `finally` does. A nested `def` is not descended into - it is a stub or a
    callback, and whether it ever runs is not visible here.
    """
    for n in body:
        if isinstance(n, ast.Assert) and not in_try:
            return True
        if isinstance(n, ast.Try):
            if any(_reachable_asserts(h.body) for h in n.handlers):
                return True
            if _reachable_asserts(n.finalbody):
                return True
            continue
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse"):
            sub = getattr(n, field, None)
            if isinstance(sub, list) and _reachable_asserts(sub, in_try):
                return True
    return False


def can_go_red(path: Path) -> bool:
    """Can this suite fail AT ALL - as `tests/test_self_checks.py` runs it, by return code?

    The counter guard answers a narrower question: a suite that DOES exit nonzero must not
    look green to bare pytest. It is scoped to suites with collectible `test_*` functions and
    a verdict-dependent exit, and a suite with neither falls through it untouched - which is
    how `_test_failure_injection.py` sat in the battery printing "PROBE FAILURES: N" from a
    process that always exited 0. Nine probes, 31 checks, no `sys.exit` anywhere in the file:
    the registrar reads the return code, and read 0 whatever the probes found.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if _nonzero_exits(tree):
        return True
    return _reachable_asserts(tree.body) or any(
        _reachable_asserts(n.body) for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))

def classify(path: Path) -> dict:
    """What kind of suite is this, and can bare pytest see its failures?

    A suite is *counting* when its nonzero exit depends on module-level state rather than on an
    exception. Only a counting suite with collectible `test_*` functions can go falsely green: a
    suite whose checks are plain `assert`s fails pytest on its own, and a suite with no
    module-level `test_*` function is not collected at all - naming it on the command line runs
    its module body, and its `sys.exit` becomes a collection error, which is noisy but never a
    passing report.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    tests = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith("test_")]
    live = _assigned_outside_main(tree.body)
    verdict: set[str] = set()
    for _value, names in _nonzero_exits(tree):
        verdict |= names & live
    guard_asserts: set[str] = set()
    guard_src = ""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == GUARD:
            guard_src = ast.get_source_segment(src, node) or ""
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assert):
                    guard_asserts |= _names(sub.test)
    return {"path": path, "tests": tests, "verdict": verdict,
            "counting": bool(tests and verdict),
            "guard_asserts": guard_asserts, "guard_src": guard_src}


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

    wrong = [f"{r['path'].name} (exit rests on {', '.join(sorted(r['verdict']))})"
             for r in COUNTING
             if GUARD in r["tests"] and not r["verdict"] <= r["guard_asserts"]]
    check("and the guard ASSERTS on every name the nonzero exit rests on", not wrong,
          "; ".join(sorted(wrong)[:6]))

    late = [r["path"].name for r in COUNTING
            if GUARD in r["tests"] and r["tests"][-1] != GUARD]
    check("and the guard is the last collected check, so every other one has run first",
          not late, ", ".join(sorted(late)[:6]))


def test_a_suite_that_does_not_count_needs_no_guard() -> None:
    """The rule is scoped, not universal - a suite whose checks raise is already honest."""
    print("\n- the rule does not fire on suites that raise -")
    raising = [r for r in REPORT if r["tests"] and not r["verdict"]]
    check("some collectible suites report failures by raising", bool(raising), "none found")
    check("none of them was asked for a guard",
          not any(GUARD in r["tests"] for r in raising),
          "a raising suite carries a counter guard it does not need")
    print(f"       ({len(COUNTING)} counting, {len(raising)} raising, "
          f"{len(REPORT) - len(COUNTING) - len(raising)} not collected by bare pytest)")


def test_every_suite_can_go_red_at_all() -> None:
    """The counter guard asks whether a red suite can look green. This asks the question
    under it: can the suite go red? A file with no nonzero exit and no reachable assert is
    not a weak test, it is a decoration - `tests/test_self_checks.py` judges by return code,
    and such a file returns 0 whatever it found."""
    print("\n- every suite has some way to fail -")
    mute = sorted(p.name for p in SUITES if not can_go_red(p))
    check("no suite reports failures it cannot act on", not mute,
          f"{len(mute)} always exit 0: " + ", ".join(mute[:6]))


def test_the_muteness_rule_bites() -> None:
    """A rule that has never refused anything is indistinguishable from one that cannot."""
    print("\n- and the rule refuses the shape it is for -")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "_test_mute.py").write_text(
            "FAILS = []\ndef check(n, c):\n    if not c:\n        FAILS.append(n)\ncheck('x', True)\nprint(len(FAILS))\n", encoding="utf-8")
        check("a suite that only counts is refused", not can_go_red(d / "_test_mute.py"))

        # ...and both honest shapes are accepted, so the rule is not simply "no".
        (d / "_test_exits.py").write_text(
            'import sys\nFAILS = []\nsys.exit(1 if FAILS else 0)\n', encoding="utf-8")
        check("one that exits on its verdict passes", can_go_red(d / "_test_exits.py"))
        (d / "_test_asserts.py").write_text(
            'def test_x():\n    assert 1 == 1\n', encoding="utf-8")
        check("one whose checks assert passes", can_go_red(d / "_test_asserts.py"))

        # The distinction the rule rests on: an injected fault is not a check.
        (d / "_test_injects.py").write_text(
            "FAILS = []\ndef boom(*a):\n    raise OSError('injected')\ntry:\n    boom()\nexcept OSError:\n    FAILS.append(1)\n", encoding="utf-8")
        check("a raise that is the fault under test is not a check",
              not can_go_red(d / "_test_injects.py"))

def test_the_classifier_reads_the_exit_path_not_the_spelling() -> None:
    """The regression the owner caught: two spellings of the same verdict, one rule.

    `1 if FAILED else 0` and `if failures: sys.exit(1)` are the same statement about the same
    kind of state. A classifier keyed on the first misses the second, which is how
    `tests/_test_memory_hook.py` - the suite guarding all five hook events - sat outside the
    rule while reporting `13 passed` on a file whose script exits 1.
    """
    print("\n- one rule, whatever the verdict is spelled like -")
    spellings = {
        "ternary in sys.exit":
            "import sys\nFAILED = 1\ndef test_x():\n    pass\nsys.exit(1 if FAILED else 0)\n",
        "ternary returned from main":
            "import sys\nFAILED = 1\ndef test_x():\n    pass\ndef main():\n    return 1 if FAILED"
            " else 0\nraise SystemExit(main())\n",
        "a list tested by an if":
            "import sys\nfailures = []\ndef test_x():\n    pass\nif failures:\n    sys.exit(1)\n",
        "a list tested inside main":
            "import sys\nfailures = []\ndef test_x():\n    pass\ndef main():\n    if failures:\n"
            "        return 1\n    return 0\nsys.exit(main())\n",
    }
    for label, body in spellings.items():
        tree = ast.parse(body)
        live = _assigned_outside_main(tree.body)
        verdict = set().union(*(n & live for _v, n in _nonzero_exits(tree))) \
            if _nonzero_exits(tree) else set()
        check(f"{label}: the verdict name is found", bool(verdict), str(verdict))

    # The other side of the rule: a counter that only exists in the script block is NOT a
    # counting suite, because pytest never runs that block and such a suite raises instead.
    only_in_main = ("import sys\ndef test_x():\n    assert True\n"
                    "if __name__ == '__main__':\n    failed = 0\n    sys.exit(1 if failed"
                    " else 0)\n")
    tree = ast.parse(only_in_main)
    live = _assigned_outside_main(tree.body)
    check("a counter that lives only in the __main__ block is not counted",
          not any(n & live for _v, n in _nonzero_exits(tree)), str(live))


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


def _pytest_available() -> bool:
    """Is there a pytest to measure? The core CI matrix installs nothing, so there is not.

    The blocks below measure what BARE PYTEST does with a red suite - that is the whole defect
    they exist for - and without pytest they measured an empty string and called the fixture
    broken. On CI's first matrix run (2026-09-22) that read as "the fixture no longer
    reproduces the defect", which is the worst available message: it blames the fixture for the
    absence of the instrument. The `packaging` job installs `[dev]` and runs this for real.
    """
    try:
        return subprocess.run([sys.executable, "-m", "pytest", "--version"],
                              capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def test_the_defect_is_real_and_the_guard_closes_it() -> None:
    """Two suites of the same shape, one guarded, run through a real pytest subprocess."""
    print("\n- measured through pytest itself -")
    if not _pytest_available():
        print("  SKIP  no pytest here - this block measures a property OF pytest; `packaging` installs it")
        return
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
    if not _pytest_available():
        print("  SKIP  no pytest in this environment (see the block above)")
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        path = _fixture(tmp, "_test_green_fixture.py", guarded=True, red=False)
        script = _run([sys.executable, path.name], tmp)
        check("the script exits zero", script.returncode == 0, f"exit {script.returncode}")
        py = _run([sys.executable, "-m", "pytest", path.name, "-q"], tmp)
        check("and pytest passes it too", py.returncode == 0, py.stdout.strip()[-200:])


def test_a_population_guard_watches_the_loop_it_guards() -> None:
    """A guard that pins a sweep's population must count INSIDE that sweep.

    `8ce3cbb` pinned five populations so an empty discovery could not pass for a clean sweep, and
    four of the five measured the population with a SECOND, independent call to the same glob.
    Such a guard watches its own copy of the intention: emptying the LOOP left both the offender
    check and the guard green, and one of the four also carried the wrong number - 36 against the
    loop's 34, because the loop's allowlist skipped two. A second source of truth cannot disagree
    with the first on the day it is written, or the author would notice; it disagrees later.

    So the shape is refused here rather than remembered: an assignment `X = len(<expr>)` whose
    expression names the same source as a `for` in the same scope. Spelling is ignored - the
    comparison is over the Names, attributes and string constants, so `len(list(D.glob("*.py")))`
    beside `for p in sorted(D.glob("*.py"))` is caught too, and so is a counter that carries the
    loop's filter, which is what the real case looked like.

    Two shapes are out of reach, named rather than half-caught, and neither is in the tree today
    (checked: zero occurrences of the first, and `sum(1 for` appears in sixteen suites but never
    as a population counter beside a loop):

        n = sum(1 for _ in sorted(D.glob("*.py")))     no `len` call
        mods = list(D.glob("*.py"))                    the loop iterates a NAME, so its
        for p in mods: ...                             fingerprint shares nothing with
        n = len(D.glob("*.py"))                        the counter's

    That is the rule's boundary, not a hole in it - a signature catches a class, never its edge.
    """
    def fingerprint(node):
        out = set()
        for k in ast.walk(node):
            if isinstance(k, ast.Name):
                out.add(k.id)
            elif isinstance(k, ast.Attribute):
                out.add(k.attr)
            elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.add(k.value)
        return out - {"sorted", "list", "len", "set", "tuple"}

    def duplicates(tree):
        found = []
        scopes = [tree] + [n for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for scope in scopes:
            loops = [(n.lineno, fingerprint(n.iter)) for n in ast.walk(scope)
                     if isinstance(n, (ast.For, ast.AsyncFor))]
            for n in ast.walk(scope):
                if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                        and isinstance(n.value.func, ast.Name) and n.value.func.id == "len"
                        and n.value.args):
                    continue
                fp = fingerprint(n.value.args[0])
                if len(fp) < 2:
                    continue          # `len(rows)` names one thing; too thin to be a duplicate
                for ln, lfp in loops:
                    #: EITHER containment, and the direction matters. The first version asked
                    #: only `fp <= lfp` - the counter must be a subset of the loop - and the
                    #: real case is the other way round: a counter carrying the loop's filter
                    #: (`[p for p in glob if p.name not in (...)]`) always names MORE than the
                    #: loop. So the rule was silent on the very instance it was written for,
                    #: `8ce3cbb:tests/_test_one_engine.py`, and looked proven because the
                    #: planted examples had no filter. Caught by the auditing session
                    #: 2026-09-22, which fed the genuine historical form back through it.
                    if ln != n.lineno and len(lfp) >= 2 and (lfp <= fp or fp <= lfp):
                        found.append((n.lineno, ln))
                        break
        return found

    print("")
    print("- a population guard counts inside the loop, not beside it -")
    #: The rule bites, before its silence is read as a result: a planted duplicate in each
    #: spelling must be seen, or a zero below would be the rule's blindness, not the tree's shape.
    NL = chr(10)
    check("the rule sees a counter written with the loop's own spelling",
          bool(duplicates(ast.parse(
              'for p in sorted(D.glob("*.py")):\n    pass\nn = len(sorted(D.glob("*.py")))'))))
    check("and one written with a different spelling of the same source",
          bool(duplicates(ast.parse(
              'for p in sorted(D.glob("*.py")):\n    pass\nn = len(list(D.glob("*.py")))'))))
    #: The genuine shape, which the first version of this rule did NOT see: the counter carries
    #: the loop's own filter, so it names MORE than the loop, not less. This is the form
    #: `8ce3cbb:tests/_test_one_engine.py` actually had, and the reason the rule looked proven
    #: while being blind - both planted examples above are unfiltered.
    FILTERED = ('for p in sorted(D.glob("*.py")):' + NL
                + '    if p.name in SKIP:' + NL
                + '        continue' + NL
                + 'n = len([p for p in sorted(D.glob("*.py")) if p.name not in SKIP])')
    check("the rule sees a counter that carries the loop's filter",
          bool(duplicates(ast.parse(FILTERED))))
    #: And the mirror: the LOOP filters, the counter does not.
    MIRROR = ('for p in [q for q in sorted(D.glob("*.py")) if q.name not in SKIP]:' + NL
              + '    pass' + NL
              + 'n = len(sorted(D.glob("*.py")))')
    check("and one the loop filters but the counter does not", bool(duplicates(ast.parse(MIRROR))))
    check("and leaves a counter of something else alone",
          not duplicates(ast.parse(
              'for p in sorted(D.glob("*.py")):\n    pass\nn = len(claims)')))

    check(f"there are suites to check at all ({len(SUITES)})", len(SUITES) >= 150, str(len(SUITES)))
    bad = []
    for s in SUITES:
        try:
            tree = ast.parse(s.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for at, loop_at in duplicates(tree):
            bad.append(f"{s.name}:{at} counts what line {loop_at} iterates")
    check("no guard counts a second call to the source its loop walks", not bad,
          "; ".join(bad[:4]))



def test_zz_every_check_passed() -> None:
    assert FAILED == 0, f"{FAILED} check(s) failed"


def main() -> int:
    for fn in (test_the_repository_still_has_counting_suites,
               test_every_counting_suite_guards_its_counter,
               test_a_suite_that_does_not_count_needs_no_guard,
               test_every_suite_can_go_red_at_all,
               test_the_muteness_rule_bites,
               test_the_classifier_reads_the_exit_path_not_the_spelling,
               test_the_defect_is_real_and_the_guard_closes_it,
               test_a_green_suite_stays_green_under_both_channels):
        fn()
    print(f"\nharness honesty: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
