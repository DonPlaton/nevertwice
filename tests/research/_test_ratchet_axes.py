#!/usr/bin/env python3
"""F3: the ratchet's three unaudited axes, against implementations nobody here wrote.

`RATCHET_R3.md`: *"the three axes `ruff` cannot see are exactly the ones with no
independent instrument behind them, so their contribution to the 36% is unaudited."*
That was true of `ruff`'s C901, which measures McCabe and nothing else. It is not true of
`ruff`'s pylint-derived rules, which were available on this machine the whole time:

| axis | independent instrument | kind of check |
|---|---|---|
| `nesting` | `PLR1702` too-many-nested-blocks | exact agreement |
| `returns` | `PLR0911` too-many-return-statements | exact agreement |
| `length` | `PLR0915` too-many-statements | **directional only** -- lines are not statements |

Each rule is run with its threshold set to zero, so the message carries the *value* rather
than a pass or a fail -- the same trick `ruff_complexity` uses for C901.

`length` is the axis this cannot rescue. Nothing reachable implements "lines from `def` to
the last statement"; `PLR0915` counts statements, which is a different number about the
same thing. `AXES_F3.md` declares in advance what a directional check must show and what
happens when it does not.

If `ruff` is missing these checks **skip loudly** rather than passing quietly, because a
control that silently disappears is a control nobody has.

Run:  python tests/_test_ratchet_axes.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

#: Under `tests/research/` and not beside the core suites: it imports a lab module that
#: needs a research extra (networkx), and the core matrix installs nothing. In `tests/` it
#: crashed the core job at import; the local battery collects both directories, so the
#: suite still runs here. Moved 2026-09-22 after CI's first matrix run in 27 days.
HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import complexity as C  # noqa: E402

PASSED = 0
FAILED = 0
SKIPPED = 0
NL = "\n"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = "  [" + detail + "]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def skip(name: str, why: str) -> None:
    global SKIPPED
    print("  SKIP " + name + "  -- " + why)
    SKIPPED += 1


def _write(source: str) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    fh.write(source)
    fh.close()
    return Path(fh.name)


def _ruff_available() -> bool:
    return C.ruff_complexity(_write("def f():\n    return 1\n")) is not None


# ---------------------------------------------------------------------------
# the instruments report a value, not a verdict
# ---------------------------------------------------------------------------

def test_the_instruments_report_numbers() -> None:
    print(NL + "- each rule at threshold zero reports the value itself -")
    if not _ruff_available():
        skip("the three instruments", "ruff is not available in this environment")
        return
    src = (
        "def f(a):\n"
        "    if a:\n"
        "        for i in range(3):\n"
        "            while a:\n"
        "                return 1\n"
        "    if a:\n"
        "        return 2\n"
        "    return 3\n"
    )
    path = _write(src)
    nest = C.ruff_nesting(path)
    rets = C.ruff_returns(path)
    stmts = C.ruff_statements(path)
    # `if` -> `for` -> `while` is depth 3; the second `if` is depth 1. The first draft of
    # this line said 4, and the instrument it was written to check said 3. So did a hand
    # count. The expectation was wrong, which is the whole reason for a second opinion.
    check("nesting is a per-function number", nest == {"f": 3}, str(nest))
    check("returns is a per-function number", rets == {"f": 3}, str(rets))
    check("statements is a per-function number", isinstance(stmts, dict) and "f" in stmts,
          str(stmts))
    check("and all three name the same function", set(nest) == set(rets) == set(stmts),
          str((set(nest), set(rets), set(stmts))))


def test_a_missing_ruff_returns_none_rather_than_zero() -> None:
    """A control that reports 0 when it did not run is worse than no control."""
    print(NL + "- absence is None, never an empty result -")
    missing = Path("does_not_exist_" + "x" * 12 + ".py")
    for name, fn in (("nesting", C.ruff_nesting), ("returns", C.ruff_returns),
                     ("statements", C.ruff_statements)):
        got = fn(missing)
        check(name + " on an unreadable path is None or empty, not a silent zero",
              got is None or got == {}, repr(got))


# ---------------------------------------------------------------------------
# the ratchet's own numbers, on cases written by hand
# ---------------------------------------------------------------------------

def test_this_project_and_ruff_agree_on_nesting() -> None:
    print(NL + "- nesting: this project against PLR1702 -")
    if not _ruff_available():
        skip("nesting agreement", "ruff is not available in this environment")
        return
    cases = [
        ("flat", "def f():\n    return 1\n"),
        ("one if", "def f(a):\n    if a:\n        return 1\n    return 2\n"),
        ("if in for in while",
         "def f(a):\n    while a:\n        for i in a:\n            if i:\n"
         "                return i\n    return 0\n"),
        ("try and with",
         "def f(a):\n    try:\n        with open(a) as fh:\n            return fh\n"
         "    except OSError:\n        return None\n"),
        ("a nested def does not count for the outer function",
         "def outer():\n    def inner(a):\n        if a:\n            for i in a:\n"
         "                return i\n    return inner\n"),
        # Found by the audit, not by inspection. An `elif` is an `If` inside the parent's
        # `orelse` in the AST, so walking children charged one level per branch and read
        # a flat three-way chain as depth 3. A reader feels one level. 133 of the audit's
        # 133 nesting disagreements were this shape.
        ("an elif chain is one level, not one per branch",
         "def f(a):\n    if a == 1:\n        return 1\n    elif a == 2:\n"
         "        return 2\n    elif a == 3:\n        return 3\n    return 0\n"),
        ("an else keeps the level it is attached to",
         "def f(a):\n    if a:\n        return 1\n    else:\n        return 2\n"),
        # `elif x:` and `else:` + an indented `if x:` produce an *identical* AST. The
        # column offset is the only thing that separates them, and without that test the
        # elif rule swallowed real nesting -- `psf/requests`'s `Server.__exit__` scored 1
        # where both a reader and PLR1702 say 2.
        ("an if indented inside an else is a level, unlike an elif",
         "def f(a):\n    if a:\n        return 1\n    else:\n        if a:\n"
         "            return 2\n    return 3\n"),
        # A definitional difference rather than a defect, resolved the way R1 resolved
        # the same question for cyclomatic: the metric has to be the metric everyone
        # means, and the only reachable statement of that is the instrument.
        ("a match is a branch, and PLR1702 does not count it as nesting",
         "def f(a):\n    match a:\n        case 1:\n            return 1\n"
         "        case _:\n            return 2\n"),
    ]
    for label, src in cases:
        path = _write(src)
        mine = {q: m.nesting for q, m in C.scan_file(src).items()}
        theirs = C.ruff_nesting(path)
        if theirs is None:
            skip(label, "ruff unavailable mid-run")
            continue
        # ruff reports nothing for a function with no blocks; that is nesting 0.
        theirs = {q: theirs.get(q, 0) for q in mine}
        check(label + ": " + str(mine) + " == " + str(theirs), mine == theirs)


def test_this_project_and_ruff_agree_on_return_count() -> None:
    print(NL + "- returns: this project against PLR0911 -")
    if not _ruff_available():
        skip("return-count agreement", "ruff is not available in this environment")
        return
    cases = [
        ("none", "def f():\n    x = 1\n"),
        ("one", "def f():\n    return 1\n"),
        ("three", "def f(a):\n    if a:\n        return 1\n    if a:\n"
                  "        return 2\n    return 3\n"),
        ("a bare return counts", "def f(a):\n    if a:\n        return\n    return 1\n"),
        ("a nested def keeps its own",
         "def outer():\n    def inner():\n        return 1\n    return inner\n"),
    ]
    for label, src in cases:
        path = _write(src)
        mine = {q: m.returns for q, m in C.scan_file(src).items()}
        theirs = C.ruff_returns(path)
        if theirs is None:
            skip(label, "ruff unavailable mid-run")
            continue
        theirs = {q: theirs.get(q, 0) for q in mine}
        check(label + ": " + str(mine) + " == " + str(theirs), mine == theirs)


def test_a_name_defined_twice_resolves_to_the_one_that_runs() -> None:
    """The third appearance of D6's blind spot, in a third instrument.

    `psf/requests` defines `cookiejar_from_dict` three times: two `@overload` stubs whose
    bodies are `...`, then the real implementation. `scan_file` keys by qualname, so one
    of the three wins -- and it was winning by traversal order, which put a stub's metrics
    (nesting 0) where the real function's (nesting 3) belonged. A ratchet storing that
    baseline compares next quarter's real function against this quarter's `...`.

    Python's own answer is that the **last** definition is the one that runs. That is the
    rule here, and duplicates are counted rather than silently resolved.
    """
    print(NL + "- a qualname defined more than once takes the last definition -")
    src = (
        "from typing import overload\n"
        "\n"
        "@overload\n"
        "def f(a: int) -> int: ...\n"
        "@overload\n"
        "def f(a: str) -> str: ...\n"
        "def f(a):\n"
        "    if a:\n"
        "        for i in a:\n"
        "            if i:\n"
        "                return i\n"
        "    return a\n"
    )
    metrics = C.scan_file(src)
    check("the real implementation's nesting wins, not a stub's",
          metrics["f"].nesting == 3, str(metrics["f"].nesting))
    check("and so does its return count", metrics["f"].returns == 2,
          str(metrics["f"].returns))
    if not _ruff_available():
        skip("overload agreement", "ruff is not available in this environment")
        return
    path = _write(src)
    check("which is what the instrument says too",
          (C.ruff_nesting(path) or {}).get("f") == 3, str(C.ruff_nesting(path)))


def test_a_noqa_comment_cannot_silence_the_control() -> None:
    """The fifth time this project has had to write "ungraded is not correct".

    `psf/requests` carries `def proxy_bypass(...):  # noqa`. A bare `# noqa` suppresses
    every diagnostic on that line, so the instrument returned nothing for that function
    and the comparison scored it as agreement. A control a source comment can switch off
    is a control the code under test gets to grade itself with.
    """
    print(NL + "- a # noqa in the source cannot switch the instrument off -")
    if not _ruff_available():
        skip("noqa immunity", "ruff is not available in this environment")
        return
    src = ("def f(a):  # noqa\n    if a:\n        return 1\n    return 2\n")
    path = _write(src)
    rets = C.ruff_returns(path)
    nest = C.ruff_nesting(path)
    check("the return count is reported despite the noqa", rets == {"f": 2}, str(rets))
    check("and so is the nesting", nest == {"f": 1}, str(nest))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_instruments_report_numbers,
               test_a_name_defined_twice_resolves_to_the_one_that_runs,
               test_a_noqa_comment_cannot_silence_the_control,
               test_a_missing_ruff_returns_none_rather_than_zero,
               test_this_project_and_ruff_agree_on_nesting,
               test_this_project_and_ruff_agree_on_return_count):
        fn()
    print(NL + "ratchet axes: " + str(PASSED) + " passed, " + str(FAILED)
          + " failed, " + str(SKIPPED) + " skipped")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
