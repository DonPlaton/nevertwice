#!/usr/bin/env python3
"""R1 and R2: the metrics, and the rule that a diff may not make a file worse.

The load-bearing test here is the one that compares this project's cyclomatic complexity
against **`ruff`'s C901**, an implementation in another language by other people. A ratchet
whose complexity number disagrees with the tools everyone else runs measures its author's taste, and
R3 cannot compare the mechanism against an instrument it did not write unless the two
agree about what they are measuring in the first place.

That comparison found three real errors before it agreed:

* boolean operators, ternaries, `assert` and comprehensions were being counted, and the
  standard counts none of them;
* a `match` was charged one per case instead of one per case *beyond the first*;
* a nested `def` was excluded, and the standard includes it -- a function that hides a
  branchy closure has not hidden it.

And a fourth, in the scanner rather than the metric: callables defined inside a
module-level `try:` were invisible, so four classes behind the optional-dependency idiom
were never scanned. That is the same blind spot D6 found in the checker and in the answer
key, in a third instrument.

If `ruff` is not installed the agreement checks **skip loudly** rather than passing
quietly, because a control that silently disappears is a control nobody has.

Run:  python tests/_test_complexity.py
"""
from __future__ import annotations

import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import complexity as C  # noqa: E402
import ratchet as R  # noqa: E402

PASSED = 0
FAILED = 0
SKIPPED = 0

NL = "\n"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def skip(name: str, why: str) -> None:
    global SKIPPED
    print(f"  SKIP {name}  ({why})")
    SKIPPED += 1


def cc(src: str, name: str = "f") -> int:
    return C.scan_file(src)[name].cyclomatic


# ---------------------------------------------------------------------------
# R1: the metric is the metric everyone means
# ---------------------------------------------------------------------------


def test_the_metric_counts_what_the_standard_counts() -> None:
    print(NL + "- decisions: what McCabe counts -")
    check("a plain function is 1", cc("def f():" + NL + "    return 1" + NL) == 1)
    check("an if is 2", cc("def f(a):" + NL + "    if a: pass" + NL) == 2)
    check("if/else is still 2",
          cc("def f(a):" + NL + "    if a: pass" + NL + "    else: pass" + NL) == 2)
    check("elif adds one",
          cc("def f(a):" + NL + "    if a: pass" + NL + "    elif a: pass" + NL) == 3)
    check("a for is 2", cc("def f(a):" + NL + "    for i in a: pass" + NL) == 2)
    check("a while is 2", cc("def f(a):" + NL + "    while a: pass" + NL) == 2)
    check("each except handler adds one",
          cc("def f():" + NL + "    try: pass" + NL + "    except A: pass" + NL +
             "    except B: pass" + NL) == 3)
    check("a try/else adds one",
          cc("def f():" + NL + "    try: pass" + NL + "    except A: pass" + NL +
             "    else: pass" + NL) == 3)


def test_the_metric_does_not_count_what_feels_like_a_decision() -> None:
    """Each of these was counted first, and each was wrong. They feel like branches."""
    print(NL + "- and what it deliberately does not -")
    check("`and` is not a decision", cc("def f(a,b):" + NL + "    return a and b" + NL) == 1)
    check("a chain of `and` is not either",
          cc("def f(a,b,c):" + NL + "    return a and b and c" + NL) == 1)
    check("a ternary is not",
          cc("def f(a):" + NL + "    return 1 if a else 2" + NL) == 1)
    check("an assert is not", cc("def f(a):" + NL + "    assert a" + NL) == 1)
    check("a comprehension is not",
          cc("def f(a):" + NL + "    return [x for x in a]" + NL) == 1)
    check("nor a comprehension's filter",
          cc("def f(a):" + NL + "    return [x for x in a if x]" + NL) == 1)
    check("`with` is not", cc("def f(a):" + NL + "    with a: pass" + NL) == 1)
    check("`finally` is not",
          cc("def f():" + NL + "    try: pass" + NL + "    finally: pass" + NL) == 1)


def test_the_two_rules_that_are_easy_to_get_backwards() -> None:
    print(NL + "- a nested def counts; a match counts per case beyond the first -")
    check("a nested def adds one, plus its body",
          cc("def f(a):" + NL + "    def g(b):" + NL + "        if b: pass" + NL +
             "    return g" + NL) == 3)
    check("two nested defs add two",
          cc("def f(a):" + NL + "    def g(): pass" + NL + "    def h(): pass" + NL) == 3)
    check("a method of a nested class counts into the function",
          cc("def f(a):" + NL + "    class K:" + NL + "        def m(self):" + NL +
             "            if a: pass" + NL) == 3)
    check("a single-case match adds nothing",
          cc("def f(a):" + NL + "    match a:" + NL + "        case _: pass" + NL) == 1)
    check("three cases add two",
          cc("def f(a):" + NL + "    match a:" + NL + "        case 1: pass" + NL +
             "        case 2: pass" + NL + "        case _: pass" + NL) == 3)


def test_it_agrees_with_ruff_on_this_repository() -> None:
    """The control. Not a sample: every module, every callable, exact equality."""
    print(NL + "- agreement with ruff C901, an implementation nobody here wrote -")
    files = sorted((ROOT / "nevertwice").rglob("*.py"))
    if not files:
        skip("agreement with ruff", "no source to compare")
        return
    probe = C.ruff_complexity(files[0])
    if probe is None:
        skip("agreement with ruff", "ruff is not available in this environment")
        return
    agreed = mismatched = 0
    worst = []
    for path in files:
        src = path.read_text(encoding="utf-8", errors="replace")
        mine = Counter((q.rsplit(".", 1)[-1], m.cyclomatic)
                       for q, m in C.scan_file(src).items())
        theirs = Counter((C.ruff_complexity(path) or {}).items())
        missing = theirs - mine
        if missing:
            mismatched += 1
            if len(worst) < 3:
                worst.append((path.name, dict(missing)))
        else:
            agreed += 1
    check(f"every one of {agreed + mismatched} modules agrees exactly",
          mismatched == 0, f"{mismatched} mismatched: {worst}")
    check("and the comparison was not vacuous", agreed > 20, str(agreed))


def test_the_scanner_sees_callables_behind_an_import_guard() -> None:
    """The fourth error the ruff comparison found: a class inside `try:` was invisible."""
    print(NL + "- a def inside a module-level try is still a def -")
    src = ("try:" + NL + "    import fast" + NL + "except ImportError:" + NL +
           "    fast = None" + NL + NL +
           "if fast:" + NL + "    class K:" + NL + "        def m(self, a):" + NL +
           "            if a: pass" + NL)
    found = C.scan_file(src)
    check("the guarded method is scanned", "K.m" in found, str(sorted(found)))
    check("and its complexity is right", found["K.m"].cyclomatic == 2)


def test_the_other_three_metrics() -> None:
    print(NL + "- nesting, length, returns -")
    src = ("def f(a):" + NL + "    if a:" + NL + "        for i in a:" + NL +
           "            if i:" + NL + "                return 1" + NL + "    return 0" + NL)
    m = C.scan_file(src)["f"]
    check("nesting is the deepest control structure", m.nesting == 3, str(m.nesting))
    check("length is def to last line", m.length == 6, str(m.length))
    check("returns are counted", m.returns == 2, str(m.returns))
    check("a nested function's returns are not the outer's",
          C.scan_file("def f():" + NL + "    def g():" + NL + "        return 1" + NL +
                      "    return g" + NL)["f"].returns == 1)
    check("worse_than names the axes",
          m.worse_than(C.FuncMetrics("f", 1, 1, 1, 1, 1)) ==
          ["cyclomatic", "nesting", "length", "returns"])


# ---------------------------------------------------------------------------
# R1: the import graph
# ---------------------------------------------------------------------------


def test_the_import_graph_only_knows_modules_it_was_given() -> None:
    print(NL + "- an edge to os would make every project cyclic through the stdlib -")
    sources = {"a.py": "import os" + NL + "import b" + NL, "b.py": "import json" + NL}
    g = C.import_graph(sources)
    check("both modules are nodes", set(g.nodes) == {"a", "b"})
    check("the internal edge is there", g.has_edge("a", "b"))
    check("the stdlib edges are not", g.number_of_edges() == 1, str(list(g.edges)))


def test_cycles_are_components_not_a_catalogue_of_restatements() -> None:
    print(NL + "- a cycle is the component a developer has to break -")
    sources = {"a.py": "import b" + NL, "b.py": "import c" + NL, "c.py": "import a" + NL,
               "d.py": "import a" + NL}
    g = C.import_graph(sources)
    found = C.cycles(g)
    check("one cycle, not six", len(found) == 1, str(found))
    check("and it names all three", found[0] == ["a", "b", "c"], str(found[0]))
    check("an acyclic graph has none", C.cycles(C.import_graph({"a.py": "import b" + NL,
                                                               "b.py": ""})) == [])


def test_fan_in_and_fan_out() -> None:
    print(NL + "- how much a change here can cost -")
    sources = {"hub.py": "", "a.py": "import hub" + NL, "b.py": "import hub" + NL,
               "c.py": "import hub" + NL + "import a" + NL}
    f = C.fan(C.import_graph(sources))
    check("the hub has fan-in 3", f["hub"][0] == 3, str(f["hub"]))
    check("and fan-out 0", f["hub"][1] == 0)
    check("c has fan-out 2", f["c"][1] == 2, str(f["c"]))


# ---------------------------------------------------------------------------
# R2: the rule
# ---------------------------------------------------------------------------


SIMPLE = "def f(a):" + NL + "    return a" + NL
BRANCHY = ("def f(a):" + NL + "    if a:" + NL + "        return 1" + NL +
           "    return 0" + NL)


def test_the_ratchet_is_differential_not_absolute() -> None:
    print(NL + "- a diff may not make a file worse than its own baseline -")
    base = R.make_baseline({"m.py": SIMPLE})
    check("an unchanged file is silent", R.regressions(base, {"m.py": SIMPLE}) == [])
    got = R.regressions(base, {"m.py": BRANCHY})
    axes = {r.axis for r in got}
    check("adding a branch is a regression", bool(got), str(got))
    check("and every axis it moved is reported, not just the first",
          axes == {"cyclomatic", "nesting", "returns"}, str(axes))
    cyc = next(r for r in got if r.axis == "cyclomatic")
    check("with both numbers", (cyc.baseline, cyc.now) == (1, 2))
    check("an already-complex file that does not get worse is silent",
          R.regressions(R.make_baseline({"m.py": BRANCHY}), {"m.py": BRANCHY}) == [])


def test_new_code_has_no_baseline_to_be_worse_than() -> None:
    print(NL + "- a new callable is not a regression -")
    base = R.make_baseline({"m.py": SIMPLE})
    added = SIMPLE + ("def g(a):" + NL + "    if a:" + NL + "        if a:" + NL +
                      "            return 1" + NL + "    return 0" + NL)
    check("a new function is silent", R.regressions(base, {"m.py": added}) == [])
    check("a new file is silent", R.regressions(base, {"new.py": BRANCHY}) == [])


def test_the_baseline_only_moves_down() -> None:
    print(NL + "- that is what makes it a ratchet -")
    base = R.make_baseline({"m.py": BRANCHY})
    check("simplifying moves it", R.tighten(base, {"m.py": SIMPLE}) > 0)
    check("and the new floor is enforced",
          {r.axis for r in R.regressions(base, {"m.py": BRANCHY})}
          == {"cyclomatic", "nesting", "returns"})
    before = dict(base["metrics"]["m.py"]["f"])
    R.tighten(base, {"m.py": BRANCHY})
    check("complicating does not move it", base["metrics"]["m.py"]["f"] == before,
          str(base["metrics"]["m.py"]["f"]))


def test_reformatting_does_not_trip_the_ratchet() -> None:
    """Without slack on `length` the ratchet fires on a docstring."""
    print(NL + "- a docstring is not a degradation -")
    base = R.make_baseline({"m.py": SIMPLE})
    documented = ("def f(a):" + NL + '    """Return a.' + NL * 2 + "    Longer prose." +
                  NL + '    """' + NL + "    return a" + NL)
    check("adding a docstring is silent", R.regressions(base, {"m.py": documented}) == [])
    padded = "def f(a):" + NL + ("    x = 1" + NL) * 20 + "    return a" + NL
    check("but twenty new lines is not", len(R.regressions(base, {"m.py": padded})) >= 1)


def test_exemption_is_one_call_and_carries_a_reason() -> None:
    print(NL + "- the escape hatch has to be cheaper than the argument -")
    base = R.make_baseline({"m.py": SIMPLE})
    R.exempt(base, "f", "the parser genuinely has this many cases")
    check("the exempt callable is silent", R.regressions(base, {"m.py": BRANCHY}) == [])
    check("and the reason is in the note", "parser" in base["exemptions"]["f"])
    try:
        R.exempt(base, "g", "  ")
        check("an empty reason is refused", False, "no exception")
    except ValueError:
        check("an empty reason is refused", True)


def test_the_checker_reports_one_finding_and_names_the_axis() -> None:
    print(NL + "- one finding, and it is actionable -")
    worse = ("def f(a):" + NL + "    if a:" + NL + "        for i in a:" + NL +
             "            if i:" + NL + "                return 1" + NL + "    return 0" + NL)
    note = {"id": "i-test", "message": "the ratchet", "baseline":
            R.make_baseline({"m.py": SIMPLE})}
    out = R.checker({"m.py": SIMPLE}, {"m.py": worse}, note)
    check("exactly one finding", len(out) == 1, str(out))
    check("cyclomatic is reported before nesting",
          "cyclomatic" in out[0].subject, out[0].subject)
    check("the evidence counts the rest",
          "regression(s)" in out[0].evidence, out[0].evidence)
    check("a clean diff produces nothing",
          R.checker({"m.py": SIMPLE}, {"m.py": SIMPLE}, note) == [])
    check("a note with no baseline produces nothing",
          R.checker({"m.py": SIMPLE}, {"m.py": worse}, {"id": "x"}) == [])


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_metric_counts_what_the_standard_counts,
               test_the_metric_does_not_count_what_feels_like_a_decision,
               test_the_two_rules_that_are_easy_to_get_backwards,
               test_it_agrees_with_ruff_on_this_repository,
               test_the_scanner_sees_callables_behind_an_import_guard,
               test_the_other_three_metrics,
               test_the_import_graph_only_knows_modules_it_was_given,
               test_cycles_are_components_not_a_catalogue_of_restatements,
               test_fan_in_and_fan_out,
               test_the_ratchet_is_differential_not_absolute,
               test_new_code_has_no_baseline_to_be_worse_than,
               test_the_baseline_only_moves_down,
               test_reformatting_does_not_trip_the_ratchet,
               test_exemption_is_one_call_and_carries_a_reason,
               test_the_checker_reports_one_finding_and_names_the_axis):
        fn()
    print(f"\ncomplexity and ratchet: {PASSED} passed, {FAILED} failed, {SKIPPED} skipped")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
